"""`relay pairs archive` / `restore` / `list --archived` (v0.16).

Archived pairs move to `.shared/_archive/<slug>/` so the top level stays
uncluttered. The central guarantee is *quarantine*: an archived pair must be
invisible to every live scan (status / wait / doctor / pairs list / active-pair
resolution). The fail-closed + must-fix sections lock in the codex review
(seq 2) outcomes: atomic move, bindings dropped only after a successful move,
shelve-semantics for --force, strict slug validation, and a name-honest
--terminated sweep (closed/terminal only)."""

import errno
import json
import os
from pathlib import Path

import pytest

import relay


# --- helpers (self-contained; mirror test_relay_pairs / _pairs_list) --------

def _shared(monkeypatch, tmp_path: Path, author: str = "claude",
            asid: str = "test-window") -> Path:
    shared = tmp_path / ".shared"
    (shared / "_relay").mkdir(parents=True, mode=0o700)
    (shared / "_relay" / ".sentinel").touch()
    for k in list(os.environ):
        if k.startswith("RELAY_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RELAY_SYNC", "none")
    monkeypatch.setenv("RELAY_AUTHOR", author)
    monkeypatch.setenv("RELAY_AGENT_SESSION_ID", asid)
    monkeypatch.setenv("RELAY_SHARED_ROOT", str(shared))
    return shared


def _mk_pair(shared: Path, slug: str, *, state: str = "active",
             closed_sentinel: bool = False) -> Path:
    sd = shared / slug
    sd.mkdir()
    (sd / "session.json").write_text(json.dumps({
        "schema_version": 3, "project": "p", "session_id": slug, "title": slug,
        "state": state, "created_at": "2026-01-01T00:00:00+08:00",
        "closed_at": None, "close_reason": None,
        "participants": ["claude", "codex"],
    }))
    if closed_sentinel:
        (sd / "CLOSED").write_text('reason = "done"\n')
    return sd


def _publish_terminal(session: Path) -> None:
    base = "001-codex-decision"
    fm = {
        "seq": 1, "author": "codex", "peer": "claude", "kind": "decision",
        "status": "failed", "created": "2026-01-01T01:00:00+08:00",
        "in_reply_to": None, "prompt_for_next": "done\n",
        "sync_needed": False, "touched_paths": [], "corrects": None,
    }
    (session / f"{base}.md").write_text(relay.dump_frontmatter(fm, "\nbody.\n"))
    digest = relay.sha256_of_file(session / f"{base}.md")
    (session / f"{base}.md.sha256").write_text(f"{digest}  {base}.md\n")
    (session / f"{base}.ready").write_text("")


def _args(**kw):
    return type("A", (), kw)()


def _archive(slug=None, *, terminated=False, force=False) -> int:
    return relay.cmd_pairs_archive(_args(slug=slug, terminated=terminated, force=force))


def _restore(slug: str) -> int:
    return relay.cmd_pairs_restore(_args(slug=slug))


def _arc(shared: Path) -> Path:
    return shared / "_archive"


# --- happy paths ------------------------------------------------------------

def test_archive_moves_closed_pair(monkeypatch, tmp_path, capsys):
    shared = _shared(monkeypatch, tmp_path)
    _mk_pair(shared, "20260101-foo", state="closed", closed_sentinel=True)
    assert _archive("20260101-foo") == 0
    assert not (shared / "20260101-foo").exists()
    assert (_arc(shared) / "20260101-foo" / "session.json").exists()


def test_first_archive_creates_archive_dir_0700(monkeypatch, tmp_path):
    shared = _shared(monkeypatch, tmp_path)
    _mk_pair(shared, "20260101-foo", state="closed", closed_sentinel=True)
    assert not _arc(shared).exists()
    assert _archive("20260101-foo") == 0
    assert oct(_arc(shared).stat().st_mode & 0o777) == "0o700"


def test_restore_moves_pair_back(monkeypatch, tmp_path):
    shared = _shared(monkeypatch, tmp_path)
    _mk_pair(shared, "20260101-foo", state="closed", closed_sentinel=True)
    _archive("20260101-foo")
    assert _restore("20260101-foo") == 0
    assert (shared / "20260101-foo" / "session.json").exists()
    assert not (_arc(shared) / "20260101-foo").exists()


def test_terminated_sweeps_only_closed_and_terminal(monkeypatch, tmp_path):
    """must-fix 5: --terminated is name-honest — closed + terminal only. An
    active, a state-weird `inactive`, and a corrupt `invalid` pair stay put."""
    shared = _shared(monkeypatch, tmp_path)
    _mk_pair(shared, "20260101-active")
    _mk_pair(shared, "20260101-closed", state="closed", closed_sentinel=True)
    term = _mk_pair(shared, "20260101-terminal")
    _publish_terminal(term)
    _mk_pair(shared, "20260101-inactive", state="paused")          # -> category inactive
    invalid = shared / "20260101-invalid"
    invalid.mkdir()
    (invalid / "session.json").write_text("{ not valid json")       # -> category invalid

    assert _archive(terminated=True) == 0
    archived = {p.name for p in (_arc(shared)).iterdir()}
    assert archived == {"20260101-closed", "20260101-terminal"}
    for stays in ("20260101-active", "20260101-inactive", "20260101-invalid"):
        assert (shared / stays).exists(), f"{stays} must NOT be swept"


