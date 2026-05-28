"""relay wait — block until peer publishes a new artifact targeting this author.

Exit code contract (Stage 1 implementation):
    0   new .ready targeted at me; stdout = absolute artifact path
    10  timeout (RELAY_WAIT_TIMEOUT seconds elapsed)
    12  session entered terminal state (closed / cancelled / failed / timed_out)
    130 SIGINT
    2   protocol / env / mount error
"""

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import relay


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _isolated_env(monkeypatch, **kwargs):
    for k in list(os.environ):
        if k.startswith("RELAY_"):
            monkeypatch.delenv(k, raising=False)
    for k, v in kwargs.items():
        monkeypatch.setenv(k, v)


def _bootstrap(monkeypatch, tmp_path: Path, *, author="claude", peer="codex"):
    """Create a fresh git repo + .shared/ + bootstrap a session.

    Returns the active session Path.
    """
    repo = tmp_path / "myproj"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    monkeypatch.chdir(repo)
    shared = repo / ".shared"
    shared.mkdir(mode=0o700)
    (shared / "_relay").mkdir()
    (shared / "_relay" / ".sentinel").touch()
    _isolated_env(monkeypatch,
        RELAY_ROLE="remote" if author == "claude" else "host",
        RELAY_AUTHOR=author,
        RELAY_PEER=peer,
        RELAY_SHARED_ROOT=str(shared),
    )
    relay.cmd_bootstrap(type("A", (), {"topic": "t", "title": None})())
    return relay.resolve_active_session(relay.load_env())


def _publish_artifact(session: Path, *, seq: int, author: str, peer: str,
                       kind: str = "review", status: str = "ready",
                       prompt: str = "do real things\n", body: str = "ok body"):
    """Manually publish a ready artifact to the session.

    Bypasses cmd_claim/cmd_publish so tests can craft arbitrary author/peer
    combos quickly without managing env switching.
    """
    fm = {
        "seq": seq,
        "author": author,
        "peer": peer,
        "kind": kind,
        "status": status,
        "created": relay.now_iso(),
        "in_reply_to": None,
        "prompt_for_next": prompt,
        "sync_needed": False,
        "touched_paths": [],
        "corrects": None,
    }
    name = f"{seq:03d}-{author}-{kind}.md"
    md = session / name
    text = relay.dump_frontmatter(fm, f"\n{body}\n")
    md.write_text(text)
    sha = relay.sha256_of_file(md)
    (session / f"{name}.sha256").write_text(f"{sha}  {name}\n")
    (session / f"{seq:03d}-{author}-{kind}.ready").touch()
    return md


def _wait_args(**kw):
    base = {"project": None, "session_id": None, "timeout": None, "poll": None}
    base.update(kw)
    return type("A", (), base)()


# ---------------------------------------------------------------------------
# in-process tests (fast)
# ---------------------------------------------------------------------------


def test_wait_returns_0_when_latest_already_targets_me(monkeypatch, tmp_path, capsys):
    """Entry edge case: latest published artifact already targets me → exit 0 immediately."""
    session = _bootstrap(monkeypatch, tmp_path)
    _publish_artifact(session, seq=1, author="codex", peer="claude")
    capsys.readouterr()  # drain bootstrap chatter
    rc = relay.cmd_wait(_wait_args(timeout=2, poll=1))
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out.endswith("001-codex-review.md")


def test_wait_returns_10_on_timeout(monkeypatch, tmp_path, capsys):
    """No new artifact targeting me within RELAY_WAIT_TIMEOUT → exit 10."""
    session = _bootstrap(monkeypatch, tmp_path)
    # Self-published baseline so peer field of latest != me.
    _publish_artifact(session, seq=1, author="claude", peer="codex")
    capsys.readouterr()  # drain bootstrap chatter
    rc = relay.cmd_wait(_wait_args(timeout=2, poll=1))
    assert rc == 10
    out = capsys.readouterr().out
    assert out == ""


