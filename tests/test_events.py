import json
from pathlib import Path

from awb import events


def test_append_creates_file_with_ts(tmp_path: Path):
    log = tmp_path / "events.ndjson"
    rec = events.append(log, {"event": "x", "round": 1})
    assert "ts" in rec
    assert log.read_text().strip().count("\n") == 0  # one line
    parsed = json.loads(log.read_text())
    assert parsed["event"] == "x"


def test_append_preserves_explicit_ts(tmp_path: Path):
    log = tmp_path / "events.ndjson"
    events.append(log, {"ts": "2026-01-01T00:00:00Z", "event": "x"})
    parsed = json.loads(log.read_text())
    assert parsed["ts"] == "2026-01-01T00:00:00Z"


def test_multiple_appends(tmp_path: Path):
    log = tmp_path / "events.ndjson"
    for i in range(5):
        events.append(log, {"event": f"e{i}"})
    lines = log.read_text().strip().splitlines()
    assert len(lines) == 5
    assert json.loads(lines[2])["event"] == "e2"


def test_read_all(tmp_path: Path):
    log = tmp_path / "events.ndjson"
    for i in range(3):
        events.append(log, {"event": f"e{i}"})
    recs = events.read_all(log)
    assert [r["event"] for r in recs] == ["e0", "e1", "e2"]


def test_read_all_missing_returns_empty(tmp_path: Path):
    assert events.read_all(tmp_path / "nope.ndjson") == []


def test_read_all_skips_blank_and_bad_lines(tmp_path: Path):
    log = tmp_path / "events.ndjson"
    log.write_text('{"event":"a"}\n\nnotjson\n{"event":"b"}\n')
    recs = events.read_all(log)
    assert [r["event"] for r in recs] == ["a", "b"]
