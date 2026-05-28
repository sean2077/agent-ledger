# Changelog

All notable changes to `agent-ledger` / `agent-relay` are tracked here.
Pre-1.0; expect occasional breaking changes between minor versions until
the protocol stabilizes.

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
