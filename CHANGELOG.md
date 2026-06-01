# Changelog

All notable changes to `agent-ledger` / `agent-relay` are tracked here.
Pre-1.0; expect occasional breaking changes between minor versions until
the protocol stabilizes.

## 0.15.1 — 2026-06-01

Relay CLI lookup guidance and hook parity fix.

### Fixed

- `SKILL.md` now documents a shorter relay CLI resolution flow that keeps
  project-local skill installs (`.agents`, `.claude`, `.codex`, and repo
  `skills/`) ahead of `PATH`, so a stale global symlink cannot shadow the
  checked-out CLI.
- The hook dispatcher lookup chain now includes the project-local `.codex`
  skill install path and stays covered by docs-consistency tests.

## 0.15.0 — 2026-05-31

Claim-time liveness and command-surface cleanup.

### New

- `relay claim` now auto-starts a renewal-file heartbeat for the claimed draft.
  If heartbeat startup fails, claim rolls the draft back so no published turn can
  leave an uncovered in-flight draft by accident. `--no-heartbeat` and
  `RELAY_CLAIM_NO_HEARTBEAT=1` remain escape hatches for tests and advanced use.
- `relay heartbeat start` now defaults to `--owner-kind renewal-file`; the
  `agent`, `tool-process`, `pidfile`, and `none` modes are still available for
  advanced or legacy callers.

### Fixed

- A second claim while an author heartbeat is already running now fails closed
  and rolls the new draft back instead of keeping a draft with no heartbeat
  sidecar. The rc=3 path also removes any newly seeded renewal token before it
  returns, so rollback does not leave an orphan local liveness file.
- The protocol docs and docs-consistency tests no longer refer to retired
  `relay sessions list`, `--session-id`, public `relay next-seq`, or
  `.active-session` close semantics as live surfaces.

### Breaking changes

- The public `relay next-seq` command is removed. Sequence allocation is internal
  to `relay claim`, which reserves draft names atomically.
- The internal/listing function name follows the public surface:
  `cmd_sessions_list` is now `cmd_pairs_list`.

## 0.14.0 — 2026-05-31

Identity simplification and agent-facing documentation refresh.

Note: 0.14.0 is a logical changelog milestone; it shipped inside the v0.15.0
tag (commit `72a5cbc`) and has no standalone `v0.14.0` git tag.

### New

- Author identity auto-detects from platform signals:
  `CLAUDE_CODE_SESSION_ID` resolves `claude`, and `CODEX_THREAD_ID` resolves
  `codex`. `RELAY_AUTHOR` is now only a fallback/override for custom agents or
  an explicit disambiguator when both platform signals are present.
- Runtime peer resolution now comes from `session.json` participants via
  `resolve_peer()`. `RELAY_PEER` is no longer read at runtime.
- `relay whoami` and `relay preflight` report author source, platform conflicts,
  dual-platform ambiguity, derived peer state, and binding health.
- `AGENTS.md` is now the authoritative project onboarding doc, with
  `CLAUDE.md` symlinked to it.

### Fixed

- Identity-boundary commands fail closed when author or peer cannot be resolved.
  In particular, `relay claim` refuses malformed pair participants before
  creating `.draft/`, so it cannot scaffold `author: unknown` or `peer: unknown`
  artifacts.
- `relay bootstrap` validates author and peer before creating a pair directory;
  canonical `claude`/`codex` pairs derive the peer automatically, while custom
  agents pass `--peer`.

### Breaking changes

- Same-host claude+codex setup is zero-config. `relay init --same-host` now
  explains that no identity env is needed and no longer writes a per-host
  same-host envrc.
- `envrc.same-host.example` is removed. Only the dispatcher template remains,
  and only the rsync transport owner normally needs a per-host env file.

## 0.13.0 — 2026-05-29

Multiple concurrent **pairs** per project, keyed by per-instance bindings. The
collaboration unit (formerly "session") is now a **pair** of two **instances**,
where an instance is one running agent session identified by
`<author>:<agent-session-id>`. This lets two agent windows on one host each work
in their own pair without colliding. (Pre-1.0: the product/CLI is still named
`relay` / `RELAY_*` / `agent-relay`; only the collaboration-unit vocabulary
changed. A product rename is a separate future pass.)

