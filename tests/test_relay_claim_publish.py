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
    monkeypatch.setenv("RELAY_SYNC", "rsync")
    monkeypatch.setenv("RELAY_REMOTE_SSH", "x@y")
    monkeypatch.setenv("RELAY_REMOTE_PATH", "/r")
    monkeypatch.setenv("RELAY_AUTHOR", "codex")
    monkeypatch.setenv("RELAY_SHARED_ROOT", str(shared))
    relay.cmd_bootstrap(type("A", (), {"topic": topic, "title": None})())
    return relay.resolve_active_pair(relay.load_env())


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


def test_claim_collision_after_ten_retries_exits_2(monkeypatch, tmp_path, capsys):
    """PR2 (M4) widened retry from 2 to 10. Plant 10 squatters to force exhaustion."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    draft_dir = session / ".draft"
    draft_dir.mkdir(exist_ok=True)
    # Squat the 10 slots claim will attempt after latest_seq returns 0.
    for n in range(1, 11):
        (draft_dir / f"{n:03d}-codex-plan.md").write_text(f"squatter-{n}\n")
    # Force latest_seq to under-report so claim starts at seq=1 and collides 10 times.
    monkeypatch.setattr(relay, "latest_seq", lambda s: 0)
    args = type("A", (), {"kind": "plan", "in_reply_to": None, "project": None})()
    rc = relay.cmd_claim(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "could not allocate sequence after 10 attempts" in err
    assert "relay doctor" in err  # recovery hint present


def test_claim_resolves_through_five_squatters(monkeypatch, tmp_path, capsys):
    """With retry widened to 10, 5 concurrent collisions must resolve cleanly
    (regression from old range(2) behavior which would have errored out)."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    draft_dir = session / ".draft"
    draft_dir.mkdir(exist_ok=True)
    for n in range(1, 6):  # plant 5 squatters at seq 1..5
        (draft_dir / f"{n:03d}-codex-plan.md").write_text(f"squatter-{n}\n")
    monkeypatch.setattr(relay, "latest_seq", lambda s: 0)
    args = type("A", (), {"kind": "plan", "in_reply_to": None, "project": None})()
    rc = relay.cmd_claim(args)
    assert rc == 0
    draft = Path(capsys.readouterr().out.strip())
    assert draft.name.startswith("006-")  # claim walked past 5 squatters


def test_publish_warns_on_each_seq_bump(monkeypatch, tmp_path, capsys):
    """Finding C1: when publish bumps seq due to concurrent path collision,
    emit a stderr line per bump so concurrent publishers are observable
    rather than silent."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    relay.cmd_claim(type("A", (), {"kind": "plan", "in_reply_to": None, "project": None})())
    draft = Path(capsys.readouterr().out.strip())
    _fill_draft(draft)

    fm, _ = relay.parse_frontmatter(draft.read_text())
    seq = int(fm["seq"])  # 1
    # Squat seq 1 and seq 2 — publish must bump twice and warn twice.
    (session / f"{seq:03d}-codex-plan.md").write_text("squatter-1\n")
    (session / f"{seq + 1:03d}-codex-plan.md").write_text("squatter-2\n")

    rc = relay.cmd_publish(type("A", (), {
        "draft_path": str(draft), "status": None,
        "force": False, "force_reason": None,
        "project": None, "session_id": None,
    })())
    captured = capsys.readouterr()
    assert rc == 0
    pub = Path(captured.out.strip())
    assert pub.name.startswith(f"{seq + 2:03d}-")
    # Both bumps must appear on stderr
    assert "seq 001 taken by concurrent publisher" in captured.err
    assert "seq 002 taken by concurrent publisher" in captured.err


def test_publish_resolves_through_squatters(monkeypatch, tmp_path, capsys):
    """PR2 widened cmd_publish retry too; codex review 2026-05-29 noted only
    cmd_claim had a behavior test. Prove publish walks past squatters at the
    published-path layer."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    relay.cmd_claim(type("A", (), {"kind": "plan", "in_reply_to": None, "project": None})())
    draft = Path(capsys.readouterr().out.strip())
    _fill_draft(draft)
    # Squat two published-name slots that publish will try first
    # (it starts from the draft's seq and increments on collision).
    fm, _ = relay.parse_frontmatter(draft.read_text())
    seq = int(fm["seq"])  # 1
    for offset in (0, 1, 2):
        target = session / f"{seq + offset:03d}-codex-plan.md"
        target.write_text("squatter\n")

    rc = relay.cmd_publish(type("A", (), {
        "draft_path": str(draft), "status": None,
        "force": False, "force_reason": None,
        "project": None, "session_id": None,
    })())
    assert rc == 0
    pub = Path(capsys.readouterr().out.strip())
    # Published file must be at the first non-squatted slot.
    assert pub.name.startswith(f"{seq + 3:03d}-")
    assert (pub.parent / (pub.name + ".sha256")).exists()
    assert (pub.parent / (pub.name[:-3] + ".ready")).exists()


