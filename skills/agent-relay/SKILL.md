---
name: agent-relay
description: "Relay work between local Codex and remote Claude Code through a shared .shared/ directory. Use when user says: continue the relay, handoff to claude/codex, start a relay session, sync code to remote, check relay status. Project-agnostic; uses the `relay` CLI for mechanical ops and RELAY_* env vars for config."
metadata:
  requires:
    bins: ["relay", "bash"]
---

# agent-relay

You are part of a relay between local Codex CLI (`host` role) and remote interactive Claude Code (`remote` role). Each turn one side reads what the other published, does work, publishes a response containing instructions for the next turn. The protocol is **user-driven** — there is no autopilot loop.

The `relay` CLI does mechanical operations (atomic writes, sequence numbers, validation, rsync). **You** do everything that requires judgment: read peer's last message, decide what to do, write substantive content and clear instructions for the peer.

## Resolve `relay` once per turn

`relay` may not be on `$PATH`. Walk this chain at the start of each turn (project-local wins over global) and use `"$RELAY"` everywhere below:

```bash
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
for cand in \
    "$ROOT/.agents/skills/agent-relay/bin/relay" \
    "$ROOT/.claude/skills/agent-relay/bin/relay" \
    "$(command -v relay 2>/dev/null)" \
    "$HOME/.agents/skills/agent-relay/bin/relay" \
    "$HOME/.claude/skills/agent-relay/bin/relay" \
    "$HOME/.codex/skills/agent-relay/bin/relay" ; do
  [ -n "$cand" ] && [ -x "$cand" ] && { RELAY="$cand"; break; }
done
[ -n "$RELAY" ] || { echo "cannot locate relay CLI" >&2; exit 2; }
export RELAY
```

## Critical: every turn starts with preflight

Before any other action:

```bash
"$RELAY" preflight
```

Interpret the exit code as three levels:

| exit | meaning | what to do |
|---|---|---|
| `0` | no blocking issues | **continue**; the `checks` array may still contain non-blocking `warn` lines — surface any you see in your final report |
| `1` | blocking warnings | **continue**; emphasize the warn lines in your report so the user can decide |
| `2` | fail | **stop and report**; do not bootstrap / claim / publish / sync / close |

`warn`s currently classified as **non-blocking** (still appear in `checks`, do not bump exit to 1):
- `fs.mtime_monotonic` "mtime unchanged …coarse resolution" — typical on sshfs with attribute caching; the protocol uses `.sha256` + `.ready` sentinels, not mtime.

Other `warn`s still bump exit to 1 (e.g. `fs.posix_mode` "mode 0xxx exceeds target 0700" — privacy preference, not protocol-breaking, but worth flagging).

`fail` examples that MUST block: missing env vars, missing `.shared/_relay/.sentinel` (mount dead), `project.consistency` mismatch, `tmp_rename` or `fsync_readback` failures (atomic write unreliable).

## Decide intent from user input

Read `{{ARGUMENTS}}` and the most recent user message. Pick exactly one intent. **Default is `handoff`.**

| Intent | User phrasing examples |
|---|---|
| `handoff` (default) | "continue the relay", "respond to claude", "review the plan", "fix what they asked", anything implying "do the next round" |
| `bootstrap` | "start a relay session about X", "set up relay for this project", or when `relay status` shows no active session |
| `status` | "what's the relay state", "show me the session", "who needs to act next" |
| `sync` | "push to remote", "pull from remote", "sync the code" |
| `close` | "close the session", "we're done", "wrap up the relay" |

If user input is ambiguous → ask once, then proceed.

---

## Intent: handoff (default)

This is the core 95% case. Take one full turn in the relay.