### New

- **Instance identity.** `relay` resolves this instance's agent session id from
  `RELAY_AGENT_SESSION_ID` (hook-injected) → `CLAUDE_CODE_SESSION_ID` (Claude
  Code) → `CODEX_THREAD_ID` (Codex) → a persisted per-terminal fallback, so it
  never hard-fails. `relay whoami` prints it and the current binding.
- **Binding registry.** Each instance binds to one pair via a per-instance file
  `.shared/_relay/bindings/<author>-<sha256(full-id)[:24]>.json` (full id hashed
  — never a truncated prefix). This replaces the single global `.active-session`
  marker, so parallel pairs each resolve to their own pair with no `--pair-id`.
- **New commands.** `relay pair ensure` (smart resolver: use binding → auto-join
  the sole compatible pair → else report `choose`/`bootstrap`/`full`),
  `relay pair join <slug>` / `relay pair leave`, `relay whoami`.
- `relay bootstrap` binds its creator; `relay close` / terminal publish drop the
  closing instance's binding. `relay doctor [--fix]` GCs stale bindings (files
  only — never signals a PID).

### Breaking changes

- **`.shared/.active-session` is retired.** The global marker and its
  read/write/clear/`_check_active_marker` helpers are removed (no compat shim,
  pre-1.0). Existing markers are inert; `relay doctor` reports them as
  informational. Multi-pair users now bind via `relay pair ensure` / `pair join`.
- **`relay sessions list` → `relay pairs list`** (adds `bound_instances` /
  `open_slots`; JSON key `sessions` → `pairs`).
- **`--session-id` → `--pair-id`** on `status` / `next-seq` / `claim` / `close` /
  `wait` / `heartbeat stop|tick`.
- **Resolver errors reworded**: `no active session` → `no active pair`;
  `multiple active sessions` → `multiple active pairs`. Resolution now consults
  this instance's binding before falling back to the sole-active rule.
- **preflight** replaces the `session.active_marker` check with
  `session.binding` (bound→active = pass; missing binding = pass, or warn if >1
  active pair; binding→inactive pair = warn, not the old hard fail).

### Notes

- The published-artifact protocol is unchanged (filenames, frontmatter, sidecars,
  append-only, `session.json` schema stays v3). The agent session id is used only
  by the binding layer.
- **Same-agent pairs (claude+claude) are unsupported**: artifacts route by
  `author`, so `join`/`ensure` refuse a pair already holding a live same-author
  instance. The canonical claude+codex pair is unaffected.
- **Degraded identity fallback is ephemeral.** When no platform id / tty / atuin
  signal can distinguish two same-author windows on a host, `resolve_instance_id`
  mints a per-process id (never persisted), `pair ensure` returns `degraded`, and
  `pair join` refuses — so two such windows can't silently collapse onto one
  binding/pair. Pass `--pair-id` (or set `RELAY_AGENT_SESSION_ID`) instead.

## 0.12.0 — 2026-05-29

- `RELAY_SHARED_ROOT` is now optional for normal per-project setups. When it
  is unset, relay CLI commands default to the current git project's
  `.shared`; envrc templates leave the old
  `export RELAY_SHARED_ROOT="$PWD/.shared"` line commented as an explicit
  override only.
- `RELAY_SYNC` now defaults to `none` when unset. Only the rsync owner needs
  to opt in with `RELAY_SYNC=rsync`.

## 0.11.0 — 2026-05-29

Pre-1.0 cleanup: the `role` concept is fully retired. v0.6 split the old
unified "role" into explicit `RELAY_SYNC` (sync capability) + an
`--author/--peer/--sync` setup path, but left behind a migration/warning
layer and the `--role` flag name. Both are now gone. There is no `role`
anywhere in the CLI, env, docs, or tests.

### Breaking changes

- **`RELAY_ROLE` is no longer recognized at all.** v0.6 kept a fail-level
  `env.RELAY_ROLE.removed` preflight check (and matching `relay sync`
  refusal) that detected a leftover `RELAY_ROLE=host|remote` and printed a
  migration hint. That check is removed. `RELAY_ROLE` is now an ordinary
  unknown environment variable: ignored. If your `.envrc.<hostname>` still
  exports it, delete the line and set `RELAY_SYNC` instead
  (`host -> RELAY_SYNC=rsync`, `remote -> RELAY_SYNC=none`); otherwise
  preflight just falls through to the normal "RELAY_SYNC not set" failure
  outside shape A.

