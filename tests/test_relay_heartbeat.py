"""relay heartbeat — owner-tied liveness daemon (Stage 2)."""

import json
import os
import signal
import subprocess
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


def _bootstrap(monkeypatch, tmp_path, *, author="claude", peer="codex"):
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


def _claim_draft(session):
    rc = relay.cmd_claim(type("A", (), {
        "kind": "plan", "in_reply_to": None, "project": None, "session_id": None,
    })())
    assert rc == 0
    draft = session / ".draft" / "001-claude-plan.md"
    return draft


def _hb_args(**kw):
    base = {
        "draft": None, "owner_kind": None, "owner_pid": None,
        "owner_pidfile": None, "owner_renewal_file": None,
        "interval": None, "force": False,
        "project": None, "session_id": None,
    }
    base.update(kw)
    return type("A", (), base)()


def _stop_active_heartbeat(session, author="claude"):
    """Best-effort: kill any heartbeat daemon for an author at test teardown."""
    pidfile = relay._heartbeat_pidfile_path(session, author)
    if pidfile.exists():
        try:
            pid = int(pidfile.read_text().strip())
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
            pidfile.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_claim_does_not_spawn_heartbeat(monkeypatch, tmp_path, capsys):
    """Codex seq 4 invariant: relay claim itself MUST NOT spawn a heartbeat
    daemon. The skill explicitly invokes `relay heartbeat start`."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    draft = _claim_draft(session)
    capsys.readouterr()
    assert not relay._heartbeat_sidecar_path(draft).exists()
    assert not relay._heartbeat_pidfile_path(session, "claude").exists()


def test_heartbeat_start_refuses_unknown_owner_kind(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    draft = _claim_draft(session)
    capsys.readouterr()
    rc = relay.cmd_heartbeat_start(_hb_args(draft=str(draft), owner_kind="bogus", interval=1))
    assert rc == 2
    err = capsys.readouterr().err
    assert "owner-kind" in err.lower() or "owner_kind" in err.lower() or "bogus" in err


def test_heartbeat_start_refuses_missing_owner_pid_for_tool_process(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    draft = _claim_draft(session)
    capsys.readouterr()
    rc = relay.cmd_heartbeat_start(_hb_args(draft=str(draft), owner_kind="tool-process", interval=1))
    assert rc == 2
    err = capsys.readouterr().err
    assert "owner-pid" in err.lower() or "owner_pid" in err.lower()


def test_heartbeat_start_owner_kind_none_succeeds_and_stop(monkeypatch, tmp_path, capsys):
    """owner_kind=none is the explicit timeout-only mode."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    draft = _claim_draft(session)
    capsys.readouterr()
    try:
        rc = relay.cmd_heartbeat_start(_hb_args(draft=str(draft), owner_kind="none", interval=1))
        assert rc == 0
        out = capsys.readouterr().out.strip()
        daemon_pid = int(out)
        assert daemon_pid > 0
        sidecar = relay._heartbeat_sidecar_path(draft)
        pidfile = relay._heartbeat_pidfile_path(session, "claude")
        # Give daemon time to write pidfile.
        for _ in range(20):
            if pidfile.exists():
                break
            time.sleep(0.1)
        assert sidecar.exists()
        assert pidfile.exists()
        assert int(pidfile.read_text().strip()) == daemon_pid
        # daemon should still be alive
        os.kill(daemon_pid, 0)
    finally:
        rc_stop = relay.cmd_heartbeat_stop(_hb_args(draft=str(draft), force=True))
    assert rc_stop == 0
    assert not relay._heartbeat_sidecar_path(draft).exists()
    assert not relay._heartbeat_pidfile_path(session, "claude").exists()
    # daemon should now be dead
    time.sleep(0.2)
    with pytest.raises(OSError):
        os.kill(daemon_pid, 0)