def _fill_draft(draft_path: Path, *, prompt: str = "do real things\n", body: str = "real body"):
    fm, _ = relay.parse_frontmatter(draft_path.read_text())
    fm["prompt_for_next"] = prompt
    text = relay.dump_frontmatter(fm, f"\n{body}\n")
    draft_path.write_text(text)


def _write_artifact(session: Path, *, seq: int = 1, author: str = "claude",
                    peer: str = "codex", kind: str = "note",
                    with_sha: bool = True, with_ready: bool = True) -> Path:
    fm = {
        "seq": seq,
        "author": author,
        "peer": peer,
        "kind": kind,
        "status": "ready",
        "created": relay.now_iso(),
        "in_reply_to": None,
        "prompt_for_next": "respond\n",
        "sync_needed": False,
        "touched_paths": [],
        "corrects": None,
    }
    name = f"{seq:03d}-{author}-{kind}.md"
    md = session / name
    md.write_text(relay.dump_frontmatter(fm, "\nbody.\n"))
    if with_sha:
        digest = relay.sha256_of_file(md)
        (session / f"{name}.sha256").write_text(f"{digest}  {name}\n")
    if with_ready:
        (session / f"{seq:03d}-{author}-{kind}.ready").touch()
    return md


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


def test_list_published_excludes_md_without_ready(monkeypatch, tmp_path, capsys):
    """Incomplete publish triads are invisible until the final .ready sentinel."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    md = _write_artifact(session, with_sha=True, with_ready=False)
    assert md.exists()
    assert relay.list_published(session) == []


def test_list_published_excludes_md_without_sha256(monkeypatch, tmp_path, capsys):
    """A ready sentinel alone is not enough; readers also require sha256."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    md = _write_artifact(session, with_sha=False, with_ready=True)
    assert md.exists()
    assert relay.list_published(session) == []