- **`relay init --role` is removed; use `--same-host`.** The only live
  value (`--role same-host`) is replaced by a boolean `--same-host` flag
  with identical behavior (copies `envrc.same-host.example` to
  `.envrc.<hostname>`; one file serves both terminals via a per-terminal
  `RELAY_AUTHOR` override). The already-rejected `--role host|remote` are
  gone from the parser entirely — passing `--role` is now an
  "unrecognized arguments" error. `--same-host` stays mutually exclusive
  with `--author/--peer/--sync`.

### Internals

- `Env.role` field and the `RELAY_ROLE` read in `load_env` removed.
- `_resolve_sync` lost its `role-removed` source; it now returns only
  `env` / `shape-a-infer` / `env-invalid` / `unset`.
- `cmd_preflight` and `cmd_sync` lost their `role-removed` arms.
- `_copy_envrc_template(role, ...)` is now `_copy_same_host_template(...)`.
- Docs (`README`, `SKILL.md`, `docs/why.md`), the committed `.envrc`, and
  `envrc.dispatcher.example` drop all `--role` / `RELAY_ROLE` references.
  The historical 0.6.0 migration table below is preserved as a record.

### Notes

- Tests: the v0.6 migration tests (preflight/sync/init `RELAY_ROLE` and
  `--role host|remote` arms) were deleted; two new tests assert a leftover
  `RELAY_ROLE` is now inert in `preflight` and `sync`. Suite 300 -> 294.

## 0.10.1 — 2026-05-29

Hooks robustness + consistency, driven by the first two entries the
v0.10 issue ledger captured (session `20260529-v010-hook-fixes`).

### Fixed

- **Hook fails open when its dispatcher is missing.** Previously, if
  `relay-hook.py` was absent (e.g. the package dir was moved/deleted),
  the rendered command `<python> <script>` errored at the OS level
  (`can't open file`, exit 2) and the host treated that as a blocking
  hook failure — wedging *every* `Edit`/`Write` until a human diagnosed
  it. Hit live during the v0.10 review session. `_hooks_render_command`
  now renders a shell-evaluated guard: `[ -f <script> ] || exit 0;
  exec <python> <script>`. Missing script → exit 0 (allow); present →
  exec python (whose `main()` already fails open on any internal
  error). Still PATH-independent (absolute interpreter; `[`/`exec` are
  shell builtins). Re-run `relay hooks install` to pick up the new
  command form.
- **hook-trail.log timestamps now match the project convention.** The
  trail and the Stop-hook state cache used `datetime.now(timezone.utc)`
  → UTC with microseconds (`...T01:23:01.690549+00:00`), inconsistent
  with `now_iso()` used everywhere else (local offset, no microseconds,
  e.g. `...T09:23:01+08:00`). Both now use a hook-local `now_iso()`
  mirroring `bin/relay`.

### Notes

- Bug source: this release fixes the two issues filed in the v0.10
  ledger (`major/hooks` dispatcher-missing, `minor/hooks` timestamp).
  Both are marked resolved there.
- Tests: fail-open command (missing + present script via `sh -c`),
  trail timestamp shape, and the updated path-independence installer
  regression (now parses the guarded `exec` form). Suite 298 -> 300.

## 0.10.0 — 2026-05-29

Adds a feedback channel. The tool is early-stage and agents hit rough
edges mid-session that previously evaporated unless a human noticed.
The issue ledger captures them durably for later triage.

### Added

