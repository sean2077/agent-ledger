"""Instance identity: resolve_instance_id precedence + fallback, short id,
load_env population, whoami, and --agent-session-id injection (v0.13 phase 1)."""

import json
import os

import relay


def _clear_id_env(monkeypatch):
    """Drop every signal resolve_instance_id reads so a test starts from a
    known baseline. The dev/CI shell running pytest under Claude Code has
    CLAUDE_CODE_SESSION_ID set, which would otherwise mask the fallback path."""
    for k in ("RELAY_AGENT_SESSION_ID", "CLAUDE_CODE_SESSION_ID",
              "CODEX_THREAD_ID", "ATUIN_SESSION"):
        monkeypatch.delenv(k, raising=False)


def _clear_relay_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("RELAY_"):
            monkeypatch.delenv(k, raising=False)


def test_no_author_returns_none(monkeypatch):
    _clear_id_env(monkeypatch)
    assert relay.resolve_instance_id(None) == (None, "none")
    assert relay.resolve_instance_id("") == (None, "none")


def test_override_beats_platform(monkeypatch):
    _clear_id_env(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "claude-sess")
    monkeypatch.setenv("CODEX_THREAD_ID", "codex-thread")
    monkeypatch.setenv("RELAY_AGENT_SESSION_ID", "override-sess")
    assert relay.resolve_instance_id("claude") == ("override-sess", "env-override")


def test_claude_then_codex_precedence(monkeypatch):
    _clear_id_env(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "claude-sess")
    monkeypatch.setenv("CODEX_THREAD_ID", "codex-thread")
    assert relay.resolve_instance_id("claude") == ("claude-sess", "platform-claude")
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    assert relay.resolve_instance_id("codex") == ("codex-thread", "platform-codex")


def test_fallback_mints_and_persists(monkeypatch, tmp_path):
    """A stable per-terminal signal (atuin/tty) -> mint, persist, and return the
    SAME id on the next call (so a binding survives across relay invocations)."""
    _clear_id_env(monkeypatch)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(relay, "_terminal_signal", lambda: ("ttyhash", "tty"))
    first_id, first_src = relay.resolve_instance_id("codex")
    assert first_id  # never empty -> never hard-fails
    assert first_src == "fallback-tty"
    second_id, second_src = relay.resolve_instance_id("codex")
    assert second_id == first_id  # persisted -> stable across calls
    assert second_src == first_src
    # a different author keys a different fallback file -> different id
    other_id, _ = relay.resolve_instance_id("claude")
    assert other_id != first_id


def test_fallback_degraded_is_ephemeral_not_shared(monkeypatch, tmp_path):
    """Degraded fallback (no per-window signal) must NOT persist a shared id:
    two calls (i.e. two same-author windows) get DIFFERENT ids, so they cannot
    collapse onto one binding/pair (codex code-review must-fix)."""
    _clear_id_env(monkeypatch)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(relay, "_terminal_signal", lambda: ("shared", "degraded"))
    a, a_src = relay.resolve_instance_id("claude")
    b, b_src = relay.resolve_instance_id("claude")
    assert a_src == b_src == "fallback-degraded"
    assert a != b  # ephemeral -> never shared between windows
    persisted = list((tmp_path / "relay").rglob("*.id")) if (tmp_path / "relay").is_dir() else []
    assert persisted == []  # nothing written to a shared file


def test_fallback_survives_unwritable_runtime_dir(monkeypatch, tmp_path):
    """Even if the fallback file can't be persisted, the call still returns a
    minted id for this run (best-effort, never raises)."""
    _clear_id_env(monkeypatch)
    monkeypatch.setattr(relay, "_terminal_signal", lambda: ("ttyhash", "tty"))
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(blocker))  # mkdir under a file fails
    got, src = relay.resolve_instance_id("codex")
    assert got
    assert src == "fallback-tty"


def test_short_instance_id_forms():
    assert relay.short_instance_id("claude", "ce0cfda4-36fb-4d6b") == "claude:ce0cfda4"
    assert relay.short_instance_id("codex", None) == "codex"
    assert relay.short_instance_id(None, "x") is None


def test_load_env_populates_instance_fields(monkeypatch, tmp_path):
    _clear_relay_env(monkeypatch)
    _clear_id_env(monkeypatch)
    monkeypatch.setenv("RELAY_AUTHOR", "claude")
    monkeypatch.setenv("RELAY_SHARED_ROOT", str(tmp_path / ".shared"))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "ce0cfda4-36fb")
    env = relay.load_env()
    assert env.agent_session_id == "ce0cfda4-36fb"
    assert env.agent_session_id_source == "platform-claude"
    assert env.instance_id == "claude:ce0cfda4"


def test_whoami_json_reflects_identity(monkeypatch, capsys):
    _clear_relay_env(monkeypatch)
    _clear_id_env(monkeypatch)
    monkeypatch.setenv("RELAY_AUTHOR", "codex")
    monkeypatch.setenv("CODEX_THREAD_ID", "019e7408-6a3a-76d0")
    rc = relay.cmd_whoami(type("A", (), {"json": True})())
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["author"] == "codex"
    assert out["agent_session_id"] == "019e7408-6a3a-76d0"
    assert out["agent_session_id_source"] == "platform-codex"
    assert out["instance_id"] == "codex:019e7408"


def test_main_injects_agent_session_id(monkeypatch, capsys):
    """`--agent-session-id` (how a hook passes its stdin session id) must reach
    instance resolution via RELAY_AGENT_SESSION_ID."""
    _clear_relay_env(monkeypatch)
    _clear_id_env(monkeypatch)  # records RELAY_AGENT_SESSION_ID pre-state -> restored on teardown
    monkeypatch.setenv("RELAY_AUTHOR", "claude")
    rc = relay.main(["whoami", "--json", "--agent-session-id", "sess-XYZ"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["agent_session_id"] == "sess-XYZ"
    assert out["agent_session_id_source"] == "env-override"
    assert out["instance_id"] == "claude:sess-XYZ"