def test_heartbeat_stops_when_owner_dies(monkeypatch, tmp_path, capsys):
    """tool-process owner: SIGKILL the owner; daemon should exit on its next iteration."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    draft = _claim_draft(session)
    capsys.readouterr()
    # Spawn a long-lived sleep subprocess as owner
    owner = subprocess.Popen(["sleep", "60"])
    try:
        rc = relay.cmd_heartbeat_start(_hb_args(
            draft=str(draft), owner_kind="tool-process", owner_pid=owner.pid, interval=1,
        ))
        assert rc == 0
        daemon_pid = int(capsys.readouterr().out.strip())
        # Wait a moment so daemon writes pidfile
        time.sleep(0.5)
        # Kill the owner
        owner.kill()
        owner.wait(timeout=2)
        # Within ~3 intervals the daemon should exit
        deadline = time.monotonic() + 5
        died = False
        while time.monotonic() < deadline:
            try:
                os.kill(daemon_pid, 0)
            except OSError:
                died = True
                break
            time.sleep(0.2)
        assert died, "daemon did not self-stop after owner died"
    finally:
        if owner.poll() is None:
            owner.kill()
        _stop_active_heartbeat(session)


def test_on_entry_gc_cleans_orphan_pidfile(monkeypatch, tmp_path, capsys):
    """If pidfile points at a dead pid, GC should remove pidfile + sidecars on next subcommand."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    draft = _claim_draft(session)
    capsys.readouterr()
    # Fake an orphan: write a pid that's guaranteed dead (use a tiny short-lived child).
    proc = subprocess.Popen(["true"])
    proc.wait()
    dead_pid = proc.pid
    pidfile = relay._heartbeat_pidfile_path(session, "claude")
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(f"{dead_pid}\n")
    sidecar = relay._heartbeat_sidecar_path(draft)
    sidecar.write_text("{}\n")
    assert pidfile.exists() and sidecar.exists()
    # Run status — its on-entry GC should clean these up.
    relay.cmd_status(type("A", (), {
        "project": None, "session_id": None, "last": 0, "json": False,
    })())
    capsys.readouterr()
    assert not pidfile.exists()
    assert not sidecar.exists()


def test_publish_stops_heartbeat_best_effort(monkeypatch, tmp_path, capsys):
    """cmd_publish success kills the running daemon + removes its files."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    draft = _claim_draft(session)
    capsys.readouterr()
    # Fill draft with real body
    fm, _ = relay.parse_frontmatter(draft.read_text())
    fm["prompt_for_next"] = "do the thing\n"
    draft.write_text(relay.dump_frontmatter(fm, "\nbody.\n"))

    rc = relay.cmd_heartbeat_start(_hb_args(draft=str(draft), owner_kind="none", interval=1))
    assert rc == 0
    daemon_pid = int(capsys.readouterr().out.strip())
    pidfile = relay._heartbeat_pidfile_path(session, "claude")
    sidecar = relay._heartbeat_sidecar_path(draft)
    # Wait for pidfile
    for _ in range(20):
        if pidfile.exists():
            break
        time.sleep(0.1)
    assert pidfile.exists() and sidecar.exists()

    rc_pub = relay.cmd_publish(type("A", (), {
        "draft_path": str(draft), "status": None, "force": False, "force_reason": None,
        "project": None, "session_id": None,
    })())
    assert rc_pub == 0
    capsys.readouterr()
    # Heartbeat should be gone
    assert not pidfile.exists()
    assert not sidecar.exists()
    time.sleep(0.3)
    with pytest.raises(OSError):
        os.kill(daemon_pid, 0)


def test_publish_validation_failure_leaves_heartbeat_running(monkeypatch, tmp_path, capsys):
    """If publish rejects the draft (e.g. TODO: placeholder), heartbeat should
    survive so peer doesn't see a false-dead window during fix-and-retry."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    draft = _claim_draft(session)
    capsys.readouterr()
    # Leave the placeholder TODO in place to force validation failure.
    rc = relay.cmd_heartbeat_start(_hb_args(draft=str(draft), owner_kind="none", interval=1))
    assert rc == 0
    daemon_pid = int(capsys.readouterr().out.strip())
    pidfile = relay._heartbeat_pidfile_path(session, "claude")
    sidecar = relay._heartbeat_sidecar_path(draft)
    for _ in range(20):
        if pidfile.exists():
            break
        time.sleep(0.1)
    assert pidfile.exists() and sidecar.exists()

    rc_pub = relay.cmd_publish(type("A", (), {
        "draft_path": str(draft), "status": None, "force": False, "force_reason": None,
        "project": None, "session_id": None,
    })())
    assert rc_pub == 2
    capsys.readouterr()
    # Heartbeat must still be alive
    try:
        os.kill(daemon_pid, 0)  # still alive
        assert pidfile.exists()
        assert sidecar.exists()
    finally:
        relay.cmd_heartbeat_stop(_hb_args(draft=str(draft), force=True))


