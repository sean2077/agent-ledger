"""preflight + FS probes."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

import relay


def _isolated_env(monkeypatch, **kwargs):
    for k in list(os.environ):
        if k.startswith("RELAY_"):
            monkeypatch.delenv(k, raising=False)
    for k, v in kwargs.items():
        monkeypatch.setenv(k, v)


def test_probes_all_pass_on_tmpfs(tmp_path: Path):
    results = relay.run_probes(tmp_path, posix_target=0o700)
    statuses = {r.name: (r.status, r.detail) for r in results}
    # tmpdir is 0o700 in modern Python's TemporaryDirectory
    os.chmod(tmp_path, 0o700)
    results = relay.run_probes(tmp_path, posix_target=0o700)
    assert all(r.status == "pass" for r in results), {r.name: (r.status, r.detail) for r in results}


def test_probes_posix_warn_when_group_readable(tmp_path: Path):
    os.chmod(tmp_path, 0o750)
    results = relay.run_probes(tmp_path, posix_target=0o700)
    pm = next(r for r in results if r.name == "posix_mode")
    assert pm.status == "warn"


def test_probes_posix_fail_when_world_writable(tmp_path: Path):
    os.chmod(tmp_path, 0o777)
    results = relay.run_probes(tmp_path, posix_target=0o700)
    pm = next(r for r in results if r.name == "posix_mode")
    assert pm.status == "fail"


def test_preflight_mtime_warning_is_non_blocking(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    _isolated_env(monkeypatch,
        RELAY_ROLE="host", RELAY_AUTHOR="codex", RELAY_PEER="claude",
        RELAY_SHARED_ROOT=str(tmp_path),
        RELAY_REMOTE_SSH="x@y", RELAY_REMOTE_PATH="/r",
        RELAY_PROJECT="myproj",
    )
    os.chmod(tmp_path, 0o700)
    (tmp_path / "_relay").mkdir()
    (tmp_path / "_relay" / ".sentinel").touch()
    monkeypatch.setattr(relay, "run_probes", lambda root: [
        relay.ProbeResult("mtime_monotonic", "warn", "mtime unchanged (1); coarse resolution", 1),
        relay.ProbeResult("posix_mode", "pass", "mode 0700 matches target 0700", 0),
    ])
    args = type("A", (), {"json": True})()
    rc = relay.cmd_preflight(args)
    out = capsys.readouterr().out
    import json
    data = json.loads(out)
    mtime = next(c for c in data["checks"] if c["name"] == "fs.mtime_monotonic")
    assert mtime["status"] == "warn"
    assert data["exit_code"] == 0
    assert rc == 0


def test_preflight_other_warnings_still_return_warning_exit(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    _isolated_env(monkeypatch,
        RELAY_ROLE="host", RELAY_AUTHOR="codex", RELAY_PEER="claude",
        RELAY_SHARED_ROOT=str(tmp_path),
        RELAY_REMOTE_SSH="x@y", RELAY_REMOTE_PATH="/r",
        RELAY_PROJECT="myproj",
    )
    os.chmod(tmp_path, 0o700)
    (tmp_path / "_relay").mkdir()
    (tmp_path / "_relay" / ".sentinel").touch()
    monkeypatch.setattr(relay, "run_probes", lambda root: [
        relay.ProbeResult("posix_mode", "warn", "mode 0750 exceeds target 0700", 0),
    ])
    args = type("A", (), {"json": True})()
    rc = relay.cmd_preflight(args)
    out = capsys.readouterr().out
    import json
    data = json.loads(out)
    assert data["exit_code"] == 1
    assert rc == 1


def test_preflight_fails_when_no_env(monkeypatch, capsys):
    _isolated_env(monkeypatch)
    args = type("A", (), {"json": False})()
    rc = relay.cmd_preflight(args)
    assert rc == 2
    out = capsys.readouterr().out
    assert "env.RELAY_ROLE" in out
    assert "fail" in out


def test_preflight_json_mode(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    _isolated_env(monkeypatch,
        RELAY_ROLE="host", RELAY_AUTHOR="codex", RELAY_PEER="claude",
        RELAY_SHARED_ROOT=str(tmp_path),
        RELAY_REMOTE_SSH="x@y", RELAY_REMOTE_PATH="/r",
        RELAY_PROJECT="myproj",
    )
    os.chmod(tmp_path, 0o700)
    (tmp_path / "_relay").mkdir()
    (tmp_path / "_relay" / ".sentinel").touch()
    args = type("A", (), {"json": True})()
    rc = relay.cmd_preflight(args)
    out = capsys.readouterr().out
    import json
    data = json.loads(out)
    assert "checks" in data and "exit_code" in data
    assert data["exit_code"] == rc


def test_preflight_sentinel_missing(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    _isolated_env(monkeypatch,
        RELAY_ROLE="host", RELAY_AUTHOR="codex", RELAY_PEER="claude",
        RELAY_SHARED_ROOT=str(tmp_path),
        RELAY_REMOTE_SSH="x@y", RELAY_REMOTE_PATH="/r",
        RELAY_PROJECT="myproj",
    )
    os.chmod(tmp_path, 0o700)
    args = type("A", (), {"json": True})()
    rc = relay.cmd_preflight(args)
    out = capsys.readouterr().out
    import json
    data = json.loads(out)
    sentinel = next(c for c in data["checks"] if c["name"] == "mount.sentinel")
    assert sentinel["status"] == "fail"
    assert rc == 2


def test_preflight_shape_a_does_not_require_remote_vars(monkeypatch, capsys, tmp_path):
    """Host on a fuse-mounted project root (shape A) should not require RELAY_REMOTE_*."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path / "repo")], check=True)
    repo = tmp_path / "repo"
    monkeypatch.chdir(repo)
    shared = repo / ".shared"
    shared.mkdir(mode=0o700)
    (shared / "_relay").mkdir()
    (shared / "_relay" / ".sentinel").touch()
    _isolated_env(monkeypatch,
        RELAY_ROLE="host", RELAY_AUTHOR="codex", RELAY_PEER="claude",
        RELAY_SHARED_ROOT=str(shared),
        # Deliberately omitting RELAY_REMOTE_SSH / RELAY_REMOTE_PATH.
    )
    # Force fuse-mount detection to return True
    import relay as _relay
    monkeypatch.setattr(_relay, "_is_fuse_mount", lambda p: True)
    args = type("A", (), {"json": True})()
    rc = _relay.cmd_preflight(args)
    out = capsys.readouterr().out
    import json
    data = json.loads(out)
    env_keys = set(data["env"].keys())
    assert "RELAY_REMOTE_SSH" not in env_keys
    assert "RELAY_REMOTE_PATH" not in env_keys
    shape = next(c for c in data["checks"] if c["name"] == "project.shape")
    assert "shape A" in shape["detail"]
    # exit 0 expected (sentinel exists, env complete for shape A)
    assert rc == 0


