#!/usr/bin/env python3
"""Build the canonical ledger-v1_0 fixture once.

This script is intentionally NOT part of CI. The committed fixture bytes are the
contract artifact; regenerating them with a newer relay would turn the check into
"current relay reads current relay output" instead of "current relay reads the
frozen 1.0 ledger shape".
"""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RELAY_PATH = ROOT / "skills" / "agent-relay" / "bin" / "relay"
OUT = Path(__file__).resolve().parent / "ledger-v1_0"


def _load_relay():
    loader = importlib.machinery.SourceFileLoader("relay_fixture_builder", str(RELAY_PATH))
    spec = importlib.util.spec_from_loader("relay_fixture_builder", loader)
    if spec is None:
        raise RuntimeError(f"cannot load relay module from {RELAY_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["relay_fixture_builder"] = mod
    loader.exec_module(mod)
    return mod


relay = _load_relay()


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _write_artifact(
    session: Path,
    *,
    seq: int,
    author: str,
    peer: str,
    kind: str,
    status: str,
    created: str,
    in_reply_to: int | None,
    prompt: str,
    body: str,
) -> None:
    fm = {
        "seq": seq,
        "author": author,
        "peer": peer,
        "kind": kind,
        "status": status,
        "created": created,
        "in_reply_to": in_reply_to,
        "prompt_for_next": prompt,
        "sync_needed": False,
        "touched_paths": [],
        "corrects": None,
    }
    base = f"{seq:03d}-{author}-{kind}"
    md = session / f"{base}.md"
    md.write_text(relay.dump_frontmatter(fm, body.rstrip() + "\n"))
    digest = relay.sha256_of_file(md)
    (session / f"{base}.md.sha256").write_text(f"{digest}  {base}.md\n")
    (session / f"{base}.ready").write_text("")


def build() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "_relay" / "bindings").mkdir(parents=True, mode=0o700)
    (OUT / "_relay" / ".sentinel").write_text("")
    (OUT / "_archive").mkdir(mode=0o700)

    active = OUT / "20260201-frozen-pair"
    active.mkdir(mode=0o700)
    _write_json(active / "session.json", {
        "schema_version": 3,
        "project": "agent-ledger",
        "session_id": "20260201-frozen-pair",
        "title": "Canonical 1.0 forward-read fixture",
        "state": "active",
        "created_at": "2026-02-01T10:00:00+08:00",
        "closed_at": None,
        "close_reason": None,
        "participants": ["claude", "codex"],
    })
    (active / "README.md").write_text(
        "# Canonical 1.0 forward-read fixture\n\n"
        "Static fixture pair used to prove newer relay versions can read the "
        "1.0 ledger contract.\n"
    )
    _write_artifact(
        active,
        seq=1,
        author="claude",
        peer="codex",
        kind="plan",
        status="ready",
        created="2026-02-01T10:01:00+08:00",
        in_reply_to=None,
        prompt="Review the frozen 1.0 plan and respond with kind: review.\n",
        body="## Plan\n\nFreeze the file-ledger contract and verify forward reads.",
    )
    _write_artifact(
        active,
        seq=2,
        author="codex",
        peer="claude",
        kind="review",
        status="ready",
        created="2026-02-01T10:05:00+08:00",
        in_reply_to=1,
        prompt="Confirm the canonical 1.0 fixture remains readable.\n",
        body="## Review\n\nThe fixture is intentionally active so wait entry can return this artifact.",
    )

    archived = OUT / "_archive" / "20260115-archived-topic"
    archived.mkdir(parents=True, mode=0o700)
    _write_json(archived / "session.json", {
        "schema_version": 3,
        "project": "agent-ledger",
        "session_id": "20260115-archived-topic",
        "title": "Archived fixture pair",
        "state": "closed",
        "created_at": "2026-01-15T09:00:00+08:00",
        "closed_at": "2026-01-15T09:30:00+08:00",
        "close_reason": "fixture archived",
        "participants": ["claude", "codex"],
    })
    (archived / "README.md").write_text(
        "# Archived fixture pair\n\n"
        "Static archived pair used to prove _archive remains outside live scans.\n"
    )
    (archived / "CLOSED").write_text(
        'reason = "fixture archived"\n'
        'outcome = "complete"\n'
        'closed_by = "claude"\n'
        'closed_at = "2026-01-15T09:30:00+08:00"\n'
    )
    _write_artifact(
        archived,
        seq=1,
        author="claude",
        peer="codex",
        kind="plan",
        status="closed",
        created="2026-01-15T09:10:00+08:00",
        in_reply_to=None,
        prompt="Archived fixture; no action required.\n",
        body="## Archived Plan\n\nThis artifact proves archived triads remain parseable.",
    )

    agent_session_id = "fixture-claude-session"
    digest = hashlib.sha256(agent_session_id.encode("utf-8")).hexdigest()[:24]
    _write_json(OUT / "_relay" / "bindings" / f"claude-{digest}.json", {
        "schema_version": 1,
        "instance_id": "claude:fixture",
        "author": "claude",
        "agent_session_id": agent_session_id,
        "pair_slug": "20260201-frozen-pair",
        "bound_at": "2026-02-01T10:00:00+08:00",
        "last_seen": "2026-02-01T10:00:00+08:00",
    })


if __name__ == "__main__":
    build()
