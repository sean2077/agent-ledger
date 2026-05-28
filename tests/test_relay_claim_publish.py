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


def test_claim_collision_after_two_retries_exits_2(monkeypatch, tmp_path, capsys):
    """Stage 0: file-protocol.md §7.1 step 4 says second-failure exit code is 2.
    Force claim into a real two-collision-then-fail path by stubbing latest_seq
    to lie, then planting squatters at the seqs claim will try."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    draft_dir = session / ".draft"
    draft_dir.mkdir(exist_ok=True)
    # Squat the two slots claim will attempt after latest_seq returns 0.
    (draft_dir / "001-codex-plan.md").write_text("squatter-a\n")
    (draft_dir / "002-codex-plan.md").write_text("squatter-b\n")
    # Force latest_seq to under-report so claim starts at seq=1 and collides twice.
    monkeypatch.setattr(relay, "latest_seq", lambda s: 0)
    args = type("A", (), {"kind": "plan", "in_reply_to": None, "project": None})()
    rc = relay.cmd_claim(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "could not allocate sequence" in err


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


def test_list_published_rejects_tampered_sha256(monkeypatch, tmp_path, capsys):
    """Bug fix MAJOR #2: a .md whose content no longer matches its .sha256 sidecar
    must not appear in list_published() — silent tampering breaks append-only trust."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    # publish one good artifact
    relay.cmd_claim(type("A", (), {"kind": "plan", "in_reply_to": None, "project": None})())
    draft = Path(capsys.readouterr().out.strip())
    _fill_draft(draft)
    relay.cmd_publish(type("A", (), {"draft_path": str(draft), "status": None})())
    capsys.readouterr()
    pub = next(session.glob("001-codex-plan.md"))
    # tamper: edit the published md AFTER its sha256 was written
    pub.write_text(pub.read_text() + "\nsneaky tamper line\n")
    listed = relay.list_published(session)
    assert pub not in listed, f"tampered artifact must not be returned: {listed}"