def test_wait_returns_12_when_session_closes_mid_wait(monkeypatch, tmp_path, capsys):
    """Realistic terminal path: session was active at wait entry, peer closes it
    during my poll → exit 12."""
    session = _bootstrap(monkeypatch, tmp_path)
    _publish_artifact(session, seq=1, author="claude", peer="codex")

    def _close_in_a_bit():
        time.sleep(1.0)
        (session / "CLOSED").write_text('reason = "peer closed"\n')
        sj = session / "session.json"
        import json
        data = json.loads(sj.read_text())
        data["state"] = "closed"
        sj.write_text(json.dumps(data, indent=2))

    t = threading.Thread(target=_close_in_a_bit, daemon=True)
    t.start()
    capsys.readouterr()  # drain bootstrap chatter
    rc = relay.cmd_wait(_wait_args(timeout=5, poll=1))
    t.join(timeout=2)
    assert rc == 12
    out = capsys.readouterr().out.strip()
    # Latest artifact's status field is "ready"; terminal trigger came from
    # session_is_active() seeing the CLOSED sentinel + state=closed.
    assert out in {"closed", "ready", "cancelled", "failed", "timed_out"}


def test_wait_returns_2_when_author_missing(monkeypatch, tmp_path, capsys):
    """RELAY_AUTHOR unset → exit 2 with clear error."""
    session = _bootstrap(monkeypatch, tmp_path)
    monkeypatch.delenv("RELAY_AUTHOR", raising=False)
    rc = relay.cmd_wait(_wait_args(timeout=1, poll=1))
    assert rc == 2
    err = capsys.readouterr().err
    assert "RELAY_AUTHOR" in err


def test_wait_returns_2_when_no_session(monkeypatch, tmp_path, capsys):
    """No active session → exit 2."""
    repo = tmp_path / "myproj"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    monkeypatch.chdir(repo)
    shared = repo / ".shared"
    shared.mkdir(mode=0o700)
    (shared / "_relay").mkdir()
    (shared / "_relay" / ".sentinel").touch()
    _isolated_env(monkeypatch,
        RELAY_ROLE="remote",
        RELAY_AUTHOR="claude",
        RELAY_PEER="codex",
        RELAY_SHARED_ROOT=str(shared),
    )
    rc = relay.cmd_wait(_wait_args(timeout=1, poll=1))
    assert rc == 2
    err = capsys.readouterr().err
    assert "no active session" in err.lower() or "active session" in err.lower()


def test_wait_returns_0_when_peer_publishes_mid_wait(monkeypatch, tmp_path, capsys):
    """Peer publishes while we're blocked in poll loop → exit 0, path on stdout.

    Use a background thread to publish after a short delay.
    """
    session = _bootstrap(monkeypatch, tmp_path)
    _publish_artifact(session, seq=1, author="claude", peer="codex")  # baseline

    def _delayed_publish():
        time.sleep(1.0)
        _publish_artifact(session, seq=2, author="codex", peer="claude")

    t = threading.Thread(target=_delayed_publish, daemon=True)
    t.start()
    capsys.readouterr()  # drain bootstrap chatter
    rc = relay.cmd_wait(_wait_args(timeout=5, poll=1))
    t.join(timeout=2)
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out.endswith("002-codex-review.md")


# ---------------------------------------------------------------------------
# subprocess test for SIGINT handling
# ---------------------------------------------------------------------------


RELAY_BIN = (
    Path(__file__).resolve().parent.parent
    / "skills" / "agent-relay" / "bin" / "relay"
)


def test_wait_returns_130_on_sigint(monkeypatch, tmp_path):
    """SIGINT during wait → exit 130 (128 + SIGINT)."""
    session = _bootstrap(monkeypatch, tmp_path)
    _publish_artifact(session, seq=1, author="claude", peer="codex")
    env = os.environ.copy()
    env.update({
        "RELAY_ROLE": "remote",
        "RELAY_AUTHOR": "claude",
        "RELAY_PEER": "codex",
        "RELAY_SHARED_ROOT": str(session.parent),
        "RELAY_WAIT_TIMEOUT": "60",
        "RELAY_WAIT_POLL_INTERVAL": "1",
    })
    # Drop unrelated RELAY_ vars that may pollute (project, etc.)
    for k in list(env):
        if k.startswith("RELAY_") and k not in {
            "RELAY_ROLE", "RELAY_AUTHOR", "RELAY_PEER",
            "RELAY_SHARED_ROOT", "RELAY_WAIT_TIMEOUT", "RELAY_WAIT_POLL_INTERVAL",
        }:
            del env[k]
    proc = subprocess.Popen(
        [sys.executable, str(RELAY_BIN), "wait"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(1.5)  # let it enter the poll loop
    proc.send_signal(signal.SIGINT)
    try:
        out, err = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("wait did not exit within 5s of SIGINT")
    assert proc.returncode == 130, f"stdout={out!r} stderr={err!r}"
