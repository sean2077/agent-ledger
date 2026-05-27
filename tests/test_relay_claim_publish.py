"""claim + publish + append-only invariants."""

import json
import os
import subprocess
from pathlib import Path

import pytest

import relay


def _bootstrap(monkeypatch, tmp_path: Path, topic="t"):
    repo = tmp_path / "myproj"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    monkeypatch.chdir(repo)
    shared = repo / ".shared"
    shared.mkdir(mode=0o700)
    (shared / "_relay").mkdir()
    (shared / "_relay" / ".sentinel").touch()
    for k in list(os.environ):
        if k.startswith("RELAY_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RELAY_ROLE", "host")
    monkeypatch.setenv("RELAY_AUTHOR", "codex")
    monkeypatch.setenv("RELAY_PEER", "claude")
    monkeypatch.setenv("RELAY_SHARED_ROOT", str(shared))
    relay.cmd_bootstrap(type("A", (), {"topic": topic, "title": None})())
    return relay.resolve_active_session(relay.load_env())


def test_claim_creates_draft_with_scaffold(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    args = type("A", (), {"kind": "plan", "in_reply_to": None, "project": None})()
    rc = relay.cmd_claim(args)
    out = capsys.readouterr().out.strip()
    assert rc == 0
    draft = Path(out)
    assert draft.exists() and draft.parent.name == ".draft"
    fm, body = relay.parse_frontmatter(draft.read_text())
    assert fm["seq"] == 1
    assert fm["author"] == "codex"
    assert fm["peer"] == "claude"
    assert fm["kind"] == "plan"
    assert fm["status"] == "draft"
    assert "TODO:" in fm["prompt_for_next"]
    assert body.strip().startswith("# plan by codex")


def test_claim_unknown_kind_rejected(monkeypatch, tmp_path, capsys):
    _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    args = type("A", (), {"kind": "weird-kind", "in_reply_to": None, "project": None})()
    rc = relay.cmd_claim(args)
    assert rc == 2
    assert "unknown kind" in capsys.readouterr().err


def test_claim_collision_increments(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    args = type("A", (), {"kind": "plan", "in_reply_to": None, "project": None})()
    p1 = relay.cmd_claim(args); p1_path = Path(capsys.readouterr().out.strip())
    p2 = relay.cmd_claim(args); p2_path = Path(capsys.readouterr().out.strip())
    assert p1 == 0 and p2 == 0
    assert p1_path != p2_path
    assert p1_path.name.startswith("001-")
    assert p2_path.name.startswith("002-")


def _fill_draft(draft_path: Path, *, prompt: str = "do real things\n", body: str = "real body"):
    fm, _ = relay.parse_frontmatter(draft_path.read_text())
    fm["prompt_for_next"] = prompt
    text = relay.dump_frontmatter(fm, f"\n{body}\n")
    draft_path.write_text(text)


def test_publish_happy_path(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    relay.cmd_claim(type("A", (), {"kind": "plan", "in_reply_to": None, "project": None})())
    draft = Path(capsys.readouterr().out.strip())
    _fill_draft(draft)
    rc = relay.cmd_publish(type("A", (), {"draft_path": str(draft), "status": None})())
    out = capsys.readouterr().out.strip()
    assert rc == 0
    pub = Path(out)
    assert pub.exists()
    assert (pub.parent / (pub.name + ".sha256")).exists()
    assert (pub.parent / (pub.name[:-3] + ".ready")).exists()
    assert not draft.exists()
    fm, _ = relay.parse_frontmatter(pub.read_text())
    assert fm["status"] == "ready"


def test_publish_rejects_placeholder_prompt(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    relay.cmd_claim(type("A", (), {"kind": "plan", "in_reply_to": None, "project": None})())
    draft = Path(capsys.readouterr().out.strip())
    # don't fill — leaves "TODO:" placeholder
    _fill_draft(draft, prompt="TODO: still placeholder\n")
    rc = relay.cmd_publish(type("A", (), {"draft_path": str(draft), "status": None})())
    assert rc == 2
    assert "TODO:" in capsys.readouterr().err
    assert draft.exists()  # draft preserved


def test_publish_rejects_empty_body(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    relay.cmd_claim(type("A", (), {"kind": "plan", "in_reply_to": None, "project": None})())
    draft = Path(capsys.readouterr().out.strip())
    _fill_draft(draft, prompt="real prompt\n", body="")
    rc = relay.cmd_publish(type("A", (), {"draft_path": str(draft), "status": None})())
    assert rc == 2
    assert "body" in capsys.readouterr().err


def test_publish_with_status_override(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    relay.cmd_claim(type("A", (), {"kind": "decision", "in_reply_to": None, "project": None})())
    draft = Path(capsys.readouterr().out.strip())
    _fill_draft(draft, prompt="N/A; this is a final decision\n", body="decided.")
    rc = relay.cmd_publish(type("A", (), {"draft_path": str(draft), "status": "closed"})())
    pub = Path(capsys.readouterr().out.strip())
    assert rc == 0
    fm, _ = relay.parse_frontmatter(pub.read_text())
    assert fm["status"] == "closed"


def test_publish_refuses_non_draft_path(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    fake = session / "001-codex-plan.md"
    fake.write_text(relay.dump_frontmatter({
        "seq": 1, "author": "codex", "peer": "claude", "kind": "plan",
        "status": "draft", "prompt_for_next": "x\n", "sync_needed": False,
    }, "body"))
    rc = relay.cmd_publish(type("A", (), {"draft_path": str(fake), "status": None})())
    assert rc == 2
    assert "under .draft" in capsys.readouterr().err
