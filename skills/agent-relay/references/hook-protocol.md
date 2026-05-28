# agent-relay hook protocol

> Companion to `bin/relay`, `hooks/relay-hook.py`, and `SKILL.md` §
> "Hooks (optional autopilot)". v0.8 (PR-stage).

## 1. Overview

The hook layer is a thin, additive autopilot for agent-relay running inside
Claude Code or Codex CLI. A single dispatcher (`hooks/relay-hook.py`,
stdlib-only Python 3.10+) is installed into both hosts' hook configs by
`relay hooks install`. The dispatcher reads stdin JSON from the host, fast-
path skips non-relay projects, and emits a platform-aware response on
stdout.

**The hook layer never replaces the relay protocol.** `init+preflight`
remain required every turn (Hard rule 4 stays load-bearing). The hooks
only do three things:

1. **SessionStart** — early hint + stale-state doctor.
2. **PreToolUse** — deny edits to `.ready` published artifacts.
3. **Stop** — non-blocking peer-status surface (auto-continue handoff
   when peer has published; remind about unpublished drafts).

PostToolUse is intentionally not installed.

## 2. Host detection

The dispatcher distinguishes Claude Code vs Codex CLI by stdin shape:

| Signal | Claude | Codex |
|---|---|---|
| `effort.level` in stdin | ✅ | ❌ |
| `turn_id` in stdin | ❌ | ✅ |
| `CLAUDE_PROJECT_DIR` env | ✅ | ❌ |

Fallback when none of the above are present (e.g. `SessionStart` with no
extra signal): default to `codex`, since both event handlers tolerate the
shared subset.

## 3. Fast-path: is this a relay project?

Strict sentinel check (codex-review fix #2). Only one signal counts:

```
shared_root = $RELAY_SHARED_ROOT or $cwd/.shared
relay_project = exists(shared_root/_relay/.sentinel)
```

`.shared/` existing **alone** is not sufficient (could be unrelated). The
`RELAY_AUTHOR` env var **alone** is not sufficient (can leak from parent
shells into non-relay projects).

`RELAY_HOOK_FORCE=1` is an explicit test/debug override only.

If `relay_project` is false: exit 0, no stdout, no trail-log write. Cost
≈ one `stat()`.

## 4. Event handlers

### 4.1 `SessionStart`

```python
relay doctor --json    # NOT relay preflight — too expensive on sshfs
if findings_count == 0:
    log "ok"; exit 0 silent
else:
    emit { "systemMessage": "[relay-hint] stale state: N doctor finding(s). ..." }
    log "hint"
```

Soft timeout 10s. Never blocks the session.

### 4.2 `PreToolUse`

Matchers:

- Claude: `^(Edit|Write|MultiEdit)$` — read `tool_input.file_path`.
- Codex: `^(apply_patch|Edit|Write)$` — for `apply_patch`, parse
  `tool_input.command` for these envelope lines:
  - `*** Add File: <path>`
  - `*** Update File: <path>`
  - `*** Delete File: <path>`
  - `*** Move to: <dest>` (returned **in addition to** the source path
    from the surrounding `*** Update File:` hunk)

All extracted paths are canonicalized against `payload.cwd` (or git root)
before comparison; relative paths like `./.shared/...` and absolute paths
both resolve correctly (codex-review fix: path-canonicalization).

Decision:

```
for path in extracted_paths:
    if path is under shared_root/<session>/ and path.with_suffix(".ready").exists():
        emit deny
```

**Deny shape (cross-platform):**

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "Published relay artifact is append-only. Use: relay claim --kind correction --corrects <seq>"
  },
  "systemMessage": "[relay-hint] denied edit to ready artifact: ..."
}
```

**Do not** emit `continue: false` / `stopReason` / `suppressOutput` for
PreToolUse — these fields are explicitly unsupported on Codex (codex-
review fix #1) and the shape above is canonical.

**Parse failure on Codex `apply_patch`** (no `***` headers extractable but
the input looks patch-shaped): fail-open + write a `decision: "fail-open"`
trail line. The agent's soft discipline (hard rule 1) is the secondary
backstop here. Hooks docs frame PreToolUse as a guardrail, not a complete
enforcement boundary.

### 4.3 `Stop`

Anti-loop guard first: if `payload["stop_hook_active"]` is true on either
platform, exit 0 silent. This prevents infinite continuation chains and
applies regardless of state-change.

Then:

```python
status = relay status --json
fingerprint = compute_fingerprint(status)
if cache.fingerprint == fingerprint:
    log "dedup-quiet"; exit 0 silent     # codex-review: exit 0 + no stdout
update cache

if latest_published.peer == $RELAY_AUTHOR:
    emit { "decision": "block",
           "reason": "[relay-state] peer published seq=N kind=K addressed=me\n[relay-action] read .shared/<session>/<path>" }
elif my_drafts:
    emit { "decision": "block",
           "reason": "[relay-state] unpublished draft(s): ...\n[relay-action] `relay publish <draft>` or close" }
else:
    log "clean-exit"; exit 0
