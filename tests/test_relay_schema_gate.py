"""Schema-version gates for session.json and binding records."""

import json
import os
import subprocess
from pathlib import Path

import pytest

import relay


def _bootstrap_env(monkeypatch, tmp_path: Path):
    repo = tmp_path / "myproj"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    monkeypatch.chdir(repo)
    shared = repo / ".shared"
    shared.mkdir(mode=0o700)
    (shared / "_relay").mkdir()
    (shared / "_relay" / ".sentinel").touch()
    for key in list(os.environ):
        if key.startswith("RELAY_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("RELAY_SYNC", "none")
    monkeypatch.setenv("RELAY_AUTHOR", "claude")
    monkeypatch.setenv("RELAY_AGENT_SESSION_ID", "schema-test-claude")
    monkeypatch.setenv("RELAY_SHARED_ROOT", str(shared))
    return shared


def _write_pair(shared: Path, slug: str = "20260601-schema", *, schema_version=3):
    session = shared / slug
    session.mkdir()
    (session / ".draft").mkdir()
    payload = {
        "schema_version": schema_version,
        "project": "myproj",
        "session_id": slug,
        "title": slug,
        "state": "active",
        "created_at": relay.now_iso(),
        "closed_at": None,
        "close_reason": None,
        "participants": ["claude", "codex"],
    }
    if schema_version is None:
        payload.pop("schema_version")
    (session / "session.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    return session


def _write_malformed_pair(shared: Path, slug: str = "20260601-malformed"):
    session = shared / slug
    session.mkdir()
    (session / "session.json").write_text("{ not json", encoding="utf-8")
    return session


def _status_args(slug: str):
    return type("A", (), {
        "project": None,
        "pair_id": slug,
        "require_binding": False,
        "last": 0,
        "json": True,
    })()


def _doctor_args(**kw):
    base = {"json": True, "fix": False, "older_than": None}
    base.update(kw)
    return type("A", (), base)()


def _pairs_args(**kw):
    base = {"json": True, "archived": False}
    base.update(kw)
    return type("A", (), base)()


def _preflight_args(**kw):
    base = {"json": True}
    base.update(kw)
    return type("A", (), base)()


def _whoami_args(**kw):
    base = {"json": True}
    base.update(kw)
    return type("A", (), base)()


def _write_binding(shared: Path, env, pair_slug: str, *, schema_version: int):
    bpath = relay.binding_path(shared, env.author, env.agent_session_id)
    bpath.parent.mkdir(parents=True, exist_ok=True)
    bpath.write_text(json.dumps({
        "schema_version": schema_version,
        "instance_id": env.instance_id,
        "author": env.author,
        "agent_session_id": env.agent_session_id,
        "pair_slug": pair_slug,
        "bound_at": relay.now_iso(),
        "last_seen": relay.now_iso(),
    }) + "\n", encoding="utf-8")
    return bpath


def test_status_refuses_future_session_schema(monkeypatch, tmp_path, capsys):
    shared = _bootstrap_env(monkeypatch, tmp_path)
    session = _write_pair(shared, schema_version=relay.SCHEMA_VERSION + 1)

    with pytest.raises(SystemExit) as exc:
        relay.cmd_status(_status_args(session.name))

    msg = str(exc.value)
    assert "schema_version" in msg
    assert "newer than this relay supports" in msg
    assert str(relay.SCHEMA_VERSION) in msg
    assert "upgrade relay" in msg


def test_status_refuses_missing_session_schema(monkeypatch, tmp_path, capsys):
    shared = _bootstrap_env(monkeypatch, tmp_path)
    session = _write_pair(shared, schema_version=None)

    with pytest.raises(SystemExit) as exc:
        relay.cmd_status(_status_args(session.name))

    assert "missing or non-integer" in str(exc.value)


def test_doctor_reports_future_session_schema_without_mutation(
    monkeypatch, tmp_path, capsys
):
    shared = _bootstrap_env(monkeypatch, tmp_path)
    session = _write_pair(shared, schema_version=relay.SCHEMA_VERSION + 1)
    before = (session / "session.json").read_text(encoding="utf-8")

    rc = relay.cmd_doctor(_doctor_args(fix=True, older_than="1h"))
    report = json.loads(capsys.readouterr().out)

    assert rc == 1
    err = report["sessions"][0]["schema_error"]
    assert err["code"] == "unsupported_schema"
    assert err["record_type"] == "session"
    assert (session / "session.json").read_text(encoding="utf-8") == before


def test_pairs_list_reports_future_session_schema(monkeypatch, tmp_path, capsys):
    shared = _bootstrap_env(monkeypatch, tmp_path)
    session = _write_pair(shared, schema_version=relay.SCHEMA_VERSION + 1)

    rc = relay.cmd_pairs_list(_pairs_args())
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    item = payload["pairs"][0]
    assert item["session_id"] == session.name
    assert item["category"] == "unsupported_schema"
    assert item["schema_error"]["record_type"] == "session"


def test_discovery_skips_unreadable_bystander_pairs_when_unbound(
    monkeypatch, tmp_path
):
    shared = _bootstrap_env(monkeypatch, tmp_path)
    valid = _write_pair(shared, "20260601-valid")
    future = _write_pair(
        shared,
        "20260601-future",
        schema_version=relay.SCHEMA_VERSION + 1,
    )
    malformed = _write_malformed_pair(shared)
    env = relay.load_env()

    assert [sd.name for sd in relay.active_pair_dirs(shared)] == [valid.name]
    assert relay.resolve_active_pair(env) == valid

    with pytest.raises(SystemExit) as exc:
        relay.resolve_pair(env, future.name)

    assert "session schema_version" in str(exc.value)
    assert "newer than this relay supports" in str(exc.value)
    assert (future / "session.json").exists()
    assert (malformed / "session.json").exists()


def test_preflight_reports_future_session_schema_without_mutation(
    monkeypatch, tmp_path, capsys
):
    shared = _bootstrap_env(monkeypatch, tmp_path)
    session = _write_pair(shared, schema_version=relay.SCHEMA_VERSION + 1)
    before = (session / "session.json").read_text(encoding="utf-8")

    rc = relay.cmd_preflight(_preflight_args())
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    details = "\n".join(c["detail"] for c in payload["checks"])
    assert "unsupported_schema" in details
    assert "session schema_version" in details
    assert (session / "session.json").read_text(encoding="utf-8") == before


def test_future_binding_blocks_resolution_without_deleting_or_fallback(
    monkeypatch, tmp_path
):
    shared = _bootstrap_env(monkeypatch, tmp_path)
    session = _write_pair(shared)
    env = relay.load_env()
    bpath = _write_binding(
        shared,
        env,
        session.name,
        schema_version=relay.BINDING_SCHEMA_VERSION + 1,
    )
    before = bpath.read_text(encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        relay.resolve_active_pair(env, require_binding=True)

    assert "binding schema_version" in str(exc.value)
    assert "newer than this relay supports" in str(exc.value)
    assert bpath.exists()
    assert bpath.read_text(encoding="utf-8") == before

    with pytest.raises(SystemExit):
        relay.resolve_active_pair(env)

    assert bpath.exists(), "future binding must not be deleted before fallback"


def test_doctor_reports_future_binding_without_deleting(monkeypatch, tmp_path, capsys):
    shared = _bootstrap_env(monkeypatch, tmp_path)
    _write_pair(shared)
    env = relay.load_env()
    bpath = _write_binding(
        shared,
        env,
        "20260601-schema",
        schema_version=relay.BINDING_SCHEMA_VERSION + 1,
    )

    rc = relay.cmd_doctor(_doctor_args(fix=True))
    report = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert report["unsupported_bindings"][0]["code"] == "unsupported_schema"
    assert report["unsupported_bindings"][0]["record_type"] == "binding"
    assert bpath.exists()


def test_whoami_reports_future_binding_without_deleting(monkeypatch, tmp_path, capsys):
    shared = _bootstrap_env(monkeypatch, tmp_path)
    session = _write_pair(shared)
    env = relay.load_env()
    bpath = _write_binding(
        shared,
        env,
        session.name,
        schema_version=relay.BINDING_SCHEMA_VERSION + 1,
    )

    rc = relay.cmd_whoami(_whoami_args())
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["schema_error"]["code"] == "unsupported_schema"
    assert payload["schema_error"]["record_type"] == "binding"
    assert bpath.exists()
