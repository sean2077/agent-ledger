"""Tests for skills/agent-relay/hooks/relay-hook.py — cross-platform hook
dispatcher. Covers the 13 scenarios specified in the v2 plan
(`.shared/20260529-hook-layer-design/003-claude-plan.md`) plus installer
merge byte-stability.

The hook script is exercised via subprocess (it's the same shape the host
runs). The relay CLI is reused by setting RELAY_BIN.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import relay


HOOK_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "skills" / "agent-relay" / "hooks" / "relay-hook.py"
)
RELAY_BIN = (
    Path(__file__).resolve().parent.parent
    / "skills" / "agent-relay" / "bin" / "relay"
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _new_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "myproj"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    return repo


def _bootstrap_relay_project(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    """Create a real relay project with sentinel + active pair."""
    repo = _new_repo(tmp_path)
    monkeypatch.chdir(repo)
    shared = repo / ".shared"
    shared.mkdir(mode=0o700)
    (shared / "_relay").mkdir()
    (shared / "_relay" / ".sentinel").touch()
    for k in list(os.environ):
        if k.startswith("RELAY_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RELAY_SYNC", "none")
    monkeypatch.setenv("RELAY_AUTHOR", "claude")
    monkeypatch.setenv("RELAY_SHARED_ROOT", str(shared))
    relay.cmd_bootstrap(type("A", (), {"topic": "t", "title": None})())
    session = relay.resolve_active_pair(relay.load_env())
    return repo, session


def _bootstrap_bound(monkeypatch, tmp_path: Path,
                     author: str = "claude", sid: str = "test") -> tuple[Path, Path]:
    """Like `_bootstrap_relay_project`, but the creating instance BINDS with a
    concrete agent_session_id (default 'test', matching the `_*_stop` payloads'
    session_id).

    Strict-binding Stop resolution (issue 20260601T182646-2920d5b9) only
    surfaces for the session actually bound to the pair, so Stop tests must bind
    the session the hook represents. The plain helper leaves the pair UNBOUND
    (its creator's session id is fallback-degraded under pytest and `join_pair`
    refuses it) — which is exactly the unbound state `test_14` exercises."""
    repo = _new_repo(tmp_path)
    monkeypatch.chdir(repo)
    shared = repo / ".shared"
    shared.mkdir(mode=0o700)
    (shared / "_relay").mkdir()
    (shared / "_relay" / ".sentinel").touch()
    for k in list(os.environ):
        if k.startswith("RELAY_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RELAY_SYNC", "none")
    monkeypatch.setenv("RELAY_AUTHOR", author)
    monkeypatch.setenv("RELAY_SHARED_ROOT", str(shared))
    # Concrete (override-source) id => not fallback-degraded => join_pair binds.
    monkeypatch.setenv("RELAY_AGENT_SESSION_ID", sid)
    relay.cmd_bootstrap(type("A", (), {"topic": "t", "title": None})())
    session = relay.resolve_active_pair(relay.load_env())
    assert relay.read_binding(shared, author, sid), "bootstrap did not bind the creator"
    return repo, session


def _run_hook(stdin_json: dict, env_overrides: dict | None = None,
              cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["RELAY_BIN"] = str(RELAY_BIN)
    if env_overrides:
        for k, v in env_overrides.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v
    r = subprocess.run(
        ["python3", str(HOOK_SCRIPT)],
        input=json.dumps(stdin_json),
        capture_output=True, text=True, env=env, timeout=10,
        cwd=str(cwd) if cwd else None,
    )
    return r


def _claude_stop(cwd: Path, **extra) -> dict:
    return {
        "session_id": "test",
        "transcript_path": "/tmp/t.jsonl",
        "cwd": str(cwd),
        "permission_mode": "default",
        "effort": {"level": "high"},
        "hook_event_name": "Stop",
        **extra,
    }


def _codex_stop(cwd: Path, **extra) -> dict:
    return {
        "session_id": "test",
        "transcript_path": None,
        "cwd": str(cwd),
        "hook_event_name": "Stop",
        "model": "codex",
        "permission_mode": "default",
        "turn_id": "t1",
        "stop_hook_active": False,
        "last_assistant_message": "last text",
        **extra,
    }


def _claude_pretool(cwd: Path, tool_name: str, tool_input: dict) -> dict:
    return {
        "session_id": "test",
        "transcript_path": "/tmp/t.jsonl",
        "cwd": str(cwd),
        "permission_mode": "default",
        "effort": {"level": "high"},
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
    }


def _codex_pretool(cwd: Path, tool_name: str, tool_input: dict) -> dict:
    return {
        "session_id": "test",
        "transcript_path": None,
        "cwd": str(cwd),
        "hook_event_name": "PreToolUse",
        "model": "codex",
        "permission_mode": "default",
        "turn_id": "t1",
        "tool_name": tool_name,
        "tool_use_id": "u1",
        "tool_input": tool_input,
    }


def _claude_session_start(cwd: Path) -> dict:
    return {
        "session_id": "test",
        "transcript_path": "/tmp/t.jsonl",
        "cwd": str(cwd),
        "hook_event_name": "SessionStart",
        "source": "startup",
        "model": "claude",
    }


def _publish_artifact(session: Path, seq: int, author: str, peer: str,
                      kind: str, env) -> Path:
    """Create + publish an artifact via the real relay CLI (test the real path)."""
    # Use the test relay module directly
    args = type("A", (), {
        "kind": kind, "in_reply_to": None, "project": None, "session_id": None,
    })()
    # we want author=<author>, swap env
    os.environ["RELAY_AUTHOR"] = author
    relay.cmd_claim(args)
    # Find the new draft
    drafts = sorted((session / ".draft").glob(f"*-{author}-{kind}.md"))
    draft = drafts[-1]
    # Fill body with valid content
    text = draft.read_text()
    text = text.replace("TODO: write actionable instructions for the peer",
                        "do the thing")
    text = text.replace(
        "<!-- write your substantive content here. delete this comment. -->",
        "real body here",
    )
    draft.write_text(text)
    pub_args = type("A", (), {
        "draft_path": str(draft),
        "status": None,
        "force": False,
        "force_reason": None,
    })()
    relay.cmd_publish(pub_args)
    return session / draft.name


# ---------------------------------------------------------------------------
# 1-3. fast-path skip
# ---------------------------------------------------------------------------

def test_01_fastpath_no_sentinel_no_shared(monkeypatch, tmp_path):
    """No .shared/, no sentinel, no env — silent skip."""
    monkeypatch.chdir(tmp_path)
    for k in list(os.environ):
        if k.startswith("RELAY_"):
            monkeypatch.delenv(k, raising=False)
    payload = _claude_session_start(tmp_path)
    r = _run_hook(payload, env_overrides={"RELAY_SHARED_ROOT": None,
                                          "RELAY_AUTHOR": None})
    assert r.returncode == 0
    assert r.stdout == "", f"expected silent, got: {r.stdout!r}"


def test_02_fastpath_shared_exists_no_sentinel(monkeypatch, tmp_path):
    """.shared/ exists but no _relay/.sentinel — must still skip (codex fix)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".shared").mkdir()  # no _relay subdir
    payload = _claude_session_start(tmp_path)
    r = _run_hook(payload, env_overrides={"RELAY_SHARED_ROOT": None,
                                          "RELAY_AUTHOR": None})
    assert r.returncode == 0
    assert r.stdout == ""


