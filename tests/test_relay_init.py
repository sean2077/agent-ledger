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
    base = {"same_host": False, "author": None, "peer": None, "sync": None}
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


def test_init_hints_lead_with_same_host_and_flag_alternatives(monkeypatch, tmp_path, capsys):
    """With no identity auto-detected and no .envrc, the hint covers same-host
    (zero-config) and the rsync owner path."""
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(repo / ".shared"))
    rc = relay.cmd_init(_args())
    assert rc == 0
    out = capsys.readouterr().out
    assert "--same-host" in out
    assert "--sync rsync" in out
    assert "auto-detect" in out  # v0.14: explains no RELAY_AUTHOR needed
    # The retired --role flag must not reappear in hints.
    assert "--role" not in out


def test_init_same_host_needs_no_env_and_writes_no_file(monkeypatch, tmp_path, capsys):
    """v0.14: --same-host for claude+codex is zero-config — it must NOT write a
    per-host .envrc and must tell the user author auto-detects."""
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(repo / ".shared"))
    rc = relay.cmd_init(_args(same_host=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "auto-detect" in out
    assert "no identity env" in out
    # No per-host .envrc is created for same-host anymore.
    assert not (repo / f".envrc.{relay._hostname_short()}").exists()
    # And it must not instruct any RELAY_AUTHOR export.
    assert "RELAY_AUTHOR" not in out


def test_init_hint_lists_same_host_first(monkeypatch, tmp_path, capsys):
    """Hint should lead with same-host (the v0.5+ recommendation)."""
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(repo / ".shared"))
    rc = relay.cmd_init(_args())
    assert rc == 0
    out = capsys.readouterr().out
    # same-host leads the hint; the rsync owner path is the secondary one.
    # The retired --role flag must not appear.
    assert "--same-host" in out
    assert "--role" not in out
    assert "--sync rsync" in out


def test_init_suppresses_envrc_nag_when_identity_env_set(monkeypatch, tmp_path, capsys):
    """If identity env is set, no nag about missing .envrc.<hostname>.

    RELAY_SYNC now defaults to none, so it is no longer required just to
    suppress the first-run envrc hint.
    """
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch,
                  RELAY_SHARED_ROOT=str(repo / ".shared"),
                  RELAY_AUTHOR="codex", RELAY_PEER="claude")
    hostname = relay._hostname_short()
    assert not (repo / f".envrc.{hostname}").exists()
    rc = relay.cmd_init(_args())
    assert rc == 0
    out = capsys.readouterr().out
    assert "--same-host" not in out
    assert "not found" not in out


# Explicit-flags init tests.

def test_init_author_sync_renders_envrc(monkeypatch, tmp_path, capsys):
    """`relay init --author X --sync none` writes a working envrc that pins the
    custom author. v0.14: RELAY_PEER is NOT rendered (runtime peer is derived
    from the pair)."""
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(repo / ".shared"))
    rc = relay.cmd_init(_args(author="codex", peer="claude", sync="none"))
    assert rc == 0
    target = repo / f".envrc.{relay._hostname_short()}"
    assert target.is_file()
    body = target.read_text()
    assert "RELAY_AUTHOR=codex" in body
    assert "RELAY_PEER" not in body  # peer is derived from the pair, never env
    assert "RELAY_SYNC=none" in body
    lines = [line.strip() for line in body.splitlines()]
    assert '# export RELAY_SHARED_ROOT="$PWD/.shared"' in lines
    assert 'export RELAY_SHARED_ROOT="$PWD/.shared"' not in lines
    # No legacy RELAY_ROLE.
    assert "RELAY_ROLE=" not in body
    # Dispatcher still gets installed.
    assert (repo / ".envrc").is_file()


def test_init_author_peer_sync_rsync_advises_remote_vars(monkeypatch, tmp_path, capsys):
    """--sync=rsync warns about missing RELAY_REMOTE_* (but doesn't fail)."""
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(repo / ".shared"))
    rc = relay.cmd_init(_args(author="codex", peer="claude", sync="rsync"))
    assert rc == 0
    err = capsys.readouterr().err
    assert "--sync=rsync" in err
    assert "RELAY_REMOTE_SSH" in err


def test_init_same_host_and_author_are_mutually_exclusive(monkeypatch, tmp_path, capsys):
    """Pick one: --same-host OR --author/--peer/--sync."""
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(repo / ".shared"))
    rc = relay.cmd_init(_args(same_host=True, author="codex", peer="claude", sync="none"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "mutually exclusive" in err


def test_init_author_without_peer_succeeds(monkeypatch, tmp_path, capsys):
    """v0.14: --peer is no longer required (runtime peer is derived from the
    pair). --author alone renders a valid envrc pinning the custom author."""
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(repo / ".shared"))
    rc = relay.cmd_init(_args(author="gpt55"))
    assert rc == 0
    body = (repo / f".envrc.{relay._hostname_short()}").read_text()
    assert "RELAY_AUTHOR=gpt55" in body
    assert "RELAY_PEER" not in body


@pytest.mark.parametrize("bad", [
    "has space",
    "semi;colon",
    "back`tick",
    "dollar$ign",
    "new\nline",
    "Upper",
    "-leadinghyphen",
    "",
    "a" * 49,  # too long
])
def test_init_rejects_unsafe_author_slug(monkeypatch, tmp_path, capsys, bad):
    """Finding 8: --author values land in a sourceable shell file. Reject
    anything that's not a clean slug to prevent envrc injection AND to
    keep author identity consistent with artifact filename grammar.
    """
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(repo / ".shared"))
    rc = relay.cmd_init(_args(author=bad, peer="codex", sync="none"))
    assert rc == 2, f"unsafe author {bad!r} should be rejected"
    err = capsys.readouterr().err
    assert "--author" in err
    assert not (repo / f".envrc.{relay._hostname_short()}").exists()


@pytest.mark.parametrize("bad", [
    "has space",
    "semi;colon",
    "back`tick",
])
def test_init_rejects_unsafe_peer_slug(monkeypatch, tmp_path, capsys, bad):
    """Finding 8: --peer gets the same treatment as --author."""
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(repo / ".shared"))
    rc = relay.cmd_init(_args(author="codex", peer=bad, sync="none"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "--peer" in err


def test_init_rejects_author_equals_peer(monkeypatch, tmp_path, capsys):
    """Finding 8 corollary: A==B is a clear configuration mistake."""
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(repo / ".shared"))
    rc = relay.cmd_init(_args(author="codex", peer="codex", sync="none"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "cannot be the same" in err


@pytest.mark.parametrize("ok", ["claude", "codex", "gpt55", "agent-2", "x"])
def test_init_accepts_valid_slug_identities(monkeypatch, tmp_path, capsys, ok):
    """Finding 8 positive: known-good slugs still work — gpt55, agent-2, etc."""
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(repo / ".shared"))
    # use a distinct peer so author!=peer
    peer = "other-side" if ok != "other-side" else "claude"
    rc = relay.cmd_init(_args(author=ok, peer=peer, sync="none"))
    assert rc == 0
    body = (repo / f".envrc.{relay._hostname_short()}").read_text()
    assert f"RELAY_AUTHOR={ok}" in body
