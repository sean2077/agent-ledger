# AGENTS.md — agent-ledger / agent-relay

> Authoritative onboarding for an AI agent (or human) joining this repo to
> **review or develop**. Read this top-to-bottom once; it is the fastest path
> to being productive. Deeper rationale lives in [`docs/why.md`](docs/why.md);
> the agent-facing workflow lives in
> [`skills/agent-relay/SKILL.md`](skills/agent-relay/SKILL.md); version history
> in [`CHANGELOG.md`](CHANGELOG.md).

## 1. What this project is (and isn't)

`agent-relay` connects **two interactive coding agents** — canonically Claude
Code and Codex CLI — through an **append-only shared-file ledger** so they can
cross-review each other's work **without an API-key orchestrator**. One side
publishes an artifact (plan / review / fix / decision …), the other reads it,
does work, and publishes a response. The filesystem is the only substrate:
there is no daemon, no message bus, no per-call API billing introduced by the
relay itself.

- **Is:** a single-file Python CLI (`relay`) that does the mechanical, corruption-
  proof parts (atomic writes, sequence numbers, frontmatter validation, rsync,
  liveness sidecars) + a skill (`agent-relay`) that teaches an agent the
  workflow. **Exactly two agents pair at a time** — this is the deliberate
  design target. More than two works mechanically but is explicitly not a goal
  (less robust, marginal benefit).
- **Isn't:** a multi-agent framework, a human-review substitute, a CI/CD
  pipeline, or vendor-blessed. See `docs/why.md` for the long form + the
  billing/limits caveats.

North star: **practical, easy to use, stable, robust.** When in doubt, prefer
the option that can't deadlock and that a user can always interrupt.

## 2. Repo layout

```
skills/agent-relay/
  bin/relay              # the entire CLI — one Python file, stdlib only, 3.10+
  SKILL.md               # workflow guide loaded by Claude Code / Codex
  references/            # file-protocol.md, hook-protocol.md, rsync-recipes.md
  templates/             # envrc.dispatcher.example (per-host env, optional)
  hooks/                 # relay-hook.py dispatcher + install-target fragments
tests/                   # pytest; one test_relay_<area>.py per area, conftest.py
docs/why.md              # the longer "what/why/caveats" take
CHANGELOG.md             # pre-1.0; breaking changes noted per minor version
AGENTS.md                # this file  (CLAUDE.md is a symlink to it)
```

The CLI is intentionally **one stdlib-only file**. Do not split it into a
package or add third-party deps without a strong reason — the single-file,
zero-install property is load-bearing for the "drop it on any host" use case.
When the file feels big, extract *helpers with clear boundaries* (identity /
pair / heartbeat), not a package.

## 3. Architecture — four layers

1. **Artifact protocol** (the ledger). Pairs live at
   `.shared/<YYYYMMDD-topic>/`. Each turn appends `NNN-<author>-<kind>.md` with
   YAML frontmatter + a markdown body. `session.json` (schema v3) holds pair
   metadata incl. `participants`. Full spec: `references/file-protocol.md`.
2. **Sidecars** (integrity). On publish, `relay` writes `<artifact>.sha256`
   (content hash) and `<artifact>.ready` (publish-complete sentinel). **A `.md`
   without a `.ready` is not published — never trust it.**
3. **Binding registry** (multi-pair). Each running agent *instance* binds to one
   pair via `.shared/_relay/bindings/<author>-<sha256(full-session-id)[:24]>.json`.
   This is what lets two windows on one host each track their own pair without a
   global marker. Lock-free (one file per instance); no deadlock.
