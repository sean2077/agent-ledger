"""bootstrap + status + session-active rule."""

import json
import os
import subprocess
from pathlib import Path

import pytest

import relay


def _setup_shared(monkeypatch, tmp_path: Path, *, with_sentinel: bool = True) -> Path:
    repo = tmp_path / "myproj"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    monkeypatch.chdir(repo)
    shared = repo / ".shared"
    shared.mkdir(mode=0o700)
    if with_sentinel:
        (shared / "_relay").mkdir()
        (shared / "_relay" / ".sentinel").touch()
    for k in list(os.environ):
        if k.startswith("RELAY_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RELAY_ROLE", "host")
    monkeypatch.setenv("RELAY_AUTHOR", "codex")
    monkeypatch.setenv("RELAY_PEER", "claude")
    monkeypatch.setenv("RELAY_SHARED_ROOT", str(shared))
    return shared


def test_bootstrap_creates_full_structure(monkeypatch, tmp_path, capsys):
    shared = _setup_shared(monkeypatch, tmp_path, with_sentinel=False)
    args = type("A", (), {"topic": "smoke", "title": "smoke test"})()
    rc = relay.cmd_bootstrap(args)
    assert rc == 0

    proj_dirs = list((shared / "myproj").iterdir())
    assert len(proj_dirs) == 1
    sess = proj_dirs[0]
    assert sess.name.endswith("-smoke")
    assert (sess / "session.json").is_file()
    assert (sess / "README.md").is_file()
    assert (sess / ".draft").is_dir()
    assert (shared / "_relay" / ".sentinel").exists()
    sj = json.loads((sess / "session.json").read_text())
    assert sj["state"] == "active"
    assert sj["project"] == "myproj"
    assert sj["participants"] == ["codex", "claude"]


def test_bootstrap_refuses_duplicate(monkeypatch, tmp_path, capsys):
    _setup_shared(monkeypatch, tmp_path)
    args = type("A", (), {"topic": "dup", "title": None})()
    assert relay.cmd_bootstrap(args) == 0
    rc = relay.cmd_bootstrap(args)  # same day, same topic → same dir
    assert rc == 2
    assert "already exists" in capsys.readouterr().err


def test_bootstrap_invalid_slug(monkeypatch, tmp_path, capsys):
    _setup_shared(monkeypatch, tmp_path)
    args = type("A", (), {"topic": "Bad Slug", "title": None})()
    rc = relay.cmd_bootstrap(args)
    assert rc == 2
    assert "topic must match" in capsys.readouterr().err


def test_status_empty_session_active(monkeypatch, tmp_path, capsys):
    _setup_shared(monkeypatch, tmp_path)
    relay.cmd_bootstrap(type("A", (), {"topic": "x", "title": None})())
    capsys.readouterr()
    args = type("A", (), {"project": None, "last": 0, "json": True})()
    rc = relay.cmd_status(args)
    out = capsys.readouterr().out
    data = json.loads(out)
    assert rc == 0
    assert data["is_active"] is True
    assert data["next_seq"] == 1
    assert data["published"] == []


def test_resolve_active_session_multiple_raises(monkeypatch, tmp_path):
    shared = _setup_shared(monkeypatch, tmp_path)
    relay.cmd_bootstrap(type("A", (), {"topic": "a", "title": None})())
    # create a second active session manually (we cannot bootstrap twice on same day same topic)
    sess2 = shared / "myproj" / "20990101-other"
    sess2.mkdir(parents=True)
    (sess2 / "session.json").write_text(json.dumps({
        "schema_version": 2, "project": "myproj", "session_id": "20990101-other",
        "title": "x", "state": "active",
        "created_at": "2099-01-01T00:00:00+00:00", "closed_at": None,
        "close_reason": None, "participants": [],
    }))
    env = relay.load_env()
    with pytest.raises(SystemExit, match="multiple active"):
        relay.resolve_active_session(env)
