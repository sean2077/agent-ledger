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
    monkeypatch.setenv("RELAY_SYNC", "rsync")
    monkeypatch.setenv("RELAY_REMOTE_SSH", "x@y")
    monkeypatch.setenv("RELAY_REMOTE_PATH", "/r")
    monkeypatch.setenv("RELAY_AUTHOR", "codex")
    monkeypatch.setenv("RELAY_PEER", "claude")
    monkeypatch.setenv("RELAY_SHARED_ROOT", str(shared))
    return shared


def test_bootstrap_creates_full_structure(monkeypatch, tmp_path, capsys):
    shared = _setup_shared(monkeypatch, tmp_path, with_sentinel=False)
    args = type("A", (), {"topic": "smoke", "title": "smoke test"})()
    rc = relay.cmd_bootstrap(args)
    assert rc == 0

    sessions = [p for p in shared.iterdir() if p.is_dir() and p.name != "_relay"]
    assert len(sessions) == 1
    sess = sessions[0]
    assert sess.name.endswith("-smoke")
    assert (sess / "session.json").is_file()
    assert (sess / "README.md").is_file()
    assert (sess / ".draft").is_dir()
    assert (shared / "_relay" / ".sentinel").exists()
    assert (shared / "myproj").exists() is False
    sj = json.loads((sess / "session.json").read_text())
    assert sj["schema_version"] == 3
    assert sj["state"] == "active"
    assert sj["project"] == "myproj"
    assert sj["participants"] == ["codex", "claude"]


def test_bootstrap_defaults_shared_root_inside_git(monkeypatch, tmp_path, capsys):
    repo = tmp_path / "myproj"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    monkeypatch.chdir(repo)
    for k in list(os.environ):
        if k.startswith("RELAY_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RELAY_SYNC", "none")
    monkeypatch.setenv("RELAY_AUTHOR", "codex")
    monkeypatch.setenv("RELAY_PEER", "claude")

    rc = relay.cmd_bootstrap(type("A", (), {"topic": "default-root", "title": None})())
    assert rc == 0
    shared = repo / ".shared"
    sessions = [p for p in shared.iterdir() if p.is_dir() and p.name != "_relay"]
    assert len(sessions) == 1
    assert (shared / "_relay" / ".sentinel").is_file()


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


def test_sanitize_project_slug_handles_dots_underscores_capitals():
    """git-toplevel-derived names with dots / underscores / capitals must
    coerce into valid slugs, not crash bootstrap (real user bug 2026-05-28:
    'derived project actibot_ego.jy not a valid slug')."""
    assert relay.sanitize_project_slug("actibot_ego.jy") == "actibot-ego-jy"
    assert relay.sanitize_project_slug("MyProject") == "myproject"
    assert relay.sanitize_project_slug("foo.bar.baz") == "foo-bar-baz"
    assert relay.sanitize_project_slug("_leading_under") == "leading-under"
    assert relay.sanitize_project_slug("trailing.") == "trailing"
    # Length clip
    long_raw = "x" * 60
    assert len(relay.sanitize_project_slug(long_raw)) == 48


def test_bootstrap_succeeds_with_dotted_repo_name(monkeypatch, tmp_path, capsys):
    """Bootstrap must succeed when the git toplevel name has dots/underscores —
    the slug sanitizer should coerce it instead of erroring."""
    repo = tmp_path / "actibot_ego.jy"
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
    monkeypatch.setenv("RELAY_AUTHOR", "codex")
    monkeypatch.setenv("RELAY_PEER", "claude")
    monkeypatch.setenv("RELAY_SHARED_ROOT", str(shared))
    rc = relay.cmd_bootstrap(type("A", (), {"topic": "smoke", "title": None})())
    assert rc == 0
    sj = json.loads(next(p for p in shared.iterdir() if p.name.endswith("-smoke")).joinpath("session.json").read_text())
    assert sj["project"] == "actibot-ego-jy"