```

**Do not** emit `"continue": false` alongside `"decision": "block"` for Stop.
Codex hook docs state that `continue: false` from any matching Stop hook
takes precedence over continuation `decision: "block"` from other matching
Stop hooks — including itself in the same response. The intended
continuation would be silently cancelled.

**State fingerprint** (stable string):

```
{latest_seq}|{latest_author}|{latest_peer}|{latest_kind}|{latest_status}|{drafts_count}|{sorted_draft_names}
```

Stored at `.shared/_relay/hook-state/<host>.json` as
`{"fingerprint": "...", "updated_at": "..."}`. Per-host file (claude vs
codex) keeps writes serialized — no `CLAUDE_PLUGIN_DATA` dependency.

## 5. Platform differences (full table)

| Field / env | Claude Code | Codex CLI | Hook behaviour |
|---|---|---|---|
| `hook_event_name` | PascalCase | PascalCase | Same dispatch table |
| `session_id`, `cwd`, `transcript_path` | Present | Present | Used directly |
| `permission_mode` | Present | Present | Used for log only |
| `effort.level` | Present | Absent | Host-detect signal |
| `turn_id` | Absent | Present | Host-detect signal |
| `stop_hook_active` (Stop) | Present | Present | Anti-loop guard |
| `last_assistant_message` (Stop) | Present | Present | Available for fingerprint |
| `CLAUDE_PROJECT_DIR` env | Set | Not set | Host-detect signal |
| `CLAUDE_PLUGIN_ROOT/DATA` env | Plugin hooks only | Plugin hooks only | **Not** assumed by this dispatcher |
| `CLAUDE_ENV_FILE` env | SessionStart etc. | Not documented | Not used |
| `suppressOutput` output field | Effective | Parsed, no-op | Use exit 0 + no stdout instead |
| `continue: false` (PreToolUse) | Possible but discouraged | **Unsupported** | Use `hookSpecificOutput.permissionDecision` instead |
| Codex `apply_patch` tool | n/a | `tool_name == "apply_patch"`; paths in `tool_input.command` | Extracted via `_APPLY_PATCH_HEADER` regex |

## 6. Trail log

Every hook decision (after sentinel check passes) appends one JSON line
to `.shared/_relay/hook-trail.log`:

```json
{"ts":"2026-05-29T01:23:45+00:00","event":"PreToolUse","host":"codex","decision":"deny","tool":"apply_patch","path":"20260529-x/003-claude-plan.md","reason":"ready-sidecar"}
```

Decisions seen:

- `ok` (SessionStart): doctor reports clean
- `hint` (SessionStart): doctor reports findings, systemMessage emitted
- `doctor-failed`, `no-relay-cli` (SessionStart): degraded, never blocks
- `deny` (PreToolUse): edit to ready artifact rejected
- `fail-open` (PreToolUse): apply_patch parse failed; allowed through with warning
- `block-peer-new`, `block-draft` (Stop): `decision: "block"` emitted
- `dedup-quiet` (Stop): fingerprint unchanged, silent skip
- `skip-active` (Stop): `stop_hook_active=true`, silent skip
- `clean-exit` (Stop): state changed but nothing actionable
- `slow`: hook took longer than 5s — diagnostic only
- `error`: defensive exception path; hook never breaks the host

## 7. Installation

`relay hooks install --target {claude|codex|both} [--dry-run]`:

- Loads the fragment JSON from `hooks/install-targets/` and substitutes
  `{RELAY_HOOK_PATH}` with the absolute path to `relay-hook.py`.
- Reads the existing target config (`~/.claude/settings.json` or
  `~/.codex/hooks.json`) as JSON. Missing file = `{}`.
- For each event in `(SessionStart, PreToolUse, Stop)`, **appends** the
  managed entry to the END of `hooks.<Event>` array. Existing entries
  keep their indices (Codex `state` trust keys stay valid).
- Each managed entry carries `"_agent_relay_managed": true`. Subsequent
  `install` runs strip and re-add (idempotent).
- Atomic write via `atomic_write_text`.
- Prints next steps including the Codex `/hooks` trust reminder.

`relay hooks uninstall` filters out only `_agent_relay_managed` entries
and writes back. Never touches anyone else's hooks.

`relay hooks doctor` verifies dispatcher executable bit, target configs
present + correctly wired, and (Codex) suggests `/hooks` trust if state
map lacks managed-entry keys.

`relay hooks status` prints install state and last 10 trail-log lines.

## 8. Environment variables consumed by `relay-hook.py`

| Var | Default | Effect |
|---|---|---|
| `RELAY_SHARED_ROOT` | `cwd/.shared` | Where to look for `_relay/.sentinel` |
| `RELAY_AUTHOR` | (none) | Whose drafts to surface in Stop |
| `RELAY_BIN` | (search chain) | Pin the `relay` binary explicitly (tests) |
| `RELAY_HOOK_FORCE` | `0` | `1` skips the sentinel check (debug / tests) |
| `RELAY_HOOK_QUIET` | `1` | Reserved for future verbosity tuning |
| `RELAY_HOOK_VERBOSE` | `0` | Reserved for future verbosity tuning |

## 9. Failure modes

- `relay` CLI not on the chain: SessionStart and Stop degrade to silent
  exit; PreToolUse still works (it uses only filesystem checks).
- `relay status --json` fails: Stop degrades to silent exit.
- `apply_patch` parse failure: PreToolUse fail-open + warning trail line.
- Hook >5s wall time: emit `slow` trail entry; no functional effect.
- Any exception inside a handler: caught, logged as `error`, exit 0.
  The hook will never break the host process.

## 10. Out of scope (by design)

These are explicitly **not** done by the hook layer in this version:

- `relay sync push` / `relay close` automation (require user confirmation).
- `relay claim` / `relay publish` automation (require agent content judgment).
- Statusline integration (use `tail -f hook-trail.log` for now).
- Rewriting `relay-hook.py` as an `oh-my-codex` plugin (chose append-after
  coexistence instead).
