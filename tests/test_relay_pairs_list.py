"""relay pairs list."""

import json
import os
from pathlib import Path

import relay


def _setup_shared(monkeypatch, tmp_path: Path) -> Path:
    shared = tmp_path / ".shared"
    shared.mkdir(mode=0o700)
    for k in list(os.environ):
        if k.startswith("RELAY_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RELAY_SYNC", "rsync")
    monkeypatch.setenv("RELAY_REMOTE_SSH", "x@y")
    monkeypatch.setenv("RELAY_REMOTE_PATH", "/r")
    monkeypatch.setenv("RELAY_AUTHOR", "codex")
    monkeypatch.setenv("RELAY_PEER", "claude")
    monkeypatch.setenv("RELAY_SHARED_ROOT", str(shared))
    return shared


def _write_session(shared: Path, slug: str, *, state: str = "active",
                   closed_sentinel: bool = False) -> Path:
    session = shared / slug
    session.mkdir()
    (session / "session.json").write_text(json.dumps({
        "schema_version": 3,
        "project": "myproj",
        "session_id": slug,
        "title": slug,
        "state": state,
        "created_at": "2026-05-27T00:00:00+08:00",
        "closed_at": None,
        "close_reason": None,
        "participants": [],
    }))
    if closed_sentinel:
        (session / "CLOSED").write_text('reason = "done"\n')
    return session


def _publish_terminal(session: Path) -> None:
    base = "001-codex-decision"
    md = session / f"{base}.md"
    fm = {
        "seq": 1,
        "author": "codex",
        "peer": "claude",
        "kind": "decision",
        "status": "failed",
        "created": "2026-05-27T01:00:00+08:00",
        "in_reply_to": None,
        "prompt_for_next": "done\n",
        "sync_needed": False,
        "touched_paths": [],
        "corrects": None,
    }
    md.write_text(relay.dump_frontmatter(fm, "\nbody.\n"))
    digest = relay.sha256_of_file(md)
    (session / f"{base}.md.sha256").write_text(f"{digest}  {base}.md\n")
    (session / f"{base}.ready").write_text("")


def test_pairs_list_shows_active_terminal_closed_separately(monkeypatch, tmp_path, capsys):
    """`relay pairs list` categorizes every pair as active / terminal / closed,
    reports bindings + open slots, and never raises on zero- or multiple-active."""
    shared = _setup_shared(monkeypatch, tmp_path)
    active = _write_session(shared, "20260527-active")
    terminal = _write_session(shared, "20260527-terminal")
    _publish_terminal(terminal)
    closed = _write_session(shared, "20260527-closed", state="closed", closed_sentinel=True)
    relay.join_pair(relay.load_env(), shared, active.name)

    rc = relay.cmd_pairs_list(type("A", (), {"json": True})())
    data = json.loads(capsys.readouterr().out)
    by_id = {item["session_id"]: item for item in data["pairs"]}
    assert rc == 0
    assert by_id[active.name]["category"] == "active"
    assert by_id[terminal.name]["category"] == "terminal"
    assert by_id[closed.name]["category"] == "closed"
    # the bound instance shows up and one slot is now taken
    assert by_id[active.name]["bound_instances"]
    assert by_id[active.name]["open_slots"] == 1

    second_active = _write_session(shared, "20260527-second-active")
    rc = relay.cmd_pairs_list(type("A", (), {"json": True})())
    data = json.loads(capsys.readouterr().out)
    categories = {item["session_id"]: item["category"] for item in data["pairs"]}
    assert rc == 0
    assert categories[second_active.name] == "active"