1. **Read state**: `relay status` (use `--json` if you need to parse). Note the active session path, latest published file, and `next-seq`.
2. **Read the peer's latest message**: use your Read tool on the latest published `.md` whose `peer` field is you (or `author` is the other side). Pay attention to its `prompt_for_next` block — that's what the peer wants from you.
3. **Do the work**: this is the part the CLI cannot do. Plan / review / write code / debug / investigate. Use Read, Edit, Bash, Grep, Glob as needed. Keep track of any non-`.shared/` files you change (you'll list them under `touched_paths`).
4. **Claim a draft**:
   ```bash
   DRAFT=$("$RELAY" claim --kind <kind> --in-reply-to <peer-seq>)
   ```
   `kind` is one of: `plan | review | fix | note | question | decision | correction | addendum`. The CLI creates a hidden `.draft/NNN-<you>-<kind>.md` with frontmatter scaffold; body is a placeholder.
5. **Fill the draft**: use your Edit tool on `$DRAFT`. Replace the placeholder body with your substantive content. **Critical**: replace the `prompt_for_next: |` block — the scaffold has `TODO: ...` and `publish` will reject anything still containing `TODO:`. See "Writing prompt_for_next" below.
6. **Set `sync_needed: true`** in frontmatter if you modified any non-`.shared/` files. List them under `touched_paths`.
7. **Publish**:
   ```bash
   "$RELAY" publish "$DRAFT"
   ```
   On success: file moves out of `.draft/`, sha256 + ready sidecars appear. On rejection: CLI prints which field failed validation; fix the draft and retry.
8. **Sync if needed** (host only, see Intent: sync). First time push? **always `--dry-run` first**.
9. **Report to user** what you did, the published path, and whether sync is pending.

### Writing `prompt_for_next` well

This is the part of the artifact that determines whether the peer can act effectively. Bad `prompt_for_next` → wasted round.

- Be specific. Reference files by path and lines if relevant.
- Set acceptance criteria. "Do X such that test Y passes" beats "do X".
- Note risks or open questions the peer should address.
- If the next round needs a specific `kind`, say so: "Please respond with `kind: review`."
- If you're blocking on user input, write `prompt_for_next:` directed `@user:` and set `status: timed_out` on publish.

Avoid:
- Vague verbs without context ("review this", "improve that").
- Burying the actual ask in prose. Lead with a bulleted instruction list.
- Re-stating background you both already share.

---

## Intent: bootstrap

Run this when starting a new project-session, **not** when continuing an existing one.

```bash
"$RELAY" bootstrap --topic <slug> [--title "Human readable"]
```

`<slug>`: lowercase ASCII + digits + `-`, ≤ 48 chars. Examples: `auth-refactor`, `prod-incident-2026-05`. The CLI prefixes with today's date to form session ID `YYYYMMDD-<slug>`.

After bootstrap, immediately do `handoff` to write the first artifact (typically `kind: plan` or `kind: question`).

If `relay status` already shows an active session, **do not bootstrap silently** — ask the user whether to continue the existing session or close it before starting a new one.

---

## Intent: status

```bash
"$RELAY" status            # human-readable
"$RELAY" status --json     # machine-readable (you can parse)
"$RELAY" status --last 5   # only most recent 5 artifacts
```

Report to user:
- Active session path
- Latest published artifact (seq, author, kind, status)
- Whether session is still active (`is_active` field)
- Next available seq
- Any drafts sitting in `.draft/` (someone interrupted mid-claim)

---

## Intent: sync

Only on `host` role. Remote cannot SSH back to host, so all rsync originates from host.

```bash
"$RELAY" sync push --dry-run    # ALWAYS first
"$RELAY" sync push              # then real push
```

Pull works the same way:

```bash
"$RELAY" sync pull --dry-run
"$RELAY" sync pull
```

`--strict-gitignore` switches to git-backed file list (honors `!path` reverse rules; required if `.gitignore` uses them). Use when the default-mode banner warns about negation rules.

`--delete` mirrors deletions; **off by default**. Only enable when the user explicitly says "mirror" or "delete extras".

If `cmd_sync` reports "this project root is a fuse mount" → that's shape A (whole project mounted from remote). No sync needed; edits land on remote directly.

---

## Intent: close

```bash
"$RELAY" close --reason "what concluded" --outcome approve
```

Writes `CLOSED` sentinel and updates `session.json` state to closed. **Does not modify prior published files** (append-only invariant).

If user wants a final synthesis on record, do a `handoff` first with `relay claim --kind decision`, fill it with the synthesis, then `publish` and only then `close`.

---

## Hard rules

1. **Never edit a file under `.shared/<project>/<session>/` that has a `.ready` sidecar.** Those are append-only published artifacts. Corrections go via `relay claim --kind correction` with the `corrects:` field pointing to the original seq.
2. **Never write `.sha256` or `.ready` sidecars yourself.** `relay publish` does that. If a sidecar is missing on a `.md` you published, something failed — re-run publish or escalate.
3. **Never ls `.draft/` from peer's side.** Drafts are hidden by convention. `relay status` correctly excludes them.
4. **Never bypass `relay preflight`.** If it fails, the mount is broken or env is wrong; writing anywhere risks data loss.
5. **Never `relay close` without checking with user.** Close is intended; missed by accident, it's awkward to recover from.
6. **If `relay claim` fails twice, stop and ask the user.** Two seq collisions in a row means concurrent activity you don't understand.

## When things go wrong

- **`relay preflight` fails `mount.sentinel`**: the sshfs mount is broken or you're running outside a relay-bootstrapped project. Tell user; do not write.
- **`relay preflight` fails `project.consistency`**: `$RELAY_PROJECT` env var doesn't match the git toplevel. Tell user the two values; ask which is correct.
- **`relay publish` rejects with "prompt_for_next still contains placeholder"**: you forgot to replace the `TODO: ...` line. Edit the draft and retry.
- **`relay publish` rejects with "body is empty"**: scaffold body is the placeholder comment; replace it with real content.
- **`relay sync push` aborts with "fuse mount"**: shape A — project root IS the mount, nothing to sync.
- **`relay sync push` aborts with "must run on host"**: you're on remote; remote cannot sync. Tell user; the host side must run the push.

## References

- `references/file-protocol.md` — full schema for session.json, frontmatter, terminal states, append-only rules, concurrency
- `references/rsync-recipes.md` — default vs strict-gitignore tradeoffs, shape A vs B, SSH troubleshooting