def test_status_hides_incomplete_triad(monkeypatch, tmp_path, capsys):
    """relay status funnels through list_published and hides partial publishes."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    _write_artifact(session, with_sha=True, with_ready=False)
    rc = relay.cmd_status(type("A", (), {
        "project": None, "pair_id": None, "require_binding": False,
        "last": 0, "json": True,
    })())
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["published"] == []
    assert data["next_seq"] == 2  # claim allocation still avoids partial seqs.


def test_list_published_accepts_intact(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    relay.cmd_claim(type("A", (), {"kind": "plan", "in_reply_to": None, "project": None})())
    draft = Path(capsys.readouterr().out.strip())
    _fill_draft(draft)
    relay.cmd_publish(type("A", (), {"draft_path": str(draft), "status": None})())
    listed = relay.list_published(session)
    assert len(listed) == 1


def test_publish_two_drafts_same_seq_one_bumps(monkeypatch, tmp_path, capsys):
    """A stale loser draft with the same seq publishes by bumping, not clobbering."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()

    relay.cmd_claim(type("A", (), {"kind": "plan", "in_reply_to": None, "project": None})())
    first_draft = Path(capsys.readouterr().out.strip())
    _fill_draft(first_draft, body="first body")
    assert relay.cmd_publish(type("A", (), {
        "draft_path": str(first_draft), "status": None,
        "force": False, "force_reason": None,
        "project": None, "session_id": None,
    })()) == 0
    first_pub = Path(capsys.readouterr().out.strip())

    # Simulate the losing publisher still holding a same-seq draft after the
    # winner promoted seq 001. On publish it must reserve seq 002 instead.
    second_draft = session / ".draft" / "001-codex-plan.md"
    fm = {
        "seq": 1,
        "author": "codex",
        "peer": "claude",
        "kind": "plan",
        "status": "draft",
        "created": relay.now_iso(),
        "in_reply_to": None,
        "prompt_for_next": "review the bumped artifact\n",
        "sync_needed": False,
        "touched_paths": [],
        "corrects": None,
    }
    second_draft.write_text(relay.dump_frontmatter(fm, "\nsecond body\n"))
    rc = relay.cmd_publish(type("A", (), {
        "draft_path": str(second_draft), "status": None,
        "force": False, "force_reason": None,
        "project": None, "session_id": None,
    })())
    captured = capsys.readouterr()
    second_pub = Path(captured.out.strip())

    assert rc == 0
    assert first_pub.name == "001-codex-plan.md"
    assert second_pub.name == "002-codex-plan.md"
    assert "seq 001 taken by concurrent publisher" in captured.err
    for pub in (first_pub, second_pub):
        assert (session / f"{pub.name}.sha256").exists()
        assert (session / f"{pub.name[:-3]}.ready").exists()
    assert [p.name for p in relay.list_published(session)] == [
        "001-codex-plan.md",
        "002-codex-plan.md",
    ]
    fm2, body2 = relay.parse_frontmatter(second_pub.read_text())
    assert fm2["seq"] == 2
    assert "second body" in body2


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


def test_publish_rejects_cross_author_env(monkeypatch, tmp_path, capsys):
    """Finding 3: publish must refuse to ship a draft authored by someone
    other than the current RELAY_AUTHOR. Repro from codex's review: claim
    a draft as one author, then publish with a different RELAY_AUTHOR —
    pre-fix this returned 0 and shipped a forged-attribution artifact.
    """
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    # Claim as codex (the bootstrap default)
    relay.cmd_claim(type("A", (), {"kind": "plan", "in_reply_to": None, "project": None})())
    draft = Path(capsys.readouterr().out.strip())
    _fill_draft(draft)
    # Switch RELAY_AUTHOR to claude and try to publish codex's draft
    monkeypatch.setenv("RELAY_AUTHOR", "claude")
    rc = relay.cmd_publish(type("A", (), {
        "draft_path": str(draft), "status": None,
        "force": False, "force_reason": None,
        "project": None, "session_id": None,
    })())
    assert rc == 2
    err = capsys.readouterr().err
    assert "'claude'" in err  # the current (publishing) author
    assert "'codex'" in err  # the draft's author
    # Original draft must still be there; no .ready emitted.
    assert draft.exists()
    pub_md = session / draft.name
    assert not pub_md.exists()


def test_publish_rejects_missing_author_env(monkeypatch, tmp_path, capsys):
    """Finding 2 (codex seq 2): publish must FAIL CLOSED when RELAY_AUTHOR is
    unset. Pre-fix the guard was `if env.author and ...`, so an env-less
    publish skipped the author check entirely and shipped the artifact."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    relay.cmd_claim(type("A", (), {"kind": "plan", "in_reply_to": None, "project": None})())
    draft = Path(capsys.readouterr().out.strip())
    _fill_draft(draft)
    monkeypatch.delenv("RELAY_AUTHOR", raising=False)
    rc = relay.cmd_publish(type("A", (), {
        "draft_path": str(draft), "status": None,
        "force": False, "force_reason": None,
        "project": None, "session_id": None,
    })())
    assert rc == 2
    err = capsys.readouterr().err
    assert "could not resolve author identity" in err
    assert not (session / draft.name).exists()
    assert draft.exists()


def test_publish_force_terminal_still_requires_author_env(monkeypatch, tmp_path, capsys):
    """Finding 2 (codex seq 2): the --force terminal-note path must not be a
    backdoor around the missing-author guard."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    relay.cmd_claim(type("A", (), {"kind": "note", "in_reply_to": None, "project": None})())
    draft = Path(capsys.readouterr().out.strip())
    _fill_draft(draft)
    monkeypatch.delenv("RELAY_AUTHOR", raising=False)
    rc = relay.cmd_publish(type("A", (), {
        "draft_path": str(draft), "status": "timed_out",
        "force": True, "force_reason": "operator note",
        "project": None, "session_id": None,
    })())
    assert rc == 2
    err = capsys.readouterr().err
    assert "could not resolve author identity" in err


