"""Instance binding registry: key collision-safety, CRUD, staleness, join_pair
capacity / same-author / stale-reclaim, and throttled last_seen (v0.13 phase 2)."""

import json
from pathlib import Path

import relay


def _mk_session(root: Path, slug: str, *, state: str = "active",
                closed: bool = False) -> Path:
    session = root / slug
    session.mkdir(parents=True)
    (session / "session.json").write_text(json.dumps({
        "schema_version": 3, "project": "p", "session_id": slug, "title": slug,
        "state": state, "created_at": "2026-05-29T00:00:00+08:00",
        "closed_at": None, "close_reason": None, "participants": [],
    }))
    if closed:
        (session / "CLOSED").write_text('reason = "x"\n')
    return session


def _env(author: str, asid: str, root: Path) -> "relay.Env":
    return relay.Env(
        author=author,
        peer="codex" if author == "claude" else "claude",
        shared_root=root, shared_root_source="env", project="p",
        remote_ssh=None, remote_path=None, sync_raw=None,
        agent_session_id=asid, agent_session_id_source="platform-test",
        instance_id=relay.short_instance_id(author, asid),
    )


def _bind(root: Path, author: str, asid: str, pair: str, *, last_seen: str) -> None:
    relay.write_binding(root, {
        "schema_version": 1, "instance_id": relay.short_instance_id(author, asid),
        "author": author, "agent_session_id": asid, "pair_slug": pair,
        "bound_at": "2026-05-29T00:00:00+08:00", "last_seen": last_seen,
    })


# --- binding_key: the gate-#1 collision fix --------------------------------

def test_binding_key_uses_full_id_not_short_prefix():
    # Two Codex-style time-prefixed ids that share the first 8 chars must NOT
    # collide on the binding key (the [:8] bug codex flagged).
    a = "019e7408-6a3a-76d0-9393-cc8a4fed5512"
    b = "019e7408-9999-7000-0000-000000000000"
    assert a[:8] == b[:8]
    assert relay.binding_key("codex", a) != relay.binding_key("codex", b)
    # same id -> stable key; key is filename-safe (no ':' '/' whitespace)
    k = relay.binding_key("codex", a)
    assert relay.binding_key("codex", a) == k
    assert all(c not in k for c in ':/ \t')


# --- CRUD round-trip --------------------------------------------------------

def test_binding_crud_roundtrip(tmp_path):
    root = tmp_path / ".shared"
    root.mkdir()
    assert relay.read_binding(root, "claude", "id1") is None
    assert relay.list_bindings(root) == []
    _bind(root, "claude", "id1", "20260529-x", last_seen=relay.now_iso())
    got = relay.read_binding(root, "claude", "id1")
    assert got["pair_slug"] == "20260529-x" and got["author"] == "claude"
    assert len(relay.list_bindings(root)) == 1
    relay.delete_binding(root, "claude", "id1")
    assert relay.read_binding(root, "claude", "id1") is None
    relay.delete_binding(root, "claude", "id1")  # idempotent, no raise


def test_binding_for_pair_filters(tmp_path):
    root = tmp_path / ".shared"
    root.mkdir()
    _bind(root, "claude", "id1", "pairA", last_seen=relay.now_iso())
    _bind(root, "codex", "id2", "pairA", last_seen=relay.now_iso())
    _bind(root, "claude", "id3", "pairB", last_seen=relay.now_iso())
    assert len(relay.binding_for_pair(root, "pairA")) == 2
    assert len(relay.binding_for_pair(root, "pairB")) == 1
    assert relay.binding_for_pair(root, "nope") == []


# --- staleness --------------------------------------------------------------

def test_binding_is_stale():
    assert relay._binding_is_stale({"last_seen": "2020-01-01T00:00:00+08:00"}) is True
    assert relay._binding_is_stale({"last_seen": relay.now_iso()}) is False
    assert relay._binding_is_stale({}) is True            # missing -> stale
    assert relay._binding_is_stale({"last_seen": "garbage"}) is True


