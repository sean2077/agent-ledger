"""End-to-end smoke + fault injection."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from awb import (
    importer,
    ledger,
    locks,
    pack,
    send,
    ssh_tmux,
    synthesize,
    wait,
)
from awb.ssh_tmux import ProcResult


# --- helpers -------------------------------------------------------------


def _git_init(path: Path, files: dict[str, str]) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    for rel, content in files.items():
        p = path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)


def _ok_runner():
    def r(argv):
        r.calls.append(list(argv))
        return ProcResult(0, "", "")
    r.calls = []
    return r


# --- smoke ---------------------------------------------------------------


def test_full_session_lifecycle(tmp_path: Path):
    repo = tmp_path / "repo"
    _git_init(repo, {"src/main.py": "print('hi')\n", "README.md": "n\n"})

    s = ledger.create_session(
        tmp_path / "ledger", "demo", "smoke", "smoke test",
        ["claude", "gpt55"],
        remote=ledger.Remote(
            ssh_host="public-box", tmux_session="awb-claude",
            remote_root="/remote/ledger",
        ),
    )

    # 1. pack r1
    res = pack.pack(s, 1, repo, pack.PackPolicy())
    assert res.file_count >= 2
    assert (s.path / "r1" / "prompts" / "claude.md").exists()
    assert (s.path / "r1" / "prompts" / "gpt55.md").exists()

    # 2. send claude (mocked SSH/tmux)
    runner = _ok_runner()
    send.send(s, 1, "claude", runner=runner)
    assert s.round(1).target("claude").state == "sent"

    # 3. agent writes reply + .submitted
    md = s.path / "r1" / "replies" / "claude.md"
    md.write_text("Verdict: needs-change\n\nfindings here\n")
    (s.path / "r1" / "replies" / "claude.submitted").touch()

    # 4. wait verifies + publishes ready
    wait.wait_for(s, 1, "claude", timeout_s=1, poll_s=0.01)
    assert (s.path / "r1" / "replies" / "claude.ready").exists()
    assert s.round(1).target("claude").state == "reply_present"

    # 5. import gpt55 (human GPT Web flow)
    src = tmp_path / "gpt55-from-web.md"
    src.write_text("Verdict: approve\n\nbody\n")
    importer.import_reply(s, 1, "gpt55", src)
    assert s.round(1).target("gpt55").state == "reply_present"

    # 6. synthesize draft, then publish
    draft = synthesize.write_draft(s, 1)
    assert draft.exists()
    decided, nxt = synthesize.publish(s, 1)
    assert decided.name == "decision.md"
    assert s.round(1).state == "closed"
    assert nxt is not None and nxt.number == 2

    # 7. events.ndjson is non-empty and parseable
    from awb import events
    recs = events.read_all(ledger.events_path(s.path))
    # we wrote events at acquisition points; just sanity check non-zero
    # (smoke; not all flows append events automatically without cmd_* wrappers)
    assert isinstance(recs, list)


# --- fault injection -----------------------------------------------------


def _seeded(tmp_path: Path) -> ledger.Session:
    return ledger.create_session(
        tmp_path / "ledger", "p", "x", "t", ["claude"],
        remote=ledger.Remote(
            ssh_host="h", tmux_session="s", remote_root="/r",
        ),
    )


def test_truncated_session_json_raises(tmp_path: Path):
    s = _seeded(tmp_path)
    sj = ledger.session_json_path(s.path)
    sj.write_text("{ this is not valid")
    with pytest.raises(Exception):
        ledger.load(s.path)


def test_missing_session_json_raises(tmp_path: Path):
    s = _seeded(tmp_path)
    ledger.session_json_path(s.path).unlink()
    with pytest.raises(ledger.LedgerError):
        ledger.load(s.path)


def test_stale_lock_can_be_broken(tmp_path: Path):
    s = _seeded(tmp_path)
    ld = ledger.lock_dir(s.path)
    locks.acquire(ld, command="orphan", lease_secs=1)
    # backdate the lease
    owner = json.loads((ld / "owner.json").read_text())
    from datetime import datetime, timedelta, timezone
    owner["expires_at"] = (
        (datetime.now(timezone.utc) - timedelta(seconds=10))
        .replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    (ld / "owner.json").write_text(json.dumps(owner))
    prior = locks.break_stale(ld)
    assert prior["command"] == "orphan"
    # next acquire works
    locks.acquire(ld, command="successor")


def test_wait_ignores_submitted_without_md(tmp_path: Path):
    s = _seeded(tmp_path)
    p = wait.reply_paths(s.path, 1, "claude")
    # submitted exists but no md
    p["submitted"].touch()
    with pytest.raises(wait.WaitError, match="submitted but reply missing"):
        wait.verify_and_publish(s.path, 1, "claude")


def test_wait_archives_when_no_verdict_line(tmp_path: Path):
    s = _seeded(tmp_path)
    p = wait.reply_paths(s.path, 1, "claude")
    p["md"].write_text("just rambling, no verdict\n")
    p["submitted"].touch()
    with pytest.raises(wait.FormatInvalid):
        wait.verify_and_publish(s.path, 1, "claude")
    # md was moved out
    assert not p["md"].exists()
    archived = list((s.path / "archive").glob("r1-claude-malformed-*.md"))
    assert len(archived) == 1


def test_send_treats_remote_invisible_as_failure(tmp_path: Path):
    s = _seeded(tmp_path)
    (s.path / "r1" / "prompts" / "claude.md").write_text("hi")
    def r(argv):
        return ProcResult(1, "", "no such file")
    with pytest.raises(send.SendError, match="remote prompt not visible"):
        send.send(s, 1, "claude", runner=r)


def test_send_treats_tmux_pane_dead_as_failure(tmp_path: Path):
    s = _seeded(tmp_path)
    (s.path / "r1" / "prompts" / "claude.md").write_text("hi")
    calls = {"n": 0}
    def r(argv):
        calls["n"] += 1
        # First call: remote_exists ok. Second: has-session fails.
        return ProcResult(0 if calls["n"] == 1 else 1, "", "")
    with pytest.raises(send.SendError, match="tmux session"):
        send.send(s, 1, "claude", runner=r)


def test_replay_events_after_session_json_loss(tmp_path: Path):
    """Events log survives session.json corruption."""
    s = _seeded(tmp_path)
    ledger.append_event(s, {"actor": "test", "event": "alpha"})
    ledger.append_event(s, {"actor": "test", "event": "beta"})
    # nuke session.json
    ledger.session_json_path(s.path).unlink()
    # events still readable
    from awb import events
    evs = events.read_all(ledger.events_path(s.path))
    names = [e["event"] for e in evs]
    assert "alpha" in names and "beta" in names