def test_draft_set_rejects_cross_author(monkeypatch, tmp_path, capsys):
    """Finding 1 (codex seq 2, BLOCKER): draft set must enforce ownership.
    A peer who discovers a draft path must not be able to rewrite another
    author's draft. Repro: claim as codex, switch RELAY_AUTHOR=claude, set."""
    session = _bootstrap(monkeypatch, tmp_path)  # author=codex
    capsys.readouterr()
    relay.cmd_claim(type("A", (), {"kind": "review", "in_reply_to": None,
                                     "corrects": None,
                                     "project": None, "session_id": None})())
    draft = Path(capsys.readouterr().out.strip())
    assert "codex" in draft.name
    body_file = tmp_path / "b.txt"; body_file.write_text("injected body\n")
    pfn_file = tmp_path / "p.txt"; pfn_file.write_text("injected pfn\n")
    monkeypatch.setenv("RELAY_AUTHOR", "claude")
    rc = relay.cmd_draft_set(type("A", (), {
        "draft_path": str(draft),
        "body_file": str(body_file), "prompt_for_next_file": str(pfn_file),
        "sync_needed": False, "touched_path": [], "corrects": None,
    })())
    assert rc == 2
    err = capsys.readouterr().err
    assert "refusing to mutate a draft authored by 'codex'" in err
    # The draft body must be untouched (still the scaffold placeholder).
    assert "injected body" not in draft.read_text()


def test_draft_set_rejects_missing_author_env(monkeypatch, tmp_path, capsys):
    """Finding 1 (codex seq 2): draft set with no RELAY_AUTHOR fails closed."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    relay.cmd_claim(type("A", (), {"kind": "review", "in_reply_to": None,
                                     "corrects": None,
                                     "project": None, "session_id": None})())
    draft = Path(capsys.readouterr().out.strip())
    body_file = tmp_path / "b.txt"; body_file.write_text("x\n")
    monkeypatch.delenv("RELAY_AUTHOR", raising=False)
    rc = relay.cmd_draft_set(type("A", (), {
        "draft_path": str(draft),
        "body_file": str(body_file), "prompt_for_next_file": None,
        "sync_needed": False, "touched_path": [], "corrects": None,
    })())
    assert rc == 2
    assert "could not resolve author identity" in capsys.readouterr().err


def test_claim_rejects_corrects_on_non_correcting_kind(monkeypatch, tmp_path, capsys):
    """Finding 3 (codex seq 2): --corrects is only valid for correction/addendum.
    `relay claim --kind plan --corrects 1` must fail fast."""
    _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    rc = relay.cmd_claim(type("A", (), {
        "kind": "plan", "in_reply_to": 1, "corrects": 1,
        "project": None, "session_id": None,
    })())
    assert rc == 2
    err = capsys.readouterr().err
    assert "corrects" in err.lower()


def test_addendum_may_carry_corrects(monkeypatch, tmp_path, capsys):
    """Finding 3 (codex seq 2): addendum MAY set corrects (optional). Verify
    the allowed-kind path still works end to end."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    # plant seq 1 to point at
    relay.cmd_claim(type("A", (), {"kind": "plan", "in_reply_to": None,
                                     "corrects": None,
                                     "project": None, "session_id": None})())
    d1 = Path(capsys.readouterr().out.strip())
    _fill_draft(d1)
    relay.cmd_publish(type("A", (), {"draft_path": str(d1), "status": None,
                                      "force": False, "force_reason": None,
                                      "project": None, "session_id": None})())
    capsys.readouterr()
    rc = relay.cmd_claim(type("A", (), {"kind": "addendum", "in_reply_to": 1,
                                         "corrects": 1,
                                         "project": None, "session_id": None})())
    assert rc == 0
    draft = Path(capsys.readouterr().out.strip())
    _fill_draft(draft)
    rc = relay.cmd_publish(type("A", (), {"draft_path": str(draft), "status": None,
                                           "force": False, "force_reason": None,
                                           "project": None, "session_id": None})())
    assert rc == 0
    fm, _ = relay.parse_frontmatter(Path(capsys.readouterr().out.strip()).read_text())
    assert fm["corrects"] == 1


