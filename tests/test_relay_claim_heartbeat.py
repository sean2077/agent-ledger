"""v0.15: `relay claim` auto-starts a renewal-file heartbeat for the draft,
with guardrails (rollback on genuine failure, --no-heartbeat / env escape
hatch). These tests stub cmd_heartbeat_start so no real daemon is spawned."""

import os
import subprocess

import relay


def _args(**kw):
    base = {"kind": "plan", "in_reply_to": None, "corrects": None,
            "project": None, "pair_id": None, "no_heartbeat": False}
    base.update(kw)
    return type("A", (), base)()


def _bootstrap(monkeypatch, tmp_path):
    repo = tmp_path / "proj"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    monkeypatch.chdir(repo)
    for k in list(os.environ):
        if k.startswith("RELAY_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "claude-sess")  # author=claude
    assert relay.cmd_bootstrap(
        type("A", (), {"topic": "t", "title": None, "peer": None, "force": False})()
    ) == 0
    return repo


def _stub_heartbeat(monkeypatch, rc):
    calls = []

    def stub(hb_args):
        calls.append(hb_args)
        return rc

    monkeypatch.setattr(relay, "cmd_heartbeat_start", stub)
    return calls


def test_claim_autostarts_renewal_heartbeat(monkeypatch, tmp_path, capsys):
    repo = _bootstrap(monkeypatch, tmp_path)
    monkeypatch.setattr(relay, "_CLAIM_HEARTBEAT_AUTOSTART", True)  # enable auto-start
    calls = _stub_heartbeat(monkeypatch, 0)
    capsys.readouterr()
    rc = relay.cmd_claim(_args())
    out = capsys.readouterr().out
    assert rc == 0
    draft = out.strip()
    assert draft.endswith(".md") and "/.draft/" in draft
    assert os.path.exists(draft)  # draft kept
    # heartbeat auto-started exactly once, renewal-file, for this draft
    assert len(calls) == 1
    assert calls[0].owner_kind == "renewal-file"
    assert calls[0].draft == draft
    # claim's stdout is ONLY the draft path (heartbeat's stdout was captured)
    assert out.strip() == draft


def test_claim_rolls_back_draft_on_heartbeat_failure(monkeypatch, tmp_path, capsys):
    repo = _bootstrap(monkeypatch, tmp_path)
    monkeypatch.setattr(relay, "_CLAIM_HEARTBEAT_AUTOSTART", True)
    calls = _stub_heartbeat(monkeypatch, 2)  # genuine failure
    capsys.readouterr()
    rc = relay.cmd_claim(_args())
    cap = capsys.readouterr()
    assert rc == 2
    assert "rolled back" in cap.err
    # No draft left behind, and nothing claimed-looking on stdout.
    session = next((repo / ".shared").glob("20*/"))
    drafts = list((session / ".draft").glob("*.md"))
    assert drafts == []
    assert ".md" not in cap.out


def test_claim_already_running_heartbeat_rolls_back(monkeypatch, tmp_path, capsys):
    """rc 3 (a per-author heartbeat already running) must NOT keep the new draft:
    that daemon only refreshes the first draft's sidecar, so the new draft would
    have no liveness coverage. Claim fails closed and rolls back (codex seq 9)."""
    repo = _bootstrap(monkeypatch, tmp_path)
    monkeypatch.setattr(relay, "_CLAIM_HEARTBEAT_AUTOSTART", True)
    _stub_heartbeat(monkeypatch, 3)  # already running
    capsys.readouterr()
    rc = relay.cmd_claim(_args())
    cap = capsys.readouterr()
    assert rc == 2
    assert "already running" in cap.err
    session = next((repo / ".shared").glob("20*/"))
    assert list((session / ".draft").glob("*.md")) == []  # rolled back


def test_claim_second_draft_rolls_back_when_first_heartbeat_live(monkeypatch, tmp_path, capsys):
    """Multi-draft regression (codex seq 9): with a live heartbeat from a first
    draft, claiming a second must roll it back rather than leave an uncovered
    draft. Modelled with a counting stub: first start succeeds (0), second
    reports already-running (3)."""
    repo = _bootstrap(monkeypatch, tmp_path)
    monkeypatch.setattr(relay, "_CLAIM_HEARTBEAT_AUTOSTART", True)
    seq = {"n": 0}

    def stub(hb_args):
        seq["n"] += 1
        return 0 if seq["n"] == 1 else 3  # first OK, second already-running

    monkeypatch.setattr(relay, "cmd_heartbeat_start", stub)
    capsys.readouterr()
    rc1 = relay.cmd_claim(_args(kind="plan"))
    draft1 = capsys.readouterr().out.strip()
    assert rc1 == 0 and os.path.exists(draft1)
    rc2 = relay.cmd_claim(_args(kind="review"))
    cap = capsys.readouterr()
    assert rc2 == 2  # second claim rolled back
    session = next((repo / ".shared").glob("20*/"))
    drafts = sorted((session / ".draft").glob("*.md"))
    assert len(drafts) == 1 and drafts[0].name == os.path.basename(draft1)  # only the covered one remains


def test_claim_no_heartbeat_flag_skips_autostart(monkeypatch, tmp_path, capsys):
    repo = _bootstrap(monkeypatch, tmp_path)
    monkeypatch.setattr(relay, "_CLAIM_HEARTBEAT_AUTOSTART", True)
    calls = _stub_heartbeat(monkeypatch, 0)
    capsys.readouterr()
    rc = relay.cmd_claim(_args(no_heartbeat=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert os.path.exists(out.strip())
    assert calls == []  # heartbeat never invoked


def test_claim_env_disables_autostart(monkeypatch, tmp_path, capsys):
    repo = _bootstrap(monkeypatch, tmp_path)
    # Module seam ON, but the RELAY_CLAIM_NO_HEARTBEAT env must still disable it
    # (the real-user escape hatch).
    monkeypatch.setattr(relay, "_CLAIM_HEARTBEAT_AUTOSTART", True)
    monkeypatch.setenv("RELAY_CLAIM_NO_HEARTBEAT", "1")
    calls = _stub_heartbeat(monkeypatch, 0)
    capsys.readouterr()
    rc = relay.cmd_claim(_args())
    assert rc == 0
    assert calls == []
