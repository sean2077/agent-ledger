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
        RELAY_SYNC="none" if author == "claude" else "rsync",
        RELAY_AUTHOR=author,
        RELAY_PEER=peer,
        RELAY_SHARED_ROOT=str(shared),
        RELAY_REMOTE_SSH="x@y",
        RELAY_REMOTE_PATH="/r",
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
        RELAY_SYNC="none",
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


def _write_peer_heartbeat(session: Path, *, peer: str, draft_name: str,
                            owner_kind: str, mtime_offset: float = 0.0):
    """Plant a heartbeat sidecar in peer's .draft/ scope for wait to read."""
    import json as _json
    draft_dir = session / ".draft"
    draft_dir.mkdir(exist_ok=True)
    # Also need a stub .md so _heartbeat_drafts_for_author/_peer_heartbeat_sidecars
    # can find it. peer's draft name format: NNN-<peer>-<kind>.md
    md = draft_dir / draft_name
    if not md.exists():
        md.write_text("stub\n")
    sidecar = md.with_name(md.name + ".heartbeat")
    sidecar.write_text(_json.dumps({
        "heartbeat_pid": None,
        "owner_pid": None,
        "owner_kind": owner_kind,
        "owner_pidfile": None,
        "owner_renewal_file": None,
        "host": "x", "author": peer,
        "draft": draft_name,
        "started_at": "x", "last_beat": "x",
    }) + "\n")
    if mtime_offset:
        target = time.time() + mtime_offset
        os.utime(sidecar, (target, target))
    return sidecar


def test_wait_exit_11_only_on_stale_renewal_file_heartbeat(monkeypatch, tmp_path, capsys):
    """M3 regression: exit 11 must fire only when a stale peer heartbeat has
    owner_kind=renewal-file. A stale non-renewal sidecar must not trigger it."""
    session = _bootstrap(monkeypatch, tmp_path)
    _publish_artifact(session, seq=1, author="claude", peer="codex")
    monkeypatch.setenv("RELAY_RENEWAL_STALE_THRESHOLD", "2")
    # Peer (codex) has a stale 'none' kind sidecar — must NOT trigger exit 11.
    _write_peer_heartbeat(session, peer="codex",
        draft_name="002-codex-review.md", owner_kind="none", mtime_offset=-30)
    capsys.readouterr()
    rc = relay.cmd_wait(_wait_args(timeout=2, poll=1))
    # Must time out (10), not crash-signal (11), because no renewal-file kind exists.
    assert rc == 10


def test_wait_exit_11_fires_on_stale_renewal_file_heartbeat(monkeypatch, tmp_path, capsys):
    """M3 regression: a stale renewal-file kind sidecar must trigger exit 11.

    The threshold floor in code is 60s (cannot go lower per spec). Backdate the
    sidecar > 60s old so the stale-threshold comparison fires regardless of
    the RELAY_RENEWAL_STALE_THRESHOLD env override."""
    session = _bootstrap(monkeypatch, tmp_path)
    _publish_artifact(session, seq=1, author="claude", peer="codex")
    monkeypatch.setenv("RELAY_WAIT_POLL_INTERVAL", "1")
    _write_peer_heartbeat(session, peer="codex",
        draft_name="002-codex-review.md", owner_kind="renewal-file", mtime_offset=-3700)
    capsys.readouterr()
    rc = relay.cmd_wait(_wait_args(timeout=2, poll=1))
    assert rc == 11


def test_wait_detects_renewal_heartbeat_appearing_after_wait_started(
    monkeypatch, tmp_path, capsys
):
    """Codex seq 4 regression: cmd_wait must DYNAMICALLY detect peer renewal-file
    heartbeat that appears AFTER wait entry. In the normal auto-loop flow,
    waiter publishes and enters wait BEFORE peer starts its own heartbeat."""
    session = _bootstrap(monkeypatch, tmp_path)
    _publish_artifact(session, seq=1, author="claude", peer="codex")
    monkeypatch.setenv("RELAY_WAIT_POLL_INTERVAL", "1")
    capsys.readouterr()

    def _delayed_stale_heartbeat():
        time.sleep(0.5)
        _write_peer_heartbeat(session, peer="codex",
            draft_name="002-codex-review.md", owner_kind="renewal-file",
            mtime_offset=-3700)  # backdated past default threshold (3600s)

    t = threading.Thread(target=_delayed_stale_heartbeat, daemon=True)
    t.start()
    # Without dynamic detection, wait would exit 10 at the nominal timeout
    # because has_renewal_file_peer was False at entry. With dynamic detection,
    # exit 11 fires within ~2 poll iterations after the sidecar appears.
    rc = relay.cmd_wait(_wait_args(timeout=10, poll=1))
    t.join(timeout=2)
    assert rc == 11