def test_publish_rejects_corrects_on_plan_kind(monkeypatch, tmp_path, capsys):
    """Finding 3 (codex seq 2): even if a corrects value reaches a non-
    correcting kind's frontmatter (hand-edit), publish must reject it."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    relay.cmd_claim(type("A", (), {"kind": "plan", "in_reply_to": None,
                                     "corrects": None,
                                     "project": None, "session_id": None})())
    draft = Path(capsys.readouterr().out.strip())
    fm, _ = relay.parse_frontmatter(draft.read_text())
    fm["corrects"] = 1  # tamper: plan must not carry corrects
    fm["prompt_for_next"] = "real instructions\n"
    draft.write_text(relay.dump_frontmatter(fm, "\nbody\n"))
    rc = relay.cmd_publish(type("A", (), {
        "draft_path": str(draft), "status": None,
        "force": False, "force_reason": None,
        "project": None, "session_id": None,
    })())
    assert rc == 2
    err = capsys.readouterr().err
    assert "corrects" in err.lower()


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


def test_claim_correction_requires_corrects(monkeypatch, tmp_path, capsys):
    """Finding 6: --kind correction must be claimed with --corrects <seq>."""
    _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    rc = relay.cmd_claim(type("A", (), {
        "kind": "correction", "in_reply_to": 1, "corrects": None,
        "project": None, "session_id": None,
    })())
    assert rc == 2
    err = capsys.readouterr().err
    assert "--corrects" in err


def test_claim_corrects_must_be_positive(monkeypatch, tmp_path, capsys):
    """Finding 6: --corrects must be a positive integer."""
    _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    rc = relay.cmd_claim(type("A", (), {
        "kind": "correction", "in_reply_to": 1, "corrects": 0,
        "project": None, "session_id": None,
    })())
    assert rc == 2
    err = capsys.readouterr().err
    assert "positive" in err.lower()


def test_claim_with_corrects_lands_in_frontmatter(monkeypatch, tmp_path, capsys):
    """Finding 6: a successful --corrects claim writes the value into
    the scaffold so publish-time validation sees it.
    """
    session = _bootstrap(monkeypatch, tmp_path)
    # First, plant a published seq 1 to correct.
    capsys.readouterr()
    relay.cmd_claim(type("A", (), {"kind": "plan", "in_reply_to": None,
                                     "corrects": None,
                                     "project": None, "session_id": None})())
    draft1 = Path(capsys.readouterr().out.strip())
    _fill_draft(draft1)
    relay.cmd_publish(type("A", (), {
        "draft_path": str(draft1), "status": None,
        "force": False, "force_reason": None,
        "project": None, "session_id": None,
    })())
    capsys.readouterr()
    # Now claim a correction pointing at seq 1.
    rc = relay.cmd_claim(type("A", (), {
        "kind": "correction", "in_reply_to": 1, "corrects": 1,
        "project": None, "session_id": None,
    })())
    assert rc == 0
    draft = Path(capsys.readouterr().out.strip())
    fm, _ = relay.parse_frontmatter(draft.read_text())
    assert fm["kind"] == "correction"
    assert fm["corrects"] == 1


def test_publish_rejects_correction_without_corrects(monkeypatch, tmp_path, capsys):
    """Finding 6 belt+braces: even if someone hand-edits a correction draft
    to clear corrects, publish must refuse it."""
    session = _bootstrap(monkeypatch, tmp_path)
    # plant seq 1
    capsys.readouterr()
    relay.cmd_claim(type("A", (), {"kind": "plan", "in_reply_to": None,
                                     "corrects": None,
                                     "project": None, "session_id": None})())
    d1 = Path(capsys.readouterr().out.strip())
    _fill_draft(d1)
    relay.cmd_publish(type("A", (), {
        "draft_path": str(d1), "status": None,
        "force": False, "force_reason": None,
        "project": None, "session_id": None,
    })())
    capsys.readouterr()
    # claim correction properly
    relay.cmd_claim(type("A", (), {"kind": "correction", "in_reply_to": 1,
                                     "corrects": 1,
                                     "project": None, "session_id": None})())
    draft = Path(capsys.readouterr().out.strip())
    fm, _ = relay.parse_frontmatter(draft.read_text())
    fm["corrects"] = None  # tamper
    fm["prompt_for_next"] = "real instructions\n"
    draft.write_text(relay.dump_frontmatter(fm, "\nbody\n"))
    rc = relay.cmd_publish(type("A", (), {
        "draft_path": str(draft), "status": None,
        "force": False, "force_reason": None,
        "project": None, "session_id": None,
    })())
    assert rc == 2
    err = capsys.readouterr().err
    assert "corrects" in err


def test_publish_rejects_corrects_pointing_at_future_seq(monkeypatch, tmp_path, capsys):
    """Finding 6: corrects must point at a prior seq, not self or later."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    relay.cmd_claim(type("A", (), {"kind": "correction", "in_reply_to": None,
                                     "corrects": 1,
                                     "project": None, "session_id": None})())
    draft = Path(capsys.readouterr().out.strip())
    # seq is 1; corrects points at 1 (self) — must be rejected.
    fm, _ = relay.parse_frontmatter(draft.read_text())
    fm["prompt_for_next"] = "real instructions\n"
    draft.write_text(relay.dump_frontmatter(fm, "\nbody\n"))
    rc = relay.cmd_publish(type("A", (), {
        "draft_path": str(draft), "status": None,
        "force": False, "force_reason": None,
        "project": None, "session_id": None,
    })())
    assert rc == 2
    err = capsys.readouterr().err
    assert "future" in err or "less than" in err


