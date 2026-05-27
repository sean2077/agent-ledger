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


def _write_session(parent: Path, slug: str, *, state: str = "active",
                    closed_sentinel: bool = False) -> Path:
    """Create a minimal session dir under `parent`. Returns the session path."""
    sd = parent / slug
    sd.mkdir(parents=True)
    (sd / ".draft").mkdir()
    sd.joinpath("session.json").write_text(json.dumps({
        "schema_version": 2, "project": parent.name, "session_id": slug,
        "title": slug, "state": state,
        "created_at": "2026-05-27T00:00:00+08:00", "closed_at": None,
        "close_reason": None, "participants": ["codex", "claude"],
    }))
    if closed_sentinel:
        sd.joinpath("CLOSED").write_text('reason = "x"\n')
    return sd


def _publish_terminal_artifact(session: Path, seq: int = 1, status: str = "closed"):
    """Drop a published artifact whose status is terminal."""
    base = f"{seq:03d}-codex-decision"
    md = session / f"{base}.md"
    fm = {
        "seq": seq, "author": "codex", "peer": "claude", "kind": "decision",
        "status": status, "created": "2026-05-27T01:00:00+08:00",
        "in_reply_to": None, "prompt_for_next": "n/a; concluded\n",
        "sync_needed": False, "touched_paths": [], "corrects": None,
    }
    md.write_text(relay.dump_frontmatter(fm, "\nbody.\n"))
    digest = relay.sha256_of_file(md)
    (session / f"{base}.md.sha256").write_text(f"{digest}  {base}.md\n")
    (session / f"{base}.ready").write_text("")


def test_resolve_skips_session_with_terminal_latest(monkeypatch, tmp_path):
    """Bug fix MAJOR #1: resolve_active_session must filter through session_is_active."""
    shared = _setup_shared(monkeypatch, tmp_path)
    proj_dir = shared / "myproj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    # session A: state=active but latest artifact is closed -> NOT truly active
    sa = _write_session(proj_dir, "20260527-stale")
    _publish_terminal_artifact(sa, seq=1, status="closed")
    # session B: state=active, no artifacts yet -> truly active
    sb = _write_session(proj_dir, "20260527-real")

    env = relay.load_env()
    resolved = relay.resolve_active_session(env)
    assert resolved == sb, f"expected real session, got {resolved}"


def test_resolve_skips_session_with_CLOSED_sentinel(monkeypatch, tmp_path):
    """Bug fix MAJOR #1: CLOSED sentinel must filter out stale-active sessions."""
    shared = _setup_shared(monkeypatch, tmp_path)
    proj_dir = shared / "myproj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    _write_session(proj_dir, "20260527-with-closed-file", closed_sentinel=True)
    sb = _write_session(proj_dir, "20260527-real")
    env = relay.load_env()
    resolved = relay.resolve_active_session(env)
    assert resolved == sb


def test_bootstrap_creates_dirs_with_0700_mode(monkeypatch, tmp_path):
    """Bug fix MINOR #5: project + session dirs must be 0700."""
    import stat as stat_mod
    shared = _setup_shared(monkeypatch, tmp_path)
    args = type("A", (), {"topic": "perms", "title": None})()
    assert relay.cmd_bootstrap(args) == 0
    proj_dir = shared / "myproj"
    assert (proj_dir.stat().st_mode & 0o777) == 0o700, \
        f"project dir mode is {oct(proj_dir.stat().st_mode & 0o777)}"
    sess = next(proj_dir.iterdir())
    assert (sess.stat().st_mode & 0o777) == 0o700, \
        f"session dir mode is {oct(sess.stat().st_mode & 0o777)}"


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
