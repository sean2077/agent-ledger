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
    if "RELAY_AUTHOR" in kwargs and "RELAY_AGENT_SESSION_ID" not in kwargs:
        kwargs["RELAY_AGENT_SESSION_ID"] = "test-agent-window"
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
        RELAY_SYNC="rsync", RELAY_AUTHOR="codex",
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
        RELAY_SYNC="rsync", RELAY_AUTHOR="codex",
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
    # Author is auto-detected; with no platform signal and no
    # RELAY_AUTHOR, identity.author fails. RELAY_SYNC defaults to none.
    assert "identity.author" in out
    assert "sync=none (source: default)" in out
    assert "fail" in out


def test_preflight_json_mode(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    _isolated_env(monkeypatch,
        RELAY_SYNC="rsync", RELAY_AUTHOR="codex",
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
        RELAY_SYNC="rsync", RELAY_AUTHOR="codex",
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


def test_preflight_defaults_shared_root_inside_git(monkeypatch, capsys, tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    monkeypatch.chdir(repo)
    shared = repo / ".shared"
    shared.mkdir(mode=0o700)
    (shared / "_relay").mkdir()
    (shared / "_relay" / ".sentinel").touch()
    _isolated_env(monkeypatch,
        RELAY_SYNC="none", RELAY_AUTHOR="codex",
    )
    args = type("A", (), {"json": True})()
    rc = relay.cmd_preflight(args)
    out = capsys.readouterr().out
    import json
    data = json.loads(out)
    shared_check = next(c for c in data["checks"] if c["name"] == "env.RELAY_SHARED_ROOT")
    assert shared_check["status"] == "pass"
    assert str(shared) in shared_check["detail"]
    assert data["env"]["RELAY_SHARED_ROOT"] is True
    assert rc == 0


def test_preflight_shape_a_does_not_require_remote_vars(monkeypatch, capsys, tmp_path):
    """Fuse-mounted project root (shape A) without explicit RELAY_SYNC should
    infer SYNC=none and not require RELAY_REMOTE_*."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path / "repo")], check=True)
    repo = tmp_path / "repo"
    monkeypatch.chdir(repo)
    shared = repo / ".shared"
    shared.mkdir(mode=0o700)
    (shared / "_relay").mkdir()
    (shared / "_relay" / ".sentinel").touch()
    _isolated_env(monkeypatch,
        RELAY_AUTHOR="codex",
        RELAY_SHARED_ROOT=str(shared),
        # Deliberately omitting RELAY_SYNC and RELAY_REMOTE_*.
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
        RELAY_SYNC="rsync", RELAY_AUTHOR="codex",
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
        RELAY_SYNC="rsync", RELAY_AUTHOR="codex",
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
        RELAY_SYNC="rsync", RELAY_AUTHOR="codex",
        RELAY_SHARED_ROOT=str(shared),
        RELAY_REMOTE_SSH="x@y", RELAY_REMOTE_PATH="/r",
    )
    rc = relay.cmd_preflight(type("A", (), {"json": True})())
    import json
    data = json.loads(capsys.readouterr().out)
    check = next(c for c in data["checks"] if c["name"] == "shared_root.location")
    assert check["status"] == "fail"
    assert rc == 2


def _bootstrap_repo_with_shared(tmp_path: Path) -> Path:
    """Helper: init a git repo with a .shared/_relay/.sentinel ready for preflight."""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    shared = repo / ".shared"
    shared.mkdir(mode=0o700)
    (shared / "_relay").mkdir()
    (shared / "_relay" / ".sentinel").touch()
    return repo


def test_preflight_resolves_sync_from_explicit_env(monkeypatch, capsys, tmp_path):
    """RELAY_SYNC=rsync drives sync resolution."""
    repo = _bootstrap_repo_with_shared(tmp_path)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch,
        RELAY_AUTHOR="codex",
        RELAY_SHARED_ROOT=str(repo / ".shared"),
        RELAY_SYNC="rsync",
        RELAY_REMOTE_SSH="x@y", RELAY_REMOTE_PATH="/r",
    )
    monkeypatch.setattr(relay, "_is_fuse_mount", lambda p: False)
    rc = relay.cmd_preflight(type("A", (), {"json": True})())
    import json
    data = json.loads(capsys.readouterr().out)
    sync_check = next(c for c in data["checks"] if c["name"] == "env.RELAY_SYNC")
    assert sync_check["status"] == "pass"
    assert "source: env" in sync_check["detail"]
    assert rc == 0


def test_preflight_unset_sync_defaults_to_none_in_shape_a(monkeypatch, capsys, tmp_path):
    """Unset RELAY_SYNC defaults to none, including shape A."""
    repo = _bootstrap_repo_with_shared(tmp_path)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch,
        RELAY_AUTHOR="codex",
        RELAY_SHARED_ROOT=str(repo / ".shared"),
        # Deliberately omit RELAY_SYNC.
    )
    monkeypatch.setattr(relay, "_is_fuse_mount", lambda p: True)
    rc = relay.cmd_preflight(type("A", (), {"json": True})())
    import json
    data = json.loads(capsys.readouterr().out)
    sync_check = next(c for c in data["checks"] if c["name"] == "env.RELAY_SYNC")
    assert sync_check["status"] == "pass"
    assert "source: default" in sync_check["detail"]
    assert rc == 0


def test_preflight_unset_sync_defaults_to_none_in_shape_b(monkeypatch, capsys, tmp_path):
    """Unset RELAY_SYNC defaults to none in normal two-copy shape B."""
    repo = _bootstrap_repo_with_shared(tmp_path)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch,
        RELAY_AUTHOR="codex",
        RELAY_SHARED_ROOT=str(repo / ".shared"),
    )
    monkeypatch.setattr(relay, "_is_fuse_mount", lambda p: False)
    rc = relay.cmd_preflight(type("A", (), {"json": True})())
    import json
    data = json.loads(capsys.readouterr().out)
    sync_check = next(c for c in data["checks"] if c["name"] == "env.RELAY_SYNC")
    assert sync_check["status"] == "pass"
    assert "source: default" in sync_check["detail"]
    assert rc == 0


def test_preflight_ignores_leftover_relay_role(monkeypatch, capsys, tmp_path):
    """RELAY_ROLE is fully retired: a leftover value is an inert unknown env
    var, not a special migration check. With no RELAY_SYNC, preflight uses
    the normal RELAY_SYNC=none default and emits no env.RELAY_ROLE.* check."""
    repo = _bootstrap_repo_with_shared(tmp_path)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch,
        RELAY_ROLE="host",  # leftover from a pre-cleanup envrc — must be ignored
        RELAY_AUTHOR="codex",
        RELAY_SHARED_ROOT=str(repo / ".shared"),
    )
    monkeypatch.setattr(relay, "_is_fuse_mount", lambda p: False)
    rc = relay.cmd_preflight(type("A", (), {"json": True})())
    import json
    data = json.loads(capsys.readouterr().out)
    sync_check = next(c for c in data["checks"] if c["name"] == "env.RELAY_SYNC")
    assert sync_check["status"] == "pass"
    assert "source: default" in sync_check["detail"]
    # No RELAY_ROLE check survives anywhere.
    assert not any(c["name"].startswith("env.RELAY_ROLE") for c in data["checks"])
    assert rc == 0


def test_preflight_sync_rsync_with_shape_a_is_contradiction(monkeypatch, capsys, tmp_path):
    """D1: RELAY_SYNC=rsync + shape A is a contradiction and must fail."""
    repo = _bootstrap_repo_with_shared(tmp_path)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch,
        RELAY_AUTHOR="codex",
        RELAY_SHARED_ROOT=str(repo / ".shared"),
        RELAY_SYNC="rsync",
        RELAY_REMOTE_SSH="x@y", RELAY_REMOTE_PATH="/r",
    )
    monkeypatch.setattr(relay, "_is_fuse_mount", lambda p: True)
    rc = relay.cmd_preflight(type("A", (), {"json": True})())
    import json
    data = json.loads(capsys.readouterr().out)
    shape = next(c for c in data["checks"] if c["name"] == "project.shape")
    assert shape["status"] == "fail"
    assert "RELAY_SYNC=rsync" in shape["detail"]
    assert rc == 2


def test_preflight_invalid_sync_value_fails(monkeypatch, capsys, tmp_path):
    repo = _bootstrap_repo_with_shared(tmp_path)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch,
        RELAY_AUTHOR="codex",
        RELAY_SHARED_ROOT=str(repo / ".shared"),
        RELAY_SYNC="ftp",  # nonsense
    )
    monkeypatch.setattr(relay, "_is_fuse_mount", lambda p: False)
    rc = relay.cmd_preflight(type("A", (), {"json": True})())
    import json
    data = json.loads(capsys.readouterr().out)
    sync_check = next(c for c in data["checks"] if c["name"] == "env.RELAY_SYNC")
    assert sync_check["status"] == "fail"
    assert "invalid" in sync_check["detail"]
    assert rc == 2


def test_preflight_sync_rsync_requires_remote_vars(monkeypatch, capsys, tmp_path):
    """RELAY_SYNC=rsync but missing REMOTE_SSH/PATH must fail."""
    repo = _bootstrap_repo_with_shared(tmp_path)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch,
        RELAY_AUTHOR="codex",
        RELAY_SHARED_ROOT=str(repo / ".shared"),
        RELAY_SYNC="rsync",
    )
    monkeypatch.setattr(relay, "_is_fuse_mount", lambda p: False)
    rc = relay.cmd_preflight(type("A", (), {"json": True})())
    import json
    data = json.loads(capsys.readouterr().out)
    assert data["env"].get("RELAY_REMOTE_SSH") is False
    assert rc == 2


def test_preflight_project_consistency_passes_when_canonical_form_matches(
    monkeypatch, capsys, tmp_path,
):
    """Finding 7: declared RELAY_PROJECT in sanitized form must match a
    git toplevel basename whose canonical form is the same — even if the
    raw basename has dots/underscores. Pre-fix this raised a fail because
    the comparison was literal."""
    repo = tmp_path / "actibot_ego.jy"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    monkeypatch.chdir(repo)
    shared = repo / ".shared"
    shared.mkdir(mode=0o700)
    (shared / "_relay").mkdir()
    (shared / "_relay" / ".sentinel").touch()
    _isolated_env(monkeypatch,
        RELAY_SYNC="rsync", RELAY_AUTHOR="codex",
        RELAY_SHARED_ROOT=str(shared),
        RELAY_REMOTE_SSH="x@y", RELAY_REMOTE_PATH="/r",
        RELAY_PROJECT="actibot-ego-jy",  # sanitized form of toplevel basename
    )
    rc = relay.cmd_preflight(type("A", (), {"json": True})())
    import json
    data = json.loads(capsys.readouterr().out)
    pc = next(c for c in data["checks"] if c["name"] == "project.consistency")
    assert pc["status"] == "pass", f"unexpected: {pc}"
    assert "actibot-ego-jy" in pc["detail"]


def test_preflight_project_consistency_still_fails_on_real_mismatch(
    monkeypatch, capsys, tmp_path,
):
    """Finding 7 negative: sanitization must not let unrelated names pass."""
    repo = tmp_path / "alpha"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    monkeypatch.chdir(repo)
    shared = repo / ".shared"
    shared.mkdir(mode=0o700)
    (shared / "_relay").mkdir()
    (shared / "_relay" / ".sentinel").touch()
    _isolated_env(monkeypatch,
        RELAY_SYNC="rsync", RELAY_AUTHOR="codex",
        RELAY_SHARED_ROOT=str(shared),
        RELAY_REMOTE_SSH="x@y", RELAY_REMOTE_PATH="/r",
        RELAY_PROJECT="beta",
    )
    rc = relay.cmd_preflight(type("A", (), {"json": True})())
    import json
    data = json.loads(capsys.readouterr().out)
    pc = next(c for c in data["checks"] if c["name"] == "project.consistency")
    assert pc["status"] == "fail"
    assert "alpha" in pc["detail"]
    assert "beta" in pc["detail"]


def test_preflight_warns_when_unbound_with_multiple_active(
    monkeypatch, capsys, tmp_path,
):
    """An unbound instance facing >1 active pairs should warn (not fail) so the
    skill can resolve via `relay pair ensure` / `relay pair join`."""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    monkeypatch.chdir(repo)
    shared = repo / ".shared"
    shared.mkdir(mode=0o700)
    (shared / "_relay").mkdir()
    (shared / "_relay" / ".sentinel").touch()
    # Plant two active pairs.
    for sid in ("20260529-a", "20260529-b"):
        s = shared / sid
        s.mkdir()
        (s / "session.json").write_text(
            f'{{"schema_version": 3, "project": "repo", "session_id": "{sid}", '
            f'"state": "active"}}'
        )
    # NO .active-session marker — caller is in parallel mode.
    _isolated_env(monkeypatch,
        RELAY_SYNC="rsync", RELAY_AUTHOR="codex",
        RELAY_SHARED_ROOT=str(shared),
        RELAY_REMOTE_SSH="x@y", RELAY_REMOTE_PATH="/r",
    )
    rc = relay.cmd_preflight(type("A", (), {"json": True})())
    import json
    data = json.loads(capsys.readouterr().out)
    chk = next(c for c in data["checks"] if c["name"] == "pair.binding")
    assert chk["status"] == "warn"
    assert "20260529-a" in chk["detail"]
    assert "20260529-b" in chk["detail"]
    assert "pair" in chk["detail"]
    # warn -> exit 1, not 2; the skill resolves via pair ensure/join
    assert rc == 1


def test_preflight_passes_when_bound_among_many_actives(
    monkeypatch, capsys, tmp_path,
):
    """An instance bound to one of N active pairs is the legitimate parallel
    state — preflight must pass."""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    monkeypatch.chdir(repo)
    shared = repo / ".shared"
    shared.mkdir(mode=0o700)
    (shared / "_relay").mkdir()
    (shared / "_relay" / ".sentinel").touch()
    for sid in ("20260529-a", "20260529-b"):
        s = shared / sid
        s.mkdir()
        (s / "session.json").write_text(
            f'{{"schema_version": 3, "project": "repo", "session_id": "{sid}", '
            f'"state": "active"}}'
        )
    _isolated_env(monkeypatch,
        RELAY_SYNC="rsync", RELAY_AUTHOR="codex",
        RELAY_SHARED_ROOT=str(shared),
        RELAY_REMOTE_SSH="x@y", RELAY_REMOTE_PATH="/r",
    )
    assert relay.join_pair(relay.load_env(), shared, "20260529-a") == 0
    rc = relay.cmd_preflight(type("A", (), {"json": True})())
    import json
    data = json.loads(capsys.readouterr().out)
    chk = next(c for c in data["checks"] if c["name"] == "pair.binding")
    assert chk["status"] == "pass"
    assert "20260529-a" in chk["detail"]
    assert rc == 0


def test_preflight_warns_when_binding_points_at_inactive_pair(monkeypatch, capsys, tmp_path):
    """A binding pointing at a missing/inactive pair is recoverable (self-healed
    by `pair ensure`), so it warns — not the old marker-mismatch hard fail."""
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
    _isolated_env(monkeypatch,
        RELAY_SYNC="rsync", RELAY_AUTHOR="codex",
        RELAY_SHARED_ROOT=str(shared),
        RELAY_REMOTE_SSH="x@y", RELAY_REMOTE_PATH="/r",
    )
    env = relay.load_env()
    relay.write_binding(shared, {
        "schema_version": 1, "instance_id": env.instance_id,
        "author": env.author, "agent_session_id": env.agent_session_id,
        "pair_slug": "20260527-gone", "bound_at": relay.now_iso(),
        "last_seen": relay.now_iso(),
    })
    rc = relay.cmd_preflight(type("A", (), {"json": True})())
    import json
    data = json.loads(capsys.readouterr().out)
    chk = next(c for c in data["checks"] if c["name"] == "pair.binding")
    assert chk["status"] == "warn"
    assert "20260527-gone" in chk["detail"]
    assert rc == 1