def test_draft_set_fills_body_and_prompt_from_files(monkeypatch, tmp_path, capsys):
    """Finding 9: relay draft set must update body + prompt_for_next from
    files without the agent's Write tool."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    relay.cmd_claim(type("A", (), {"kind": "plan", "in_reply_to": None,
                                     "corrects": None,
                                     "project": None, "session_id": None})())
    draft = Path(capsys.readouterr().out.strip())
    # Pre-fill: scaffold has TODO: in prompt_for_next.
    body_file = tmp_path / "body.txt"
    body_file.write_text("# my body\n\nreal content here\n")
    pfn_file = tmp_path / "pfn.txt"
    pfn_file.write_text("respond with kind: review\n")
    rc = relay.cmd_draft_set(type("A", (), {
        "draft_path": str(draft),
        "body_file": str(body_file),
        "prompt_for_next_file": str(pfn_file),
        "sync_needed": False,
        "touched_path": [],
        "corrects": None,
    })())
    assert rc == 0
    fm, body = relay.parse_frontmatter(draft.read_text())
    assert "TODO:" not in fm["prompt_for_next"]
    assert "review" in fm["prompt_for_next"]
    assert "real content here" in body
    assert "TODO" not in body
    # Now publish should succeed without the placeholder reject path.
    capsys.readouterr()
    rc = relay.cmd_publish(type("A", (), {
        "draft_path": str(draft), "status": None,
        "force": False, "force_reason": None,
        "project": None, "session_id": None,
    })())
    assert rc == 0, f"publish should succeed after draft set; err={capsys.readouterr().err}"


def test_draft_set_rejects_double_stdin(monkeypatch, tmp_path, capsys):
    """Finding 9: only one source may be '-' since stdin is consumed once."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    relay.cmd_claim(type("A", (), {"kind": "plan", "in_reply_to": None,
                                     "corrects": None,
                                     "project": None, "session_id": None})())
    draft = Path(capsys.readouterr().out.strip())
    rc = relay.cmd_draft_set(type("A", (), {
        "draft_path": str(draft),
        "body_file": "-",
        "prompt_for_next_file": "-",
        "sync_needed": False,
        "touched_path": [],
        "corrects": None,
    })())
    assert rc == 2
    err = capsys.readouterr().err
    assert "stdin" in err.lower()