def test_recovery_lock_o_excl_acquire_and_release(monkeypatch, tmp_path, capsys):
    """Acquire creates lockfile; second acquire fails; release removes it."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    assert relay._acquire_recovery_lock(session, "claude") is True
    lock = relay._recovery_lock_path(session, "claude")
    assert lock.exists()
    # Content should include pid + author
    payload = json.loads(lock.read_text().strip())
    assert payload["author"] == "claude"
    assert payload["pid"] == os.getpid()
    # Second acquire is denied
    assert relay._acquire_recovery_lock(session, "claude") is False
    relay._release_recovery_lock(session, "claude")
    assert not lock.exists()
    # After release, fresh acquire works again
    assert relay._acquire_recovery_lock(session, "claude") is True
    relay._release_recovery_lock(session, "claude")


# ---------------------------------------------------------------------------
# Stage 2/3 review fixes — codex seq 2 findings
# ---------------------------------------------------------------------------


def test_heartbeat_start_refuses_draft_author_mismatch(monkeypatch, tmp_path, capsys):
    """M2: --draft NNN-<someone-else>-<kind>.md must be refused even if file exists."""
    session = _bootstrap(monkeypatch, tmp_path, author="claude", peer="codex")
    capsys.readouterr()
    # Manually plant a draft whose filename author is `codex` (not us).
    draft_dir = session / ".draft"
    draft_dir.mkdir(exist_ok=True)
    imposter = draft_dir / "001-codex-plan.md"
    imposter.write_text("body\n")
    rc = relay.cmd_heartbeat_start(_hb_args(
        draft=str(imposter), owner_kind="none", interval=1,
    ))
    assert rc == 2
    err = capsys.readouterr().err
    assert "author" in err.lower()


def test_heartbeat_start_renewal_file_ignores_caller_path(monkeypatch, tmp_path, capsys):
    """M1: even if args carry an owner_renewal_file, start derives the scoped path."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    draft = _claim_draft(session)
    capsys.readouterr()
    bogus = tmp_path / "bogus-renewal-file"
    bogus.touch()
    try:
        rc = relay.cmd_heartbeat_start(type("A", (), {
            "draft": str(draft), "owner_kind": "renewal-file",
            "owner_pid": None, "owner_pidfile": None,
            "owner_renewal_file": str(bogus),  # caller tries to inject; impl must ignore
            "interval": 1, "force": False,
            "project": None, "session_id": None,
        })())
        assert rc == 0
        capsys.readouterr()
        sidecar = relay._heartbeat_sidecar_path(draft)
        data = json.loads(sidecar.read_text())
        # The recorded path must be the scoped one, not the bogus one.
        assert data["owner_renewal_file"] != str(bogus)
        env = relay.load_env()
        derived = relay._renewal_path_for_draft(draft, session, env)
        assert data["owner_renewal_file"] == str(derived)
    finally:
        relay.cmd_heartbeat_stop(_hb_args(draft=str(draft), force=True))


def test_owner_kind_tmux_pane_no_longer_accepted(monkeypatch, tmp_path, capsys):
    """M5: tmux-pane kind was removed from taxonomy; start should refuse it."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    draft = _claim_draft(session)
    capsys.readouterr()
    rc = relay.cmd_heartbeat_start(_hb_args(
        draft=str(draft), owner_kind="tmux-pane", owner_pid=os.getpid(), interval=1,
    ))
    assert rc == 2
    err = capsys.readouterr().err
    assert "tmux-pane" in err or "owner_kind" in err.lower() or "owner-kind" in err.lower()


def test_recovery_lock_gc_cleans_dead_pid(monkeypatch, tmp_path, capsys):
    """M4: on-entry GC must remove recovery locks whose recorded pid is dead."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    lock = relay._recovery_lock_path(session, "claude")
    lock.parent.mkdir(parents=True, exist_ok=True)
    # Write a lock that points to a dead pid.
    proc = subprocess.Popen(["true"])
    proc.wait()
    dead_pid = proc.pid
    lock.write_text(json.dumps({"pid": dead_pid, "author": "claude", "started_at": "x"}) + "\n")
    assert lock.exists()
    relay._gc_recovery_locks(session, "claude")
    assert not lock.exists()


def test_heartbeat_gc_purges_when_owner_dead(monkeypatch, tmp_path, capsys):
    """M4: GC must kill daemon + clean files when owner is dead even though daemon is alive."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    draft = _claim_draft(session)
    capsys.readouterr()
    owner = subprocess.Popen(["sleep", "60"])
    try:
        rc = relay.cmd_heartbeat_start(_hb_args(
            draft=str(draft), owner_kind="tool-process", owner_pid=owner.pid,
            interval=60,  # long interval so daemon won't self-stop quickly
        ))
        assert rc == 0
        daemon_pid = int(capsys.readouterr().out.strip())
        pidfile = relay._heartbeat_pidfile_path(session, "claude")
        sidecar = relay._heartbeat_sidecar_path(draft)
        for _ in range(20):
            if pidfile.exists():
                break
            time.sleep(0.1)
        assert pidfile.exists() and sidecar.exists()
        # Kill owner; daemon still alive (its 60s sleep hasn't elapsed).
        owner.kill()
        owner.wait(timeout=2)
        # GC should detect owner-dead and purge.
        relay._gc_heartbeat_orphans(session, "claude")
        assert not pidfile.exists()
        assert not sidecar.exists()
        time.sleep(0.2)
        with pytest.raises(OSError):
            os.kill(daemon_pid, 0)
    finally:
        if owner.poll() is None:
            owner.kill()
        _stop_active_heartbeat(session)
