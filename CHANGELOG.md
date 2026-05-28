# Changelog

All notable changes to `agent-ledger` / `agent-relay` are tracked here.
Pre-1.0; expect occasional breaking changes between minor versions until
the protocol stabilizes.

## 0.8.0 — 2026-05-29

The cross-platform hook layer release. This version keeps the manual
relay workflow intact while adding optional Claude Code + Codex CLI
hooks that surface relay state and protect published artifacts.

### Added

- `relay hooks install|uninstall|status|doctor` for managing optional
  Claude Code and Codex CLI hook wiring.
- Cross-platform hook dispatcher covering `SessionStart`, `PreToolUse`,
  and `Stop` events.
- Managed hook config fragments for Claude Code and Codex CLI.
- Hook protocol reference documenting payload shapes, emitted token
  prefixes, trust requirements, and non-goals.
- Dispatcher tests covering fast-path skip, published-artifact edit
  denial, Stop-hook deduplication, and installer merge stability.

### Changed

- `SKILL.md` now documents optional hook behaviour and the
  `[relay-state]` / `[relay-action]` / `[relay-hint]` prefixes emitted
  by the hook layer.
- Hook installation appends managed entries after existing hooks and
  preserves unmanaged config state byte-for-byte.

## 0.7.0 — 2026-05-28

The "uncrashable by input" pass. Six PRs widen the most common
false-error paths and add a discoverable cleanup surface, so an agent
that does the wrong thing under load gets a useful error instead of a
wedged session.

### Added

- `relay wait --no-timeout` flag: disable the nominal no-heartbeat
  deadline for long interactive turns. Stale-heartbeat exit 11 still
  fires when the peer renewal-file heartbeat goes stale, so a real
  crash is still detected.
- `relay doctor` subcommand: report stale state across `.shared/`
  (drafts, heartbeat pidfiles, sidecars, recovery_locks). Default is
  report-only; `--fix` cleans owner-safe junk (dead pidfiles); `--fix
  --older-than 1h` additionally deletes drafts older than the
  threshold. Doctor never signals a live PID.
- `--json` output mode on `relay doctor`.

### Changed

- Default `RELAY_WAIT_TIMEOUT` and `RELAY_RENEWAL_STALE_THRESHOLD`
  raised from 600s to 3600s. Long interactive agent turns are no
  longer false-timed-out or false-stale by default.
- `relay claim` and `relay publish` retry counts widened from 2 to
  10, with `random.uniform(0.01, 0.05)` jitter between attempts.
  Two simultaneous agents racing for a sequence number no longer
  trip the "could not allocate" error.
- Project slugs are auto-sanitized when derived from `git toplevel`
  (lower-case, fold non-slug chars into hyphens, clip to 48 chars).
  Explicit `RELAY_PROJECT` values are NOT sanitized — still validated
  strictly so user typos surface clearly. Bootstrap from a repo named
  `actibot_ego.jy` now produces project `actibot-ego-jy` instead of
  crashing.
- 8 stderr error exits now carry concrete recovery hints:
  - claim/publish collision → `relay doctor`
  - heartbeat already running → `relay heartbeat stop --force`
  - heartbeat owner-not-alive → suggest `--owner-kind renewal-file`
  - heartbeat cannot-derive-renewal → set `RELAY_PROJECT`
  - wait/claim no-active-session → `relay sessions list` / `relay
    bootstrap --topic <slug>`
  - wait/claim multiple-active-sessions → `--session-id <id>`
  - publish into inactive session → `relay bootstrap` or `--force
    --force-reason --status <terminal>`
  - sync missing `RELAY_REMOTE_*` → only the rsync owner runs sync
  - bootstrap bad-slug → set `RELAY_PROJECT` matching the regex
- `tests/test_relay_docs_consistency.py` gains a lint that greps the
  relay source for each error exit and asserts the recovery hint
  keyword sits within 400 chars. Drift gets caught at test time.

### Fixed

- Clean stale heartbeat pidfiles that have no matching heartbeat
  sidecar without signaling the recorded PID. This covers PID-reuse
  / no-sidecar states where the PID may now belong to an unrelated
  process. Robustness priority: a false kill is strictly worse than
  a leaked file. (M3 / PR1)
- Remove local renewal files when heartbeat GC cleans orphan pidfile
  state, so Ctrl-C or crash recovery does not leave stale renewal
  state behind. (PR1)
- `relay bootstrap` no longer crashes when the git toplevel name
  contains dots or underscores (real user bug: `actibot_ego.jy`).
  See "Changed" above for the sanitization rule.

### Notes

- `SKILL.md` Hard rule 6 was rewritten to reflect the new 10-attempt
  retry: the agent stops and asks the user only after the CLI's own
  retry budget is exhausted, and the recommended diagnostic is now
  `relay doctor`.
