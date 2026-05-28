"""relay init — idempotent first-run filesystem setup."""

import os
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


def _args(**kw):
    base = {"role": None}
    base.update(kw)
    return type("A", (), base)()


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)


def test_init_creates_full_layout_from_scratch(monkeypatch, tmp_path, capsys):
    shared = tmp_path / ".shared"
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(shared))
    rc = relay.cmd_init(_args())
    assert rc == 0
    assert shared.is_dir()
    assert (shared / "_relay").is_dir()
    assert (shared / "_relay" / ".sentinel").is_file()
    out = capsys.readouterr().out
    assert "created" in out
    assert str(shared) in out


def test_init_is_idempotent(monkeypatch, tmp_path, capsys):
    shared = tmp_path / ".shared"
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(shared))
    assert relay.cmd_init(_args()) == 0
    sentinel_before = (shared / "_relay" / ".sentinel").read_bytes()
    capsys.readouterr()  # drain
    rc = relay.cmd_init(_args())
    assert rc == 0
    out = capsys.readouterr().out
    assert "already initialized" in out
    assert (shared / "_relay" / ".sentinel").read_bytes() == sentinel_before


def test_init_creates_dirs_with_0700_mode(monkeypatch, tmp_path):
    shared = tmp_path / ".shared"
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(shared))
    assert relay.cmd_init(_args()) == 0
    assert (shared.stat().st_mode & 0o777) == 0o700
    assert ((shared / "_relay").stat().st_mode & 0o777) == 0o700


def test_init_fills_in_missing_sentinel_only(monkeypatch, tmp_path, capsys):
    """If RELAY_SHARED_ROOT exists but sentinel is missing, init only adds the sentinel."""
    shared = tmp_path / ".shared"
    shared.mkdir(mode=0o700)
    (shared / "_relay").mkdir(mode=0o700)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(shared))
    rc = relay.cmd_init(_args())
    assert rc == 0
    assert (shared / "_relay" / ".sentinel").is_file()
    out = capsys.readouterr().out
    assert ".sentinel" in out


def test_init_defaults_shared_root_to_project_local(monkeypatch, tmp_path, capsys):
    """Without RELAY_SHARED_ROOT, init uses <git_toplevel>/.shared when inside a repo."""
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch)
    rc = relay.cmd_init(_args())
    assert rc == 0
    assert (repo / ".shared" / "_relay" / ".sentinel").is_file()
    err = capsys.readouterr().err
    assert "defaulting to" in err
    assert str(repo / ".shared") in err


def test_init_fails_without_shared_root_outside_git(monkeypatch, tmp_path, capsys):
    """Without RELAY_SHARED_ROOT AND outside a git repo, init refuses to guess."""
    monkeypatch.chdir(tmp_path)
    _isolated_env(monkeypatch)
    rc = relay.cmd_init(_args())
    assert rc == 2
    err = capsys.readouterr().err
    assert "git repo" in err
    assert "RELAY_SHARED_ROOT" in err


def test_init_role_copies_envrc_template(monkeypatch, tmp_path, capsys):
    """--role host writes .envrc.<hostname> AND the dispatcher .envrc."""
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(repo / ".shared"))
    rc = relay.cmd_init(_args(role="host"))
    assert rc == 0
    hostname = relay._hostname_short()
    target = repo / f".envrc.{hostname}"
    assert target.is_file()
    body = target.read_text()
    assert "RELAY_ROLE=host" in body
    assert "RELAY_AUTHOR=codex" in body
    # Dispatcher .envrc must also be installed.
    dispatcher = repo / ".envrc"
    assert dispatcher.is_file()
    assert "LOCAL_ENVRC=" in dispatcher.read_text()
    out = capsys.readouterr().out
    assert "envrc.host.example" in out
    assert "envrc.dispatcher.example" in out


def test_init_role_dispatcher_idempotent(monkeypatch, tmp_path, capsys):
    """If .envrc dispatcher already exists, --role leaves it alone."""
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(repo / ".shared"))
    dispatcher = repo / ".envrc"
    dispatcher.write_text("# user dispatcher; do not clobber\n")
    rc = relay.cmd_init(_args(role="host"))
    assert rc == 0
    assert dispatcher.read_text() == "# user dispatcher; do not clobber\n"
    out = capsys.readouterr().out
    # Dispatcher line should say "already present", not "created".
    assert ".envrc already present" in out or f"{dispatcher} already present" in out


def test_init_role_is_idempotent_when_envrc_exists(monkeypatch, tmp_path, capsys):
    """If .envrc.<hostname> already exists, --role does not overwrite."""
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(repo / ".shared"))
    hostname = relay._hostname_short()
    target = repo / f".envrc.{hostname}"
    target.write_text("# user-edited; do not clobber\n")
    rc = relay.cmd_init(_args(role="remote"))
    assert rc == 0
    assert target.read_text() == "# user-edited; do not clobber\n"
    out = capsys.readouterr().out
    assert "already present" in out


def test_init_hints_when_envrc_missing_and_no_role(monkeypatch, tmp_path, capsys):
    """In a git repo, missing .envrc.<hostname> and no --role => print copy hint."""
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(repo / ".shared"))
    rc = relay.cmd_init(_args())
    assert rc == 0
    out = capsys.readouterr().out
    assert "--role host" in out
    assert "--role remote" in out


def test_init_role_emits_direnv_aware_next_step(monkeypatch, tmp_path, capsys):
    """After copying envrc, output names direnv or source depending on availability."""
    import shutil as _shutil
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(repo / ".shared"))
    monkeypatch.setattr(_shutil, "which", lambda name: "/fake/direnv" if name == "direnv" else None)
    # patch the module-level shutil used by relay
    monkeypatch.setattr(relay.shutil, "which", lambda name: "/fake/direnv" if name == "direnv" else None)
    rc = relay.cmd_init(_args(role="host"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "direnv allow" in out
    assert "source .envrc" not in out

    # remove .envrc.<hostname> for second run; pretend direnv missing
    hostname = relay._hostname_short()
    (repo / f".envrc.{hostname}").unlink()
    monkeypatch.setattr(relay.shutil, "which", lambda name: None)
    capsys.readouterr()
    rc = relay.cmd_init(_args(role="host"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "source .envrc" in out
    assert "install direnv" in out


def test_init_suppresses_envrc_nag_when_role_env_set(monkeypatch, tmp_path, capsys):
    """Q1: if RELAY_ROLE is set, no nag about missing .envrc.<hostname>.
    The user has already sourced an envrc somehow (or set vars by hand);
    further reminders to copy the template would just be noise."""
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch,
                  RELAY_SHARED_ROOT=str(repo / ".shared"),
                  RELAY_ROLE="host")
    hostname = relay._hostname_short()
    assert not (repo / f".envrc.{hostname}").exists()  # precondition
    rc = relay.cmd_init(_args())
    assert rc == 0
    out = capsys.readouterr().out
    assert "--role host" not in out
    assert "--role remote" not in out
    assert "not found" not in out
