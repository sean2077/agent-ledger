"""Contract gate for frozen 1.0 surfaces (file-protocol.md §15.1).

Locks the *shape* of two frozen contract surfaces so a future refactor that
silently changes them fails loudly:
  - the publish triad: ``<base>.md`` + ``<base>.md.sha256`` + ``<base>.ready``,
    consumable only as a complete, hash-matching triad (§8, §10);
  - the instance binding key + record schema:
    ``_relay/bindings/<author>-<sha256(full-id)[:24]>.json``, schema 1 (§13).

These complement the *behavioral* tests in ``test_relay_claim_publish.py`` /
``test_relay_bindings.py``: those assert behavior; these assert the contract,
so a deliberate change must touch this file (and the §15.1 table) on purpose.
"""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import relay


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _bootstrap(monkeypatch, tmp_path: Path, topic="contract") -> Path:
    repo = tmp_path / "proj"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    monkeypatch.chdir(repo)
    for k in list(os.environ):
        if k.startswith("RELAY_"):
            monkeypatch.delenv(k, raising=False)
    shared = repo / ".shared"
    shared.mkdir(mode=0o700)
    (shared / "_relay").mkdir()
    (shared / "_relay" / ".sentinel").touch()
    monkeypatch.setenv("RELAY_SHARED_ROOT", str(shared))
    monkeypatch.setenv("RELAY_AUTHOR", "codex")
    monkeypatch.setenv("RELAY_AGENT_SESSION_ID", "contract-codex-window")
    relay.cmd_bootstrap(type("A", (), {"topic": topic, "title": None})())
    return relay.resolve_active_pair(relay.load_env())


def _publish_one(session: Path, capsys) -> Path:
    assert relay.cmd_claim(type("A", (), {
        "kind": "note", "in_reply_to": None, "corrects": None,
        "project": None, "pair_id": None,
    })()) == 0
    draft = Path(capsys.readouterr().out.strip())
    fm, _ = relay.parse_frontmatter(draft.read_text())
    fm["prompt_for_next"] = "do the thing\n"
    draft.write_text(relay.dump_frontmatter(fm, "\nreal body\n"))
    assert relay.cmd_publish(type("A", (), {
        "draft_path": str(draft), "status": None, "force": False,
        "force_reason": None, "project": None, "session_id": None,
    })()) == 0
    return Path(capsys.readouterr().out.strip())


# --------------------------------------------------------------------------
# publish triad contract (§8, §10, §15.1)
# --------------------------------------------------------------------------

def test_triad_member_names_are_frozen(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    md = _publish_one(session, capsys)
    base = md.name[:-3]  # drop .md
    paths = relay.published_paths(session, base)
    assert paths["md"] == session / f"{base}.md"
    assert paths["sha256"] == session / f"{base}.md.sha256"
    assert paths["ready"] == session / f"{base}.ready"
    assert all(paths[k].exists() for k in ("md", "sha256", "ready"))


def test_sha256_sidecar_format_and_match(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    md = _publish_one(session, capsys)
    parts = Path(f"{md}.sha256").read_text().split()
    assert len(parts) == 2  # sha256sum format: "<hex>  <name>"
    assert parts[0].lower() == relay.sha256_of_file(md).lower()
    assert parts[1] == md.name


def test_partial_triad_is_invisible(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    md = _publish_one(session, capsys)
    assert md in relay.list_published(session)
    (session / f"{md.name[:-3]}.ready").unlink()  # a .md without .ready
    assert md not in relay.list_published(session)


def test_tampered_md_breaks_triad(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    md = _publish_one(session, capsys)
    assert md in relay.list_published(session)
    md.write_text(md.read_text() + "\nINJECTED\n")  # hash no longer matches
    assert md not in relay.list_published(session)


# --------------------------------------------------------------------------
# binding registry contract (§13, §15.1)
# --------------------------------------------------------------------------

def test_binding_key_derivation_is_frozen():
    author, asid = "claude", "019e8348-dead-beef-0000-000000000001"
    expected = f"{author}-{hashlib.sha256(asid.encode()).hexdigest()[:24]}"
    assert relay.binding_key(author, asid) == expected


def test_binding_path_layout_is_frozen(tmp_path):
    root = tmp_path / ".shared"
    p = relay.binding_path(root, "codex", "sess-xyz")
    assert p.parent == root / "_relay" / "bindings"
    assert p.name == f"{relay.binding_key('codex', 'sess-xyz')}.json"


def test_binding_record_schema_is_v1_with_required_fields(tmp_path):
    root = tmp_path / ".shared"
    rec = {
        "schema_version": 1,
        "instance_id": relay.short_instance_id("codex", "sess-1"),
        "author": "codex", "agent_session_id": "sess-1",
        "pair_slug": "20260602-x",
        "bound_at": "2026-06-02T00:00:00+08:00",
        "last_seen": "2026-06-02T00:00:00+08:00",
    }
    relay.write_binding(root, rec)
    on_disk = json.loads(relay.binding_path(root, "codex", "sess-1").read_text())
    assert on_disk["schema_version"] == relay.BINDING_SCHEMA_VERSION == 1
    for field in ("schema_version", "instance_id", "author",
                  "agent_session_id", "pair_slug", "bound_at", "last_seen"):
        assert field in on_disk