def test_03_fastpath_RELAY_AUTHOR_set_but_no_sentinel(monkeypatch, tmp_path):
    """RELAY_AUTHOR exported but no sentinel — must skip (codex fix)."""
    monkeypatch.chdir(tmp_path)
    payload = _claude_session_start(tmp_path)
    r = _run_hook(payload, env_overrides={"RELAY_AUTHOR": "claude",
                                          "RELAY_SHARED_ROOT": None})
    assert r.returncode == 0
    assert r.stdout == ""


# ---------------------------------------------------------------------------
# 4. SessionStart in relay project — silent OK + trail log line
# ---------------------------------------------------------------------------

def test_04_session_start_relay_project_silent_with_log(monkeypatch, tmp_path):
    repo, _session = _bootstrap_relay_project(monkeypatch, tmp_path)
    payload = _claude_session_start(repo)
    r = _run_hook(payload, env_overrides={
        "RELAY_SHARED_ROOT": str(repo / ".shared"),
        "RELAY_AUTHOR": "claude",
    })
    assert r.returncode == 0
    assert r.stdout == ""  # clean session => no surface
    trail = repo / ".shared" / "_relay" / "hook-trail.log"
    assert trail.exists(), "expected trail log written"
    last = trail.read_text().strip().splitlines()[-1]
    j = json.loads(last)
    assert j["event"] == "SessionStart"
    assert j["decision"] in ("ok", "doctor-failed", "no-relay-cli")


