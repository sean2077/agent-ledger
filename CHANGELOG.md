# Changelog

All notable changes to `agent-ledger` / `agent-relay` are tracked here.
Starting at 1.0.0, compatibility follows the frozen contract in
`skills/agent-relay/references/file-protocol.md` §15.

## Unreleased

### Docs

- Restructure `SKILL.md` into an always-loaded core plus on-demand references;
  add `references/troubleshooting.md`; the fill step now uses
  `relay draft set` instead of hand-editing the draft.
- Compact the always-loaded `relay` resolver block while preserving its lookup
  priority; move the longer rationale into `references/troubleshooting.md`.

## 1.5.2 — 2026-06-10

### Docs

- Clarify Codex unified-exec relay waiting guidance: a long `relay wait` may
  become a background-terminal session, so Codex should poll with the longest
  available wait window and avoid assistant commentary on empty wakes.

## 1.5.1 — 2026-06-08

### Fixed

- `relay statusline` now renders the full pair slug, including any leading date
  prefix, instead of shortening it for display.

## 1.5.0 — 2026-06-08

### Added

- `relay close` now **auto-archives** the just-closed pair into `.shared/_archive/`
  so the top level stays uncluttered without a separate `relay pairs archive`
  step. The archive runs after the `CLOSED` sentinel + `session.json` are written,
  reusing the same move-then-drop-bindings path as `pairs archive` (atomic
  rename; bindings dropped only after a successful move). It is **best-effort**:
  the close itself is already durable, so an archive failure (e.g. a destination
  collision) is reported as a note and `close` still exits 0 — `relay pairs
  archive <slug>` can retry. Pass `--no-archive` to leave the closed pair under
  `.shared/`. The two commands stay separate: `pairs archive` still owns the
  `--terminated` sweep, `--force` active-shelving, and the `restore` counterpart.

## 1.4.2 — 2026-06-08

### Fixed

