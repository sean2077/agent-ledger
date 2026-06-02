"""Worktree robustness — ledger anchor (main worktree) vs content root, and the
optional `worktree_root` artifact field (v1.3.0).

The ledger must NOT split when one agent operates from a git linked worktree: an
agent in the main checkout and one in a linked worktree resolve the SAME
`.shared/`. `relay sync` and shape detection deliberately stay on the content
root (the current checkout). `relay claim` stamps the authoring worktree so the
peer reads/edits the right tree."""

import json
import os
import subprocess
from pathlib import Path

import pytest

import relay


# --------------------------------------------------------------------------- #
# git helpers                                                                  #
# --------------------------------------------------------------------------- #

def _git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], check=True,
                          capture_output=True, text=True)


def _repo_with_commit(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", "-b", "main", str(path))
    _git("-C", str(path), "config", "user.email", "t@example.com")
    _git("-C", str(path), "config", "user.name", "Test")
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    _git("-C", str(path), "add", "-A")
    _git("-C", str(path), "commit", "-q", "-m", "init")
    return path


def _add_worktree(main: Path, wt: Path, branch: str = "feat") -> Path:
    _git("-C", str(main), "worktree", "add", "-q", "-b", branch, str(wt))
    return wt


def _isolate(monkeypatch, **kwargs):
    for k in list(os.environ):
        if k.startswith("RELAY_"):
            monkeypatch.delenv(k, raising=False)
    for k, v in kwargs.items():
        monkeypatch.setenv(k, v)


# --------------------------------------------------------------------------- #
# main_worktree() — the anchor resolver                                        #
# --------------------------------------------------------------------------- #

def test_main_worktree_from_linked_worktree_returns_main(tmp_path):
    main = _repo_with_commit(tmp_path / "repo")
    wt = _add_worktree(main, tmp_path / "wt")
    # The current-checkout view (git_toplevel) sees the worktree...
    assert relay.git_toplevel(wt).resolve() == wt.resolve()
    # ...but the ledger anchor converges on the main worktree.
    assert relay.main_worktree(wt).resolve() == main.resolve()


def test_main_checkout_anchor_is_unchanged(tmp_path):
    """No linked worktree: the anchor IS --show-toplevel (zero behavior change)."""
    main = _repo_with_commit(tmp_path / "repo")
    assert relay.main_worktree(main).resolve() == main.resolve()
    assert relay.main_worktree(main).resolve() == relay.git_toplevel(main).resolve()


def test_main_worktree_resolves_porcelain_path_symlink(monkeypatch, tmp_path):
    """Canonicalize the porcelain path so symlinked checkout spellings converge."""
    real = tmp_path / "real-repo"
    real.mkdir()
    link = tmp_path / "repo-link"
    link.symlink_to(real, target_is_directory=True)

    def fake_run(cmd, **_kwargs):
        assert cmd[:3] == ["git", "-C", str(link)]
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=f"worktree {link}\nHEAD 0\nbranch refs/heads/main\n\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert relay.main_worktree(link) == real.resolve()


def test_bare_backed_worktree_falls_back_not_bare(tmp_path):
    """A bare-backed worktree's first porcelain record is `bare` — never anchor
    `.shared` inside a bare repo; fall back to the current checkout."""
    src = _repo_with_commit(tmp_path / "src")
    bare = tmp_path / "bare.git"
    _git("clone", "-q", "--bare", str(src), str(bare))
    wt = tmp_path / "wt"
    _git("-C", str(bare), "worktree", "add", "-q", str(wt), "main")
    anchor = relay.main_worktree(wt)
    assert anchor is not None
    assert anchor.resolve() != bare.resolve()
    # Fallback is git_toplevel() = the worktree itself (old per-checkout behavior).
    assert anchor.resolve() == relay.git_toplevel(wt).resolve() == wt.resolve()


def test_main_worktree_outside_repo_is_none(tmp_path):
    outside = tmp_path / "plain"
    outside.mkdir()
    assert relay.main_worktree(outside) is None


# --------------------------------------------------------------------------- #
# default_shared_root() — the ledger anchor in practice                        #
# --------------------------------------------------------------------------- #

def test_shared_root_identical_from_main_and_worktree(tmp_path):
    """The crux: both checkouts resolve the SAME .shared with no env override."""
    main = _repo_with_commit(tmp_path / "repo")
    wt = _add_worktree(main, tmp_path / "wt")
    expected = (main / ".shared").resolve()
    assert relay.default_shared_root(main) == expected
    assert relay.default_shared_root(wt) == expected


# --------------------------------------------------------------------------- #
# worktree_root scaffold/field plumbing                                        #
# --------------------------------------------------------------------------- #

def test_scaffold_includes_worktree_root_only_when_provided():
    without = relay._scaffold_frontmatter("claude", "codex", 1, "plan", None)
    assert "worktree_root" not in without
    with_wt = relay._scaffold_frontmatter("claude", "codex", 1, "plan", None,
                                          worktree_root="/abs/wt")
    assert with_wt["worktree_root"] == "/abs/wt"
    # Round-trips through the YAML subset dump/parse.
    fm, _ = relay.parse_frontmatter(relay.dump_frontmatter(with_wt, "body\n"))
    assert fm["worktree_root"] == "/abs/wt"


# --------------------------------------------------------------------------- #
# functional: claim / publish / status from a worktree                         #
# --------------------------------------------------------------------------- #

def _bootstrap_no_explicit_root(monkeypatch, tmp_path, topic="wt"):
    """Set up a pair whose .shared is resolved by DEFAULT (no RELAY_SHARED_ROOT),
    so the test proves cross-worktree anchoring. Returns (main, worktree)."""
    main = _repo_with_commit(tmp_path / "repo")
    wt = _add_worktree(main, tmp_path / "wt")
    _isolate(monkeypatch,
             RELAY_AUTHOR="claude",
             RELAY_AGENT_SESSION_ID="wt-window")
    monkeypatch.chdir(main)
    relay.cmd_init(type("A", (), {"same_host": False, "author": None,
                                  "sync": None})())
    relay.cmd_bootstrap(type("A", (), {"topic": topic, "title": None,
                                       "peer": None, "force": False})())
    return main, wt


def _claim_args(kind="fix"):
    return type("A", (), {"kind": kind, "in_reply_to": None, "corrects": None,
                          "project": None, "pair_id": None})()


def test_claim_in_main_checkout_omits_worktree_root(monkeypatch, tmp_path, capsys):
    main, _wt = _bootstrap_no_explicit_root(monkeypatch, tmp_path)
    capsys.readouterr()
    assert relay.cmd_claim(_claim_args()) == 0
    draft = Path(capsys.readouterr().out.strip())
    fm, _ = relay.parse_frontmatter(draft.read_text())
    assert "worktree_root" not in fm


def test_claim_in_linked_worktree_stamps_worktree_root(monkeypatch, tmp_path, capsys):
    main, wt = _bootstrap_no_explicit_root(monkeypatch, tmp_path)
    # The pair lives under the MAIN checkout's .shared even though we now act
    # from the worktree — proving the ledger did not split.
    monkeypatch.chdir(wt)
    capsys.readouterr()
    assert relay.cmd_claim(_claim_args()) == 0
    draft = Path(capsys.readouterr().out.strip())
    assert draft.resolve().is_relative_to((main / ".shared").resolve())
    fm, _ = relay.parse_frontmatter(draft.read_text())
    assert fm["worktree_root"] == str(wt.resolve())


def test_status_json_exposes_worktree_root(monkeypatch, tmp_path, capsys):
    main, wt = _bootstrap_no_explicit_root(monkeypatch, tmp_path)
    monkeypatch.chdir(wt)
    capsys.readouterr()
    assert relay.cmd_claim(_claim_args()) == 0
    draft = Path(capsys.readouterr().out.strip())

    # Fill the draft (clear the placeholder) and publish; worktree_root must
    # survive validation + promotion.
    fm, _ = relay.parse_frontmatter(draft.read_text())
    fm["prompt_for_next"] = "- review the change in this worktree\n"
    draft.write_text(relay.dump_frontmatter(fm, "\nreal body\n"), encoding="utf-8")
    assert relay.cmd_publish(type("A", (), {
        "draft_path": str(draft), "status": None, "force": False,
        "force_reason": None})()) == 0

    capsys.readouterr()
    rc = relay.cmd_status(type("A", (), {
        "json": True, "last": None, "pair_id": None, "project": None,
        "require_binding": False})())
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    pub = [a for a in payload["published"] if a.get("kind") == "fix"]
    assert pub and pub[0]["worktree_root"] == str(wt.resolve())


# --------------------------------------------------------------------------- #
# preflight + sync surfacing                                                   #
# --------------------------------------------------------------------------- #

def test_preflight_from_worktree_passes_location_and_surfaces_worktree(
        monkeypatch, tmp_path, capsys):
    main = _repo_with_commit(tmp_path / "repo")
    wt = _add_worktree(main, tmp_path / "wt")
    _isolate(monkeypatch, RELAY_AUTHOR="claude", RELAY_AGENT_SESSION_ID="wt-window")
    monkeypatch.chdir(main)
    relay.cmd_init(type("A", (), {"same_host": False, "author": None,
                                  "sync": None})())
    monkeypatch.chdir(wt)
    capsys.readouterr()
    relay.cmd_preflight(type("A", (), {"json": True})())
    out = json.loads(capsys.readouterr().out)
    checks = {c["name"]: c for c in out["checks"]}
    # The shared root (main/.shared) validates against the ledger anchor, not the
    # worktree — so location is pass, not the old "outside git toplevel" fail.
    assert checks["shared_root.location"]["status"] == "pass"
    # Worktree awareness surfaced (non-blocking).
    assert "project.worktree" in checks
    assert checks["project.worktree"]["status"] == "pass"
    assert str(wt.resolve()) in checks["project.worktree"]["detail"]


def _mock_rsync(monkeypatch):
    captured: list[list[str]] = []
    real_run = subprocess.run

    def selective(cmd, *args, **kw):
        if isinstance(cmd, list) and cmd and cmd[0] == "rsync":
            captured.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0)
        return real_run(cmd, *args, **kw)

    monkeypatch.setattr(subprocess, "run", selective)
    return captured


def test_sync_from_worktree_uses_content_root_not_anchor(monkeypatch, tmp_path, capsys):
    """sync mirrors the checkout you're in: a worktree author pushes the
    worktree's files, NOT the main-anchored ledger root."""
    main = _repo_with_commit(tmp_path / "repo")
    wt = _add_worktree(main, tmp_path / "wt")
    _isolate(monkeypatch,
             RELAY_SYNC="rsync",
             RELAY_REMOTE_SSH="user@remote",
             RELAY_REMOTE_PATH="/remote/path",
             RELAY_AUTHOR="claude",
             RELAY_AGENT_SESSION_ID="wt-window")
    monkeypatch.chdir(wt)
    monkeypatch.setattr(relay, "_is_fuse_mount", lambda p: False)
    captured = _mock_rsync(monkeypatch)
    rc = relay.cmd_sync(type("A", (), {
        "direction": "push", "dry_run": True, "strict_gitignore": False,
        "delete": False})())
    assert rc == 0
    cmd = captured[0]
    src = cmd[-2].rstrip("/")  # push: [..., src, dst]
    assert Path(src).resolve() == wt.resolve()
    assert Path(src).resolve() != main.resolve()
