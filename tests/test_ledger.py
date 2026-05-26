import json
from pathlib import Path

import pytest

from awb import ledger


def test_create_session_lays_out_files(tmp_path: Path):
    s = ledger.create_session(
        ledger_root=tmp_path, project="proj-a", slug="hello",
        title="hello world", target_agents=["claude", "gpt55"],
    )
    sp = s.path
    assert (sp / "session.json").is_file()
    assert (sp / "archive").is_dir()
    assert (sp / "logs").is_dir()
    assert (sp / "locks").is_dir()
    assert (sp / "r1" / "prompts").is_dir()
    assert (sp / "r1" / "replies").is_dir()
    assert (sp / "r1" / "context").is_dir()
    assert (sp / "latest").is_symlink()
    assert (sp / "latest").resolve(strict=False).name == "r1"


def test_create_session_id_format(tmp_path: Path):
    s = ledger.create_session(
        tmp_path, "p", "tag-x", "t", ["claude"],
        when="2026-05-26T09:00:00Z",
    )
    assert s.session_id == "20260526-tag-x"


def test_targets_default_pending_required(tmp_path: Path):
    s = ledger.create_session(
        tmp_path, "p", "x", "t", ["claude", "gpt55"],
    )
    r1 = s.round(1)
    assert {t.agent for t in r1.targets} == {"claude", "gpt55"}
    assert all(t.required and t.state == "pending" for t in r1.targets)


def test_load_round_trip(tmp_path: Path):
    ledger.create_session(tmp_path, "p", "x", "t", ["claude"])
    sp = tmp_path / "p" / "20260526-x"
    loaded = ledger.load(sp)
    assert loaded.project == "p"
    assert loaded.round(1).target("claude").state == "pending"


def test_load_legacy_agents_field(tmp_path: Path):
    sp = tmp_path / "p" / "20260101-legacy"
    sp.mkdir(parents=True)
    (sp / "session.json").write_text(json.dumps({
        "schema_version": 1, "project": "p", "session_id": "20260101-legacy",
        "title": "t", "state": "active", "current_round": 1,
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        "rounds": {"1": {"state": "closed", "agents": ["claude", "gpt55"]}},
    }))
    loaded = ledger.load(sp)
    r1 = loaded.round(1)
    assert {t.agent for t in r1.targets} == {"claude", "gpt55"}
    assert all(t.state == "reply_present" for t in r1.targets)


def test_invalid_slug_rejected(tmp_path: Path):
    with pytest.raises(ledger.LedgerError):
        ledger.create_session(tmp_path, "p", "Bad Slug", "t", ["claude"])


def test_duplicate_session_rejected(tmp_path: Path):
    ledger.create_session(tmp_path, "p", "x", "t", ["claude"], when="2026-05-26T09:00:00Z")
    with pytest.raises(ledger.LedgerError):
        ledger.create_session(tmp_path, "p", "x", "t", ["claude"], when="2026-05-26T09:00:00Z")


def test_open_next_round(tmp_path: Path):
    s = ledger.create_session(tmp_path, "p", "x", "t", ["claude"])
    ledger.open_next_round(s, ["gpt55"], note="cross-review")
    assert s.current_round == 2
    assert s.round(2).target("gpt55").required


def test_round_terminal_helpers(tmp_path: Path):
    s = ledger.create_session(tmp_path, "p", "x", "t", ["claude", "gpt55"])
    r1 = s.round(1)
    assert not r1.all_required_terminal()
    r1.target("claude").state = "reply_present"
    assert not r1.all_required_terminal()
    r1.target("gpt55").state = "cancelled"
    assert r1.all_required_terminal()


def test_append_event_writes_under_lock(tmp_path: Path):
    s = ledger.create_session(tmp_path, "p", "x", "t", ["claude"])
    ledger.append_event(s, {"actor": "test", "event": "smoke"})
    from awb import events
    recs = events.read_all(ledger.events_path(s.path))
    assert any(r["event"] == "smoke" for r in recs)