def test_preflight_shape_b_still_requires_remote_vars(monkeypatch, capsys, tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path / "repo")], check=True)
    repo = tmp_path / "repo"
    monkeypatch.chdir(repo)
    shared = repo / ".shared"
    shared.mkdir(mode=0o700)
    (shared / "_relay").mkdir()
    (shared / "_relay" / ".sentinel").touch()
    _isolated_env(monkeypatch,
        RELAY_ROLE="host", RELAY_AUTHOR="codex", RELAY_PEER="claude",
        RELAY_SHARED_ROOT=str(shared),
    )
    import relay as _relay
    monkeypatch.setattr(_relay, "_is_fuse_mount", lambda p: False)
    args = type("A", (), {"json": True})()
    rc = _relay.cmd_preflight(args)
    out = capsys.readouterr().out
    import json
    data = json.loads(out)
    # RELAY_REMOTE_* required, missing → fail
    assert data["env"].get("RELAY_REMOTE_SSH") is False
    assert rc == 2


def test_preflight_project_consistency_mismatch(monkeypatch, capsys, tmp_path):
    # set up a git repo to derive project name
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path / "repo")], check=True)
    repo = tmp_path / "repo"
    monkeypatch.chdir(repo)
    shared = repo / ".shared"
    shared.mkdir(mode=0o700)
    (shared / "_relay").mkdir()
    (shared / "_relay" / ".sentinel").touch()
    _isolated_env(monkeypatch,
        RELAY_ROLE="host", RELAY_AUTHOR="codex", RELAY_PEER="claude",
        RELAY_SHARED_ROOT=str(shared),
        RELAY_REMOTE_SSH="x@y", RELAY_REMOTE_PATH="/r",
        RELAY_PROJECT="totally-wrong",  # mismatch with derived 'repo'
    )
    args = type("A", (), {"json": True})()
    rc = relay.cmd_preflight(args)
    out = capsys.readouterr().out
    import json
    data = json.loads(out)
    pc = next(c for c in data["checks"] if c["name"] == "project.consistency")
    assert pc["status"] == "fail"
    assert "totally-wrong" in pc["detail"]


