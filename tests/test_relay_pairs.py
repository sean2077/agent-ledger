"""Pair commands: whoami binding display, pair join / leave, and the
`pair ensure` smart decision table (v0.13 phase 3). The underlying join_pair
logic is unit-tested in test_relay_bindings.py; this covers the CLI wrappers
and ensure's action selection across multiple instances."""

import json
import os
from pathlib import Path

import relay


def _mk_session(root: Path, slug: str, *, closed: bool = False) -> Path:
    sd = root / slug
    sd.mkdir(parents=True)
    (sd / "session.json").write_text(json.dumps({
        "schema_version": 3, "project": "p", "session_id": slug, "title": slug,
        "state": "active", "created_at": "2026-05-29T00:00:00+08:00",
        "closed_at": None, "close_reason": None, "participants": [],
    }))
    if closed:
        (sd / "CLOSED").write_text('reason = "x"\n')
    return sd


def _shared(monkeypatch, tmp_path) -> Path:
    shared = tmp_path / ".shared"
    shared.mkdir()
    for k in list(os.environ):
        if k.startswith("RELAY_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RELAY_SHARED_ROOT", str(shared))
    return shared


def _instance(monkeypatch, author: str, asid: str, peer: str = "codex") -> None:
    """Pin a deterministic instance identity via the override env var (beats
    any ambient CLAUDE_CODE_SESSION_ID)."""
    monkeypatch.setenv("RELAY_AUTHOR", author)
    monkeypatch.setenv("RELAY_PEER", peer)
    monkeypatch.setenv("RELAY_AGENT_SESSION_ID", asid)


def _args(**kw):
    return type("A", (), kw)()


def _join(capsys, slug: str) -> int:
    rc = relay.cmd_pair_join(_args(slug=slug))
    capsys.readouterr()  # drain the "bound ..." line so later JSON reads are clean
    return rc


def _whoami(capsys) -> dict:
    relay.cmd_whoami(_args(json=True))
    return json.loads(capsys.readouterr().out)


def _ensure(capsys, **kw):
    rc = relay.cmd_pair_ensure(_args(json=True, **kw))
    return rc, json.loads(capsys.readouterr().out)


def test_whoami_join_leave_roundtrip(monkeypatch, tmp_path, capsys):
    shared = _shared(monkeypatch, tmp_path)
    _instance(monkeypatch, "claude", "id-aaa")
    _mk_session(shared, "20260529-x")
    assert _whoami(capsys)["bound_pair"] is None
    assert _join(capsys, "20260529-x") == 0
    assert _whoami(capsys)["bound_pair"] == "20260529-x"
    assert relay.cmd_pair_leave(_args()) == 0
    capsys.readouterr()
    assert _whoami(capsys)["bound_pair"] is None


def test_pair_join_unknown_refused(monkeypatch, tmp_path, capsys):
    _shared(monkeypatch, tmp_path)
    _instance(monkeypatch, "claude", "id-aaa")
    assert relay.cmd_pair_join(_args(slug="20260529-nope")) == 2


def test_ensure_use_when_bound(monkeypatch, tmp_path, capsys):
    shared = _shared(monkeypatch, tmp_path)
    _instance(monkeypatch, "claude", "id-aaa")
    _mk_session(shared, "20260529-x")
    _join(capsys, "20260529-x")
    rc, out = _ensure(capsys)
    assert rc == 0 and out["action"] == "use" and out["pair"] == "20260529-x"


def test_ensure_bootstrap_when_no_pairs(monkeypatch, tmp_path, capsys):
    _shared(monkeypatch, tmp_path)
    _instance(monkeypatch, "claude", "id-aaa")
    rc, out = _ensure(capsys)
    assert rc == 3 and out["action"] == "bootstrap"


def test_ensure_auto_joins_sole_compatible(monkeypatch, tmp_path, capsys):
    shared = _shared(monkeypatch, tmp_path)
    _instance(monkeypatch, "claude", "id-aaa")
    _mk_session(shared, "20260529-only")
    rc, out = _ensure(capsys)
    assert rc == 0 and out["action"] == "joined" and out["pair"] == "20260529-only"
    assert relay.read_binding(shared, "claude", "id-aaa")["pair_slug"] == "20260529-only"