def test_list_published_accepts_intact(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    relay.cmd_claim(type("A", (), {"kind": "plan", "in_reply_to": None, "project": None})())
    draft = Path(capsys.readouterr().out.strip())
    _fill_draft(draft)
    relay.cmd_publish(type("A", (), {"draft_path": str(draft), "status": None})())
    listed = relay.list_published(session)
    assert len(listed) == 1


def test_publish_rejects_seq_mismatch(monkeypatch, tmp_path, capsys):
    """Bug fix MAJOR #3: frontmatter seq must match filename NNN prefix."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    relay.cmd_claim(type("A", (), {"kind": "plan", "in_reply_to": None, "project": None})())
    draft = Path(capsys.readouterr().out.strip())
    # filename is 001-codex-plan.md; tamper frontmatter seq
    fm, _ = relay.parse_frontmatter(draft.read_text())
    fm["seq"] = 99
    fm["prompt_for_next"] = "real instructions\n"
    draft.write_text(relay.dump_frontmatter(fm, "\nbody.\n"))
    rc = relay.cmd_publish(type("A", (), {"draft_path": str(draft), "status": None})())
    assert rc == 2
    err = capsys.readouterr().err
    assert "seq" in err.lower() and ("mismatch" in err.lower() or "filename" in err.lower())
    assert draft.exists()  # preserved


def test_publish_rejects_author_mismatch(monkeypatch, tmp_path, capsys):
    """Bug fix MAJOR #3: frontmatter author must match filename."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    relay.cmd_claim(type("A", (), {"kind": "plan", "in_reply_to": None, "project": None})())
    draft = Path(capsys.readouterr().out.strip())
    fm, _ = relay.parse_frontmatter(draft.read_text())
    fm["author"] = "imposter"
    fm["prompt_for_next"] = "real instructions\n"
    draft.write_text(relay.dump_frontmatter(fm, "\nbody.\n"))
    rc = relay.cmd_publish(type("A", (), {"draft_path": str(draft), "status": None})())
    assert rc == 2
    err = capsys.readouterr().err
    assert "author" in err.lower()


def test_publish_rejects_kind_mismatch(monkeypatch, tmp_path, capsys):
    """Bug fix MAJOR #3: frontmatter kind must match filename."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    relay.cmd_claim(type("A", (), {"kind": "plan", "in_reply_to": None, "project": None})())
    draft = Path(capsys.readouterr().out.strip())
    fm, _ = relay.parse_frontmatter(draft.read_text())
    fm["kind"] = "review"
    fm["prompt_for_next"] = "real instructions\n"
    draft.write_text(relay.dump_frontmatter(fm, "\nbody.\n"))
    rc = relay.cmd_publish(type("A", (), {"draft_path": str(draft), "status": None})())
    assert rc == 2
    err = capsys.readouterr().err
    assert "kind" in err.lower()


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


# -----------------------------------------------------------------------------
# v3 bundle coverage for M3 --session-id + R1 publish guard.
# -----------------------------------------------------------------------------


def _claim_filled(session: Path, capsys, *, kind: str = "note") -> Path:
    rc = relay.cmd_claim(type("A", (), {
        "kind": kind, "in_reply_to": None, "project": None, "session_id": session.name,
    })())
    assert rc == 0
    draft = Path(capsys.readouterr().out.strip())
    _fill_draft(draft, prompt="record this terminal note\n", body="body.")
    return draft


def test_claim_with_session_id_resolves_among_multiple_active(monkeypatch, tmp_path, capsys):
    """M3 (b): relay claim --session-id picks the right session under multiple-active."""
    first = _bootstrap(monkeypatch, tmp_path, topic="first")
    capsys.readouterr()
    assert relay.cmd_bootstrap(type("A", (), {"topic": "second", "title": None, "force": True})()) == 0
    capsys.readouterr()
    second = next(
        p for p in first.parent.iterdir()
        if p.is_dir() and p.name.endswith("-second")
    )
    rc = relay.cmd_claim(type("A", (), {
        "kind": "plan", "in_reply_to": None, "project": None, "session_id": first.name,
    })())
    draft = Path(capsys.readouterr().out.strip())
    assert rc == 0
    assert draft.parent == first / ".draft"
    assert second != first


def test_publish_refuses_draft_when_session_inactive(monkeypatch, tmp_path, capsys):
    """R1: cmd_publish blocks publishing into a session whose latest is terminal or CLOSED."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    draft = _claim_filled(session, capsys)
    (session / "CLOSED").write_text('reason = "already done"\n')
    rc = relay.cmd_publish(type("A", (), {"draft_path": str(draft), "status": None})())
    assert rc == 2
    assert "inactive session" in capsys.readouterr().err
    assert draft.exists()


def test_publish_force_requires_force_reason(monkeypatch, tmp_path, capsys):
    """R1 escape hatch: --force without --force-reason is rejected."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    draft = _claim_filled(session, capsys)
    (session / "CLOSED").write_text('reason = "already done"\n')
    rc = relay.cmd_publish(type("A", (), {
        "draft_path": str(draft), "status": "closed", "force": True,
    })())
    assert rc == 2
    assert "--force-reason" in capsys.readouterr().err


def test_publish_force_requires_terminal_status(monkeypatch, tmp_path, capsys):
    """R1 escape hatch: --force --force-reason TEXT requires --status in TERMINAL_STATUSES."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    draft = _claim_filled(session, capsys)
    (session / "CLOSED").write_text('reason = "already done"\n')
    rc = relay.cmd_publish(type("A", (), {
        "draft_path": str(draft), "status": None, "force": True,
        "force_reason": "append terminal note after close",
    })())
    assert rc == 2
    assert "terminal --status" in capsys.readouterr().err


def test_publish_force_note_preserves_inactive_session(monkeypatch, tmp_path, capsys):
    """R1 escape hatch: kind:note --status closed --force lands without resurrecting the session."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    draft = _claim_filled(session, capsys, kind="note")
    (session / "CLOSED").write_text('reason = "already done"\n')
    rc = relay.cmd_publish(type("A", (), {
        "draft_path": str(draft), "status": "closed", "force": True,
        "force_reason": "record post-close note",
    })())
    pub = Path(capsys.readouterr().out.strip())
    assert rc == 0
    assert pub.exists()
    fm, _ = relay.parse_frontmatter(pub.read_text())
    assert fm["status"] == "closed"
    assert fm["force_reason"] == "record post-close note"
    assert (session / "CLOSED").exists()
    assert relay.session_is_active(session) is False
