# agent-relay troubleshooting playbook

Read this when a relay command fails or surprises you, or preflight reports
something you don't understand. It complements — never overrides — the hard
rules in SKILL.md, and the CLI's own stderr hints always name the immediate
next step: follow those first.

## 0. Locating relay

SKILL.md resolves the binary fresh each turn because the skill runtime does not expose a
portable `$SKILL_DIR`: explicit `RELAY_BIN`, then `PATH`, then — only when both
miss — fallback candidates for installs that never put `relay` on `PATH`:
project-local skill installs
(`$ROOT/{.agents,.claude,.codex}/skills/agent-relay/bin/relay`), a repo that
vendors the skill (`$ROOT/skills/agent-relay/bin/relay`), and per-user skill
installs (`$HOME/{.codex,.claude,.agents}/…`, where `npx skills add … -g`
lands). If everything misses, relay is not installed on this host — surface
the `npx skills add` command from the error to the user. To pin a specific
build (e.g. a development checkout), export `RELAY_BIN`; it always wins.

## 1. Preflight results

Exit levels: `0` ok (may still carry non-blocking warns), `1` blocking
warnings (continue but report them to the user), `2` fail (stop; no
bootstrap / claim / publish / sync / close).

Non-blocking warns (reported, but do not bump the exit code):

- `fs.mtime_monotonic` "mtime unchanged … coarse resolution" — typical on
  sshfs with attribute caching; the protocol relies on `.sha256` + `.ready`
  sentinels, not mtime.

Warns that bump exit to 1 (examples):

- `fs.posix_mode` "mode 0xxx exceeds target 0700" — privacy preference, not
  protocol-breaking; flag it to the user.
- `pair.binding` stale/missing — resolve with `relay pair ensure`; a binding
  problem is always a warn, never a fail.

Fails that MUST block:

- missing required env (other than `RELAY_SHARED_ROOT`, which defaults to the
  project's `.shared/`);
- `project.consistency` mismatch — `$RELAY_PROJECT` disagrees with the git
  toplevel; show the user both values and ask which is correct;
- `fs.tmp_rename` / `fs.fsync_readback` — atomic writes are unreliable on
  this filesystem; do not write anything;
- `mount.sentinel` failing after a clean `init` — `init` self-heals first-run
  setup, so a persistent sentinel failure means the mount itself is broken
  (or `RELAY_SHARED_ROOT` points somewhere unwritable). Tell the user; stop.

## 2. Binding and pair resolution

- `relay status` errors `multiple active pairs` → this instance isn't bound:
  `relay pair ensure` auto-binds the sole compatible pair or lists
  candidates; `relay pairs list` is the discovery view; or pass
  `--pair-id <slug>` explicitly.
- `pair ensure` reports `degraded` → no stable session id (no platform id /
  tty signal); pass `--pair-id` per command or
  `export RELAY_AGENT_SESSION_ID=<stable per-window value>`.
- `pair ensure` reports `full` → every pair already holds two live instances
  (or one already holds your author); bootstrap a new pair or wait.

## 3. Claim / publish failures

- publish rejects "prompt_for_next still contains placeholder" or "body is
  empty" → the draft still carries scaffold text; fill it with
  `relay draft set` and retry.
- "could not allocate sequence/published path after 10 attempts" → evidence
  of concurrent activity or stale state; run `relay doctor` and stop for the
  user (SKILL.md hard rule 6) rather than retrying blind.

## 4. Sync aborts

- "fuse mount" → shape A: the project root IS the mount; edits land on the
  remote filesystem directly, nothing to sync.
- a `RELAY_SYNC` reason → this side isn't the rsync owner (`RELAY_SYNC=none`
  explicitly or by default); only the `RELAY_SYNC=rsync` side may push/pull.
- Everything else (gitignore negation-rule warnings, `--strict-gitignore`,
  `--delete`, first-sync safety, SSH errors): `rsync-recipes.md`.

## 5. `unsupported_schema`

Any read-only surface (`preflight`, `whoami`, `pairs list`, `doctor`) may
report `unsupported_schema`: this relay build cannot safely interpret that
session or binding record. Treat it as a compatibility blocker for that
record — do not claim, publish, close, archive, or auto-rebind it; upgrade
relay first, or report the blocker if upgrading is outside your authority.
`doctor --fix` deliberately leaves unsupported-schema records untouched.

## 6. Stale state and `relay doctor`

Unsure what `.shared/` holds (stuck drafts, leftover heartbeats, incomplete
publish triads)? `relay doctor` is a read-only report. `--fix` cleans
owner-safe junk (dead pidfiles); `--fix --older-than 1h` additionally removes
abandoned drafts and incomplete publish triads older than the threshold.
Doctor never signals a live PID.

## 7. Pair clutter and the archive

`relay close` auto-archives the pair it closes (v1.5.0+). For pairs closed
earlier or with `--no-archive`: `relay pairs archive --terminated` sweeps all
closed/terminal pairs into `.shared/_archive/`; `relay pairs archive <slug>`
moves one (refuses an active pair unless `--force`, which shelves it and
drops its bindings); `relay pairs list --archived` lists the archive;
`relay pairs restore <slug>` brings one back unchanged. **User-initiated
maintenance only — never run archive operations inside the auto-loop.**