def test_ensure_rediscovers_after_bound_pair_closes(monkeypatch, tmp_path, capsys):
    shared = _shared(monkeypatch, tmp_path)
    _instance(monkeypatch, "claude", "id-aaa")
    _mk_session(shared, "20260529-x")
    _mk_session(shared, "20260529-y")
    _join(capsys, "20260529-x")
    (shared / "20260529-x" / "CLOSED").write_text('reason = "x"\n')  # x now inactive
    rc, out = _ensure(capsys)  # stale binding dropped -> only y joinable
    assert rc == 0 and out["action"] == "joined" and out["pair"] == "20260529-y"


def test_ensure_choose_when_multiple_joinable(monkeypatch, tmp_path, capsys):
    shared = _shared(monkeypatch, tmp_path)
    _instance(monkeypatch, "claude", "id-aaa")
    _mk_session(shared, "20260529-a")
    _mk_session(shared, "20260529-b")
    rc, out = _ensure(capsys)
    assert rc == 3 and out["action"] == "choose"
    assert {c["slug"] for c in out["candidates"]} == {"20260529-a", "20260529-b"}


def test_ensure_full_when_only_pair_is_occupied(monkeypatch, tmp_path, capsys):
    shared = _shared(monkeypatch, tmp_path)
    _mk_session(shared, "20260529-x")
    _instance(monkeypatch, "claude", "occ-1")
    _join(capsys, "20260529-x")
    _instance(monkeypatch, "codex", "occ-2")
    _join(capsys, "20260529-x")
    _instance(monkeypatch, "gpt", "id-ccc")  # third instance, pair full
    rc, out = _ensure(capsys)
    assert rc == 3 and out["action"] == "full"


def test_ensure_excludes_same_author_pair(monkeypatch, tmp_path, capsys):
    shared = _shared(monkeypatch, tmp_path)
    _mk_session(shared, "20260529-x")
    _instance(monkeypatch, "claude", "first-claude")
    _join(capsys, "20260529-x")
    # a DIFFERENT claude instance: x already has a live claude -> unroutable, so
    # it's excluded; x is the only pair -> nothing joinable -> full
    _instance(monkeypatch, "claude", "id-aaa")
    rc, out = _ensure(capsys)
    assert rc == 3 and out["action"] == "full"


def _degrade(monkeypatch) -> None:
    """Force an unresolvable (degraded) instance identity: no override / platform
    id and no per-window signal."""
    for k in ("RELAY_AGENT_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RELAY_AUTHOR", "claude")
    monkeypatch.setenv("RELAY_PEER", "codex")
    monkeypatch.setattr(relay, "_terminal_signal", lambda: ("shared", "degraded"))


def test_ensure_degraded_never_auto_binds(monkeypatch, tmp_path, capsys):
    """Two same-author windows with an unresolvable (degraded) id must never
    silently share a pair: ensure returns `degraded`, never use/joined, and
    writes no binding (codex code-review must-fix regression)."""
    shared = _shared(monkeypatch, tmp_path)
    _mk_session(shared, "20260530-x")
    _degrade(monkeypatch)
    rc1, out1 = _ensure(capsys)   # window 1
    rc2, out2 = _ensure(capsys)   # window 2 (same author, same degraded host)
    assert out1["action"] == "degraded" and rc1 == 3
    assert out2["action"] == "degraded" and rc2 == 3
    assert relay.list_bindings(shared) == []  # neither auto-bound -> no sharing


def test_pair_join_refused_when_degraded(monkeypatch, tmp_path, capsys):
    """Explicit join is also refused while degraded (a shared key would let two
    windows overwrite one binding); the user must pass --pair-id instead."""
    shared = _shared(monkeypatch, tmp_path)
    _mk_session(shared, "20260530-x")
    _degrade(monkeypatch)
    assert relay.cmd_pair_join(_args(slug="20260530-x")) == 2
    assert relay.list_bindings(shared) == []