# --- quarantine: archived pairs invisible to every live scan ----------------

def test_iter_pair_dirs_skips_archive(monkeypatch, tmp_path):
    shared = _shared(monkeypatch, tmp_path)
    (_arc(shared) / "20260101-x").mkdir(parents=True)
    (_arc(shared) / "20260101-x" / "session.json").write_text("{}")
    names = [p.name for p in relay.iter_pair_dirs(shared)]
    assert "20260101-x" not in names
    assert "_archive" not in names


def test_archived_invisible_to_resolve_active_pair(monkeypatch, tmp_path):
    shared = _shared(monkeypatch, tmp_path)
    _mk_pair(shared, "20260101-live")               # sole active
    _mk_pair(shared, "20260101-old", state="closed", closed_sentinel=True)
    _archive("20260101-old")
    # the lone active pair still resolves; the archived one is gone from the scan
    assert relay.resolve_active_pair(relay.load_env()).name == "20260101-live"
    # archive the live one too (shelve) -> nothing active remains
    assert _archive("20260101-live", force=True) == 0
    with pytest.raises(SystemExit, match="no active pair"):
        relay.resolve_active_pair(relay.load_env())


def test_archived_invisible_to_doctor(monkeypatch, tmp_path, capsys):
    shared = _shared(monkeypatch, tmp_path)
    # an archived pair carrying an abandoned draft must not surface in doctor
    sd = _arc(shared) / "20260101-x"
    (sd / ".draft").mkdir(parents=True)
    (sd / "session.json").write_text(json.dumps({
        "schema_version": 3, "state": "closed", "session_id": "20260101-x",
        "participants": ["claude", "codex"],
    }))
    (sd / ".draft" / "001-claude-plan.md").write_text("draft")
    relay.cmd_doctor(_args(json=True, fix=False, older_than=None))
    report = json.loads(capsys.readouterr().out)
    assert "20260101-x" not in [s["session_id"] for s in report["sessions"]]


def test_archived_invisible_to_pairs_list_shown_only_with_flag(monkeypatch, tmp_path, capsys):
    shared = _shared(monkeypatch, tmp_path)
    _mk_pair(shared, "20260101-live")
    _mk_pair(shared, "20260101-old", state="closed", closed_sentinel=True)
    _archive("20260101-old")
    capsys.readouterr()  # drain archive's "archived: ..." line before JSON reads
    relay.cmd_pairs_list(_args(json=True, archived=False))
    live = json.loads(capsys.readouterr().out)
    assert [p["session_id"] for p in live["pairs"]] == ["20260101-live"]
    relay.cmd_pairs_list(_args(json=True, archived=True))
    arch = json.loads(capsys.readouterr().out)
    assert arch["archived"] is True
    assert [p["session_id"] for p in arch["pairs"]] == ["20260101-old"]
    assert arch["pairs"][0]["archived"] is True


# --- fail-closed edges ------------------------------------------------------

def test_archive_active_without_force_refused_and_not_moved(monkeypatch, tmp_path, capsys):
    shared = _shared(monkeypatch, tmp_path)
    _mk_pair(shared, "20260101-live")
    assert _archive("20260101-live") == 2
    err = capsys.readouterr().err
    assert "active pair" in err and "--force" in err and "relay close" in err
    assert (shared / "20260101-live" / "session.json").exists()      # untouched
    assert not (_arc(shared)).exists()


def test_archive_active_force_shelves_and_drops_bindings(monkeypatch, tmp_path, capsys):
    shared = _shared(monkeypatch, tmp_path)
    _mk_pair(shared, "20260101-live")
    relay.join_pair(relay.load_env(), shared, "20260101-live")       # bind this instance
    assert relay.binding_for_pair(shared, "20260101-live")           # precondition
    assert _archive("20260101-live", force=True) == 0
    assert (_arc(shared) / "20260101-live" / "session.json").exists()
    assert relay.binding_for_pair(shared, "20260101-live") == []     # binding dropped
    assert not list((shared / "_relay" / "bindings").glob("*.json"))  # file gone


def test_archive_unknown_slug_refused(monkeypatch, tmp_path, capsys):
    _shared(monkeypatch, tmp_path)
    assert _archive("20260101-nope") == 2
    assert "no pair" in capsys.readouterr().err


def test_archive_already_archived_disambiguated(monkeypatch, tmp_path, capsys):
    shared = _shared(monkeypatch, tmp_path)
    _mk_pair(shared, "20260101-foo", state="closed", closed_sentinel=True)
    _archive("20260101-foo")
    capsys.readouterr()
    assert _archive("20260101-foo") == 2
    err = capsys.readouterr().err
    assert "already archived" in err and "relay pairs restore" in err