4. **Identity** (who am I / who's my peer) — see §5. The binding + identity
   layers are the only thing that knows about the running process; the artifact
   protocol is pure files.

## 4. Command surface (quick reference)

Resolve the binary first (`relay` may not be on `$PATH`): prefer
`$(git rev-parse --show-toplevel)/skills/agent-relay/bin/relay`.

| Command | Purpose |
|---|---|
| `relay init [--same-host] [--sync rsync] [--author X]` | Idempotent first-run setup (shared root + sentinel). Same-host claude+codex is **zero-config**; only the rsync owner needs flags. |
| `relay preflight` | Health gate: identity, mount, project, FS atomicity probes, and schema compatibility diagnostics (`unsupported_schema` is reported, not fixed). Exit 0 ok / 1 blocking-warn / 2 fail. Run every turn. |
| `relay whoami` | This instance's resolved identity (author + source), session id, bound pair, derived peer, diagnostics, and schema compatibility diagnostics. |
| `relay bootstrap --topic <slug> [--peer <name>]` | Create + bind a new pair. claude/codex auto-derive the peer; custom agents pass `--peer`. |
| `relay pair ensure \| join <slug> \| leave` | Resolve / pick / drop this instance's pair binding. |
| `relay pairs list [--archived]` | Discovery: all pairs (or, with `--archived`, the archived ones), bound instances, open slots, and `unsupported_schema` categories. |
| `relay pairs archive <slug> [--force] \| --terminated` | Move terminated pair dir(s) into `.shared/_archive/` to declutter the top level. `--force` shelves an active pair (drops its bindings); `--terminated` sweeps all closed/terminal pairs. |
| `relay pairs restore <slug>` | Move an archived pair back to `.shared/` (state unchanged — closed stays closed, shelved-active stays active; never auto-rebinds). |
| `relay status [--json] [--require-binding]` | The bound pair's published artifacts + next seq. JSON includes `bound_pair` (this instance's binding, or null). `--require-binding` = strict resolution for passive automation: no sole-active fallback; unbound → exit 0 with a non-actionable payload. |
| `relay claim --kind <k> [--in-reply-to N]` | Scaffold a hidden `.draft/NNN-<author>-<kind>.md`. Fails closed if author/peer unresolved, or if this instance is unbound and no explicit `--pair-id` is supplied. May **resume** a paused (`timed_out`) round; still fails closed on hard-terminal (`closed`/`cancelled`/`failed`) pairs. |
| `relay draft set <draft> --body-file … --prompt-for-next-file …` | Fill a draft atomically (preferred over hand-editing the file). |
| `relay publish <draft>` | Validate + atomically promote a draft (writes `.sha256` + `.ready`). The authorship boundary. Supersedes a `timed_out` latest with no `--force` (resume); a hard-terminal/closed pair needs `--force` + a terminal `--status` for an append-only note. |
| `relay wait [--require-binding]` | Block until the peer publishes an artifact addressed to you. Exit 0 new / 10 timeout / 11 peer-stale / 12 terminal / 130 SIGINT. `--require-binding` refuses (non-zero) when unbound instead of waiting on a sole-active pair. |
| `relay close --reason … --outcome …` | Write `CLOSED` sentinel; mark `session.json` closed. |
| `relay sync push\|pull [--dry-run]` | rsync wrapper — only the `RELAY_SYNC=rsync` side may run it. |
| `relay heartbeat start\|stop\|tick` | Liveness daemon for a draft (see §7). |
| `relay doctor [--fix]` | Read-only ledger diagnosis; `--fix` cleans owner-safe junk, including old incomplete publish triads (never signals a live PID and never mutates unsupported-schema records). |
| `relay hooks install\|uninstall\|doctor\|status` | Manage the optional autopilot hooks (§7). |
| `relay issue add\|list\|show\|resolve` | Out-of-band feedback ledger for the *tool itself* (machine-local, never synced). |

`kind` ∈ `plan | review | fix | note | question | decision | correction | addendum`.

## 5. Identity & env model (v0.15 — simplified)

**Author is auto-detected from the platform signal. You almost never set env.**

- `author`: `CLAUDE_CODE_SESSION_ID` present → `claude`; `CODEX_THREAD_ID`
  present → `codex`. `RELAY_AUTHOR` is only a **fallback/override** for a custom
  (non-claude/codex) agent or odd setups. If `RELAY_AUTHOR` disagrees with a
  single platform signal, **the platform wins** and preflight/whoami warn
  (`author_conflict`). If *both* platform signals are present, `relay` refuses
  to guess: `RELAY_AUTHOR` may disambiguate, else author is unresolved
  (`dual_platform`) and identity-boundary commands fail closed.
