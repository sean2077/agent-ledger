"""Resume semantics for the `timed_out` user-blocking pause.

Regression for issue 20260529T190821-0ec54be9: the skill tells an agent that is
blocking on the user to publish `--status timed_out` with a `@user:` line. That
status is terminal (the peer's `relay wait` exits 12 and stops), but it must NOT
be a dead end — once the user answers, the round has to be RESUMABLE in-thread.

The fix splits "terminal" into:
  * hard-terminal (closed/cancelled/failed) — pair is over, write path fails closed;
  * resumable    (timed_out)               — pair is *paused*, write/bind path may
                                              supersede the latest artifact.

`session_is_active` keeps treating all four as not-active (wait/discovery/report);
`session_is_resumable` treats only the hard-terminal trio as not-resumable. These
tests pin both halves AND the latent re-deadlock: the binding of a paused pair
must survive a passive `status --require-binding` resolve (the Stop hook).
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

import relay


def _be(monkeypatch, author: str, agent_session: str) -> None:
    """Become `author` in window `agent_session` for the next relay call."""
    monkeypatch.setenv("RELAY_AUTHOR", author)
    monkeypatch.setenv("RELAY_AGENT_SESSION_ID", agent_session)


def _bootstrap(monkeypatch, tmp_path: Path, topic="t"):
    """Create + bind a codex pair (creator = codex), peer = claude."""
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
    monkeypatch.setenv("RELAY_SYNC", "rsync")
    monkeypatch.setenv("RELAY_REMOTE_SSH", "x@y")
    monkeypatch.setenv("RELAY_REMOTE_PATH", "/r")
    # Heartbeat autostart is irrelevant to resume routing; disable it so the
    # tests exercise pure claim/publish/bind logic without renewal sidecars.
    monkeypatch.setenv("RELAY_CLAIM_NO_HEARTBEAT", "1")
    monkeypatch.setenv("RELAY_SHARED_ROOT", str(shared))
    _be(monkeypatch, "codex", "test-codex-window")
    relay.cmd_bootstrap(type("A", (), {"topic": topic, "title": None})())
    return relay.resolve_active_pair(relay.load_env())


def _publish(monkeypatch, session, capsys, *, author, window, kind, status,
             in_reply_to=None, pair_id=None) -> Path:
    """claim -> fill -> publish one artifact as `author`; return its path."""
    _be(monkeypatch, author, window)
    rc = relay.cmd_claim(type("A", (), {
        "kind": kind, "in_reply_to": in_reply_to, "corrects": None,
        "project": None, "pair_id": pair_id,
    })())
    capsys.readouterr()
    assert rc == 0, f"claim({author},{kind}) expected rc 0, got {rc}"
    draft = sorted((session / ".draft").glob("*.md"))[-1]
    fm, _ = relay.parse_frontmatter(draft.read_text())
    fm["prompt_for_next"] = ("@user: which option?\n" if status == "timed_out"
                             else "carry on\n")
    draft.write_text(relay.dump_frontmatter(fm, "\nbody.\n"))
    rc = relay.cmd_publish(type("A", (), {
        "draft_path": str(draft), "status": status,
        "force": False, "force_reason": None,
        "project": None, "pair_id": pair_id,
    })())
    capsys.readouterr()
    assert rc == 0, f"publish({author},{status}) expected rc 0, got {rc}"
    return next(session.glob(f"{int(fm['seq']):03d}-*.md"))


# --------------------------------------------------------------------------
# predicate semantics
# --------------------------------------------------------------------------

def test_timed_out_is_resumable_but_not_active(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    _publish(monkeypatch, session, capsys, author="codex",
             window="test-codex-window", kind="question", status="timed_out")
    assert relay.session_is_active(session) is False
    assert relay.session_is_resumable(session) is True


@pytest.mark.parametrize("status", ["closed", "cancelled", "failed"])
def test_hard_terminal_is_neither_active_nor_resumable(monkeypatch, tmp_path, capsys, status):
    session = _bootstrap(monkeypatch, tmp_path)
    _publish(monkeypatch, session, capsys, author="codex",
             window="test-codex-window", kind="note", status=status)
    assert relay.session_is_active(session) is False
    assert relay.session_is_resumable(session) is False


# --------------------------------------------------------------------------
# the core issue: resume after the user answers
# --------------------------------------------------------------------------

def test_peer_resumes_timed_out_round_via_pair_id(monkeypatch, tmp_path, capsys):
    """codex escalates with timed_out; the user answers; claude (an unbound
    window) resumes the SAME round with --pair-id and publishes the follow-up."""
    session = _bootstrap(monkeypatch, tmp_path)
    _publish(monkeypatch, session, capsys, author="codex",
             window="test-codex-window", kind="question", status="timed_out")  # seq 1

    # claude resumes — exactly the call that used to fail "session is not active".
    fix = _publish(monkeypatch, session, capsys, author="claude",
                   window="test-claude-window", kind="fix", status="ready",
                   in_reply_to=1, pair_id=session.name)
    fm, _ = relay.parse_frontmatter(fix.read_text())
    assert fm["seq"] == 2 and fm["author"] == "claude" and fm["in_reply_to"] == 1
    # The round is live again — a fresh ready artifact supersedes the pause.
    assert relay.session_is_active(session) is True


def test_bound_peer_resumes_timed_out_round_via_binding(monkeypatch, tmp_path, capsys):
    """A peer that joined the pair resumes through its binding (no --pair-id)."""
    session = _bootstrap(monkeypatch, tmp_path)
    root = relay.session_root(relay.load_env())
    _be(monkeypatch, "claude", "test-claude-window")
    assert relay.join_pair(relay.load_env(), root, session.name) == 0
    _publish(monkeypatch, session, capsys, author="codex",
             window="test-codex-window", kind="question", status="timed_out")  # seq 1

    _be(monkeypatch, "claude", "test-claude-window")
    rc = relay.cmd_claim(type("A", (), {
        "kind": "fix", "in_reply_to": 1, "corrects": None,
        "project": None, "pair_id": None,
    })())
    assert rc == 0, "bound peer should resume via its binding"
    assert sorted((session / ".draft").glob("*claude*.md"))


def test_claim_refuses_hard_terminal_round(monkeypatch, tmp_path, capsys):
    """Regression guard: hard-terminal stays a dead end (no resurrection)."""
    session = _bootstrap(monkeypatch, tmp_path)
    _publish(monkeypatch, session, capsys, author="codex",
             window="test-codex-window", kind="note", status="failed")  # seq 1

    _be(monkeypatch, "claude", "test-claude-window")
    rc = relay.cmd_claim(type("A", (), {
        "kind": "fix", "in_reply_to": 1, "corrects": None,
        "project": None, "pair_id": session.name,
    })())
    err = capsys.readouterr().err
    assert rc == 2
    assert "not active" in err
    assert list((session / ".draft").glob("*.md")) == []


# --------------------------------------------------------------------------
# binding survival — the latent half of the deadlock
# --------------------------------------------------------------------------

def test_timed_out_publish_keeps_publisher_binding(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    root = relay.session_root(relay.load_env())
    _publish(monkeypatch, session, capsys, author="codex",
             window="test-codex-window", kind="question", status="timed_out")
    assert relay.read_binding(root, "codex", "test-codex-window") is not None


def test_hard_terminal_publish_drops_publisher_binding(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    root = relay.session_root(relay.load_env())
    _publish(monkeypatch, session, capsys, author="codex",
             window="test-codex-window", kind="note", status="closed")
    assert relay.read_binding(root, "codex", "test-codex-window") is None


def test_status_require_binding_does_not_gc_paused_binding(monkeypatch, tmp_path, capsys):
    """The Stop hook runs `status --require-binding` every turn. On a paused
    (timed_out) pair it must resolve to None (passive automation stays quiet)
    WITHOUT deleting the binding — deleting it was the hidden re-deadlock."""
    session = _bootstrap(monkeypatch, tmp_path)
    root = relay.session_root(relay.load_env())
    _publish(monkeypatch, session, capsys, author="codex",
             window="test-codex-window", kind="question", status="timed_out")

    _be(monkeypatch, "codex", "test-codex-window")
    env = relay.load_env()
    # allow_resumable=False mirrors the passive Stop-hook resolution.
    assert relay.resolve_active_pair(env, None, None, require_binding=True) is None
    assert relay.read_binding(root, "codex", "test-codex-window") is not None, \
        "paused pair binding must survive a passive require-binding resolve"

    # ...and the same instance can still resume in-thread afterwards.
    rc = relay.cmd_claim(type("A", (), {
        "kind": "fix", "in_reply_to": 1, "corrects": None,
        "project": None, "pair_id": None,
    })())
    assert rc == 0


# --------------------------------------------------------------------------
# bind/discovery surfaces
# --------------------------------------------------------------------------

def test_pair_join_binds_to_paused_pair(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    root = relay.session_root(relay.load_env())
    _publish(monkeypatch, session, capsys, author="codex",
             window="test-codex-window", kind="question", status="timed_out")
    _be(monkeypatch, "claude", "test-claude-window")
    assert relay.join_pair(relay.load_env(), root, session.name) == 0


def test_pair_join_refuses_hard_terminal_pair(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    root = relay.session_root(relay.load_env())
    _publish(monkeypatch, session, capsys, author="codex",
             window="test-codex-window", kind="note", status="failed")
    _be(monkeypatch, "claude", "test-claude-window")
    assert relay.join_pair(relay.load_env(), root, session.name) == 2


def test_pair_ensure_uses_paused_pair_and_keeps_binding(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    root = relay.session_root(relay.load_env())
    _publish(monkeypatch, session, capsys, author="codex",
             window="test-codex-window", kind="question", status="timed_out")

    _be(monkeypatch, "codex", "test-codex-window")
    rc = relay.cmd_pair_ensure(type("A", (), {"json": True})())
    payload = json.loads(capsys.readouterr().out.strip())
    assert rc == 0
    assert payload["action"] == "use" and payload["pair"] == session.name
    assert relay.read_binding(root, "codex", "test-codex-window") is not None


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

def test_status_reports_resumable_for_timed_out(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    _publish(monkeypatch, session, capsys, author="codex",
             window="test-codex-window", kind="question", status="timed_out")

    _be(monkeypatch, "codex", "test-codex-window")
    rc = relay.cmd_status(type("A", (), {
        "pair_id": session.name, "project": None, "json": True,
        "last": None, "require_binding": False,
    })())
    payload = json.loads(capsys.readouterr().out.strip())
    assert rc == 0
    assert payload["is_active"] is False
    assert payload["resumable"] is True


def test_status_not_resumable_when_active(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    _publish(monkeypatch, session, capsys, author="codex",
             window="test-codex-window", kind="plan", status="ready")

    _be(monkeypatch, "codex", "test-codex-window")
    rc = relay.cmd_status(type("A", (), {
        "pair_id": session.name, "project": None, "json": True,
        "last": None, "require_binding": False,
    })())
    payload = json.loads(capsys.readouterr().out.strip())
    assert rc == 0
    assert payload["is_active"] is True
    assert payload["resumable"] is False


def test_bare_status_resolves_bound_paused_pair(monkeypatch, tmp_path, capsys):
    """The normal `pair ensure -> status` handoff must not dead-end after a
    pause: bare `relay status` (no --pair-id) resolves the bound paused pair via
    its binding and reports it resumable. Regression for codex review (seq 2) —
    test_status_reports_resumable_for_timed_out passed `--pair-id` and so masked
    the default-resolution blocker."""
    session = _bootstrap(monkeypatch, tmp_path)
    _publish(monkeypatch, session, capsys, author="codex",
             window="test-codex-window", kind="question", status="timed_out")

    _be(monkeypatch, "codex", "test-codex-window")
    rc = relay.cmd_status(type("A", (), {
        "pair_id": None, "project": None, "json": True,
        "last": None, "require_binding": False,
    })())
    payload = json.loads(capsys.readouterr().out.strip())
    assert rc == 0, "bare status must resolve the bound paused pair, not error"
    assert payload["session"]["session_id"] == session.name
    assert payload["is_active"] is False
    assert payload["resumable"] is True
    assert payload["bound_pair"] == session.name


def test_require_binding_status_stays_quiet_on_paused_pair(monkeypatch, tmp_path, capsys):
    """`relay status --require-binding` (the Stop hook, run every turn) must NOT
    wake the auto-loop for a paused pair: it resolves to the non-actionable
    empty payload and leaves the binding intact."""
    session = _bootstrap(monkeypatch, tmp_path)
    root = relay.session_root(relay.load_env())
    _publish(monkeypatch, session, capsys, author="codex",
             window="test-codex-window", kind="question", status="timed_out")

    _be(monkeypatch, "codex", "test-codex-window")
    rc = relay.cmd_status(type("A", (), {
        "pair_id": None, "project": None, "json": True,
        "last": None, "require_binding": True,
    })())
    payload = json.loads(capsys.readouterr().out.strip())
    assert rc == 0
    assert payload["session"] is None
    assert payload["bound_pair"] is None
    assert payload["is_active"] is False and payload["resumable"] is False
    # binding survives — the round is still resumable by an explicit claim.
    assert relay.read_binding(root, "codex", "test-codex-window") is not None
