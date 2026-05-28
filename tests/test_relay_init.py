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
    base = {"role": None, "author": None, "peer": None, "sync": None}
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


def test_init_role_host_rejected_with_migration_hint(monkeypatch, tmp_path, capsys):
    """v0.6: --role host was removed. Init must refuse with a migration line."""
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(repo / ".shared"))
    rc = relay.cmd_init(_args(role="host"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "removed in v0.6" in err
    # Suggest the new explicit-flags form with the right sync value.
    assert "--sync rsync" in err


def test_init_role_remote_rejected_with_migration_hint(monkeypatch, tmp_path, capsys):
    """v0.6: --role remote was removed; mapping must hint --sync none."""
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(repo / ".shared"))
    rc = relay.cmd_init(_args(role="remote"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "removed in v0.6" in err
    assert "--sync none" in err


def test_init_hints_lead_with_same_host_and_flag_alternatives(monkeypatch, tmp_path, capsys):
    """v0.6: missing .envrc.<hostname> hint mentions same-host AND the explicit flags."""
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(repo / ".shared"))
    rc = relay.cmd_init(_args())
    assert rc == 0
    out = capsys.readouterr().out
    assert "--role same-host" in out
    assert "--author" in out and "--peer" in out and "--sync" in out
    # Legacy --role host/remote should NOT appear in hints anymore.
    assert "--role host" not in out
    assert "--role remote" not in out


def test_init_same_host_emits_direnv_aware_next_step(monkeypatch, tmp_path, capsys):
    """After --role same-host copies envrc, output names direnv or source."""
    import shutil as _shutil
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(repo / ".shared"))
    monkeypatch.setattr(_shutil, "which", lambda name: "/fake/direnv" if name == "direnv" else None)
    monkeypatch.setattr(relay.shutil, "which", lambda name: "/fake/direnv" if name == "direnv" else None)
    rc = relay.cmd_init(_args(role="same-host"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "direnv allow" in out
    # `source .envrc` is the other-terminal alternative line in same-host output;
    # the next: line for the first terminal should be direnv-only.
    primary_next_line = next(ln for ln in out.splitlines() if ln.startswith("next:"))
    assert "source .envrc" not in primary_next_line

    # remove .envrc.<hostname> for second run; pretend direnv missing
    hostname = relay._hostname_short()
    (repo / f".envrc.{hostname}").unlink()
    monkeypatch.setattr(relay.shutil, "which", lambda name: None)
    capsys.readouterr()
    rc = relay.cmd_init(_args(role="same-host"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "source .envrc" in out
    assert "install direnv" in out


def test_init_role_same_host_copies_template(monkeypatch, tmp_path, capsys):
    """v0.5: --role same-host copies envrc.same-host.example to .envrc.<hostname>."""
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(repo / ".shared"))
    rc = relay.cmd_init(_args(role="same-host"))
    assert rc == 0
    hostname = relay._hostname_short()
    target = repo / f".envrc.{hostname}"
    assert target.is_file()
    body = target.read_text()
    # Defining marks of the same-host template:
    assert "RELAY_SYNC=none" in body
    assert "RELAY_AUTHOR=" in body
    # The same-host template MUST NOT set the legacy RELAY_ROLE.
    assert "RELAY_ROLE=" not in body
    # Dispatcher must also exist.
    assert (repo / ".envrc").is_file()


def _source_template_and_read(env_vars: dict[str, str], template_body: str) -> dict[str, str]:
    """Source the given envrc body in a fresh bash, return resulting RELAY_* vars.

    Used to prove the same-host template actually produces distinct identities
    based on a pre-set RELAY_AUTHOR — the regression codex seq 6 asked for.
    """
    import tempfile
    import textwrap

    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
        fh.write(template_body)
        envrc_path = fh.name
    try:
        # Build the bash script: clear RELAY_*, set the requested overrides,
        # source the template, then print resolved RELAY_* as KEY=VALUE lines.
        export_block = "\n".join(f"export {k}={v!r}" for k, v in env_vars.items())
        # cd into a tmp dir so $PWD in the template lands somewhere innocuous
        with tempfile.TemporaryDirectory() as cwd:
            script = textwrap.dedent(f"""
                set -e
                # Clear any inherited RELAY_*.
                for v in $(env | awk -F= '/^RELAY_/{{print $1}}'); do unset "$v"; done
                {export_block}
                cd {cwd!r}
                # shellcheck disable=SC1090
                source {envrc_path!r}
                env | grep '^RELAY_' || true
            """).strip()
            res = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    finally:
        os.unlink(envrc_path)
    assert res.returncode == 0, f"bash source failed: {res.stderr!r}"
    out = {}
    for line in res.stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k] = v
    return out


def test_same_host_template_produces_distinct_identities_on_one_hostname(monkeypatch, tmp_path):
    """Regression for codex seq 6 blocker.

    Same hostname, same template file: a per-terminal `export RELAY_AUTHOR=...`
    BEFORE sourcing must produce two distinct (author, peer) pairs without
    editing the file between terminal launches.
    """
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(repo / ".shared"))
    assert relay.cmd_init(_args(role="same-host")) == 0
    target = repo / f".envrc.{relay._hostname_short()}"
    body = target.read_text()

    # Terminal A (default, no pre-export) -> codex / claude.
    env_a = _source_template_and_read({}, body)
    assert env_a["RELAY_AUTHOR"] == "codex"
    assert env_a["RELAY_PEER"] == "claude"
    assert env_a["RELAY_SYNC"] == "none"

    # Terminal B (per-terminal override) -> claude / codex.
    env_b = _source_template_and_read({"RELAY_AUTHOR": "claude"}, body)
    assert env_b["RELAY_AUTHOR"] == "claude"
    assert env_b["RELAY_PEER"] == "codex"
    assert env_b["RELAY_SYNC"] == "none"

    # The file on disk MUST be identical between the two terminals.
    assert target.read_text() == body, "template should not be mutated by sourcing"


def test_same_host_template_invalid_author_unsets_peer(monkeypatch, tmp_path):
    """Codex v05-post-commit-review seq 2 Minor.

    Re-sourcing the template with an unrecognized RELAY_AUTHOR must unset
    RELAY_PEER. Otherwise a previously-valid (claude, codex) pair can
    survive into a bad env (e.g. AUTHOR=gpt55, PEER=codex) that still
    passes preflight's "all required env set" check.
    """
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(repo / ".shared"))
    assert relay.cmd_init(_args(role="same-host")) == 0
    target = repo / f".envrc.{relay._hostname_short()}"
    body = target.read_text()

    # Simulate: first source as claude (sets PEER=codex), then re-source
    # with AUTHOR=gpt55 — the template's default branch must unset PEER.
    import tempfile, textwrap
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
        fh.write(body)
        envrc_path = fh.name
    try:
        with tempfile.TemporaryDirectory() as cwd:
            script = textwrap.dedent(f"""
                set -e
                for v in $(env | awk -F= '/^RELAY_/{{print $1}}'); do unset "$v"; done
                cd {cwd!r}
                # First terminal: claude.
                export RELAY_AUTHOR=claude
                source {envrc_path!r}
                # Now simulate the user typo: change AUTHOR to something unknown
                # and re-source the SAME file. RELAY_PEER must come out unset.
                export RELAY_AUTHOR=gpt55
                source {envrc_path!r} 2>/dev/null  # suppress the human-readable error
                echo "AUTHOR=${{RELAY_AUTHOR-<unset>}}"
                echo "PEER=${{RELAY_PEER-<unset>}}"
            """).strip()
            res = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    finally:
        os.unlink(envrc_path)
    assert res.returncode == 0, f"bash source failed: {res.stderr!r}"
    out = dict(line.split("=", 1) for line in res.stdout.splitlines() if "=" in line)
    assert out["AUTHOR"] == "gpt55"
    assert out["PEER"] == "<unset>", (
        f"RELAY_PEER must be unset after invalid-author re-source; got {out['PEER']!r}"
    )


def test_init_hint_lists_same_host_first(monkeypatch, tmp_path, capsys):
    """Hint should lead with same-host (the v0.5+ recommendation)."""
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(repo / ".shared"))
    rc = relay.cmd_init(_args())
    assert rc == 0
    out = capsys.readouterr().out
    # v0.6: same-host is the only --role option in the hint; the explicit
    # --author/--peer/--sync trio is the secondary path.
    assert "--role same-host" in out
    assert "--role host" not in out
    assert "--role remote" not in out
    assert "--author" in out and "--peer" in out and "--sync" in out


