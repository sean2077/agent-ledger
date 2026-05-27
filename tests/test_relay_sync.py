"""sync — wraps rsync; host-only; fuse-root abort; default vs strict; dry-run; delete."""

import os
import shutil
import subprocess
from pathlib import Path
from unittest import mock

import pytest

import relay


def _setup(monkeypatch, tmp_path, *, role="host"):
    repo = tmp_path / "myproj"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    monkeypatch.chdir(repo)
    for k in list(os.environ):
        if k.startswith("RELAY_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RELAY_ROLE", role)
    monkeypatch.setenv("RELAY_AUTHOR", "codex")
    monkeypatch.setenv("RELAY_PEER", "claude")
    monkeypatch.setenv("RELAY_SHARED_ROOT", str(repo / ".shared"))
    if role == "host":
        monkeypatch.setenv("RELAY_REMOTE_SSH", "user@remote")
        monkeypatch.setenv("RELAY_REMOTE_PATH", "/remote/path")
    return repo


def _args(direction="push", **kw):
    base = {"direction": direction, "dry_run": False, "strict_gitignore": False, "delete": False}
    base.update(kw)
    return type("A", (), base)()


def test_sync_refuses_on_remote_role(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, role="remote")
    rc = relay.cmd_sync(_args())
    assert rc == 2
    assert "must run on host" in capsys.readouterr().err


def test_sync_refuses_when_no_remote_env(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    monkeypatch.delenv("RELAY_REMOTE_SSH", raising=False)
    rc = relay.cmd_sync(_args())
    assert rc == 2
    assert "RELAY_REMOTE" in capsys.readouterr().err


def test_sync_refuses_when_project_root_is_fuse(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)

    def fake_is_fuse(p):
        return True

    monkeypatch.setattr(relay, "_is_fuse_mount", fake_is_fuse)
    rc = relay.cmd_sync(_args())
    assert rc == 2
    assert "fuse" in capsys.readouterr().err.lower()


def _mock_rsync(monkeypatch):
    """Replace subprocess.run only for rsync calls; pass everything else through."""
    captured: list[list[str]] = []
    real_run = subprocess.run

    def selective(cmd, *args, **kw):
        if isinstance(cmd, list) and cmd and cmd[0] == "rsync":
            captured.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0)
        return real_run(cmd, *args, **kw)

    monkeypatch.setattr(subprocess, "run", selective)
    return captured


def test_sync_default_invokes_rsync_with_filter(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(relay, "_is_fuse_mount", lambda p: False)
    captured = _mock_rsync(monkeypatch)
    rc = relay.cmd_sync(_args(dry_run=True))
    assert rc == 0
    cmd = captured[0]
    assert "rsync" in cmd[0]
    assert "--filter=:- .gitignore" in cmd
    assert "--exclude=.git" in cmd
    assert "--exclude=.shared" in cmd
    assert "-n" in cmd
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "!path" in err


def test_sync_default_banner_escalates_with_negation_rules(monkeypatch, tmp_path, capsys):
    repo = _setup(monkeypatch, tmp_path)
    (repo / ".gitignore").write_text("*.log\n!keep.log\n")
    monkeypatch.setattr(relay, "_is_fuse_mount", lambda p: False)
    _mock_rsync(monkeypatch)
    relay.cmd_sync(_args(dry_run=True))
    err = capsys.readouterr().err
    assert "STRONGLY recommend" in err


def test_sync_strict_uses_git_ls_files(monkeypatch, tmp_path, capsys):
    repo = _setup(monkeypatch, tmp_path)
    (repo / "a.py").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "a.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "-q", "-m", "i"], check=True)
    monkeypatch.setattr(relay, "_is_fuse_mount", lambda p: False)
    captured = _mock_rsync(monkeypatch)
    rc = relay.cmd_sync(_args(strict_gitignore=True, dry_run=True))
    assert rc == 0
    cmd = captured[0]
    assert "--files-from" in cmd
    assert "--from0" in cmd
    assert "--filter=:- .gitignore" not in cmd


def test_sync_delete_off_by_default(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(relay, "_is_fuse_mount", lambda p: False)
    captured = _mock_rsync(monkeypatch)
    relay.cmd_sync(_args(dry_run=True))
    assert "--delete" not in captured[0]


def test_sync_delete_explicit_passes_through(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(relay, "_is_fuse_mount", lambda p: False)
    captured = _mock_rsync(monkeypatch)
    relay.cmd_sync(_args(dry_run=True, delete=True))
    assert "--delete" in captured[0]


def test_sync_push_pull_directions(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(relay, "_is_fuse_mount", lambda p: False)
    captured = _mock_rsync(monkeypatch)
    relay.cmd_sync(_args(direction="push", dry_run=True))
    relay.cmd_sync(_args(direction="pull", dry_run=True))
    push_cmd = captured[0]
    pull_cmd = captured[1]
    # push: local src first, remote dst second
    assert push_cmd[-2].startswith(str(tmp_path / "myproj"))
    assert "user@remote" in push_cmd[-1]
    # pull: remote src first, local dst second
    assert "user@remote" in pull_cmd[-2]
    assert pull_cmd[-1].startswith(str(tmp_path / "myproj"))