def test_peer_heartbeat_helper_filters_non_renewal_kinds(monkeypatch, tmp_path):
    """M3 regression at the helper level: stale 'none' or 'tool-process' sidecars
    must be ignored by _peer_heartbeat_is_stale. Only owner_kind=renewal-file counts."""
    session = _bootstrap(monkeypatch, tmp_path)
    # Two stale sidecars in peer's .draft/: one 'none' kind, one 'tool-process' kind.
    # Neither should make the helper return True.
    _write_peer_heartbeat(session, peer="codex",
        draft_name="002-codex-review.md", owner_kind="none", mtime_offset=-300)
    _write_peer_heartbeat(session, peer="codex",
        draft_name="003-codex-fix.md", owner_kind="tool-process", mtime_offset=-300)
    assert relay._peer_heartbeat_is_stale(session, "codex", threshold=60) is False
    # Now add a stale renewal-file kind. Helper must return True.
    _write_peer_heartbeat(session, peer="codex",
        draft_name="004-codex-note.md", owner_kind="renewal-file", mtime_offset=-300)
    assert relay._peer_heartbeat_is_stale(session, "codex", threshold=60) is True


def test_peer_heartbeat_helper_fresh_renewal_not_stale(monkeypatch, tmp_path):
    """Direct helper test: a fresh renewal-file kind sidecar is not stale."""
    session = _bootstrap(monkeypatch, tmp_path)
    _write_peer_heartbeat(session, peer="codex",
        draft_name="002-codex-review.md", owner_kind="renewal-file", mtime_offset=0)
    assert relay._peer_heartbeat_is_stale(session, "codex", threshold=60) is False


def test_wait_returns_130_on_sigint(monkeypatch, tmp_path):
    """SIGINT during wait → exit 130 (128 + SIGINT).

    Uses a readiness sentinel (RELAY_WAIT_READY_SENTINEL) instead of a fixed
    sleep so the test does not race with subprocess startup under load. Also
    verifies the top-level KeyboardInterrupt guard in `__main__` works: even
    if SIGINT races startup before cmd_wait's internal handler runs, the
    process must exit 130 cleanly with no Python traceback.
    """
    session = _bootstrap(monkeypatch, tmp_path)
    _publish_artifact(session, seq=1, author="claude", peer="codex")
    ready_sentinel = tmp_path / "wait-ready"
    env = os.environ.copy()
    env.update({
        "RELAY_SYNC": "none",
        "RELAY_AUTHOR": "claude",
        "RELAY_PEER": "codex",
        "RELAY_SHARED_ROOT": str(session.parent),
        "RELAY_WAIT_TIMEOUT": "60",
        "RELAY_WAIT_POLL_INTERVAL": "1",
        "RELAY_WAIT_READY_SENTINEL": str(ready_sentinel),
    })
    # Drop unrelated RELAY_ vars that may pollute (project, etc.)
    keep = {"RELAY_SYNC", "RELAY_AUTHOR", "RELAY_PEER", "RELAY_SHARED_ROOT",
            "RELAY_WAIT_TIMEOUT", "RELAY_WAIT_POLL_INTERVAL",
            "RELAY_WAIT_READY_SENTINEL"}
    for k in list(env):
        if k.startswith("RELAY_") and k not in keep:
            del env[k]
    proc = subprocess.Popen(
        [sys.executable, str(RELAY_BIN), "wait"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Wait for cmd_wait to reach the poll loop (touches the sentinel).
    # Bound by 10s so a stuck subprocess can't hang the suite forever.
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if ready_sentinel.exists():
            break
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    if not ready_sentinel.exists() and proc.poll() is None:
        proc.kill()
        pytest.fail("cmd_wait never reached the poll loop (readiness sentinel never appeared)")
    proc.send_signal(signal.SIGINT)
    try:
        out, err = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("wait did not exit within 5s of SIGINT")
    assert proc.returncode == 130, f"stdout={out!r} stderr={err!r}"
    # The top-level guard means SIGINT must NOT leave a KeyboardInterrupt
    # traceback on stderr (the previous symptom under codex's environment).
    assert b"KeyboardInterrupt" not in err, (
        f"top-level SIGINT guard must suppress traceback; stderr={err!r}"
    )
