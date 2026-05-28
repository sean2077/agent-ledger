"""relay doctor — stale-state reporting and owner-safe cleanup."""

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

import relay


def _bootstrap(monkeypatch, tmp_path: Path):
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
    monkeypatch.setenv("RELAY_SYNC", "none")
    monkeypatch.setenv("RELAY_AUTHOR", "claude")
    monkeypatch.setenv("RELAY_PEER", "codex")
    monkeypatch.setenv("RELAY_SHARED_ROOT", str(shared))
    relay.cmd_bootstrap(type("A", (), {"topic": "t", "title": None})())
    return relay.resolve_active_session(relay.load_env()), shared


def _doctor_args(**kw):
    base = {"json": False, "fix": False, "older_than": None}
    base.update(kw)
    return type("A", (), base)()


def test_doctor_clean_session_reports_no_findings(monkeypatch, tmp_path, capsys):
    _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    rc = relay.cmd_doctor(_doctor_args())
    assert rc == 0
    out = capsys.readouterr().out
    assert "summary: 0 findings" in out


def test_doctor_reports_abandoned_draft_without_deleting(monkeypatch, tmp_path, capsys):
    """Default mode is report-only — drafts must not be deleted."""
    session, _ = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    # Plant a draft and backdate it
    draft_dir = session / ".draft"
    draft_dir.mkdir(exist_ok=True)
    draft = draft_dir / "001-claude-plan.md"
    draft.write_text("body\n")
    long_ago = time.time() - 7200  # 2h
    os.utime(draft, (long_ago, long_ago))

    rc = relay.cmd_doctor(_doctor_args())
    assert rc == 1  # findings
    out = capsys.readouterr().out
    assert "001-claude-plan.md" in out
    assert draft.exists()  # NOT deleted in report-only mode


def test_doctor_fix_without_older_than_does_not_delete_drafts(monkeypatch, tmp_path, capsys):
    """--fix alone cleans owner-safe junk but never touches drafts."""
    session, _ = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    draft_dir = session / ".draft"
    draft_dir.mkdir(exist_ok=True)
    draft = draft_dir / "001-claude-plan.md"
    draft.write_text("body\n")
    long_ago = time.time() - 7200
    os.utime(draft, (long_ago, long_ago))

    rc = relay.cmd_doctor(_doctor_args(fix=True))
    assert rc == 1
    assert draft.exists()  # still there: --fix alone does not delete drafts


def test_doctor_fix_with_older_than_deletes_old_drafts(monkeypatch, tmp_path, capsys):
    """--fix + --older-than 1h deletes drafts older than 1h, keeps fresh ones."""
    session, _ = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    draft_dir = session / ".draft"
    draft_dir.mkdir(exist_ok=True)
    old = draft_dir / "001-claude-plan.md"
    fresh = draft_dir / "002-claude-plan.md"
    old.write_text("old\n")
    fresh.write_text("fresh\n")
    long_ago = time.time() - 7200  # 2h
    os.utime(old, (long_ago, long_ago))
    # fresh keeps the current mtime

    rc = relay.cmd_doctor(_doctor_args(fix=True, older_than="1h"))
    out = capsys.readouterr().out
    assert rc == 1
    assert not old.exists(), "old draft should be deleted"
    assert fresh.exists(), "fresh draft should be preserved"
    assert "deleted abandoned draft" in out


def test_doctor_older_than_without_fix_errors(monkeypatch, tmp_path, capsys):
    """--older-than without --fix has no effect → explicit error with recovery hint."""
    _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    rc = relay.cmd_doctor(_doctor_args(older_than="1h"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "--older-than" in err and "--fix" in err


def test_doctor_invalid_older_than_errors(monkeypatch, tmp_path, capsys):
    _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    rc = relay.cmd_doctor(_doctor_args(fix=True, older_than="not-a-duration"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "invalid --older-than" in err


def test_doctor_json_output_is_valid(monkeypatch, tmp_path, capsys):
    session, _ = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    (session / ".draft").mkdir(exist_ok=True)
    (session / ".draft" / "001-claude-plan.md").write_text("x\n")

    rc = relay.cmd_doctor(_doctor_args(json=True))
    assert rc == 1
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["findings_count"] == 1
    assert data["sessions"][0]["session_id"].endswith("-t")
    assert data["sessions"][0]["drafts"][0]["name"] == "001-claude-plan.md"
    assert data["actions"] == []  # no --fix


def test_doctor_fix_removes_dead_pidfile(monkeypatch, tmp_path, capsys):
    """A heartbeat pidfile pointing to a dead PID should be cleaned by --fix."""
    session, _ = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    pidfile = relay._heartbeat_pidfile_path(session, "claude")
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    # Spawn a process and immediately reap it → dead PID
    proc = subprocess.Popen(["true"])
    proc.wait()
    pidfile.write_text(f"{proc.pid}\n")

    rc = relay.cmd_doctor(_doctor_args(fix=True))
    assert rc == 1
    out = capsys.readouterr().out
    assert "removed dead pidfile" in out
    assert not pidfile.exists()


def test_doctor_does_not_kill_live_unrelated_pidfile(monkeypatch, tmp_path, capsys):
    """Even with --fix, doctor must NEVER signal a live PID — files-only cleanup."""
    session, _ = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    pidfile = relay._heartbeat_pidfile_path(session, "claude")
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    unrelated = subprocess.Popen(["sleep", "30"])
    try:
        pidfile.write_text(f"{unrelated.pid}\n")
        rc = relay.cmd_doctor(_doctor_args(fix=True))
        assert rc == 1
        assert unrelated.poll() is None, "doctor must not kill unrelated process"
        # Live pidfile is reported but NOT auto-removed (could still be valid daemon)
        assert pidfile.exists()
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_parse_duration_valid_units():
    assert relay._parse_duration("30s") == 30
    assert relay._parse_duration("5m") == 300
    assert relay._parse_duration("2h") == 7200
    assert relay._parse_duration("1d") == 86400


def test_parse_duration_invalid_returns_none():
    assert relay._parse_duration("") is None
    assert relay._parse_duration("abc") is None
    assert relay._parse_duration("10") is None  # no unit
    assert relay._parse_duration("10x") is None  # unknown unit
    assert relay._parse_duration("-1h") is None