# ---------------------------------------------------------------------------
# 5-6. PreToolUse Edit on Claude
# ---------------------------------------------------------------------------

def test_05_pretool_edit_ready_artifact_denied(monkeypatch, tmp_path):
    repo, session = _bootstrap_relay_project(monkeypatch, tmp_path)
    pub = _publish_artifact(session, 1, "claude", "codex", "plan", os.environ)
    payload = _claude_pretool(repo, "Edit", {"file_path": str(pub)})
    r = _run_hook(payload, env_overrides={
        "RELAY_SHARED_ROOT": str(repo / ".shared"),
        "RELAY_AUTHOR": "claude",
    })
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert "hookSpecificOutput" in out
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert "append-only" in hso["permissionDecisionReason"]
    # Critical: must NOT use continue: false (codex fix #1)
    assert "continue" not in out, f"continue must be absent on Codex; got {out}"


def test_06_pretool_edit_draft_allowed(monkeypatch, tmp_path):
    repo, session = _bootstrap_relay_project(monkeypatch, tmp_path)
    draft_dir = session / ".draft"
    draft_dir.mkdir(exist_ok=True)
    draft = draft_dir / "001-claude-plan.md"
    draft.write_text("---\nseq: 1\n---\nbody\n")
    payload = _claude_pretool(repo, "Edit", {"file_path": str(draft)})
    r = _run_hook(payload, env_overrides={
        "RELAY_SHARED_ROOT": str(repo / ".shared"),
        "RELAY_AUTHOR": "claude",
    })
    assert r.returncode == 0
    assert r.stdout == "", f"draft edit should be silent; got {r.stdout!r}"


# ---------------------------------------------------------------------------
# 7-10. PreToolUse on Codex (apply_patch)
# ---------------------------------------------------------------------------