def test_bootstrap_rejects_explicit_invalid_RELAY_PROJECT(monkeypatch, tmp_path, capsys):
    """Explicit RELAY_PROJECT env values are NOT sanitized — user-provided
    invalid slugs still error with a clear recovery hint."""
    _setup_shared(monkeypatch, tmp_path)
    monkeypatch.setenv("RELAY_PROJECT", "Bad Project Name")
    args = type("A", (), {"topic": "smoke", "title": None})()
    rc = relay.cmd_bootstrap(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "not a valid slug" in err
    assert "set RELAY_PROJECT" in err


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
                    closed_sentinel: bool = False, project: str = "myproj") -> Path:
    """Create a minimal session dir under `parent`. Returns the session path."""
    sd = parent / slug
    sd.mkdir(parents=True)
    (sd / ".draft").mkdir()
    sd.joinpath("session.json").write_text(json.dumps({
        "schema_version": 3, "project": project, "session_id": slug,
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
    # session A: state=active but latest artifact is closed -> NOT truly active
    sa = _write_session(shared, "20260527-stale")
    _publish_terminal_artifact(sa, seq=1, status="closed")
    # session B: state=active, no artifacts yet -> truly active
    sb = _write_session(shared, "20260527-real")

    env = relay.load_env()
    resolved = relay.resolve_active_session(env)
    assert resolved == sb, f"expected real session, got {resolved}"


def test_resolve_skips_session_with_CLOSED_sentinel(monkeypatch, tmp_path):
    """Bug fix MAJOR #1: CLOSED sentinel must filter out stale-active sessions."""
    shared = _setup_shared(monkeypatch, tmp_path)
    _write_session(shared, "20260527-with-closed-file", closed_sentinel=True)
    sb = _write_session(shared, "20260527-real")
    env = relay.load_env()
    resolved = relay.resolve_active_session(env)
    assert resolved == sb


def test_bootstrap_creates_dirs_with_0700_mode(monkeypatch, tmp_path):
    """Bug fix MINOR #5: project + session dirs must be 0700."""
    import stat as stat_mod
    shared = _setup_shared(monkeypatch, tmp_path)
    args = type("A", (), {"topic": "perms", "title": None})()
    assert relay.cmd_bootstrap(args) == 0
    sess = next(p for p in shared.iterdir() if p.is_dir() and p.name != "_relay")
    assert (sess.stat().st_mode & 0o777) == 0o700, \
        f"session dir mode is {oct(sess.stat().st_mode & 0o777)}"


# -----------------------------------------------------------------------------
# v3 bundle coverage for D1 + R3 + M3.
# -----------------------------------------------------------------------------


def test_bootstrap_creates_flat_session_layout(monkeypatch, tmp_path):
    """D1: bootstrap creates .shared/<session>/ (no <project> subdir)."""
    shared = _setup_shared(monkeypatch, tmp_path)
    assert relay.cmd_bootstrap(type("A", (), {"topic": "flat", "title": None})()) == 0
    sessions = [p for p in shared.iterdir() if p.is_dir() and p.name != "_relay"]
    assert len(sessions) == 1
    assert sessions[0].name.endswith("-flat")
    assert not (shared / "myproj").exists()


def test_status_resolves_flat_layout(monkeypatch, tmp_path, capsys):
    """D1: relay status finds sessions directly under .shared/."""
    _setup_shared(monkeypatch, tmp_path)
    relay.cmd_bootstrap(type("A", (), {"topic": "flat-status", "title": None})())
    capsys.readouterr()
    rc = relay.cmd_status(type("A", (), {"project": None, "session_id": None, "last": 0, "json": True})())
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert Path(data["session_dir"]).parent.name == ".shared"
    assert data["session"]["session_id"].endswith("-flat-status")


def test_bootstrap_binds_creator(monkeypatch, tmp_path):
    """v0.13: bootstrap binds the creating instance to the new pair (replacing
    the retired global marker)."""
    shared = _setup_shared(monkeypatch, tmp_path)
    assert relay.cmd_bootstrap(type("A", (), {"topic": "marker", "title": None})()) == 0
    bindings = relay.list_bindings(shared)
    assert len(bindings) == 1
    assert bindings[0]["pair_slug"].endswith("-marker")
    assert (shared / bindings[0]["pair_slug"] / "session.json").is_file()


def test_bootstrap_refuses_when_active_session_exists(monkeypatch, tmp_path, capsys):
    """M3 (a): bootstrap refuses to create a parallel active session by default."""
    _setup_shared(monkeypatch, tmp_path)
    assert relay.cmd_bootstrap(type("A", (), {"topic": "one", "title": None})()) == 0
    rc = relay.cmd_bootstrap(type("A", (), {"topic": "two", "title": None})())
    assert rc == 2
    assert "active session already exists" in capsys.readouterr().err


def test_bootstrap_force_allows_parallel_active_session(monkeypatch, tmp_path):
    """M3 (a): bootstrap --force overrides the active-pair refusal."""
    shared = _setup_shared(monkeypatch, tmp_path)
    assert relay.cmd_bootstrap(type("A", (), {"topic": "one", "title": None})()) == 0
    assert relay.cmd_bootstrap(type("A", (), {"topic": "two", "title": None, "force": True})()) == 0
    sessions = [p for p in shared.iterdir() if p.is_dir() and p.name != "_relay"]
    assert sorted(p.name[-3:] for p in sessions) == ["one", "two"]


def test_status_with_pair_id_resolves_among_multiple_active(monkeypatch, tmp_path, capsys):
    """relay status --pair-id picks the right pair when several are active."""
    shared = _setup_shared(monkeypatch, tmp_path)
    _write_session(shared, "20990101-first")
    second = _write_session(shared, "20990101-second")
    rc = relay.cmd_status(type("A", (), {
        "project": None, "pair_id": second.name, "last": 0, "json": True,
    })())
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert Path(data["session_dir"]) == second
    assert data["session"]["session_id"] == "20990101-second"


def test_resolve_active_session_multiple_raises(monkeypatch, tmp_path):
    shared = _setup_shared(monkeypatch, tmp_path)
    _write_session(shared, "20990101-a")
    _write_session(shared, "20990101-other")
    env = relay.load_env()
    # an unbound instance facing >1 active pairs is ambiguous
    relay.delete_binding(shared, env.author, env.agent_session_id)
    with pytest.raises(SystemExit, match="multiple active pairs"):
        relay.resolve_active_session(env)


def test_resolve_uses_binding_to_disambiguate_parallel(monkeypatch, tmp_path):
    """v0.13: with N>1 active pairs, THIS instance's binding resolves to its
    pair instead of raising — per-instance bindings replace the global marker."""
    shared = _setup_shared(monkeypatch, tmp_path)
    a = _write_session(shared, "20990101-a")
    other = _write_session(shared, "20990101-other")
    env = relay.load_env()
    relay.delete_binding(shared, env.author, env.agent_session_id)
    with pytest.raises(SystemExit, match="multiple active pairs"):
        relay.resolve_active_session(env)
    # bind to 'a' -> resolve returns 'a'
    assert relay.join_pair(relay.load_env(), shared, a.name) == 0
    assert relay.resolve_active_session(relay.load_env()).name == a.name
    # rebind to the other -> resolve follows the binding
    assert relay.join_pair(relay.load_env(), shared, other.name) == 0
    assert relay.resolve_active_session(relay.load_env()).name == other.name
