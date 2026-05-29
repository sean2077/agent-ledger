#!/usr/bin/env python3
"""relay-hook — cross-platform hook dispatcher for agent-relay.

Reads stdin JSON from Claude Code or Codex CLI hook events, fast-path
skips non-relay projects, dispatches to event handler, emits a
platform-aware response on stdout. Stdlib only, Python 3.10+.

Hooked events:
  SessionStart   — early hint + stale-state doctor (does NOT replace
                   init+preflight; that stays required every turn)
  PreToolUse     — deny edits to ready relay artifacts; cross-platform
                   permissionDecision shape
  Stop           — non-blocking peer-status surface with dedup

Companion spec: skills/agent-relay/references/hook-protocol.md
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional


VERSION = "0.12.0"


def now_iso() -> str:
    """Local time with offset, no microseconds — MUST match bin/relay's
    now_iso() so the hook trail reads consistently next to session artifacts
    and the issue ledger (issue 20260529T093645: the trail previously used
    UTC + microseconds, which looked 'zoneless'/wrong beside `+08:00` stamps)."""
    return datetime.now().astimezone().replace(microsecond=0).isoformat()

HOOK_QUIET = os.environ.get("RELAY_HOOK_QUIET", "1") != "0"
HOOK_VERBOSE = os.environ.get("RELAY_HOOK_VERBOSE", "0") == "1"
HOOK_FORCE = os.environ.get("RELAY_HOOK_FORCE", "0") == "1"
HOOK_SOFT_TIMEOUT_S = 5  # warn (not fail) past this


# ---------------------------------------------------------------------------
# fast-path: is this a relay project? Strict sentinel-only check.
# ---------------------------------------------------------------------------

def is_relay_project(cwd: str) -> tuple[bool, Optional[Path]]:
    """Return (is_relay, shared_root). Strict: only `_relay/.sentinel` counts.

    RELAY_HOOK_FORCE=1 short-circuits for testing/debug only.
    """
    if HOOK_FORCE:
        # Use cwd/.shared as the assumed root under force mode
        forced = Path(cwd) / ".shared"
        return True, forced if forced.exists() else None

    shared_root_env = os.environ.get("RELAY_SHARED_ROOT")
    candidates: list[Path] = []
    if shared_root_env:
        candidates.append(Path(shared_root_env))
    candidates.append(Path(cwd) / ".shared")

    for c in candidates:
        if (c / "_relay" / ".sentinel").exists():
            return True, c
    return False, None


# ---------------------------------------------------------------------------
# host detection
# ---------------------------------------------------------------------------

def detect_host(payload: dict) -> str:
    """Return 'claude' or 'codex' based on stdin JSON shape + env signals."""
    # Codex sets turn_id on turn-scoped events. Claude does not.
    if "turn_id" in payload:
        return "codex"
    # Claude sets effort.level on most events. Codex does not.
    if "effort" in payload:
        return "claude"
    # SessionStart: neither has turn_id/effort. Fall back to env.
    if os.environ.get("CLAUDE_PROJECT_DIR"):
        return "claude"
    return "codex"  # safe default; both shapes are tolerated downstream


# ---------------------------------------------------------------------------
# locate `relay` CLI
# ---------------------------------------------------------------------------

def find_relay() -> Optional[Path]:
    """Walk the same chain SKILL.md specifies (project-local wins).

    `RELAY_BIN` env var, if set and executable, wins over the chain — useful
    for tests and for fixing the binary location explicitly.
    """
    env_pin = os.environ.get("RELAY_BIN")
    if env_pin:
        p = Path(env_pin)
        if p.exists() and os.access(p, os.X_OK):
            return p
    home = Path.home()
    cwd = Path.cwd()
    git_root: Optional[Path] = None
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0:
            git_root = Path(r.stdout.strip())
    except (subprocess.TimeoutExpired, OSError):
        pass

    chain: list[Path] = []
    for root in filter(None, [git_root, cwd]):
        chain.extend([
            root / ".agents/skills/agent-relay/bin/relay",
            root / ".claude/skills/agent-relay/bin/relay",
            root / "skills/agent-relay/bin/relay",
        ])
    chain.extend([
        Path("/usr/local/bin/relay"),
        home / ".local/bin/relay",
        home / ".agents/skills/agent-relay/bin/relay",
        home / ".claude/skills/agent-relay/bin/relay",
        home / ".codex/skills/agent-relay/bin/relay",
    ])
    for p in chain:
        if p.exists() and os.access(p, os.X_OK):
            return p
    return None


def call_relay_json(relay: Path, *args: str, timeout: int = 8) -> Optional[dict]:
    """Run `relay <args> --json` and return parsed dict, or None on failure."""
    cmd = [str(relay), *args]
    if "--json" not in args:
        cmd.append("--json")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    # status / doctor exit 0 = healthy, 1 = findings/warnings but still valid JSON
    if r.returncode not in (0, 1):
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# state cache (no CLAUDE_PLUGIN_DATA dependency — write under .shared/_relay/)
# ---------------------------------------------------------------------------

def state_cache_path(shared_root: Path, host: str) -> Path:
    return shared_root / "_relay" / "hook-state" / f"{host}.json"


def read_state_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def write_state_cache_atomic(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def compute_state_fingerprint(status_json: dict) -> str:
    """Stable string capturing whether anything actionable has changed."""
    pubs = status_json.get("published", []) or []
    latest = pubs[-1] if pubs else {}
    drafts = status_json.get("drafts", []) or []
    parts = [
        str(latest.get("seq", "")),
        str(latest.get("author", "")),
        str(latest.get("peer", "")),
        str(latest.get("kind", "")),
        str(latest.get("status", "")),
        str(len(drafts)),
        ",".join(sorted(drafts)),
    ]
    return "|".join(parts)


# ---------------------------------------------------------------------------
# trail log (JSONL; written only after sentinel check passes)
# ---------------------------------------------------------------------------

def append_trail(shared_root: Path, entry: dict) -> None:
    log = shared_root / "_relay" / "hook-trail.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    entry = {"ts": now_iso(), **entry}
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    try:
        with open(log, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Codex apply_patch path extraction (includes *** Move to:)
# ---------------------------------------------------------------------------

_APPLY_PATCH_HEADER = re.compile(
    r"^\s*\*\*\*\s+(Add File|Update File|Delete File|Move to):\s+(.+?)\s*$",
    re.MULTILINE,
)


def extract_apply_patch_paths(command_text: str) -> list[str]:
    """Return all paths referenced inside a Codex apply_patch envelope.

    Recognizes Add File / Update File / Delete File / Move to. When a hunk
    contains both Update File and Move to, both source and destination are
    returned so neither can bypass the .ready check.
    """
    return [m.group(2) for m in _APPLY_PATCH_HEADER.finditer(command_text or "")]


def canonicalize_paths(paths: list[str], cwd: str) -> list[Path]:
    base = Path(cwd).resolve() if cwd else Path.cwd()
    out: list[Path] = []
    for p in paths:
        if not p:
            continue
        try:
            pp = Path(p)
            if not pp.is_absolute():
                pp = base / pp
            out.append(pp.resolve())
        except (OSError, ValueError):
            continue
    return out


def extract_target_paths(tool_name: str, tool_input: dict) -> tuple[list[str], bool]:
    """Return (paths, parse_failed). Empty paths + parse_failed=False = no targets."""
    if not isinstance(tool_input, dict):
        tool_input = {}

    if tool_name == "apply_patch":
        cmd = tool_input.get("command") or tool_input.get("input") or ""
        if isinstance(cmd, list):
            cmd = "\n".join(str(x) for x in cmd)
        if not cmd:
            return [], False
        try:
            extracted = extract_apply_patch_paths(cmd)
        except Exception:
            return [], True
        if not extracted:
            # heuristic: looks like a patch but no headers -> parse_failed
            looks_patchy = "***" in cmd or "@@" in cmd
            return [], looks_patchy
        return extracted, False

    if tool_name in ("Edit", "Write"):
        fp = tool_input.get("file_path")
        return ([fp] if fp else []), False

    if tool_name == "MultiEdit":
        fp = tool_input.get("file_path")
        return ([fp] if fp else []), False

    return [], False


# ---------------------------------------------------------------------------
# event handlers
# ---------------------------------------------------------------------------

def handle_session_start(payload: dict, shared_root: Path,
                         relay: Optional[Path], host: str) -> Optional[dict]:
    """Early hint + stale-state doctor only.

    Does NOT run preflight (too expensive on sshfs, and per protocol
    init+preflight must remain agent-driven every turn).
    """
    if not relay:
        append_trail(shared_root, {"event": "SessionStart", "host": host,
                                   "decision": "no-relay-cli"})
        return None

    doctor = call_relay_json(relay, "doctor")
    if doctor is None:
        append_trail(shared_root, {"event": "SessionStart", "host": host,
                                   "decision": "doctor-failed"})
        return None

    findings = int(doctor.get("findings_count", 0) or 0)
    if findings == 0:
        append_trail(shared_root, {"event": "SessionStart", "host": host,
                                   "decision": "ok"})
        return None

    msg = (
        f"[relay-hint] stale state: {findings} doctor finding(s). "
        f"Run `relay doctor` to inspect, `relay doctor --fix` to clean."
    )
    append_trail(shared_root, {"event": "SessionStart", "host": host,
                               "decision": "hint", "findings": findings})
    return {"systemMessage": msg}


def handle_pre_tool_use(payload: dict, shared_root: Path,
                        relay: Optional[Path], host: str) -> Optional[dict]:
    """Deny edits to ready relay artifacts. Uses hookSpecificOutput shape."""
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}
    if tool_name not in ("apply_patch", "Edit", "Write", "MultiEdit"):
        return None

    paths, parse_failed = extract_target_paths(tool_name, tool_input)

    if parse_failed and tool_name == "apply_patch":
        # Fail-open per codex MVP guidance; log warning so it's visible.
        append_trail(shared_root, {
            "event": "PreToolUse", "host": host, "decision": "fail-open",
            "tool": tool_name, "reason": "apply_patch parse failed",
        })
        return None

    if not paths:
        return None

    cwd = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    canon = canonicalize_paths(paths, cwd)
    shared_resolved = shared_root.resolve()

    for p in canon:
        try:
            p.relative_to(shared_resolved)
        except ValueError:
            continue  # not under .shared/
        # Finding 4: previously only `.md` was protected, leaving `.ready`
        # and `.md.sha256` sidecars editable/deletable. The published
        # artifact is the triple (md, sha256, ready) per
        # references/file-protocol.md; any one being mutated breaks
        # readers that gate on .ready existence + sha256 match.
        if p.suffix == ".md":
            md = p
            ready = p.with_suffix(".ready")
        elif p.suffix == ".ready":
            md = p.with_suffix(".md")
            ready = p
        elif p.suffix == ".sha256" and p.name.endswith(".md.sha256"):
            md = p.with_name(p.name[:-len(".sha256")])  # strip .sha256
            ready = md.with_suffix(".ready")
        else:
            continue
        if not ready.exists():
            continue
        reason = (
            "Published relay artifact is append-only "
            "(.md / .ready / .md.sha256 are protected as a unit). "
            "Use: relay claim --kind correction --corrects <seq>"
        )
        append_trail(shared_root, {
            "event": "PreToolUse", "host": host, "decision": "deny",
            "tool": tool_name,
            "path": str(p.relative_to(shared_resolved)),
            "reason": "ready-sidecar",
        })
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            },
            "systemMessage": f"[relay-hint] denied edit to ready artifact: {md.name}",
        }

    return None


def handle_stop(payload: dict, shared_root: Path,
                relay: Optional[Path], host: str) -> Optional[dict]:
    """Non-blocking peer-status surface. Dedup via per-host state cache."""
    if payload.get("stop_hook_active"):
        append_trail(shared_root, {"event": "Stop", "host": host,
                                   "decision": "skip-active"})
        return None

    if not relay:
        return None

    status = call_relay_json(relay, "status")
    if status is None:
        return None

    fingerprint = compute_state_fingerprint(status)
    cache_path = state_cache_path(shared_root, host)
    cache = read_state_cache(cache_path)
    if cache.get("fingerprint") == fingerprint:
        append_trail(shared_root, {"event": "Stop", "host": host,
                                   "decision": "dedup-quiet"})
        return None

    cache["fingerprint"] = fingerprint
    cache["updated_at"] = now_iso()
    write_state_cache_atomic(cache_path, cache)

    pubs = status.get("published", []) or []
    drafts = status.get("drafts", []) or []
    author = os.environ.get("RELAY_AUTHOR", "")
    latest = pubs[-1] if pubs else None
    session_id = (status.get("session") or {}).get("session_id", "")

    # Case 1: latest published is addressed to me — block to continue handoff
    if latest and latest.get("peer") == author:
        path = latest.get("path", "")
        kind = latest.get("kind", "")
        seq = latest.get("seq", "")
        reason = (
            f"[relay-state] peer published seq={seq} kind={kind} addressed=me\n"
            f"[relay-action] read .shared/{session_id}/{path}"
        )
        append_trail(shared_root, {
            "event": "Stop", "host": host, "decision": "block-peer-new",
            "seq": seq, "kind": kind,
        })
        return {"decision": "block", "reason": reason}

    # Case 2: I have unpublished draft(s)
    my_drafts = [d for d in drafts if author and f"-{author}-" in d]
    if my_drafts:
        reason = (
            f"[relay-state] unpublished draft(s): {', '.join(my_drafts)}\n"
            f"[relay-action] `relay publish <draft>` or close the session"
        )
        append_trail(shared_root, {
            "event": "Stop", "host": host, "decision": "block-draft",
            "drafts": my_drafts,
        })
        return {"decision": "block", "reason": reason}

    append_trail(shared_root, {"event": "Stop", "host": host,
                               "decision": "clean-exit"})
    return None


# ---------------------------------------------------------------------------
# emit + main
# ---------------------------------------------------------------------------

HANDLERS = {
    "SessionStart": handle_session_start,
    "PreToolUse": handle_pre_tool_use,
    "Stop": handle_stop,
}


def emit(response: Optional[dict]) -> int:
    """Codex dedup wants exit 0 + no stdout. Claude accepts the same."""
    if not response:
        return 0
    json.dump(response, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def main() -> int:
    start = time.monotonic()
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return 0  # never break host on bad input

    cwd = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    is_relay, shared_root = is_relay_project(cwd)
    if not is_relay:
        return 0  # silent skip

    if shared_root is None:
        # HOOK_FORCE without an existing .shared/ — fall back, nothing to log
        return 0

    host = detect_host(payload)
    event = payload.get("hook_event_name", "")
    handler = HANDLERS.get(event)
    if not handler:
        return 0

    relay = find_relay()

    try:
        response = handler(payload, shared_root, relay, host)
    except Exception as exc:  # pragma: no cover - defensive
        append_trail(shared_root, {
            "event": event, "host": host, "decision": "error",
            "error": f"{type(exc).__name__}: {exc}",
        })
        return 0  # never break host

    elapsed = time.monotonic() - start
    if elapsed > HOOK_SOFT_TIMEOUT_S:
        append_trail(shared_root, {
            "event": event, "host": host, "decision": "slow",
            "elapsed_s": round(elapsed, 2),
        })

    return emit(response)


if __name__ == "__main__":
    sys.exit(main())