- `peer`: **derived from `session.json` participants** (the one that isn't you),
  via `resolve_peer()`, *after* the pair is resolved. `RELAY_PEER` is not loaded
  or consulted. A pair has exactly two participants; `resolve_peer` fails closed
  otherwise.
- `agent_session_id`: per-instance id for the binding registry only (never in
  artifacts). `RELAY_AGENT_SESSION_ID` overrides *only the session id*, not the
  author (hooks inject it). Degraded identity (no platform/tty/atuin signal) is
  ephemeral per-process → no stable binding → must pass `--pair-id`.

Env vars, all optional in the common case:

| Var | Meaning | Default |
|---|---|---|
| `RELAY_SHARED_ROOT` | ledger root | `<git toplevel>/.shared` |
| `RELAY_SYNC` | `none` or `rsync` (rsync owner only) | `none` |
| `RELAY_REMOTE_SSH` / `RELAY_REMOTE_PATH` | rsync transport (rsync owner) | unset |
| `RELAY_AUTHOR` | author override for custom agents | auto-detected |
| `RELAY_AGENT_SESSION_ID` | session-id override (hooks) | platform/terminal |

**Setup is therefore minimal:** same-host claude+codex needs nothing (`relay
init --same-host` just confirms it). The rsync owner runs `relay init --sync
rsync` and fills the two remote vars. `direnv`/`.envrc` is optional, only for
the rsync side. (The pre-v0.14 per-terminal `export RELAY_AUTHOR` dance and the
`envrc.same-host.example` template are retired.)

## 6. The handoff loop (how a turn works)

1. `relay preflight` (gate) → `relay pair ensure` (bind).
2. `relay status` — read the latest artifact. If its `peer` is you, act; if it's
   a **hard-terminal** status (`closed`/`cancelled`/`failed`) or `kind: decision`,
   stop; if it's a `timed_out` **pause** that escalated to `@user:` and the user
   has now answered, **resume** it — claim in reply to that seq (`status` flags
   such a pair `resumable: yes`); if it's yours (peer hasn't replied), wait.
3. Read the peer's `.md` (esp. its `prompt_for_next` — that's your task).
4. Do the work (plan/review/code). Track non-`.shared/` files you touch.
5. `relay claim --kind <k> --in-reply-to <peer-seq>` → fill via `relay draft
   set` → `relay publish`. Claim is a write boundary: it uses this instance's
   binding, or an explicit `--pair-id`; it never falls through to the sole-active
   convenience fallback.
6. **Auto-loop:** unless a rule-based break fires, `relay wait` for the peer and
   repeat. Break triggers (surface to the user): `kind: decision`, a terminal
   status, a `prompt_for_next` line **starting with** `@user:`, or the
   consecutive-round cap (`RELAY_AUTO_ROUND_CAP`, default 5). The gap between
   your publish and the peer's reply is **wait time, not user time** — never end
   a turn just to ask "should I wait?"/"continue?"; that bare gate is the
   interruption the loop exists to remove. **Prefer waiting in the background**
   where the runtime allows it (Claude Code: Bash `run_in_background` — the user
   stays interactive and the harness re-invokes you when the wait exits). Codex
   CLI has no backgroundable task (its exec PTY is torn down at turn end), so it
   leans on the Stop-hook auto-continue or a foreground `relay wait` instead of
   breaking out. The goal is an un-interrupted multi-round cross-review that
   still hands back on real decisions and stays interruptible (Ctrl-C any time).

**Hard rules:** never edit a `.md` that has a `.ready` sidecar (append a
`kind: correction` instead); never hand-write `.sha256`/`.ready`; never bypass
`relay preflight`; never `relay close` without the user.

## 7. Liveness & autopilot

