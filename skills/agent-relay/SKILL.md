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

`fail` examples that MUST block: missing env vars, missing `.shared/_relay/.sentinel` (mount dead), `project.consistency` mismatch, active-marker mismatch, `tmp_rename` or `fsync_readback` failures (atomic write unreliable).

## Decide intent from user input

Read `{{ARGUMENTS}}` and the most recent user message. Pick exactly one intent. **Default is `handoff`.**

**Zero-argument invocation** (just `$agent-relay` / `/agent-relay` with no following text) MUST be treated as `handoff`. Do not ask the user what to do — go run `handoff` immediately. The turn-check at the start of `handoff` will decide whether to act or report-and-stop.

| Intent | User phrasing examples |
|---|---|
| `handoff` (default) | "continue the relay", "respond to claude", "review the plan", "fix what they asked", anything implying "do the next round", **OR no arguments at all** |
| `bootstrap` | "start a relay session about X", "set up relay for this project", or when `relay status` shows no active session AND user wants to start one |
| `status` | "what's the relay state", "show me the session", "who needs to act next" — only when the user is explicitly read-only |
| `sync` | "push to remote", "pull from remote", "sync the code" |
| `close` | "close the session", "we're done", "wrap up the relay" |

If user input is ambiguous between handoff and something else → prefer handoff; the turn-check makes it safe.

---

## Intent: handoff (default)

This is the core 95% case. Take one full turn in the relay.

1. **Read state**: `"$RELAY" status --json` (or text). Note the active session path, latest published file, and `next-seq`.
   - If it errors with `multiple active sessions`, stop normal handoff, run `"$RELAY" sessions list`, report the candidates, and do not claim/close until a specific `--session-id` is chosen or the state is repaired.