- `relay issue` — a user-local machine feedback ledger, separate from
  the per-session `.shared/` ledger:
  - `relay issue add --title T [--severity minor|major]
    [--area cli|hooks|docs|protocol|tests|build|other]
    [--body TEXT | --body-file PATH|-]` records one issue file.
  - `relay issue list [--status open|resolved|all] [--area A] [--json]`.
  - `relay issue show <id|prefix> [--json]` and
    `relay issue resolve <id|prefix> [--note "fixed in <sha>"]`.
  - Stored at `~/.agent-ledger/relay-issues/` (override
    `RELAY_ISSUES_DIR`), one file per issue with frontmatter
    (`id`, `created`, `reporter`, `project`, `session`, `severity`,
    `area`, `title`, `status`, `resolved_at`, `resolution`). The store
    is intentionally outside any repo and outside `.shared/` so it
    persists across every project/session for the user on this machine;
    `relay sync` never moves it. Issues are a mutable tracker (resolve
    rewrites in place), not append-only artifacts.
  - Safety: `show`/`resolve` validate the id/prefix against
    `[0-9A-Za-z-]` before composing any path, so a reference cannot
    escape the store via `..`, absolute paths, or glob metacharacters;
    `add` reserves files with `O_CREAT|O_EXCL`; `list` surfaces
    unreadable files (stderr warning + `unreadable` array in `--json`,
    exit 1) instead of silently dropping them; ambiguous prefixes are
    reported distinctly from "not found".
- SKILL.md "Filing issues" section instructing agents to record
  tool-level problems via `relay issue add` before moving on.
- file-protocol.md §14 documenting the issue ledger.

### Changed

- Version bumped to 0.10.0 across `bin/relay`, the hook dispatcher,
  README, and the file-protocol header.

### Fixed (codex v0.10 review, pre-tag)

- **BLOCKER** `issue show`/`resolve` accepted raw path-like ids and
  could read/rewrite files outside `RELAY_ISSUES_DIR` (e.g.
  `relay issue resolve ../outside`). Now validated against a strict id
  alphabet with a defense-in-depth parent-dir check.
- **MAJOR** malformed issue files silently vanished from `issue list`.
  Now surfaced (stderr warning + JSON `unreadable` + exit 1).
- **MINOR** ambiguous prefixes reported as "not found". Now distinct,
  listing the candidate ids.
- Design-call follow-ups: `add` uses `O_CREAT|O_EXCL` reservation;
  `issue show --json` added; docs reworded "machine-global" →
  "user-local machine" and note that `relay sync` never moves issues.

### Notes

- v0.9.0 (commit `d86ca7a`) was independently verified by codex
  (approve-verified) and is the recommended tag point for the audit
  release; 0.10.0 is the first post-audit feature on top of it.

## 0.9.0 — 2026-05-29

Full-audit cleanup release. A cross-review session
(`.shared/20260529-full-audit-cleanup/`) surfaced two BLOCKERs and
seven MAJORs in the v0.8.0 surface. A follow-up independent
verification session (`.shared/20260529-v090-cross-verify/`) where
codex audited those fixes before push then found one more BLOCKER and
four further issues in the v0.9.0 code itself. All are fixed in this
version; suite went from 207 to 261 tests with regressions for each
finding.

### Fixed (codex cross-verification round, pre-push)

- **BLOCKER** `relay draft set` could rewrite another author's draft.
  The new fill primitive checked only path-under-`.draft/`, never
  author ownership, reopening the authorship-forgery class at the new
  write boundary (a peer can discover draft paths via `relay status`).
  Now requires `RELAY_AUTHOR` and refuses when it != the draft author.
- **MAJOR** `relay publish` skipped the author guard when
  `RELAY_AUTHOR` was unset (`if env.author and ...` fail-open). Publish
  is the authorship boundary and now fails closed on missing identity,
  including the `--force` terminal-note path.
- **MAJOR** `corrects` could be published on any kind, including
  self-references (`plan` with `corrects: 1`). Now only
  `correction`/`addendum` may carry it, value must be a positive int
  strictly less than `seq`, enforced at claim, `draft set`, and publish.
- **MINOR** `file-protocol.md` still described v0.8 atomic-rename
  publish semantics; rewritten around the v0.9 exclusive
  `O_CREAT|O_EXCL` reservation, sidecars-last ordering, and
  incomplete-triad invisibility guarantee.
- **MINOR** marker-disambiguated parallel mode passed preflight but
  default commands still failed with "multiple active sessions".
  `resolve_active_session()` now honors `.active-session` to
  disambiguate when N>1 sessions are active, so preflight-pass implies
  command-success.

### Added

