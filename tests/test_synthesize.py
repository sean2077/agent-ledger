from pathlib import Path

import pytest

from awb import importer, ledger, synthesize, wait


def _seeded(tmp_path: Path, targets=("claude", "gpt55")) -> ledger.Session:
    return ledger.create_session(
        tmp_path, "p", "x", "t", list(targets),
    )


def _import_reply(s: ledger.Session, agent: str, verdict: str = "approve"):
    src = s.path.parent / f"{agent}-source.md"
    src.write_text(f"Verdict: {verdict}\n\ncontent\n")
    importer.import_reply(s, 1, agent, src)


def test_draft_renders_with_reply_summary(tmp_path: Path):
    s = _seeded(tmp_path)
    _import_reply(s, "claude", "needs-change")
    draft = synthesize.write_draft(s, 1)
    text = draft.read_text()
    assert "DRAFT" in text
    assert "claude" in text and "needs-change" not in draft.name


def test_publish_refuses_when_targets_not_terminal(tmp_path: Path):
    s = _seeded(tmp_path)
    synthesize.write_draft(s, 1)
    with pytest.raises(synthesize.SynthesizeError, match="not terminal"):
        synthesize.publish(s, 1)


def test_publish_when_all_terminal_closes_and_opens_next(tmp_path: Path):
    s = _seeded(tmp_path, targets=("claude",))
    _import_reply(s, "claude", "approve")
    synthesize.write_draft(s, 1)
    decided, nxt = synthesize.publish(s, 1)
    assert decided.name == "decision.md"
    assert s.round(1).state == "closed"
    assert nxt is not None and nxt.number == 2
    assert {t.agent for t in nxt.targets} == {"claude"}


def test_publish_no_open(tmp_path: Path):
    s = _seeded(tmp_path, targets=("claude",))
    _import_reply(s, "claude", "approve")
    synthesize.write_draft(s, 1)
    _, nxt = synthesize.publish(s, 1, open_next=False)
    assert nxt is None
    assert s.current_round == 1
    assert s.round(1).state == "closed"


def test_publish_with_next_targets_override(tmp_path: Path):
    s = _seeded(tmp_path, targets=("claude",))
    _import_reply(s, "claude", "approve")
    synthesize.write_draft(s, 1)
    _, nxt = synthesize.publish(s, 1, next_targets=["gpt55"])
    assert nxt is not None
    assert {t.agent for t in nxt.targets} == {"gpt55"}


def test_publish_without_draft_errors(tmp_path: Path):
    s = _seeded(tmp_path, targets=("claude",))
    _import_reply(s, "claude", "approve")
    with pytest.raises(synthesize.SynthesizeError, match="no draft"):
        synthesize.publish(s, 1)


def test_cancelled_target_counts_as_terminal(tmp_path: Path):
    s = _seeded(tmp_path, targets=("claude", "gpt55"))
    _import_reply(s, "claude", "approve")
    # mark gpt55 cancelled
    s.round(1).target("gpt55").state = "cancelled"
    s.round(1).target("gpt55").reason = "skipped this round"
    ledger.save(s)
    synthesize.write_draft(s, 1)
    decided, _ = synthesize.publish(s, 1)
    assert decided.exists()