2. **Turn check — STOP HERE IF NOT YOUR TURN**. Of the latest published artifact:
   - If its `peer` field equals `$RELAY_AUTHOR` (i.e. it's addressed to you), **continue** to step 3.
   - If its `peer` is someone else, the relay is waiting on them — **report state to the user and stop**. Example: "Latest is `002-gpt55-review.md` (gpt55 -> claude). Nothing pending for codex. Run `$agent-relay` again after claude publishes."
   - If there are no published artifacts yet, this session is freshly bootstrapped. Suggest bootstrap intent or ask the user what to write first; **do not silently claim**.
   - If the latest artifact's `status` is terminal (`closed | cancelled | failed | timed_out`), the session is effectively over. Report and stop.

3. **Read the peer's latest message**: use your Read tool on that latest `.md`. Pay attention to its `prompt_for_next` block — that is your task.
4. **Do the work**: this is the part the CLI cannot do. Plan / review / write code / debug / investigate. Use Read, Edit, Bash, Grep, Glob as needed. Keep track of any non-`.shared/` files you change (you'll list them under `touched_paths`).
5. **Claim a draft**:
   ```bash
   DRAFT=$("$RELAY" claim --kind <kind> --in-reply-to <peer-seq>)
   ```
   `kind` is one of: `plan | review | fix | note | question | decision | correction | addendum`. The CLI creates a hidden `.draft/NNN-<you>-<kind>.md` with frontmatter scaffold; body is a placeholder.
6. **Fill the draft**: use your Edit tool on `$DRAFT`. Replace the placeholder body with your substantive content. **Critical**: replace the `prompt_for_next: |` block — the scaffold has `TODO: ...` and `publish` will reject anything still containing `TODO:`. See "Writing prompt_for_next" below.
7. **Set `sync_needed: true`** in frontmatter if you modified any non-`.shared/` files. List them under `touched_paths`.
8. **Publish**:
   ```bash
   "$RELAY" publish "$DRAFT"
   ```
   On success: file moves out of `.draft/`, sha256 + ready sidecars appear. On rejection: CLI prints which field failed validation; fix the draft and retry.
9. **Sync if needed** (host only, see Intent: sync). First time push? **always `--dry-run` first**.
10. **Report + confirmation gate**. To the user, output:
    - One-line summary: what was published, where, sync state.
    - The 2-3 **key open questions** from your `prompt_for_next` — surface them at user-level so they see the decisions without opening the artifact.
    - An explicit fork — let the user pick the next step. Each option must include the **concrete next command/window** the user runs, not a vague "wait" or "do":
      - **(a) cross-review** — hand off to the peer agent. Tell the user literally where to go and what to type:
        - If peer is `codex` (on host): "Switch to your codex CLI and run `$agent-relay`."
        - If peer is `claude` (on remote / Claude Code): "Switch to Claude Code and run `/agent-relay`."
        - If peer is `gpt55` or another agent: name the runtime and the exact command.
        This is the default safeguard.
      - **(b) execute immediately** — skip peer review; this agent implements the proposals now in the current window. Use when scope is small, well-defined, and the user trusts the call. Record what was executed in a follow-up `kind: decision` or in the next turn's body — "execute" never means "no record".
      - **(c) discuss further** — stay in the current window and talk it through with the user before anything else moves.
    - **Stop and wait for user reply.** Do not silently invoke another tool, claim, or start work on (b) until the user confirms.

Applies to every publishing intent (handoff, bootstrap-then-claim, addendum, correction). Skip only for read-only intents (`status`, `preflight`).

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

Sessions are v0.3 flat directories at `.shared/<session-id>/`. The project slug is metadata in `session.json`, not a parent directory.

After bootstrap, immediately do `handoff` to write the first artifact (typically `kind: plan` or `kind: question`).

If `relay status` already shows an active session, **do not bootstrap silently** — continue the existing session or close it before starting a new one. `relay bootstrap --force` is only for intentionally creating parallel active sessions; follow with `relay sessions list` and explicit `--session-id` for later operations.

---

## Intent: status

```bash
"$RELAY" status            # human-readable
"$RELAY" status --json     # machine-readable (you can parse)
"$RELAY" status --last 5   # only most recent 5 artifacts
"$RELAY" status --session-id 20260527-topic
"$RELAY" sessions list     # recovery/discovery; works with zero or multiple active sessions
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

1. **Never edit a file under `.shared/<session>/` that has a `.ready` sidecar.** Those are append-only published artifacts. Corrections go via `relay claim --kind correction` with the `corrects:` field pointing to the original seq.
2. **Never write `.sha256` or `.ready` sidecars yourself.** `relay publish` does that. If a sidecar is missing on a `.md` you published, something failed — re-run publish or escalate.
3. **Never ls `.draft/` from peer's side.** Drafts are hidden by convention. `relay status` correctly excludes them.
4. **Never bypass `relay preflight`.** If it fails, the mount is broken or env is wrong; writing anywhere risks data loss.
5. **Never `relay close` without checking with user.** Close is intended; missed by accident, it's awkward to recover from.
6. **If `relay claim` fails twice, stop and ask the user.** Two seq collisions in a row means concurrent activity you don't understand.

## When things go wrong

- **`relay preflight` fails `mount.sentinel`**: the sshfs mount is broken or you're running outside a relay-bootstrapped project. Tell user; do not write.
- **`relay preflight` fails `project.consistency`**: `$RELAY_PROJECT` env var doesn't match the git toplevel. Tell user the two values; ask which is correct.
- **`relay status` reports `multiple active sessions`**: use `relay sessions list`, then rerun the intended command with `--session-id <session-id>` or repair the state before claiming.
- **`relay publish` rejects with "prompt_for_next still contains placeholder"**: you forgot to replace the `TODO: ...` line. Edit the draft and retry.
- **`relay publish` rejects with "body is empty"**: scaffold body is the placeholder comment; replace it with real content.
- **`relay sync push` aborts with "fuse mount"**: shape A — project root IS the mount, nothing to sync.
- **`relay sync push` aborts with "must run on host"**: you're on remote; remote cannot sync. Tell user; the host side must run the push.

## References

- `references/file-protocol.md` — full schema for session.json, frontmatter, terminal states, append-only rules, concurrency
- `references/rsync-recipes.md` — default vs strict-gitignore tradeoffs, shape A vs B, SSH troubleshooting
