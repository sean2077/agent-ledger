"""close — writes sentinel + updates session.json without mutating prior files."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

import relay


def _bootstrap(monkeypatch, tmp_path):
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
    relay.cmd_bootstrap(type("A", (), {"topic": "t", "title": None})())
    return relay.resolve_active_pair(relay.load_env())


def _publish_one(session: Path, capsys, kind="note", status="ready"):
    relay.cmd_claim(type("A", (), {"kind": kind, "in_reply_to": None, "project": None})())
    capsys.readouterr()
    drafts = sorted((session / ".draft").glob("*.md"))
    draft = drafts[-1]
    fm, _ = relay.parse_frontmatter(draft.read_text())
    fm["prompt_for_next"] = "n/a; testing\n"
    draft.write_text(relay.dump_frontmatter(fm, "\nbody.\n"))
    relay.cmd_publish(type("A", (), {"draft_path": str(draft), "status": status})())
    capsys.readouterr()
    return next(session.glob(f"{fm['seq']:03d}-*.md"))


def test_close_writes_sentinel_and_updates_session(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    args = type("A", (), {"reason": "tests done", "outcome": "approve",
                          "project": None, "no_archive": True})()
    rc = relay.cmd_close(args)
    assert rc == 0
    sentinel = session / "CLOSED"
    assert sentinel.exists()
    text = sentinel.read_text()
    assert 'reason = "tests done"' in text
    assert 'outcome = "approve"' in text
    sj = json.loads((session / "session.json").read_text())
    assert sj["state"] == "closed"
    assert sj["close_reason"] == "tests done"
    assert sj["closed_at"] is not None


def test_close_does_not_modify_prior_files(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    pub = _publish_one(session, capsys)
    digest_before = hashlib.sha256(pub.read_bytes()).hexdigest()
    relay.cmd_close(type("A", (), {"reason": "done", "outcome": None,
                                   "project": None, "no_archive": True})())
    digest_after = hashlib.sha256(pub.read_bytes()).hexdigest()
    assert digest_before == digest_after, "close must not modify prior published files"


def test_close_refuses_when_already_closed(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    (session / "CLOSED").write_text('reason = "preexisting"\n')
    rc = relay.cmd_close(type("A", (), {"reason": "x", "outcome": None, "project": None})())
    assert rc == 2
    assert "already closed" in capsys.readouterr().err


# -----------------------------------------------------------------------------
# v3 bundle coverage for R2 close-error masking + R3.a marker clear.
# -----------------------------------------------------------------------------


def test_close_deletes_binding(monkeypatch, tmp_path, capsys):
    """v0.13: cmd_close drops the closing instance's binding (replaces the old
    .active-session marker clear)."""
    session = _bootstrap(monkeypatch, tmp_path)
    shared = session.parent
    env = relay.load_env()
    assert relay.read_binding(shared, env.author, env.agent_session_id) is not None
    rc = relay.cmd_close(type("A", (), {"reason": "done", "outcome": None, "project": None})())
    assert rc == 0
    assert relay.read_binding(shared, env.author, env.agent_session_id) is None


def test_close_auto_archives_pair_by_default(monkeypatch, tmp_path, capsys):
    """v1.5.0: close moves the just-closed pair into _archive/ so .shared/ stays
    uncluttered. The CLOSED sentinel + session.json travel with it; bindings drop."""
    session = _bootstrap(monkeypatch, tmp_path)
    shared = session.parent
    capsys.readouterr()
    env = relay.load_env()
    assert relay.read_binding(shared, env.author, env.agent_session_id) is not None
    rc = relay.cmd_close(type("A", (), {"reason": "done", "outcome": "ok",
                                        "project": None, "no_archive": False})())
    assert rc == 0
    # original location is gone; the pair now lives under _archive/<slug>
    assert not session.exists()
    archived = shared / "_archive" / session.name
    assert (archived / "CLOSED").exists()
    sj = json.loads((archived / "session.json").read_text())
    assert sj["state"] == "closed"
    # archive drops the closer's binding too
    assert relay.read_binding(shared, env.author, env.agent_session_id) is None
    out = capsys.readouterr().out
    assert "archived:" in out and "_archive" in out


def test_close_no_archive_keeps_pair_in_place(monkeypatch, tmp_path, capsys):
    """--no-archive leaves the closed pair under .shared/ (sentinel written in place)."""
    session = _bootstrap(monkeypatch, tmp_path)
    shared = session.parent
    capsys.readouterr()
    rc = relay.cmd_close(type("A", (), {"reason": "done", "outcome": None,
                                        "project": None, "no_archive": True})())
    assert rc == 0
    assert (session / "CLOSED").exists()
    assert not (shared / "_archive").exists()


def test_close_succeeds_even_when_archive_blocked(monkeypatch, tmp_path, capsys):
    """Auto-archive is best-effort: a destination collision must NOT undo the
    close — the sentinel/session.json are already durable, so close still
    returns 0 and prints a retry note."""
    session = _bootstrap(monkeypatch, tmp_path)
    shared = session.parent
    capsys.readouterr()
    # pre-create the archive destination so the move fails closed
    (shared / "_archive" / session.name).mkdir(parents=True)
    rc = relay.cmd_close(type("A", (), {"reason": "done", "outcome": None,
                                        "project": None, "no_archive": False})())
    assert rc == 0
    assert (session / "CLOSED").exists()          # close still happened, in place
    err = capsys.readouterr().err
    assert "could not archive" in err and "relay pairs archive" in err


def test_close_preserves_multiple_active_error_when_closed_sentinel_exists(monkeypatch, tmp_path, capsys):
    """R2: cmd_close does not mask 'multiple active pairs' with the 'already closed' message."""
    first = _bootstrap(monkeypatch, tmp_path)
    shared = first.parent
    second = shared / "20990101-second"
    second.mkdir()
    (second / "session.json").write_text(json.dumps({
        "schema_version": 3, "project": "myproj", "session_id": second.name,
        "title": "second", "state": "active",
        "created_at": "2099-01-01T00:00:00+00:00",
        "closed_at": None, "close_reason": None, "participants": [],
    }))
    closed = shared / "20990101-closed"
    closed.mkdir()
    (closed / "session.json").write_text(json.dumps({
        "schema_version": 3, "project": "myproj", "session_id": closed.name,
        "title": "closed", "state": "closed",
        "created_at": "2099-01-01T00:00:00+00:00",
        "closed_at": "2099-01-01T00:00:00+00:00",
        "close_reason": "preexisting", "participants": [],
    }))
    (closed / "CLOSED").write_text('reason = "preexisting"\n')
    # _bootstrap bound this instance to 'first', which would let close resolve
    # via the binding instead of surfacing the ambiguity. Drop the binding so
    # the genuine multiple-active error path (no disambiguator) is under test.
    env = relay.load_env()
    relay.delete_binding(shared, env.author, env.agent_session_id)
    rc = relay.cmd_close(type("A", (), {"reason": "done", "outcome": None, "project": None})())
    err = capsys.readouterr().err
    assert rc == 2
    assert "multiple active pairs" in err
    assert "already closed" not in err