- `relay claim --corrects <seq>` — first-class CLI for the
  documented correction workflow. Required when `--kind correction`;
  optional for `--kind addendum`. Pre-fix the flag was documented
  but absent from the parser, so every `kind: correction` artifact
  shipped with `corrects: null`.
- `relay draft set <path> --body-file <p|->
   --prompt-for-next-file <p|-> [--sync-needed]
   [--touched-path P ...] [--corrects N]` — fill a draft's body and
  prompt without using the agent's native Write tool. Removes the
  Read→Write round-trip that wasted tokens on every artifact.
- `atomic_reserve_text(path, text, mode)` primitive — O_CREAT|O_EXCL
  reservation for the published `.md` path. The brief partial-content
  window between create and fsync is gated by the `.ready` sentinel
  so protocol-compliant readers never observe it.
- `latest_published_seq(session)` helper — drafts-excluded wait
  baseline so a peer publishing the draft they had open at wait
  entry is no longer filtered out.

### Fixed

- **BLOCKER** `relay wait` could miss the very peer publish it was
  waiting for if peer had a draft visible at wait entry. The
  baseline came from `latest_seq()` which included `.draft/*`. New
  helper `latest_published_seq()` walks ready-published artifacts
  only. Live repro reproduced in the audit session itself.
- **BLOCKER** `relay publish` was a check-then-replace race, not
  exclusive promotion. `atomic_write_text` uses `os.replace` which
  is unconditional, so two publishers could pass `exists()` and
  both clobber the final `.md`. `atomic_reserve_text` raises
  FileExistsError instead; the publish retry loop bumps seq and
  emits a stderr line per bump so concurrent activity is observable.
- **MAJOR** `relay publish` validated filename-vs-frontmatter author
  agreement but never checked either against `$RELAY_AUTHOR`. Now
  refuses to publish a draft authored by someone other than the
  active env author, with a clear error naming both identities.
- **MAJOR** PreToolUse hook protected `.md` but left `.ready` and
  `.md.sha256` sidecars editable/deletable. Codex demonstrated
  deleting a `.ready` file slipped past. The hook now treats the
  triple as a unit; an Edit/Write/Delete to any one of the three is
  denied when the `.ready` sentinel exists.
- **MAJOR** `relay preflight` refused to pass when two active
  sessions existed without a marker, blocking the documented
  `bootstrap --force` parallel-session flow. Downgraded from fail
  (exit 2) to warn (exit 1); detail names both sessions and tells
  the caller to use `--session-id`.
- **MAJOR** `project.consistency` compared raw `RELAY_PROJECT` to
  raw git toplevel basename, so a correctly-sanitized slug in a
  dotted/underscored repo (`actibot_ego.jy` ↔ `actibot-ego-jy`)
  failed preflight. Both sides now canonicalize through
  `sanitize_project_slug` before comparison; detail shows both raw
  and canonical forms.
- **MAJOR** `relay init --author/--peer` wrote values verbatim into
  a sourceable shell file with no validation, opening command
  injection (`;`, `$`, backticks, newlines) and silent breakage on
  spaces. Now validated against the same `SLUG_RE` that artifact
  filenames use; `author == peer` is also rejected.

### Changed

- SKILL.md opening rewritten: was "no autopilot loop", which
  contradicted step 10's auto-loop semantics. New framing: "user-
  bootstrapped, auto-converging" with rule-based break triggers
  (kind: decision / terminal status / `@user:` escalation / round-cap).
- SKILL.md L264 no longer implies `RELAY_ROLE` is consulted for
  sync inference; only `RELAY_SYNC` matters since v0.6.
- `relay publish` inter-attempt sleep raised from 10-50ms to
  50-200ms so contending publishers spread out instead of hammering.
- `_render_envrc_body` now carries a two-line comment explaining
  the unconditional `unset RELAY_REMOTE_SSH RELAY_REMOTE_PATH`
  emitted on `--sync=none`, so users don't think their env was
  silently clobbered.

### Notes

- Suite size: 207 → 251 (+44 regressions; one per fix or close
  doc-drift loophole).
- No protocol/schema-breaking changes — pre-0.9 artifacts still
  parse and verify; pre-0.9 `relay claim --kind correction`
  callers will now see a hard error instead of silently emitting
  `corrects: null`.

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