@pytest.mark.parametrize("bad", ["../etc", "_archive/x", "a/b", "20260101-X", "nodateslug"])
def test_archive_invalid_slug_rejected_without_touching_disk(monkeypatch, tmp_path, capsys, bad):
    shared = _shared(monkeypatch, tmp_path)
    assert _archive(bad) == 2
    assert "invalid pair slug" in capsys.readouterr().err
    assert not (_arc(shared)).exists()


def test_restore_collision_with_live_slug_refused(monkeypatch, tmp_path, capsys):
    shared = _shared(monkeypatch, tmp_path)
    _mk_pair(shared, "20260101-foo", state="closed", closed_sentinel=True)
    _archive("20260101-foo")
    _mk_pair(shared, "20260101-foo")                                 # fresh live one, same slug
    assert _restore("20260101-foo") == 2
    assert "already exists" in capsys.readouterr().err
    # neither copy mutated
    assert (shared / "20260101-foo" / "session.json").exists()
    assert (_arc(shared) / "20260101-foo" / "session.json").exists()


def test_restore_unknown_refused(monkeypatch, tmp_path, capsys):
    _shared(monkeypatch, tmp_path)
    assert _restore("20260101-ghost") == 2
    assert "no archived pair" in capsys.readouterr().err


def test_restore_invalid_slug_rejected(monkeypatch, tmp_path, capsys):
    _shared(monkeypatch, tmp_path)
    assert _restore("../etc") == 2
    assert "invalid pair slug" in capsys.readouterr().err


def test_terminated_empty_sweep_is_success(monkeypatch, tmp_path, capsys):
    shared = _shared(monkeypatch, tmp_path)
    _mk_pair(shared, "20260101-live")                                # only an active pair
    assert _archive(terminated=True) == 0
    assert "no terminated pairs" in capsys.readouterr().out
    assert (shared / "20260101-live").exists()
    assert not (_arc(shared)).exists()


def test_archive_preserves_artifacts_and_drafts(monkeypatch, tmp_path):
    shared = _shared(monkeypatch, tmp_path)
    sd = _mk_pair(shared, "20260101-foo", state="closed", closed_sentinel=True)
    _publish_terminal(sd)                                            # md + sha256 + ready
    (sd / ".draft").mkdir()
    (sd / ".draft" / "002-claude-fix.md").write_text("wip")
    _archive("20260101-foo")
    dst = _arc(shared) / "20260101-foo"
    for name in ("001-codex-decision.md", "001-codex-decision.md.sha256",
                 "001-codex-decision.ready", "CLOSED",
                 ".draft/002-claude-fix.md"):
        assert (dst / name).exists(), f"{name} lost in archive"


# --- codex review must-fix regressions --------------------------------------

def test_restore_does_not_rebind_and_keeps_state(monkeypatch, tmp_path):
    """must-fix 3: restore leaves session.json untouched and never rebinds.
    closed stays closed; a --force-shelved active pair stays active but unbound."""
    shared = _shared(monkeypatch, tmp_path)
    # (a) closed pair round-trips as closed
    _mk_pair(shared, "20260101-c", state="closed", closed_sentinel=True)
    _archive("20260101-c")
    _restore("20260101-c")
    sj = json.loads((shared / "20260101-c" / "session.json").read_text())
    assert sj["state"] == "closed"
    assert relay.binding_for_pair(shared, "20260101-c") == []
    # (b) force-shelved active pair round-trips as active, still unbound
    _mk_pair(shared, "20260101-a")
    relay.join_pair(relay.load_env(), shared, "20260101-a")
    _archive("20260101-a", force=True)
    _restore("20260101-a")
    sj = json.loads((shared / "20260101-a" / "session.json").read_text())
    assert sj["state"] == "active"
    assert relay.binding_for_pair(shared, "20260101-a") == []        # NOT rebound


def test_move_failure_keeps_binding_and_pair(monkeypatch, tmp_path, capsys):
    """must-fix 2: bindings are dropped only AFTER a successful move. A dst
    collision makes the move a no-op, so the binding and live pair must survive."""
    shared = _shared(monkeypatch, tmp_path)
    _mk_pair(shared, "20260101-live")
    relay.join_pair(relay.load_env(), shared, "20260101-live")
    # pre-create the destination so _move_pair_dir's dst.exists() guard trips
    (_arc(shared) / "20260101-live").mkdir(parents=True)
    assert _archive("20260101-live", force=True) == 2
    assert "failed to archive" in capsys.readouterr().err
    assert (shared / "20260101-live" / "session.json").exists()      # pair stayed live
    assert relay.binding_for_pair(shared, "20260101-live")           # binding NOT dropped


def test_exdev_fails_closed_without_data_loss(monkeypatch, tmp_path):
    """must-fix 1: a cross-device rename fails closed, never a partial copy.
    The source pair is left intact and the command returns a protocol error."""
    shared = _shared(monkeypatch, tmp_path)
    _mk_pair(shared, "20260101-foo", state="closed", closed_sentinel=True)

    def boom(src, dst):
        raise OSError(errno.EXDEV, "cross-device link")
    monkeypatch.setattr(relay.os, "rename", boom)

    assert _archive("20260101-foo") == 2
    assert (shared / "20260101-foo" / "session.json").exists()       # source intact
