"""events.ndjson append-only audit log.

Single-writer (awb CLI) discipline. Caller must hold the session lock
before appending. Each line is one JSON object terminated by '\n'.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def append(events_path: Path, event: dict, *, sync: bool = True) -> dict:
    events_path = Path(events_path)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": event.get("ts") or now_iso(), **{k: v for k, v in event.items() if k != "ts"}}
    line = json.dumps(record, ensure_ascii=False) + "\n"
    fd = os.open(events_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
        if sync:
            os.fsync(fd)
    finally:
        os.close(fd)
    return record


def read_all(events_path: Path) -> list[dict]:
    events_path = Path(events_path)
    if not events_path.exists():
        return []
    out: list[dict] = []
    for ln in events_path.read_text().splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out