def test_draft_set_appends_touched_paths_and_corrects(monkeypatch, tmp_path, capsys):
    """Finding 9: flags compose; touched_path appends without duplicating."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    relay.cmd_claim(type("A", (), {"kind": "addendum", "in_reply_to": None,
                                     "corrects": None,
                                     "project": None, "session_id": None})())
    draft = Path(capsys.readouterr().out.strip())
    body_file = tmp_path / "body.txt"
    body_file.write_text("body\n")
    pfn_file = tmp_path / "pfn.txt"
    pfn_file.write_text("do thing\n")
    rc = relay.cmd_draft_set(type("A", (), {
        "draft_path": str(draft),
        "body_file": str(body_file),
        "prompt_for_next_file": str(pfn_file),
        "sync_needed": True,
        "touched_path": ["a.py", "b.py", "a.py"],
        "corrects": 1,
    })())
    assert rc == 0
    fm, _ = relay.parse_frontmatter(draft.read_text())
    assert fm["sync_needed"] is True
    assert fm["touched_paths"] == ["a.py", "b.py"]
    assert fm["corrects"] == 1


def test_draft_set_refuses_non_draft_path(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    bogus = tmp_path / "not-a-draft.md"
    bogus.write_text("x")
    rc = relay.cmd_draft_set(type("A", (), {
        "draft_path": str(bogus),
        "body_file": None, "prompt_for_next_file": None,
        "sync_needed": False, "touched_path": [], "corrects": None,
    })())
    assert rc == 2
    err = capsys.readouterr().err
    assert ".draft" in err


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
# v3 bundle coverage for M3 --pair-id + R1 publish guard.
# -----------------------------------------------------------------------------


def _claim_filled(session: Path, capsys, *, kind: str = "note") -> Path:
    rc = relay.cmd_claim(type("A", (), {
        "kind": kind, "in_reply_to": None, "project": None, "pair_id": session.name,
    })())
    assert rc == 0
    draft = Path(capsys.readouterr().out.strip())
    _fill_draft(draft, prompt="record this terminal note\n", body="body.")
    return draft


def test_claim_with_pair_id_resolves_among_multiple_active(monkeypatch, tmp_path, capsys):
    """relay claim --pair-id picks the right pair when several are active."""
    first = _bootstrap(monkeypatch, tmp_path, topic="first")
    capsys.readouterr()
    assert relay.cmd_bootstrap(type("A", (), {"topic": "second", "title": None, "force": True})()) == 0
    capsys.readouterr()
    second = next(
        p for p in first.parent.iterdir()
        if p.is_dir() and p.name.endswith("-second")
    )
    rc = relay.cmd_claim(type("A", (), {
        "kind": "plan", "in_reply_to": None, "project": None, "pair_id": first.name,
    })())
    draft = Path(capsys.readouterr().out.strip())
    assert rc == 0
    assert draft.parent == first / ".draft"
    assert second != first


def test_publish_refuses_draft_when_pair_inactive(monkeypatch, tmp_path, capsys):
    """R1: cmd_publish blocks publishing into a pair whose latest is terminal or CLOSED."""
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    draft = _claim_filled(session, capsys)
    (session / "CLOSED").write_text('reason = "already done"\n')
    rc = relay.cmd_publish(type("A", (), {"draft_path": str(draft), "status": None})())
    assert rc == 2
    assert "inactive pair" in capsys.readouterr().err
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