# --- join_pair --------------------------------------------------------------

def test_join_unknown_or_inactive_pair_refused(tmp_path):
    root = tmp_path / ".shared"
    root.mkdir()
    env = _env("claude", "id1", root)
    assert relay.join_pair(env, root, "20260529-missing") == 2
    _mk_session(root, "20260529-closed", state="closed", closed=True)
    assert relay.join_pair(env, root, "20260529-closed") == 2


def test_join_happy_and_idempotent(tmp_path):
    root = tmp_path / ".shared"
    root.mkdir()
    _mk_session(root, "20260529-x")
    env = _env("claude", "id1", root)
    assert relay.join_pair(env, root, "20260529-x") == 0
    b = relay.read_binding(root, "claude", "id1")
    assert b["pair_slug"] == "20260529-x"
    first_bound = b["bound_at"]
    assert relay.join_pair(env, root, "20260529-x") == 0  # idempotent
    assert relay.read_binding(root, "claude", "id1")["bound_at"] == first_bound


def test_join_rejects_same_author_live(tmp_path):
    root = tmp_path / ".shared"
    root.mkdir()
    _mk_session(root, "20260529-x")
    assert relay.join_pair(_env("claude", "id1", root), root, "20260529-x") == 0
    # a second LIVE claude instance must be refused (unroutable pair)
    assert relay.join_pair(_env("claude", "id2", root), root, "20260529-x") == 2
    assert len(relay.binding_for_pair(root, "20260529-x")) == 1


def test_join_full_pair_refused_then_reclaims_stale(tmp_path):
    root = tmp_path / ".shared"
    root.mkdir()
    _mk_session(root, "20260529-x")
    relay.join_pair(_env("claude", "id1", root), root, "20260529-x")
    relay.join_pair(_env("codex", "id2", root), root, "20260529-x")
    # full with 2 live -> third (distinct author) refused
    assert relay.join_pair(_env("gpt", "id3", root), root, "20260529-x") == 2
    # make codex's slot stale -> third can reclaim it
    _bind(root, "codex", "id2", "20260529-x", last_seen="2020-01-01T00:00:00+08:00")
    assert relay.join_pair(_env("gpt", "id3", root), root, "20260529-x") == 0
    authors = {b["author"] for b in relay.binding_for_pair(root, "20260529-x")}
    assert authors == {"claude", "gpt"}  # stale codex reclaimed


def test_join_moves_instance_between_pairs(tmp_path):
    root = tmp_path / ".shared"
    root.mkdir()
    _mk_session(root, "20260529-a")
    _mk_session(root, "20260529-b")
    env = _env("claude", "id1", root)
    relay.join_pair(env, root, "20260529-a")
    relay.join_pair(env, root, "20260529-b")
    assert relay.binding_for_pair(root, "20260529-a") == []
    assert len(relay.binding_for_pair(root, "20260529-b")) == 1
    assert relay.read_binding(root, "claude", "id1")["pair_slug"] == "20260529-b"


# --- last_seen throttle -----------------------------------------------------

def test_maybe_update_last_seen(tmp_path):
    root = tmp_path / ".shared"
    root.mkdir()
    env = _env("claude", "id1", root)
    # unbound -> no-op, still no binding
    relay._maybe_update_binding_last_seen(env, root)
    assert relay.read_binding(root, "claude", "id1") is None
    # stale binding -> refreshed
    _bind(root, "claude", "id1", "pairA", last_seen="2020-01-01T00:00:00+08:00")
    relay._maybe_update_binding_last_seen(env, root)
    assert relay.read_binding(root, "claude", "id1")["last_seen"] != "2020-01-01T00:00:00+08:00"
    # fresh binding -> throttled (unchanged)
    fresh = relay.now_iso()
    _bind(root, "claude", "id1", "pairA", last_seen=fresh)
    relay._maybe_update_binding_last_seen(env, root)
    assert relay.read_binding(root, "claude", "id1")["last_seen"] == fresh