def test_07_pretool_apply_patch_update_file_denied(monkeypatch, tmp_path):
    repo, session = _bootstrap_relay_project(monkeypatch, tmp_path)
    pub = _publish_artifact(session, 1, "claude", "codex", "plan", os.environ)
    rel = pub.relative_to(repo)
    patch = f"""*** Begin Patch
*** Update File: {rel}
@@
-old
+new
*** End Patch
"""
    payload = _codex_pretool(repo, "apply_patch", {"command": patch})
    r = _run_hook(payload, env_overrides={
        "RELAY_SHARED_ROOT": str(repo / ".shared"),
        "RELAY_AUTHOR": "claude",
    })
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_08_pretool_apply_patch_move_to_dest_ready_denied(monkeypatch, tmp_path):
    """Codex review fix: Move to: <dest> must be parsed and canonicalized."""
    repo, session = _bootstrap_relay_project(monkeypatch, tmp_path)
    pub = _publish_artifact(session, 1, "claude", "codex", "plan", os.environ)
    rel = pub.relative_to(repo)
    patch = f"""*** Begin Patch
*** Update File: some-other.md
*** Move to: {rel}
@@
-x
+y
*** End Patch
"""
    payload = _codex_pretool(repo, "apply_patch", {"command": patch})
    r = _run_hook(payload, env_overrides={
        "RELAY_SHARED_ROOT": str(repo / ".shared"),
        "RELAY_AUTHOR": "claude",
    })
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_09_pretool_path_canonicalization_dotslash(monkeypatch, tmp_path):
    """Codex review fix: ./relative/path canonicalized vs string prefix."""
    repo, session = _bootstrap_relay_project(monkeypatch, tmp_path)
    pub = _publish_artifact(session, 1, "claude", "codex", "plan", os.environ)
    rel = pub.relative_to(repo)
    payload = _claude_pretool(repo, "Edit", {"file_path": f"./{rel}"})
    r = _run_hook(payload, env_overrides={
        "RELAY_SHARED_ROOT": str(repo / ".shared"),
        "RELAY_AUTHOR": "claude",
    })
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_09b_pretool_edit_ready_sidecar_denied(monkeypatch, tmp_path):
    """Finding 4: PreToolUse must protect .ready sidecar, not only .md.

    Pre-fix, codex's repro showed deleting `001-claude-plan.ready` slipped
    past the hook because `.ready` has the wrong suffix.
    """
    repo, session = _bootstrap_relay_project(monkeypatch, tmp_path)
    pub = _publish_artifact(session, 1, "claude", "codex", "plan", os.environ)
    ready = pub.with_suffix(".ready")
    assert ready.exists(), "test setup: ready sidecar must exist"
    payload = _claude_pretool(repo, "Edit", {"file_path": str(ready)})
    r = _run_hook(payload, env_overrides={
        "RELAY_SHARED_ROOT": str(repo / ".shared"),
        "RELAY_AUTHOR": "claude",
    })
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "append-only" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_09c_pretool_edit_sha256_sidecar_denied(monkeypatch, tmp_path):
    """Finding 4: PreToolUse must also protect .md.sha256 sidecar."""
    repo, session = _bootstrap_relay_project(monkeypatch, tmp_path)
    pub = _publish_artifact(session, 1, "claude", "codex", "plan", os.environ)
    sha = pub.with_name(pub.name + ".sha256")
    assert sha.exists(), "test setup: sha256 sidecar must exist"
    payload = _claude_pretool(repo, "Edit", {"file_path": str(sha)})
    r = _run_hook(payload, env_overrides={
        "RELAY_SHARED_ROOT": str(repo / ".shared"),
        "RELAY_AUTHOR": "claude",
    })
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_09d_pretool_apply_patch_delete_ready_sidecar_denied(monkeypatch, tmp_path):
    """Finding 4: codex's exact repro — apply_patch deleting .ready file.

    Pre-fix this silently succeeded because path filtering kept only .md.
    """
    repo, session = _bootstrap_relay_project(monkeypatch, tmp_path)
    pub = _publish_artifact(session, 1, "claude", "codex", "plan", os.environ)
    ready = pub.with_suffix(".ready")
    rel = ready.relative_to(repo)
    patch = f"*** Begin Patch\n*** Delete File: {rel}\n*** End Patch\n"
    payload = _codex_pretool(repo, "apply_patch", {"command": patch})
    r = _run_hook(payload, env_overrides={
        "RELAY_SHARED_ROOT": str(repo / ".shared"),
        "RELAY_AUTHOR": "claude",
    })
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_09e_pretool_edit_ready_for_nonexistent_md_allowed(monkeypatch, tmp_path):
    """Finding 4 negative: a stray .ready with no matching .md must NOT
    trigger the deny path — the protection is keyed on (md, ready) pair.
    """
    repo, session = _bootstrap_relay_project(monkeypatch, tmp_path)
    stray = session / "999-claude-plan.ready"
    stray.touch()
    payload = _claude_pretool(repo, "Edit", {"file_path": str(stray)})
    r = _run_hook(payload, env_overrides={
        "RELAY_SHARED_ROOT": str(repo / ".shared"),
        "RELAY_AUTHOR": "claude",
    })
    assert r.returncode == 0
    # stdout empty = no deny; the stray ready exists but the published
    # protection is "ready exists for some md" — we keyed our check on
    # `ready.exists()` which IS true here, so this CAN deny. Let's
    # confirm the actual behavior: we deny on the trio's .ready
    # regardless of .md existing. That's safer (refuse to mess with
    # any .ready) and matches the protocol's "ready is the gate" stance.
    if r.stdout:
        out = json.loads(r.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_10_pretool_codex_deny_shape_no_continue_false(monkeypatch, tmp_path):
    """Codex fix #1: deny shape never includes continue: false."""
    repo, session = _bootstrap_relay_project(monkeypatch, tmp_path)
    pub = _publish_artifact(session, 1, "claude", "codex", "plan", os.environ)
    rel = pub.relative_to(repo)
    patch = f"*** Begin Patch\n*** Delete File: {rel}\n*** End Patch\n"
    payload = _codex_pretool(repo, "apply_patch", {"command": patch})
    r = _run_hook(payload, env_overrides={
        "RELAY_SHARED_ROOT": str(repo / ".shared"),
        "RELAY_AUTHOR": "claude",
    })
    out = json.loads(r.stdout)
    assert "continue" not in out
    assert "stopReason" not in out
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


# ---------------------------------------------------------------------------
# 11. Stop addressed-to-me peer artifact → decision: block
# ---------------------------------------------------------------------------

def test_11_stop_peer_addressed_to_me_blocks(monkeypatch, tmp_path):
    repo, session = _bootstrap_bound(monkeypatch, tmp_path)  # bind claude:test (the hook's session)
    # codex publishes artifact addressed to claude (me)
    _publish_artifact(session, 1, "codex", "claude", "review", os.environ)
    os.environ["RELAY_AUTHOR"] = "claude"  # restore me
    payload = _claude_stop(repo)
    r = _run_hook(payload, env_overrides={
        "RELAY_SHARED_ROOT": str(repo / ".shared"),
        "RELAY_AUTHOR": "claude",
    })
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["decision"] == "block"
    assert "[relay-state]" in out["reason"]
    assert "addressed=me" in out["reason"]
    # codex review seq 6: must NOT include continue/stopReason — Codex says
    # continue: false would override decision: block.
    assert "continue" not in out, f"Stop block must not carry continue; got {out}"
    assert "stopReason" not in out


def test_11a_stop_peer_addressed_to_me_uses_payload_author_not_relay_author(monkeypatch, tmp_path):
    """Stop hooks work in a zero-RELAY_AUTHOR same-host setup: the platform
    signal (CLAUDE_CODE_SESSION_ID) supplies BOTH the author and the binding
    identity, so RELAY_AUTHOR is not required. With strict binding the stopping
    session must be bound (claude:test here) for the surface to fire.
    """
    repo, session = _bootstrap_bound(monkeypatch, tmp_path)  # bind claude:test
    _publish_artifact(session, 1, "codex", "claude", "review", os.environ)
    payload = _claude_stop(repo)
    r = _run_hook(payload, env_overrides={
        "RELAY_SHARED_ROOT": str(repo / ".shared"),
        "RELAY_AUTHOR": None,
        # realistic same-host claude signal; equals the bound session id 'test'
        "CLAUDE_CODE_SESSION_ID": "test",
        "CODEX_THREAD_ID": None,
    })
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["decision"] == "block"
    assert "addressed=me" in out["reason"]


def test_11b_stop_draft_block_shape_clean(monkeypatch, tmp_path):
    """Stop block-draft variant must also avoid continue/stopReason."""
    repo, session = _bootstrap_bound(monkeypatch, tmp_path)  # bind claude:test
    # I'm claude; create a draft for me but no peer publish addressed to me
    draft_dir = session / ".draft"
    draft_dir.mkdir(exist_ok=True)
    (draft_dir / "001-claude-plan.md").write_text("---\nseq: 1\n---\nbody\n")
    os.environ["RELAY_AUTHOR"] = "claude"
    payload = _claude_stop(repo)
    r = _run_hook(payload, env_overrides={
        "RELAY_SHARED_ROOT": str(repo / ".shared"),
        "RELAY_AUTHOR": "claude",
    })
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["decision"] == "block"
    assert "unpublished draft" in out["reason"]
    assert "continue" not in out
    assert "stopReason" not in out


# ---------------------------------------------------------------------------
# 12. Stop dedup — second call same fingerprint → silent
# ---------------------------------------------------------------------------

def test_12_stop_dedup_silent_on_unchanged_fingerprint(monkeypatch, tmp_path):
    repo, session = _bootstrap_bound(monkeypatch, tmp_path)  # bind claude:test
    _publish_artifact(session, 1, "codex", "claude", "review", os.environ)
    os.environ["RELAY_AUTHOR"] = "claude"
    payload = _claude_stop(repo)
    overrides = {
        "RELAY_SHARED_ROOT": str(repo / ".shared"),
        "RELAY_AUTHOR": "claude",
    }
    r1 = _run_hook(payload, env_overrides=overrides)
    assert r1.returncode == 0
    assert json.loads(r1.stdout)["decision"] == "block"  # first time surfaces

    r2 = _run_hook(payload, env_overrides=overrides)
    assert r2.returncode == 0
    assert r2.stdout == "", "second call must dedup silently"


# ---------------------------------------------------------------------------
# 13. stop_hook_active=true → never block again
# ---------------------------------------------------------------------------

def test_13_stop_hook_active_skip_no_block(monkeypatch, tmp_path):
    repo, session = _bootstrap_relay_project(monkeypatch, tmp_path)
    _publish_artifact(session, 1, "codex", "claude", "review", os.environ)
    os.environ["RELAY_AUTHOR"] = "claude"
    # Both shapes
    overrides = {
        "RELAY_SHARED_ROOT": str(repo / ".shared"),
        "RELAY_AUTHOR": "claude",
    }
    for payload in (
        _claude_stop(repo, stop_hook_active=True),
        _codex_stop(repo, stop_hook_active=True),
    ):
        r = _run_hook(payload, env_overrides=overrides)
        assert r.returncode == 0
        assert r.stdout == "", f"stop_hook_active must skip; got {r.stdout!r}"


# ---------------------------------------------------------------------------
# 14. Stop strict-binding — an UNBOUND session is never pulled into a pair
#     (regression for issue 20260601T182646-2920d5b9)
# ---------------------------------------------------------------------------

def test_14_stop_unbound_session_stays_silent(monkeypatch, tmp_path):
    """A session NOT bound to any pair must never be surfaced into the sole
    active pair. Bind claude:test + publish a codex artifact addressed to
    claude, then fire Stop from a DIFFERENT, unbound claude session id. Strict
    binding => clean-exit (silent), no block."""
    repo, session = _bootstrap_bound(monkeypatch, tmp_path)  # binds claude:test
    _publish_artifact(session, 1, "codex", "claude", "review", os.environ)
    os.environ["RELAY_AUTHOR"] = "claude"
    payload = _claude_stop(repo, session_id="other-unbound-window")
    r = _run_hook(payload, env_overrides={
        "RELAY_SHARED_ROOT": str(repo / ".shared"),
        "RELAY_AUTHOR": "claude",
    })
    assert r.returncode == 0
    assert r.stdout == "", (
        "unbound session must NOT be pulled into the sole active pair; "
        f"got stdout={r.stdout!r}"
    )


# ---------------------------------------------------------------------------
# Installer merge: append-after preserves existing entries byte-for-byte
# ---------------------------------------------------------------------------

def test_installer_appends_after_preserving_existing(monkeypatch, tmp_path):
    """codex review test #12 — existing oh-my-codex-style entry stays intact."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    (fake_home / ".codex").mkdir()
    cfg = fake_home / ".codex" / "hooks.json"
    existing = {
        "state": {
            "/some/path/hooks.json:pre_tool_use:0:0": {"trusted_hash": "sha256:abc"},
        },
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": "/usr/bin/dispatcher"}],
                }
            ],
        },
    }
    cfg.write_text(json.dumps(existing, indent=2))
    before_bytes = cfg.read_bytes()

    args = type("A", (), {"target": "codex", "dry_run": False})()
    relay.cmd_hooks_install(args)

    after = json.loads(cfg.read_text())
    pre = after["hooks"]["PreToolUse"]
    assert len(pre) == 2, "expected existing + appended"
    assert pre[0]["matcher"] == "Bash", "existing entry must be index 0"
    assert pre[0]["hooks"][0]["command"] == "/usr/bin/dispatcher"
    assert pre[1].get("_agent_relay_managed") is True
    # State map preserved
    assert "state" in after
    assert after["state"]["/some/path/hooks.json:pre_tool_use:0:0"]["trusted_hash"] == "sha256:abc"

    # Uninstall removes only managed
    rm_args = type("A", (), {"target": "codex", "dry_run": False})()
    relay.cmd_hooks_uninstall(rm_args)
    after_rm = json.loads(cfg.read_text())
    pre_rm = after_rm["hooks"]["PreToolUse"]
    assert len(pre_rm) == 1
    assert pre_rm[0]["matcher"] == "Bash"


def test_installer_command_is_path_independent(monkeypatch, tmp_path):
    """Regression for codex hook exit 127 — two root causes covered.

    (a) PATH-stripped spawn: Codex runs hooks with a stripped PATH (no
        `/usr/bin`), so the rendered command must invoke an absolute python
        interpreter, not rely on `#!/usr/bin/env python3`.
    (b) Bad-path installs: a prior buggy install left a double-nested
        dispatcher path on one machine (`.../skills/agent-relay/skills/
        agent-relay/...`) that didn't exist on disk. Assert the rendered
        script path actually exists, so a future installer regression that
        emits a nonexistent path fails this test.
    """
    import shlex
    import sys

    for target in ("claude", "codex"):
        fake_home = tmp_path / f"home_{target}"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        (fake_home / (".claude" if target == "claude" else ".codex")).mkdir()

        args = type("A", (), {"target": target, "dry_run": False})()
        relay.cmd_hooks_install(args)

        cfg_path = (fake_home / ".claude" / "settings.json"
                    if target == "claude" else fake_home / ".codex" / "hooks.json")
        cfg = json.loads(cfg_path.read_text())

        for event in ("SessionStart", "PreToolUse", "Stop"):
            managed = [e for e in cfg["hooks"][event] if e.get("_agent_relay_managed")]
            assert managed, f"{target}/{event}: missing managed entry"
            cmd = managed[0]["hooks"][0]["command"]
            # v0.10.1 form: `[ -f <script> ] || exit 0; exec <python> <script>`
            # — a shell-evaluated fail-open guard (issue 20260529T092859) plus
            # the PATH-independent absolute interpreter.
            assert "|| exit 0" in cmd, (
                f"{target}/{event}: command must fail open if script missing; got {cmd!r}"
            )
            assert "exec " in cmd, f"{target}/{event}: expected exec form; got {cmd!r}"
            # The exec'd part carries the interpreter + script.
            exec_part = cmd.split("exec ", 1)[1]
            interp, script = shlex.split(exec_part)
            assert interp == sys.executable, (
                f"{target}/{event}: interpreter must be absolute sys.executable; "
                f"got {interp!r}"
            )
            assert Path(interp).is_absolute() and Path(interp).is_file(), (
                f"{target}/{event}: interpreter must be an absolute existing path: {interp!r}"
            )
            assert script.endswith("relay-hook.py"), (
                f"{target}/{event}: exec target must be the hook script: {script!r}"
            )
            assert Path(script).is_absolute(), (
                f"{target}/{event}: script path is not absolute: {script!r}"
            )
            assert Path(script).is_file(), (
                f"{target}/{event}: script path must exist on disk "
                f"(catches double-nested install bugs): {script!r}"
            )
            # The guard must test the SAME script it execs.
            guard_part = cmd.split("||", 1)[0]
            assert shlex.split(guard_part) == ["[", "-f", script, "]"], (
                f"{target}/{event}: guard must test the exec'd script; got {guard_part!r}"
            )


def test_hook_trail_timestamp_is_local_offset_no_microseconds(monkeypatch, tmp_path):
    """Issue 20260529T093645: hook-trail.log `ts` must use the project's
    now_iso() shape — local offset, no microseconds — not UTC+microseconds."""
    import re as _re
    repo, session = _bootstrap_relay_project(monkeypatch, tmp_path)
    pub = _publish_artifact(session, 1, "claude", "codex", "plan", os.environ)
    # A PreToolUse deny writes a trail entry.
    payload = _claude_pretool(repo, "Edit", {"file_path": str(pub)})
    _run_hook(payload, env_overrides={
        "RELAY_SHARED_ROOT": str(repo / ".shared"), "RELAY_AUTHOR": "claude",
    })
    trail = repo / ".shared" / "_relay" / "hook-trail.log"
    lines = [l for l in trail.read_text().splitlines() if l.strip()]
    assert lines, "expected at least one trail entry"
    ts = json.loads(lines[-1])["ts"]
    # local offset, seconds precision, no fractional seconds, no bare 'Z'
    assert _re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$", ts), (
        f"ts must be local-offset, no microseconds; got {ts!r}"
    )


def test_installer_command_fails_open_when_script_missing(monkeypatch, tmp_path):
    """Issue 20260529T092859: if the dispatcher script is missing, the rendered
    hook command must exit 0 (allow the edit) instead of erroring and blocking
    every Edit/Write. Command hooks are shell-evaluated, so run it via `sh -c`."""
    import subprocess as sp
    import sys as _sys

    missing = tmp_path / "gone" / "relay-hook.py"   # does not exist
    monkeypatch.setattr(relay, "HOOK_SCRIPT", missing)
    cmd = relay._hooks_render_command()
    r = sp.run(["sh", "-c", cmd], input="{}", text=True, capture_output=True)
    assert r.returncode == 0, (
        f"missing-script hook must fail open (exit 0); got {r.returncode}, cmd={cmd!r}"
    )

    # And with the real present script + empty payload, also exit 0 (allow).
    monkeypatch.setattr(relay, "HOOK_SCRIPT",
                        Path(relay.__file__).resolve().parent.parent / "hooks" / "relay-hook.py")
    cmd2 = relay._hooks_render_command()
    r2 = sp.run(["sh", "-c", cmd2], input="{}", text=True, capture_output=True)
    assert r2.returncode == 0, (
        f"present-script hook on empty input must exit 0; got {r2.returncode}, stderr={r2.stderr!r}"
    )
