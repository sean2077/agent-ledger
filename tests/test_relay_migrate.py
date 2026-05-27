"""relay migrate v2-to-v3."""

import json
import os
import subprocess
from pathlib import Path

import relay


def _setup_shared(monkeypatch, tmp_path: Path) -> Path:
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
    monkeypatch.setenv("RELAY_ROLE", "host")
    monkeypatch.setenv("RELAY_AUTHOR", "codex")
    monkeypatch.setenv("RELAY_PEER", "claude")
    monkeypatch.setenv("RELAY_SHARED_ROOT", str(shared))
    return shared


def _write_v2_session(shared: Path, slug: str, *, project: str = "myproj",
                      state: str = "closed") -> Path:
    session = shared / project / slug
    session.mkdir(parents=True)
    (session / ".draft").mkdir()
    (session / "session.json").write_text(json.dumps({
        "schema_version": 2,
        "project": project,
        "session_id": slug,
        "title": slug,
        "state": state,
        "created_at": "2026-05-27T00:00:00+08:00",
        "closed_at": None,
        "close_reason": None,
        "participants": ["codex", "claude"],
    }))
    return session


def test_migrate_v2_to_v3_dry_run_lists_moves(monkeypatch, tmp_path, capsys):
    """D1 migrate: --dry-run reports would-move sessions without writes."""
    shared = _setup_shared(monkeypatch, tmp_path)
    old = _write_v2_session(shared, "20260527-old")
    rc = relay.cmd_migrate(type("A", (), {
        "migration": "v2-to-v3", "dry_run": True, "apply": False,
        "confirm_quiet": False,
    })())
    out = capsys.readouterr().out
    assert rc == 0
    assert f"{old} -> {shared / old.name}" in out
    assert old.exists()
    assert not (shared / old.name).exists()


def test_migrate_v2_to_v3_apply_moves_sessions_and_bumps_schema(monkeypatch, tmp_path, capsys):
    """D1 migrate: --apply --confirm-quiet moves v2 sessions up and writes schema_version 3."""
    shared = _setup_shared(monkeypatch, tmp_path)
    old = _write_v2_session(shared, "20260527-old")
    rc = relay.cmd_migrate(type("A", (), {
        "migration": "v2-to-v3", "dry_run": False, "apply": True,
        "confirm_quiet": True,
    })())
    new = shared / old.name
    assert rc == 0
    assert not old.exists()
    assert new.is_dir()
    data = json.loads((new / "session.json").read_text())
    assert data["schema_version"] == 3
    assert data["session_id"] == "20260527-old"
    assert data["project"] == "myproj"
    assert "moved:" in capsys.readouterr().out


def test_migrate_v2_to_v3_is_idempotent(monkeypatch, tmp_path):
    """D1 migrate: running --apply on an already-migrated tree is a no-op success."""
    shared = _setup_shared(monkeypatch, tmp_path)
    new = shared / "20260527-flat"
    new.mkdir()
    (new / "session.json").write_text(json.dumps({
        "schema_version": 3,
        "project": "myproj",
        "session_id": new.name,
        "title": "flat",
        "state": "closed",
    }))
    rc = relay.cmd_migrate(type("A", (), {
        "migration": "v2-to-v3", "dry_run": False, "apply": True,
        "confirm_quiet": True,
    })())
    assert rc == 0
    assert new.exists()


def test_migrate_v2_to_v3_refuses_name_collision(monkeypatch, tmp_path, capsys):
    """D1 migrate: refuses before writes when a session name would clash with an existing flat session."""
    shared = _setup_shared(monkeypatch, tmp_path)
    old = _write_v2_session(shared, "20260527-clash")
    flat = shared / old.name
    flat.mkdir()
    (flat / "session.json").write_text(json.dumps({
        "schema_version": 3,
        "project": "myproj",
        "session_id": flat.name,
        "title": "flat",
        "state": "closed",
    }))
    rc = relay.cmd_migrate(type("A", (), {
        "migration": "v2-to-v3", "dry_run": False, "apply": True,
        "confirm_quiet": True,
    })())
    assert rc == 2
    assert "name collision" in capsys.readouterr().err
    assert old.exists()
    assert flat.exists()