def test_init_suppresses_envrc_nag_when_sync_env_set(monkeypatch, tmp_path, capsys):
    """If RELAY_SYNC is set (v0.5+ path), no nag about missing .envrc.<hostname>."""
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch,
                  RELAY_SHARED_ROOT=str(repo / ".shared"),
                  RELAY_SYNC="none",
                  RELAY_AUTHOR="codex", RELAY_PEER="claude")
    hostname = relay._hostname_short()
    assert not (repo / f".envrc.{hostname}").exists()
    rc = relay.cmd_init(_args())
    assert rc == 0
    out = capsys.readouterr().out
    assert "--role" not in out
    assert "not found" not in out


def test_init_suppresses_envrc_nag_when_legacy_role_env_set(monkeypatch, tmp_path, capsys):
    """If the user still has RELAY_ROLE set (pre-v0.6 envrc), init does NOT
    spam template-setup advice. They need a migration edit, not a template;
    preflight surfaces the migration hint separately. init stays quiet here."""
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
    assert "--role" not in out
    assert "not found" not in out


# v0.6: new explicit-flags init tests.

def test_init_author_peer_sync_renders_envrc(monkeypatch, tmp_path, capsys):
    """`relay init --author X --peer Y --sync none` writes a working envrc."""
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
    assert "RELAY_PEER=claude" in body
    assert "RELAY_SYNC=none" in body
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


def test_init_role_and_author_are_mutually_exclusive(monkeypatch, tmp_path, capsys):
    """Pick one: --role same-host OR --author/--peer/--sync."""
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(repo / ".shared"))
    rc = relay.cmd_init(_args(role="same-host", author="codex", peer="claude", sync="none"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "mutually exclusive" in err


def test_init_author_without_peer_fails(monkeypatch, tmp_path, capsys):
    """--author without --peer is incomplete; refuse so we don't generate a half-envrc."""
    repo = tmp_path / "myproj"
    _init_git_repo(repo)
    monkeypatch.chdir(repo)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(repo / ".shared"))
    rc = relay.cmd_init(_args(author="codex"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "--peer is required" in err