# -----------------------------------------------------------------------------
# v3 bundle coverage for D1 + R3 preflight semantics.
# -----------------------------------------------------------------------------


def test_preflight_fails_shared_root_outside_git_toplevel(monkeypatch, capsys, tmp_path):
    """D1: preflight fails if RELAY_SHARED_ROOT is not inside the git toplevel."""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    monkeypatch.chdir(repo)
    shared = tmp_path / "outside-shared"
    shared.mkdir(mode=0o700)
    (shared / "_relay").mkdir()
    (shared / "_relay" / ".sentinel").touch()
    _isolated_env(monkeypatch,
        RELAY_ROLE="host", RELAY_AUTHOR="codex", RELAY_PEER="claude",
        RELAY_SHARED_ROOT=str(shared),
        RELAY_REMOTE_SSH="x@y", RELAY_REMOTE_PATH="/r",
    )
    rc = relay.cmd_preflight(type("A", (), {"json": True})())
    import json
    data = json.loads(capsys.readouterr().out)
    check = next(c for c in data["checks"] if c["name"] == "shared_root.location")
    assert check["status"] == "fail"
    assert rc == 2


def test_preflight_reports_old_v2_nested_layout_requires_migration(monkeypatch, capsys, tmp_path):
    """D1: preflight surfaces old .shared/<project>/<session>/ layout as needing migrate."""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    monkeypatch.chdir(repo)
    shared = repo / ".shared"
    shared.mkdir(mode=0o700)
    (shared / "_relay").mkdir()
    (shared / "_relay" / ".sentinel").touch()
    legacy = shared / "repo" / "20260527-old"
    legacy.mkdir(parents=True)
    (legacy / "session.json").write_text('{"schema_version": 2, "state": "closed"}')
    _isolated_env(monkeypatch,
        RELAY_ROLE="host", RELAY_AUTHOR="codex", RELAY_PEER="claude",
        RELAY_SHARED_ROOT=str(shared),
        RELAY_REMOTE_SSH="x@y", RELAY_REMOTE_PATH="/r",
    )
    rc = relay.cmd_preflight(type("A", (), {"json": True})())
    import json
    data = json.loads(capsys.readouterr().out)
    layout = next(c for c in data["checks"] if c["name"] == "layout.v2_nested")
    assert layout["status"] == "fail"
    assert "relay migrate v2-to-v3" in layout["detail"]
    assert rc == 2


def test_preflight_fails_when_marker_mismatches_active_session(monkeypatch, capsys, tmp_path):
    """R3.a: .shared/.active-session disagreeing with session_is_active is a fail (corruption)."""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    monkeypatch.chdir(repo)
    shared = repo / ".shared"
    shared.mkdir(mode=0o700)
    (shared / "_relay").mkdir()
    (shared / "_relay" / ".sentinel").touch()
    session = shared / "20260527-real"
    session.mkdir()
    (session / "session.json").write_text(
        '{"schema_version": 3, "project": "repo", "session_id": "20260527-real", '
        '"state": "active"}'
    )
    (shared / ".active-session").write_text("20260527-wrong\n")
    _isolated_env(monkeypatch,
        RELAY_ROLE="host", RELAY_AUTHOR="codex", RELAY_PEER="claude",
        RELAY_SHARED_ROOT=str(shared),
        RELAY_REMOTE_SSH="x@y", RELAY_REMOTE_PATH="/r",
    )
    rc = relay.cmd_preflight(type("A", (), {"json": True})())
    import json
    data = json.loads(capsys.readouterr().out)
    marker = next(c for c in data["checks"] if c["name"] == "session.active_marker")
    assert marker["status"] == "fail"
    assert "20260527-wrong" in marker["detail"]
    assert rc == 2
