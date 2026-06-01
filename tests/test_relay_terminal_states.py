"""Terminal-state semantics — the key r7 fix.

Verifies that all 4 terminal statuses (closed/cancelled/failed/timed_out) make
session_is_active() return False on the latest published file, while `ready`
keeps it True.
"""

import os
import subprocess
from pathlib import Path

import pytest

import relay


def _bootstrap(monkeypatch, tmp_path, topic="t"):
    repo = tmp_path / "myproj"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    monkeypatch.chdir(repo)
    shared = repo / ".shared"
    shared.mkdir(mode=0o700)
    (shared / "_relay").mkdir()
    (shared / "_relay" / ".sentinel").touch()
    for k in list(os.environ):
        if k.startswith("RELAY_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RELAY_SYNC", "rsync")
    monkeypatch.setenv("RELAY_REMOTE_SSH", "x@y")
    monkeypatch.setenv("RELAY_REMOTE_PATH", "/r")
    monkeypatch.setenv("RELAY_AUTHOR", "codex")
    monkeypatch.setenv("RELAY_AGENT_SESSION_ID", "test-codex-window")
    monkeypatch.setenv("RELAY_SHARED_ROOT", str(shared))
    relay.cmd_bootstrap(type("A", (), {"topic": topic, "title": None})())
    return relay.resolve_active_pair(relay.load_env())


def _publish_one(session: Path, status: str, *, kind="note") -> Path:
    args = type("A", (), {"kind": kind, "in_reply_to": None, "project": None})()
    relay.cmd_claim(args)
    # find the freshly created draft
    drafts = sorted((session / ".draft").glob("*.md"))
    draft = drafts[-1]
    fm, _ = relay.parse_frontmatter(draft.read_text())
    fm["prompt_for_next"] = "n/a; testing\n"
    draft.write_text(relay.dump_frontmatter(fm, "\nbody.\n"))
    relay.cmd_publish(type("A", (), {"draft_path": str(draft), "status": status})())
    return next(session.glob(f"{fm['seq']:03d}-*.md"))


@pytest.mark.parametrize("status", ["closed", "cancelled", "failed", "timed_out"])
def test_session_inactive_after_terminal_status(monkeypatch, tmp_path, capsys, status):
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    _publish_one(session, status=status)
    assert relay.session_is_active(session) is False, \
        f"expected session inactive after status={status}"


def test_session_active_after_ready(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    _publish_one(session, status="ready")
    assert relay.session_is_active(session) is True


def test_session_active_with_multiple_ready(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    _publish_one(session, status="ready")
    _publish_one(session, status="ready")
    assert relay.session_is_active(session) is True


def test_session_inactive_when_latest_is_terminal_even_if_prior_ready(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    _publish_one(session, status="ready")
    _publish_one(session, status="closed")
    assert relay.session_is_active(session) is False


def test_session_inactive_when_CLOSED_sentinel_present(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    _publish_one(session, status="ready")
    (session / "CLOSED").write_text('reason = "test"\n')
    assert relay.session_is_active(session) is False


def test_session_inactive_when_session_state_closed(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    _publish_one(session, status="ready")
    import json
    sj = json.loads((session / "session.json").read_text())
    sj["state"] = "closed"
    (session / "session.json").write_text(json.dumps(sj))
    assert relay.session_is_active(session) is False


def test_empty_session_after_bootstrap_is_active(monkeypatch, tmp_path):
    session = _bootstrap(monkeypatch, tmp_path)
    assert relay.session_is_active(session) is True