- **Heartbeat (renewal-file).** `relay claim` **auto-starts** a renewal-file
  heartbeat for the draft — no separate step (`--no-heartbeat` /
  `RELAY_CLAIM_NO_HEARTBEAT=1` opt out). While the draft is open the heartbeat
  keeps a renewal file fresh; the waiting peer reads the heartbeat sidecar's
  freshness to tell a live peer from stale state (`relay wait` exit 11 = peer
  heartbeat went stale). This is the robustness core — it lets the auto-loop
  avoid relying on a blind timeout, and `claim` rolls the draft back if the
  heartbeat can't start, so a claimed draft never lacks coverage. `renewal-file`
  is the default and the only owner kind the normal workflow uses;
  `agent`/`tool-process`/`pidfile`/`none` are advanced/legacy.
- **Hooks (optional, both platforms).** `relay hooks install --target both`
  wires SessionStart (early hint), PreToolUse (denies edits to `.ready`
  artifacts), and Stop (auto-continues the turn when the peer published). Install
  on **both** sides for a smooth bidirectional loop that needs no manual
  re-invocation. **The Stop surface is binding-scoped:** it resolves only *this
  session's* bound pair (`relay status --require-binding`) and stays silent when
  the session is bound to no pair — an unbound window doing unrelated work is
  never pulled into the lone active pair (issue 20260601T182646-2920d5b9). Every
  hook decision is appended to `.shared/_relay/hook-trail.log`. Spec:
  `references/hook-protocol.md`.

## 8. Testing & dev conventions

- **Run:** `python -m pytest -q` (or `.venv/bin/pytest`). All tests must pass
  before you call work done; state failures honestly with output.
- **Layout:** one `tests/test_relay_<area>.py` per area. `conftest.py` loads the
  extensionless `bin/relay` as a module **and** has an autouse fixture clearing
  `CLAUDE_CODE_SESSION_ID` / `CODEX_THREAD_ID` so the host platform signal can't
  make identity non-deterministic — tests that exercise platform detection set
  these explicitly.
- **Fail closed.** Identity-boundary commands (`publish`, `draft set`,
  `heartbeat start`, `claim`) must refuse rather than guess when author/peer is
  unresolved. New behavior here needs a regression test asserting the refusal
  **and** that no artifact/draft was written.
- **Pre-1.0 hygiene.** Deprecated features are hard-removed (code + tests +
  docs), no compat shims; keep the history in `CHANGELOG.md`.
- **stdlib only, single file** for `bin/relay` (see §2).

## 9. Source-of-truth discipline

`AGENTS.md` is the authoritative agent-facing contract. Any change that alters
architecture, protocol semantics, command surface, workflow, identity/env model,
safety rules, testing requirements, or release/commit conventions must update
this file in the same commit, or explicitly state in the commit message why
`AGENTS.md` remains unchanged.

Do not use `AGENTS.md` as a changelog for routine internal implementation
changes; keep version history in `CHANGELOG.md`. But if future agents need to
know a rule to work safely or correctly, that rule belongs here.

Keep source-of-truth boundaries explicit:
- `AGENTS.md` owns current agent-facing rules and operational invariants.
- `references/file-protocol.md` owns durable on-disk, frontmatter, and JSON
  contracts. Historical field names such as `session.json`, `session_id`, and
  JSON output keys stay there unless a deliberate schema migration is designed.
- `CHANGELOG.md` owns version history and removed/deprecated behavior records.

## 10. Commit protocol (Lore trailers)

Every commit to this repo carries the seven **Lore** trailers plus
`Co-Authored-By`. They capture the *reasoning*, not just the diff:

```
<type>(<scope>): <subject> (vX.Y.Z)

<body: what changed and why, wrapped ~80 cols>

Constraint: <the invariant that must hold true>
Rejected: <alt A> | <why rejected>. <alt B> | <why rejected>.
Confidence: <high|medium|low> — <evidence (tests, repro)>
Scope-risk: <low|moderate|high> — <blast radius + rollback>
Directive: <forward guidance for whoever touches this next>
Tested: <what was actually tested>
Not-tested: <what was not, and why>
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

When you hit a rough edge in the relay tooling itself mid-task, record it with
`relay issue add` (out-of-band, machine-local) rather than losing the signal.

---

*This document is the authoritative entry point; `CLAUDE.md` symlinks to it so
Claude Code loads it automatically.*