- `RELAY_HEARTBEAT_STALE_FACTOR` still floors at 60s; tuning the env
  var alone without changing the formula has no effect. Documented
  here so operators don't chase it.

## 0.6.0 — 2026-05-28

### Breaking changes

- **`RELAY_ROLE=host|remote` is removed.** It was a deprecated alias in
  v0.5 with a non-blocking warn; it now causes `relay preflight` to
  **fail** (exit 2) with an explicit migration hint. Mapping:

  | old (v0.5 alias) | new (v0.6+ required) |
  |---|---|
  | `RELAY_ROLE=host`   | `RELAY_SYNC=rsync` |
  | `RELAY_ROLE=remote` | `RELAY_SYNC=none`  |

  Edit your `.envrc.<hostname>` accordingly. Existing `RELAY_ROLE=` lines
  can be deleted once `RELAY_SYNC` is set.

- **Legacy envrc templates removed**:
  `skills/agent-relay/templates/envrc.host.example` and
  `envrc.remote.example` are gone. The remaining templates are
  `envrc.same-host.example` and the project-wide `envrc.dispatcher.example`.
  Anything you previously copied from the legacy templates already lives
  in your `.envrc.<hostname>` — only the source-of-truth files in
  `skills/.../templates/` were deleted.

- **`relay init --role host` / `--role remote`** no longer copy templates.
  They now print a migration hint pointing at either
  `--role same-host` (two terminals on one machine) or the new explicit
  flag set `--author/--peer/--sync` (any other topology). Exit code 2.

### New features

- **`relay init --author <name> --peer <name> --sync <none|rsync>`**:
  explicit-flags path that renders `.envrc.<hostname>` inline (no
  template copy). Mutually exclusive with `--role same-host`. Defaults
  `--sync` to `none` if only `--author`/`--peer` are given. When
  `--sync=rsync` is set, prints an advisory if `RELAY_REMOTE_SSH/PATH`
  aren't exported yet.

  Examples:
  ```bash
  # any same-side setup (one machine or shape-A two-machine)
  relay init --author codex --peer claude --sync none

  # side that owns rsync transport in a shape-B two-machine setup
  relay init --author codex --peer claude --sync rsync
  $EDITOR ".envrc.$(hostname -s)"   # fill REMOTE_SSH/PATH
  ```

### Internals

- `_resolve_sync` lost its `RELAY_ROLE` branches. New `role-removed`
  source surfaces the migration path. `NON_BLOCKING_PREFLIGHT_WARNS`
  no longer carries `env.RELAY_ROLE.deprecated`.
- `cmd_sync`'s `role-alias` refusal arms removed; new `role-removed`
  arm emits the migration hint.

### Migration steps for existing users

1. Open `.envrc.<hostname>` on each machine.
2. Replace `export RELAY_ROLE=host` with `export RELAY_SYNC=rsync`.
   Replace `export RELAY_ROLE=remote` with `export RELAY_SYNC=none`.
3. `source .envrc` (or `direnv reload`).
4. `relay preflight` — expect `env.RELAY_SYNC: pass` and no more
   `env.RELAY_ROLE.deprecated` warn line.

## 0.5.0 — 2026-05-28

### Added

- **`RELAY_SYNC=none|rsync`** env var as the durable sync-capability
  declaration. Identity (`RELAY_AUTHOR`/`RELAY_PEER`) and sync
  capability are now independent; topology is detected by preflight,
  not declared.
- **`relay init --role same-host`** and a new
  `templates/envrc.same-host.example` for two terminals on one machine.
  The template honors a pre-existing `RELAY_AUTHOR` export so one file
  serves both terminals.
- **`docs/why.md`**: long-form positioning page with vendor citations.
- **Top-level `KeyboardInterrupt` guard** in `bin/relay`'s `__main__`,
  so SIGINT during early CLI startup also exits 130 cleanly.
- **`RELAY_WAIT_READY_SENTINEL`** opt-in env var: `cmd_wait` touches
  the given path right before entering its poll loop (tests/supervisors
  can use it as a readiness signal).

### Changed

- README first paragraph + SKILL.md preamble lead with the Claude Code
  ↔ Codex CLI cross-review framing.
- preflight matrix rewritten; shape detection is now unconditional
  (no longer gated on `RELAY_ROLE=host`) so same-host setups reach the
  shape-A inference path. `RELAY_SYNC=rsync` + shape A is now a
  contradiction fail.
- `cmd_sync` gated on resolved `RELAY_SYNC=rsync` instead of
  `RELAY_ROLE=host`.

### Deprecated (removed in 0.6.0)

- `RELAY_ROLE=host|remote` as a sync-capability alias.
- `envrc.host.example` and `envrc.remote.example` templates.

## 0.4.0 and earlier

See `git log` for the history of stages 0–3 (heartbeat, recovery lock,
renewal protocol, dynamic peer detection, etc.) and the `.envrc`
dispatcher work that landed in 0.4.0.