- `relay statusline` no longer hangs on non-agent surfaces when git or the
  shared mount stalls. The render relied on the agent host's ~1s `statusLine`
  timeout to bound it; surfaces without that reaper (`--watch` in a pane next to
  Codex, a user's own statusline script) could wedge indefinitely on a hung
  `git` (default `.shared/` anchoring shells out to git on every paint) or a
  stalled sshfs `.shared/` read — and the fail-quiet `try/except` cannot catch a
  blocked syscall. Two bounds now apply: a best-effort SIGALRM **render wall**
  (`RELAY_STATUSLINE_DEADLINE`, default `1.0`s) caps each paint, so `--watch`
  shows an idle line and keeps repainting instead of freezing; and every `git`
  subprocess is bounded (`RELAY_GIT_TIMEOUT`, default `10`s). Set either knob to
  `0` to disable. (D-state syscalls remain unkillable by any userspace bound.)

### Docs

- Install guidance now uses `npx skills add ...` for the public path and
  documents the local-checkout symlinks for `~/.agents/skills/agent-relay/bin/relay`
  and `~/.agents/skills/agent-relay/hooks`.

## 1.4.1 — 2026-06-04

### Fixed

- Hook dispatcher input and Stop status checks are now bounded so a half-open
  hook stdin pipe or slow `relay status --require-binding` cannot wedge the
  host hook until its outer timeout. Stop status failures now stay silent to the
  host while recording `status-timeout` / `status-failed` in
  `.shared/_relay/hook-trail.log`.

### Docs

- Codex relay guidance now makes the wait semantics explicit: the Stop hook can
  only auto-continue once the peer has already published; if the peer has not
  published yet, Codex should run foreground `relay wait --require-binding`
  instead of asking the user whether to wait.

## 1.4.0 — 2026-06-03

Statusline. A new `relay statusline` renders a compact, glanceable
"which pair / whose turn" line for Claude Code's command-backed `statusLine`,
plus a `--watch` dashboard for any terminal — including a tmux pane next to
Codex CLI, whose native statusline takes only a fixed enum of built-in items and
cannot run a command yet (openai/codex#20140 / #17827 / #20244). One primitive
feeds every surface; it wires into Codex unchanged the moment upstream lands
command-backed rendering.

### Added

- `relay statusline` — binding-scoped, **pure-read**, fail-quiet render of the
  bound pair's state: your move / waiting / peer writing / peer stale / `@user`
  pause / decision / terminal / fresh. `--json` emits structured state with a
  plain `text` line; `--watch [--interval N]` repaints in place until Ctrl-C;
  `--pair-id` pins a specific pair; color honors `--no-color` / `NO_COLOR`. The
  Claude `statusLine` payload's `session_id` (on stdin) drives binding-scoped
  identity, the same way the hook dispatcher reads it.
- `relay statusline install | uninstall | doctor` — wires `relay statusline`
  into Claude Code's `settings.json`. Because `statusLine` is a single slot
  (unlike the hook arrays), install **never clobbers** an existing user
  statusline: it refuses with a compose recipe, or `--force` replaces it.
  Uninstall removes only the relay-managed entry. Claude-only (Codex has no
  command-backed statusline yet).

### Notes

- The render path is a deliberate **pure read** (no `last_seen` bump, no GC): it
  runs on a ~300ms cadence over a possibly-sshfs mount, and it is binding-scoped
  like the Stop hook — an unbound window is never shown the lone active pair
  (issue 20260601T182646-2920d5b9).
- Fail-quiet is total: the stdin ingest is a **bounded `select` read** (a
  half-open / no-payload pipe like `sleep 5 | relay statusline` can never block
  the footer), and even a hard `SystemExit` from a helper (no shared root /
  outside a repo) degrades to an empty line at exit 0 rather than a rc-2 error
  in the bar.

### Compatibility

- Additive only. No session/binding schema change; no change to existing
  commands, artifacts, or sidecars. Skipping the new command is a no-op.

## 1.3.0 — 2026-06-03

Worktree robustness. Previously, if one agent operated from a git **linked
worktree**, `relay` resolved a *different* `.shared/` (via
`git rev-parse --show-toplevel`) than its peer in the main checkout — silently
splitting the ledger so the pair never saw each other. This release anchors the
ledger to the **main worktree** and records the authoring worktree in artifacts,
so two agents share one ledger with no `RELAY_SHARED_ROOT` change and without
relocating either window.

### Added

- `main_worktree()` resolves the stable **ledger anchor** (the main worktree)
  via `git worktree list --porcelain`, falling back to `git_toplevel()` for
  bare-backed first records, non-repos, missing git, or pre-`worktree` git.
- Optional `worktree_root` frontmatter field, auto-stamped by `relay claim` only
  when the author's content root differs from the ledger anchor (absent
  otherwise). Relative `touched_paths` are interpreted under it when present.
  Exposed in `relay status [--json]`.
- `relay whoami` and `relay preflight` surface the content root vs ledger anchor
  when they differ (preflight adds a non-blocking `project.worktree` check).

### Changed

- Ledger-anchoring call sites (`default_shared_root`, `derive_project`,
  preflight `shared_root.location` + `project.consistency`, `cmd_init`) now use
  the main worktree. Behavior is byte-identical for repos without linked
  worktrees (the main worktree IS `--show-toplevel`).
- `relay sync` deliberately stays on the **content root** (current worktree):
  sync mirrors the checkout being pushed/pulled, so a worktree author syncs that
  worktree's files — anchoring sync to the main worktree would push stale
  main-tree content and recreate the split on the remote.

### Compatibility

- Additive, **no schema bump** (frozen contract §15.2: new optional frontmatter
  field old readers ignore). 1.0 fixtures forward-read unchanged. Explicit
  `RELAY_SHARED_ROOT` still wins. Cross-host caveat: a `worktree_root` absolute
  path from host A may not exist on host B — same-host peers open it directly;
  cross-host peers sync first, then read relative `touched_paths` under the
  remote content root.

## 1.2.1 — 2026-06-02

Patch release for the timed-out wait/resume deadlock and the sshfs ready-sidecar
debugging note from the v1.2.0 review follow-up.

### Fixed

- `relay wait` now resolves a bound resumable `timed_out` pair and keeps waiting
  when the latest pause was authored by the current agent, so the peer can
  resume with a follow-up artifact.
- `relay wait` still exits 12 for a peer-authored `timed_out` artifact addressed
  to the current agent, instead of returning that pause as a successful reply.
- `relay heartbeat tick` now resolves bound resumable pairs, allowing an open
  resumed draft to keep its renewal-file heartbeat fresh while the latest
  published artifact remains `timed_out`.

### Docs

- `rsync-recipes.md` documents the exact publish sidecar names and the sshfs
  cache knobs to check when manual `.ready` inspection appears stale.

### Compatibility

- Additive, no schema bump. The change is limited to `wait`/heartbeat handling
  of already-resumable paused pairs; hard-terminal pair handling is unchanged.

## 1.2.0 — 2026-06-02

Release for the GPT-5.5 review triage hardening pass, covering the unreleased
1.1.1 fixes plus the B-bucket feature and contract work.

### New

- `relay version [--json]` now reports `relay_version`, `schema_version`,
  `binding_schema_version`, `package_dir`, and best-effort `git_sha`; the
  existing top-level `relay --version` flag is retained.
- Added a normative `docs/threat-model.md` covering the single-user trust
  boundary, out-of-scope same-user attacks, and the untrusted-peer operational
  policy.
- Added the frozen-triad/binding contract regression file and process-level
  concurrency tests for claim allocation and publish triad creation.
- Added a GitHub Actions workflow with an OS/Python pytest matrix plus
  production pyflakes, ruff, and shellcheck checks.

### Changed

- Relay-created files and publish sidecars now use private `0600` modes by
  default, matching the existing private shared-root directory posture.
- The issue ledger now writes `pair:` frontmatter for the active pair while
  still normalizing older `session:` issue records on read.
- Frontmatter parsing now fails closed on duplicate keys, oversized
  frontmatter/body/scalar/list fields, and disallowed control characters.
- Agent-facing docs now treat peer artifacts as untrusted operational input:
  the workflow guide, protocol reference, and AGENTS.md source-of-truth section
  point to the threat-model policy.

### Compatibility

- Additive, no schema bump. `relay version` is a new command, private file modes
  are permission tightening, issue `session:` records remain readable as legacy
  aliases, and parser caps reject malformed/pathological artifacts within the
  existing YAML subset rather than changing the on-disk schema.

## 1.1.0 — 2026-06-02

Pair-name visibility for precise pairing: make it obvious, from either side,
which pair a session is in and how the peer joins the *exact* same one.

### New

- `relay pair show [--json]` — prints this session's bound pair, its peer, and
  the exact `relay pair join <slug>` command the peer runs to pair with it. Reads
  the binding directly, so it answers "which pair am I in?" at any lifecycle
  stage (active, paused, or closed). `--json` for automation
  (`{pair, author, peer, peer_join_cmd, bound, ...}`).
- `relay bootstrap` now announces the pair name prominently and prints the
  peer's `relay pair join <slug>` command, so the other agent can pair precisely
  even when several pairs exist. (The human-readable label changed from
  `session_id:` to `pair name:`; the on-disk `session.json.session_id` field is
  unchanged.)

### Changed

- `SKILL.md`: the bootstrap flow surfaces the pair name to the user; the
  cross-review hand-off names the pair (`relay pair join <slug>`) instead of a
  bare "run agent-relay"; `relay pair show` is documented alongside `whoami`.

### Compatibility

- Additive, no schema bump (file-protocol.md §15.2): a new read-only command and
  richer human output; no on-disk surface changes.

## 1.0.1 — 2026-06-02

Post-1.0 maintenance: close the `timed_out` resume edge that 0.19.0 deferred
(issue 20260529T190821-0ec54be9).

### Fixed

- A `timed_out` round is now **resumable**. The skill tells a user-blocking
  agent to publish `--status timed_out` with a `@user:` line; that status stays
  terminal for *reading* (the peer's `relay wait` still exits 12 and stops), but
  the write/bind path no longer treats it as a dead end. Once the user answers,
  `relay claim` / `publish` / `pair join` / `pair ensure` supersede the
  `timed_out` latest with a new in-reply-to artifact — no `--force`, no fresh
  pair, in-thread continuity preserved. `closed` / `cancelled` / `failed` remain
  hard-terminal (only a `--force` terminal note may append).
- A *paused* (`timed_out`) pair now KEEPS its instance bindings. Binding GC is
  decoupled from the active-check: a binding is dropped only when its pair is
  truly dead (gone, or hard-terminal/closed), never on a mere pause. This closed
  the latent half of the deadlock where a passive `relay status --require-binding`
  resolve (the Stop hook, run every turn) garbage-collected the binding of a
  timed-out pair and stranded the round. `relay publish --status timed_out` no
  longer drops the publisher's own binding either.
- `relay status` now reports `resumable: <bool>` (true only for a paused pair:
  not active, `timed_out` latest, still writable) and prints a resume hint.

### Compatibility

- **No schema bump.** This is a writer-side relaxation: the on-disk `status`
  vocabulary and the frozen terminal set (`file-protocol.md` §15.1) are
  unchanged — `timed_out` is still a terminal status, and a 1.0 reader still
  treats a `timed_out`-latest pair as not-active (it simply never resumes one).
  Resuming is a normal append (§6.2), so a 1.0 reader parses a 1.0.1-written
  ledger with no ambiguity. New predicate `session_is_resumable` backs the
  write/bind path; `session_is_active` is unchanged for `wait`/discovery/report.
  Spec: `file-protocol.md` §4.3 + §5.

## 1.0.0 — 2026-06-02

The 1.0 protocol contract is frozen and binding.

### New

- `references/file-protocol.md` §15 is now the binding compatibility contract:
  the frozen on-disk surfaces define what 1.x readers must continue to read
  from 1.0 ledgers.
- The post-1.0 compatibility policy is in force. Breaking protocol changes now
  require a schema bump with readers for old and new records, or an explicit,
  documented, idempotent migration with clear refusal before migration.
- The pre-1.0 hard-remove window is closed. Deprecated behavior may no longer
  be removed silently without the §15 schema or migration path.

### Verified

- The v0.19 RC soak found and fixed the final publish-time participant-routing
  blocker before the freeze.
- The canonical 1.0 fixture, schema gates, incomplete-triad recovery,
  binding-scoped write boundaries, onboarding smoke, privacy/trust docs, and
  issue-ledger triage all passed the freeze-readiness review.

## 0.19.0 — 2026-06-01

1.0 RC metadata for the freeze-readiness soak.

### New

- Added first-run onboarding smoke coverage that exercises the real `relay`
  subprocess from a clean environment, proving setup, bootstrap, status, claim,
  draft-fill, publish, and peer wait recovery hints work outside the in-process
  test harness.
- The 1.0 RC gate is now satisfied across the seven freeze checkpoints:
  contract section, canonical fixture, schema read gates, incomplete triad
  recovery, quickstart privacy/trust docs, issue-ledger triage, and onboarding
  smoke.

### Fixed

- `relay claim` now resolves pairs with strict binding scope, preventing a
  same-author unbound window from writing into the lone active pair by fallback.
  This closes the remaining cross-talk write-boundary gap after the Stop-hook
  strict-status fix.
- `relay publish` now verifies that the draft author is one of the pair's two
  participants and that the draft peer matches the participant-derived route
  before any final-path reservation, including forced terminal publishes.
- Issue-ledger triage for the RC closed four already-resolved findings and left
  one timed-out resume edge deferred to post-1.0 maintenance, keeping the 1.0
  freeze gate explicit instead of silently carrying stale tool-feedback items.

## 0.18.0 — 2026-06-01

Recovery diagnostics, schema read gates, and quickstart trust-surface docs for
the 1.0 convergence path.

### New

- `relay doctor` now diagnoses incomplete published triads and orphan publish
  sidecars. `doctor --fix --older-than <duration>` removes only old,
  owner-safe remnants and preserves fresh heartbeat-covered in-flight publish
  state.
- Session and binding records now pass through read-time schema gates:
  `read_session_json` and `read_binding` validate `schema_version`,
  operational commands refuse unsupported future records cleanly, read-only
  surfaces report `unsupported_schema` without mutating those records, and a
  future-schema binding is never deleted by fallback recovery.
- `README.md` now includes a quickstart-visible "Privacy & trust surface"
  section covering ledger file modes, opt-in non-deleting sync behavior, and
  the local-only issue ledger.

### Fixed

- Active-pair discovery now skips unrelated future-schema or corrupt bystander
  pairs, while explicit `--pair-id` targets and bound future-schema pairs still
  fail closed.

## 0.17.0 — 2026-06-01

Candidate 1.0 contract freeze and compatibility guardrails.

### New

- `references/file-protocol.md` now has a candidate 1.0 frozen-contract section
  that defines which on-disk artifacts are compatibility commitments, how future
  relay versions must read older ledgers, and how schema/contract changes should
  be validated before a 1.0 release.
- The fail-closed regression matrix now covers the missing GAP-1/GAP-2 paths and
  the additional sub-gap around incomplete publish triads, keeping malformed or
  partial artifacts out of status, wait, publish, and repair-sensitive flows.
- A committed canonical 1.0 ledger fixture locks representative canonical 1.0 bytes,
  including active artifacts, sidecars, bindings, and archived pairs, so future
  readers can prove forward compatibility against the frozen 1.0 layout.

### Fixed

- Forward-read tests now exercise the canonical fixture through status, wait,
  doctor, pairs-list, archive exclusion, and frozen-byte hash guards instead of
  relying only on current-version generated test ledgers.

## 0.16.0 — 2026-06-01

Pair archival: declutter `.shared/` by moving terminated pairs aside.

### New

- `relay pairs archive <slug>` moves a terminated pair dir from `.shared/<slug>/`
  to `.shared/_archive/<slug>/`. `--terminated` sweeps every closed/terminal pair
  in one shot (continue-and-report; any failure exits non-zero, re-running is
  idempotent). `--force` also archives an active pair (shelve), dropping every
  binding that pointed at it — bindings are removed only *after* the atomic move
  succeeds, so a crash mid-archive never strands an active pair with no binding.
- `relay pairs restore <slug>` moves an archived pair back to `.shared/`. The
  pair keeps its on-disk state (closed stays closed, a shelved-active pair stays
  active) and is never auto-rebound — re-enter it with `relay pair join <slug>`.
- `relay pairs list --archived` lists pairs under `.shared/_archive/`.
- New reserved top-level dir `.shared/_archive/` (created `0700` on first
  archive). `iter_pair_dirs` skips it, so archived pairs are invisible to
  `status`, `wait`, `doctor`, `pairs list`, and active-pair resolution. It is
  distinct from the pre-existing pair-internal `archive/` reserved name.
- `archive` / `restore` (and `--pair-id`) validate the full pair slug
  (`YYYYMMDD-topic`) before composing a path, rejecting `/`, `..`, absolute
  paths, and the `_archive/<slug>` backdoor. Moves use `os.rename` (atomic,
  same-mount) and fail closed on a cross-device `EXDEV` rather than risk a
  partial copy.

## 0.15.3 — 2026-06-01

Per-instance bootstrap guard.

### Fixed

- `relay bootstrap` now refuses only when the current agent instance is already
  bound to an active pair. Active pairs owned by other instances no longer
  force `--force` for a new pair.

## 0.15.2 — 2026-06-01

Binding-scoped Stop hook and strict automation resolution.

### New

- `relay status --require-binding` now resolves only an explicit `--pair-id` or
  this instance's live binding. Unbound strict status returns exit 0 with a
  non-actionable JSON payload instead of using the sole-active-pair fallback.
- `relay status --json` now includes `bound_pair`, matching `relay whoami`
  provenance so automation can distinguish a real binding from convenience
  resolution.
- `relay wait --require-binding` now refuses to wait when unbound, keeping
  passive automation out of unrelated active pairs.

### Fixed

- The Stop hook now calls strict status with the hook session id and stays
  silent when the session has no live binding, preventing an unbound same-author
  window from being pulled into the lone active relay pair.

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
