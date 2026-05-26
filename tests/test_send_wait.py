import threading
import time
from pathlib import Path

import pytest

from awb import ledger, send, wait
from awb.ssh_tmux import ProcResult


def _runner(script):
    """Returns a runner that pops responses from `script` for each call."""
    idx = {"i": 0}
    def r(argv):
        i = idx["i"]
        idx["i"] += 1
        if i >= len(script):
            return ProcResult(0, "", "")
        return script[i]
    r.calls = []
    def wrapper(argv):
        wrapper.calls.append(list(argv))
        return r(argv)
    wrapper.calls = []
    return wrapper


def _seed_session(tmp_path: Path) -> ledger.Session:
    return ledger.create_session(
        ledger_root=tmp_path, project="p", slug="x", title="t",
        target_agents=["claude"],
        remote=ledger.Remote(
            ssh_host="public-box", tmux_session="awb-claude",
            remote_root="/remote/ledger",
        ),
    )


# --- send ----------------------------------------------------------------


def test_send_happy_path(tmp_path):
    s = _seed_session(tmp_path)
    (s.path / "r1" / "prompts" / "claude.md").write_text("hi")
    # Order: remote_exists ok, tmux has-session ok, send-keys ok, Enter ok
    script = [ProcResult(0, "", "")] * 4
    r = _runner(script)
    send.send(s, 1, "claude", runner=r)
    assert s.round(1).target("claude").state == "sent"
    # 4 ssh calls were made
    assert len(r.calls) == 4


def test_send_aborts_when_local_prompt_missing(tmp_path):
    s = _seed_session(tmp_path)
    with pytest.raises(send.SendError, match="local prompt missing"):
        send.send(s, 1, "claude", runner=_runner([]))


def test_send_aborts_when_remote_not_visible(tmp_path):
    s = _seed_session(tmp_path)
    (s.path / "r1" / "prompts" / "claude.md").write_text("hi")
    r = _runner([ProcResult(1, "", "no such file")])  # remote_exists -> fail
    with pytest.raises(send.SendError, match="remote prompt not visible"):
        send.send(s, 1, "claude", runner=r)


def test_send_aborts_when_no_remote_config(tmp_path):
    s = ledger.create_session(tmp_path, "p", "x", "t", ["claude"])
    (s.path / "r1" / "prompts" / "claude.md").write_text("hi")
    with pytest.raises(send.SendError, match="ssh_host"):
        send.send(s, 1, "claude", runner=_runner([]))


def test_send_aborts_when_tmux_session_missing(tmp_path):
    s = _seed_session(tmp_path)
    (s.path / "r1" / "prompts" / "claude.md").write_text("hi")
    script = [ProcResult(0, "", ""), ProcResult(1, "", "no session")]
    with pytest.raises(send.SendError, match="tmux session"):
        send.send(s, 1, "claude", runner=_runner(script))


# --- wait ----------------------------------------------------------------


def _good_reply() -> str:
    return "Verdict: approve\n\nContent here.\n"


def test_verify_and_publish_happy(tmp_path):
    s = _seed_session(tmp_path)
    p = wait.reply_paths(s.path, 1, "claude")
    p["md"].write_text(_good_reply())
    p["submitted"].touch()
    res = wait.verify_and_publish(s.path, 1, "claude")
    assert res["ready"].exists()
    assert res["sha256"].read_text().strip().endswith("claude.md")
    # submitted is cleaned up
    assert not p["submitted"].exists()


def test_verify_archives_malformed(tmp_path):
    s = _seed_session(tmp_path)
    p = wait.reply_paths(s.path, 1, "claude")
    p["md"].write_text("nothing useful")
    p["submitted"].touch()
    with pytest.raises(wait.FormatInvalid):
        wait.verify_and_publish(s.path, 1, "claude")
    # md was moved to archive
    assert not p["md"].exists()
    arch = list((s.path / "archive").glob("r1-claude-malformed-*.md"))
    assert len(arch) == 1


def test_wait_for_picks_up_existing_ready(tmp_path):
    s = _seed_session(tmp_path)
    p = wait.reply_paths(s.path, 1, "claude")
    p["md"].write_text(_good_reply())
    p["ready"].touch()
    res = wait.wait_for(s, 1, "claude", timeout_s=1, poll_s=0.01)
    assert res["ready"].exists()
    assert s.round(1).target("claude").state == "reply_present"


def test_wait_for_publishes_when_submitted_appears(tmp_path):
    s = _seed_session(tmp_path)
    p = wait.reply_paths(s.path, 1, "claude")

    def producer():
        time.sleep(0.1)
        p["md"].write_text(_good_reply())
        p["submitted"].touch()

    threading.Thread(target=producer, daemon=True).start()
    res = wait.wait_for(s, 1, "claude", timeout_s=5, poll_s=0.05)
    assert res["ready"].exists()
    assert s.round(1).target("claude").state == "reply_present"


def test_wait_for_times_out(tmp_path):
    s = _seed_session(tmp_path)
    with pytest.raises(wait.WaitError, match="timed out"):
        wait.wait_for(s, 1, "claude", timeout_s=0, poll_s=0.01)
    assert s.round(1).target("claude").state == "timed_out"
