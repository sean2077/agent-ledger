"""Pair-name visibility for precise pairing.

`relay pair show` lets either side see, anytime, which pair this session is bound
to and the exact `relay pair join <slug>` command the peer uses to pair with it.
`relay bootstrap` announces the same up front so the other agent can join the
exact pair even when several pairs exist.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

import relay


def _be(monkeypatch, author: str, agent_session: str) -> None:
    monkeypatch.setenv("RELAY_AUTHOR", author)
    monkeypatch.setenv("RELAY_AGENT_SESSION_ID", agent_session)


def _init_shared(monkeypatch, tmp_path: Path) -> Path:
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
    monkeypatch.setenv("RELAY_CLAIM_NO_HEARTBEAT", "1")
    monkeypatch.setenv("RELAY_SHARED_ROOT", str(shared))
    return shared


def _bootstrap(monkeypatch, tmp_path: Path, topic="t") -> Path:
    """codex creates + binds; peer = claude."""
    _init_shared(monkeypatch, tmp_path)
    _be(monkeypatch, "codex", "test-codex-window")
    relay.cmd_bootstrap(type("A", (), {"topic": topic, "title": None})())
    return relay.resolve_active_pair(relay.load_env())


def _show(json_mode=False) -> int:
    return relay.cmd_pair_show(type("A", (), {"json": json_mode,
                                              "agent_session_id": None})())


# --------------------------------------------------------------------------
# bootstrap announcement
# --------------------------------------------------------------------------

def test_bootstrap_announces_pair_name_and_peer_join(monkeypatch, tmp_path, capsys):
    _init_shared(monkeypatch, tmp_path)
    _be(monkeypatch, "codex", "test-codex-window")
    rc = relay.cmd_bootstrap(type("A", (), {"topic": "demo", "title": None})())
    out = capsys.readouterr().out
    session = relay.resolve_active_pair(relay.load_env())
    slug = session.name
    assert rc == 0
    # The slug carries a today-date prefix we can't hardcode; assert via the
    # resolved pair name.
    assert "pair name:" in out
    assert slug in out
    # The exact, copy-pasteable peer-join command for precise pairing.
    assert f"relay pair join {slug}" in out
    # Names both sides so the user knows who joins.
    assert "codex" in out  # you (creator)
    assert "claude" in out  # peer to invite


# --------------------------------------------------------------------------
# relay pair show
# --------------------------------------------------------------------------

def test_pair_show_text_bound(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    _be(monkeypatch, "codex", "test-codex-window")
    capsys.readouterr()  # flush bootstrap announcement
    rc = _show()
    out = capsys.readouterr().out
    assert rc == 0
    assert f"pair:    {session.name}" in out
    assert "peer:    claude" in out
    assert f"relay pair join {session.name}" in out


def test_pair_show_json_bound(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    _be(monkeypatch, "codex", "test-codex-window")
    capsys.readouterr()  # flush bootstrap announcement
    rc = _show(json_mode=True)
    payload = json.loads(capsys.readouterr().out.strip())
    assert rc == 0
    assert payload["bound"] is True
    assert payload["pair"] == session.name
    assert payload["author"] == "codex"
    assert payload["peer"] == "claude"
    assert payload["peer_join_cmd"] == f"relay pair join {session.name}"


def test_pair_show_unbound(monkeypatch, tmp_path, capsys):
    """A window that never joined reports unbound, with no join command."""
    _bootstrap(monkeypatch, tmp_path)  # binds codex only
    _be(monkeypatch, "claude", "test-claude-unbound")  # different, unbound instance
    capsys.readouterr()  # flush bootstrap announcement
    rc = _show(json_mode=True)
    payload = json.loads(capsys.readouterr().out.strip())
    assert rc == 0
    assert payload["bound"] is False
    assert payload["pair"] is None
    assert payload["peer_join_cmd"] is None


def test_pair_show_reports_pair_even_when_closed(monkeypatch, tmp_path, capsys):
    """`pair show` reads the binding directly, so it still names the pair after
    it has gone terminal/closed (whereas active-pair resolution would hide it).
    This is what keeps "which pair am I in" answerable at any lifecycle stage."""
    session = _bootstrap(monkeypatch, tmp_path)
    (session / "CLOSED").write_text('reason = "done"\n')
    _be(monkeypatch, "codex", "test-codex-window")
    capsys.readouterr()  # flush bootstrap announcement
    rc = _show(json_mode=True)
    payload = json.loads(capsys.readouterr().out.strip())
    assert rc == 0
    assert payload["bound"] is True
    assert payload["pair"] == session.name
    assert payload["peer"] == "claude"
