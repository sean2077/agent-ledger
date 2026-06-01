"""Forward-read coverage for the canonical 1.0 ledger fixture."""

import json
import os
import shutil
from pathlib import Path

import relay


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "ledger-v1_0"
ACTIVE_PAIR = "20260201-frozen-pair"
ARCHIVED_PAIR = "20260115-archived-topic"
FIXTURE_AGENT_SESSION_ID = "fixture-claude-session"


def _isolated_fixture(monkeypatch, tmp_path: Path) -> Path:
    shared = tmp_path / ".shared"
    shutil.copytree(FIXTURE_ROOT, shared)
    for k in list(os.environ):
        if k.startswith("RELAY_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RELAY_SYNC", "none")
    monkeypatch.setenv("RELAY_AUTHOR", "claude")
    monkeypatch.setenv("RELAY_AGENT_SESSION_ID", FIXTURE_AGENT_SESSION_ID)
    monkeypatch.setenv("RELAY_SHARED_ROOT", str(shared))
    return shared


def _status_args(**kw):
    base = {
        "project": None,
        "pair_id": ACTIVE_PAIR,
        "require_binding": False,
        "last": 0,
        "json": True,
    }
    base.update(kw)
    return type("A", (), base)()


def _wait_args(**kw):
    base = {
        "project": None,
        "pair_id": ACTIVE_PAIR,
        "require_binding": False,
        "timeout": 1,
        "poll": 1,
        "no_timeout": False,
    }
    base.update(kw)
    return type("A", (), base)()


def _doctor_args(**kw):
    base = {"json": True, "fix": False, "older_than": None}
    base.update(kw)
    return type("A", (), base)()


def _pairs_args(**kw):
    base = {"json": True, "archived": False}
    base.update(kw)
    return type("A", (), base)()


def test_v1_fixture_status_reads_pair(monkeypatch, tmp_path, capsys):
    _isolated_fixture(monkeypatch, tmp_path)

    rc = relay.cmd_status(_status_args())
    data = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert data["session"]["session_id"] == ACTIVE_PAIR
    assert data["session"]["participants"] == ["claude", "codex"]
    assert data["is_active"] is True
    assert [a["seq"] for a in data["published"]] == [1, 2]
    assert data["published"][-1]["path"] == "002-codex-review.md"
    assert data["next_seq"] == 3


def test_v1_fixture_wait_entry_returns_published(monkeypatch, tmp_path, capsys):
    _isolated_fixture(monkeypatch, tmp_path)

    rc = relay.cmd_wait(_wait_args())
    out = capsys.readouterr().out.strip()

    assert rc == 0
    assert out.endswith(f"{ACTIVE_PAIR}/002-codex-review.md")


def test_v1_fixture_doctor_parses_no_crash(monkeypatch, tmp_path, capsys):
    _isolated_fixture(monkeypatch, tmp_path)

    rc = relay.cmd_doctor(_doctor_args())
    report = json.loads(capsys.readouterr().out)

    assert rc == 1  # the frozen binding is intentionally stale/recoverable.
    assert report["findings_count"] == 1
    assert report["stale_bindings"] == ["claude:fixture"]
    assert [s["session_id"] for s in report["sessions"]] == [ACTIVE_PAIR]
    assert ARCHIVED_PAIR not in [s["session_id"] for s in report["sessions"]]


def test_v1_fixture_pairs_list_live_and_archived(monkeypatch, tmp_path, capsys):
    _isolated_fixture(monkeypatch, tmp_path)

    assert relay.cmd_pairs_list(_pairs_args(archived=False)) == 0
    live = json.loads(capsys.readouterr().out)
    live_by_id = {p["session_id"]: p for p in live["pairs"]}
    assert list(live_by_id) == [ACTIVE_PAIR]
    assert live_by_id[ACTIVE_PAIR]["category"] == "active"
    assert live_by_id[ACTIVE_PAIR]["latest"]["seq"] == 2

    assert relay.cmd_pairs_list(_pairs_args(archived=True)) == 0
    archived = json.loads(capsys.readouterr().out)
    archived_by_id = {p["session_id"]: p for p in archived["pairs"]}
    assert archived["archived"] is True
    assert list(archived_by_id) == [ARCHIVED_PAIR]
    assert archived_by_id[ARCHIVED_PAIR]["category"] == "closed"


def test_v1_fixture_frozen_bytes_intact():
    for md in sorted(FIXTURE_ROOT.rglob("[0-9][0-9][0-9]-*.md")):
        sha_path = md.with_name(md.name + ".sha256")
        ready_path = md.with_suffix("")
        ready_path = ready_path.with_name(ready_path.name + ".ready")

        assert sha_path.is_file(), f"missing sha sidecar for {md}"
        assert ready_path.is_file(), f"missing ready sidecar for {md}"
        expected = sha_path.read_text().split()[0]
        assert relay.sha256_of_file(md) == expected, f"sha mismatch for {md}"
