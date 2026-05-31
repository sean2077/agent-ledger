"""v0.14 identity layer: resolve_identity() author auto-detection + diagnostics,
resolve_peer() session-derived peer (fail-closed), default_peer(), and the
integration points that no longer require RELAY_AUTHOR / RELAY_PEER (bootstrap,
claim, preflight, whoami)."""

import json
import os

import relay


def _clear(monkeypatch):
    """Start from a known-empty identity baseline: no RELAY_* and no ambient
    platform/terminal signals (the pytest host has CLAUDE_CODE_SESSION_ID +
    ATUIN_SESSION set)."""
    for k in list(os.environ):
        if k.startswith("RELAY_"):
            monkeypatch.delenv(k, raising=False)
    for k in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID",
              "RELAY_AGENT_SESSION_ID", "ATUIN_SESSION"):
        monkeypatch.delenv(k, raising=False)


def _args(**kw):
    return type("A", (), kw)()


# --------------------------------------------------------------------------- #
# resolve_identity: author detection
# --------------------------------------------------------------------------- #

def test_author_from_claude_platform(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "claude-sess")
    ident = relay.resolve_identity()
    assert ident.author == "claude"
    assert ident.author_source == "platform-claude"
    assert ident.agent_session_id == "claude-sess"
    assert ident.session_source == "platform-claude"
    assert ident.diagnostics == []


def test_author_from_codex_platform(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("CODEX_THREAD_ID", "codex-thread")
    ident = relay.resolve_identity()
    assert ident.author == "codex"
    assert ident.author_source == "platform-codex"
    assert ident.agent_session_id == "codex-thread"
    assert ident.session_source == "platform-codex"


def test_author_falls_back_to_relay_author_for_custom_agent(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setattr(relay, "_terminal_signal", lambda: ("sig", "tty"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/tmp/relay-id-test-%d" % os.getpid())
    monkeypatch.setenv("RELAY_AUTHOR", "gpt55")
    ident = relay.resolve_identity()
    assert ident.author == "gpt55"
    assert ident.author_source == "env"
    assert ident.agent_session_id  # a fallback id was minted
    assert ident.session_source == "fallback-tty"


def test_author_none_when_no_signal_and_no_env(monkeypatch):
    _clear(monkeypatch)
    ident = relay.resolve_identity()
    assert ident.author is None
    assert ident.author_source == "none"
    assert ident.agent_session_id is None
    assert ident.session_source == "none"


def test_platform_wins_over_relay_author_and_flags_conflict(monkeypatch):
    """A stale RELAY_AUTHOR must not override the platform signal — the
    platform wins and the disagreement is recorded so preflight can nag."""
    _clear(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "claude-sess")
    monkeypatch.setenv("RELAY_AUTHOR", "codex")  # stale .envrc
    ident = relay.resolve_identity()
    assert ident.author == "claude"
    assert ident.author_source == "platform-claude"
    assert "author_conflict" in ident.diagnostics


def test_platform_match_relay_author_no_conflict(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("CODEX_THREAD_ID", "codex-thread")
    monkeypatch.setenv("RELAY_AUTHOR", "codex")  # agrees
    ident = relay.resolve_identity()
    assert ident.author == "codex"
    assert "author_conflict" not in ident.diagnostics


def test_dual_platform_disambiguated_by_relay_author(monkeypatch):
    """Both platform signals present (e.g. nested launch): refuse to guess by
    precedence; RELAY_AUTHOR disambiguates, and the session id is author-aware
    (codex's id, NOT claude's via the old precedence)."""
    _clear(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "claude-sess")
    monkeypatch.setenv("CODEX_THREAD_ID", "codex-thread")
    monkeypatch.setenv("RELAY_AUTHOR", "codex")
    ident = relay.resolve_identity()
    assert ident.author == "codex"
    assert ident.author_source == "env-disambiguated"
    assert "dual_platform" in ident.diagnostics
    assert ident.agent_session_id == "codex-thread"  # author-aware, not claude's
    assert ident.session_source == "platform-codex"


def test_dual_platform_without_disambiguation_is_none(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "claude-sess")
    monkeypatch.setenv("CODEX_THREAD_ID", "codex-thread")
    ident = relay.resolve_identity()
    assert ident.author is None
    assert "dual_platform" in ident.diagnostics
    assert ident.agent_session_id is None


def test_agent_session_id_override_does_not_change_author(monkeypatch):
    """RELAY_AGENT_SESSION_ID overrides only the session id; author still comes
    from the platform signal."""
    _clear(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "claude-sess")
    monkeypatch.setenv("RELAY_AGENT_SESSION_ID", "override-sess")
    ident = relay.resolve_identity()
    assert ident.author == "claude"
    assert ident.agent_session_id == "override-sess"
    assert ident.session_source == "env-override"


# --------------------------------------------------------------------------- #
# resolve_peer / default_peer
# --------------------------------------------------------------------------- #

def _write_session(tmp_path, participants):
    sd = tmp_path / "pair"
    sd.mkdir(exist_ok=True)
    (sd / "session.json").write_text(json.dumps({
        "schema_version": 3, "project": "p", "session_id": "pair",
        "state": "active", "participants": participants,
    }))
    return sd


def test_resolve_peer_normal(tmp_path):
    sd = _write_session(tmp_path, ["claude", "codex"])
    assert relay.resolve_peer(sd, "claude") == "codex"
    assert relay.resolve_peer(sd, "codex") == "claude"


def test_resolve_peer_fail_closed(tmp_path):
    # missing session.json
    assert relay.resolve_peer(tmp_path / "nope", "claude") is None
    # not exactly two participants
    assert relay.resolve_peer(_write_session(tmp_path, ["claude"]), "claude") is None
    assert relay.resolve_peer(
        _write_session(tmp_path, ["a", "b", "c"]), "a") is None
    # author not a participant
    assert relay.resolve_peer(
        _write_session(tmp_path, ["claude", "codex"]), "gpt55") is None
    # same-author pair leaves no distinct peer
    assert relay.resolve_peer(
        _write_session(tmp_path, ["claude", "claude"]), "claude") is None


def test_resolve_peer_malformed_json(tmp_path):
    sd = tmp_path / "pair"
    sd.mkdir()
    (sd / "session.json").write_text("{ not json")
    assert relay.resolve_peer(sd, "claude") is None


def test_default_peer():
    assert relay.default_peer("claude") == "codex"
    assert relay.default_peer("codex") == "claude"
    assert relay.default_peer("gpt55") is None


# --------------------------------------------------------------------------- #
# Integration: no RELAY_PEER needed
# --------------------------------------------------------------------------- #

def _git_repo(monkeypatch, tmp_path):
    import subprocess
    repo = tmp_path / "proj"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    monkeypatch.chdir(repo)
    return repo


def test_bootstrap_derives_peer_without_relay_peer(monkeypatch, tmp_path, capsys):
    repo = _git_repo(monkeypatch, tmp_path)
    _clear(monkeypatch)
    monkeypatch.setenv("CODEX_THREAD_ID", "codex-thread")  # author auto-detects
    rc = relay.cmd_bootstrap(_args(topic="t", title=None, peer=None, force=False))
    assert rc == 0
    sj = json.loads(next((repo / ".shared").glob("*/session.json")).read_text())
    assert sj["participants"] == ["codex", "claude"]  # peer auto-derived


def test_bootstrap_custom_author_requires_peer(monkeypatch, tmp_path, capsys):
    _git_repo(monkeypatch, tmp_path)
    _clear(monkeypatch)
    monkeypatch.setattr(relay, "_terminal_signal", lambda: ("sig", "tty"))
    monkeypatch.setenv("RELAY_AUTHOR", "gpt55")  # custom agent, no platform signal
    rc = relay.cmd_bootstrap(_args(topic="t", title=None, peer=None, force=False))
    assert rc == 2
    assert "cannot derive a peer" in capsys.readouterr().err
    # with --peer it succeeds
    rc = relay.cmd_bootstrap(_args(topic="t2", title=None, peer="claude", force=False))
    assert rc == 0


def test_bootstrap_rejects_same_agent_pair(monkeypatch, tmp_path, capsys):
    _git_repo(monkeypatch, tmp_path)
    _clear(monkeypatch)
    monkeypatch.setenv("CODEX_THREAD_ID", "codex-thread")
    rc = relay.cmd_bootstrap(_args(topic="same", title=None, peer="codex", force=False))
    assert rc == 2
    assert "--peer cannot equal author" in capsys.readouterr().err
    assert not any((tmp_path / "proj" / ".shared").glob("*/session.json"))


def test_claim_scaffolds_peer_from_session_without_relay_peer(monkeypatch, tmp_path, capsys):
    repo = _git_repo(monkeypatch, tmp_path)
    _clear(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "claude-sess")  # author=claude
    assert relay.cmd_bootstrap(_args(topic="t", title=None, peer=None, force=False)) == 0
    capsys.readouterr()
    rc = relay.cmd_claim(_args(kind="plan", in_reply_to=None, corrects=None,
                               project=None, pair_id=None))
    assert rc == 0
    draft = capsys.readouterr().out.strip()
    fm, _ = relay.parse_frontmatter(open(draft).read())
    assert fm["author"] == "claude"
    assert fm["peer"] == "codex"  # derived from participants, not RELAY_PEER


# --------------------------------------------------------------------------- #
# Integration: preflight + whoami identity surface
# --------------------------------------------------------------------------- #

def test_preflight_author_detected_without_relay_author(monkeypatch, tmp_path, capsys):
    repo = _git_repo(monkeypatch, tmp_path)
    shared = repo / ".shared"
    shared.mkdir()
    (shared / "_relay").mkdir()
    (shared / "_relay" / ".sentinel").touch()
    _clear(monkeypatch)
    monkeypatch.setenv("CODEX_THREAD_ID", "codex-thread")
    rc = relay.cmd_preflight(_args(json=True))
    data = json.loads(capsys.readouterr().out)
    chk = next(c for c in data["checks"] if c["name"] == "identity.author")
    assert chk["status"] == "pass"
    assert "codex" in chk["detail"]
    assert rc in (0, 1)  # not a fail on identity


def test_preflight_author_conflict_warns(monkeypatch, tmp_path, capsys):
    repo = _git_repo(monkeypatch, tmp_path)
    shared = repo / ".shared"
    shared.mkdir()
    (shared / "_relay").mkdir()
    (shared / "_relay" / ".sentinel").touch()
    _clear(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "claude-sess")
    monkeypatch.setenv("RELAY_AUTHOR", "codex")  # stale
    rc = relay.cmd_preflight(_args(json=True))
    data = json.loads(capsys.readouterr().out)
    conflict = next(c for c in data["checks"] if c["name"] == "identity.author_conflict")
    assert conflict["status"] == "warn"


def test_preflight_fails_when_dual_platform_ambiguous(monkeypatch, tmp_path, capsys):
    repo = _git_repo(monkeypatch, tmp_path)
    shared = repo / ".shared"
    shared.mkdir()
    (shared / "_relay").mkdir()
    (shared / "_relay" / ".sentinel").touch()
    _clear(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "claude-sess")
    monkeypatch.setenv("CODEX_THREAD_ID", "codex-thread")
    rc = relay.cmd_preflight(_args(json=True))
    data = json.loads(capsys.readouterr().out)
    chk = next(c for c in data["checks"] if c["name"] == "identity.author")
    assert chk["status"] == "fail"
    assert rc == 2


def test_whoami_surfaces_author_source_and_diagnostics(monkeypatch, capsys):
    _clear(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "claude-sess")
    monkeypatch.setenv("RELAY_AUTHOR", "codex")  # conflict
    rc = relay.cmd_whoami(_args(json=True))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["author"] == "claude"
    assert out["author_source"] == "platform-claude"
    assert "author_conflict" in out["diagnostics"]


# --------------------------------------------------------------------------- #
# Blocker regression (codex seq 6): claim must fail closed, never peer: unknown
# --------------------------------------------------------------------------- #

def _corrupt_participants(repo, participants):
    sj_path = next((repo / ".shared").glob("*/session.json"))
    data = json.loads(sj_path.read_text())
    data["participants"] = participants
    sj_path.write_text(json.dumps(data))
    return sj_path.parent


def test_claim_fail_closed_on_malformed_participants(monkeypatch, tmp_path, capsys):
    """cmd_claim must return 2 and write NO draft when the pair record can't
    yield a peer — never scaffold a `peer: unknown` artifact."""
    repo = _git_repo(monkeypatch, tmp_path)
    _clear(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "claude-sess")
    assert relay.cmd_bootstrap(_args(topic="t", title=None, peer=None, force=False)) == 0
    capsys.readouterr()
    session_dir = _corrupt_participants(repo, ["claude"])  # only one participant
    rc = relay.cmd_claim(_args(kind="plan", in_reply_to=None, corrects=None,
                               project=None, pair_id=None))
    assert rc == 2
    assert "cannot derive a peer" in capsys.readouterr().err
    assert list((session_dir / ".draft").glob("*.md")) == []  # nothing written


def test_claim_fail_closed_when_author_not_in_pair(monkeypatch, tmp_path, capsys):
    """If the resolved author isn't one of the participants, claim refuses
    rather than scaffolding a draft addressed to an unknown peer."""
    repo = _git_repo(monkeypatch, tmp_path)
    _clear(monkeypatch)
    monkeypatch.setenv("CODEX_THREAD_ID", "codex-thread")
    assert relay.cmd_bootstrap(_args(topic="t", title=None, peer=None, force=False)) == 0
    capsys.readouterr()
    session_dir = _corrupt_participants(repo, ["claude", "gpt55"])  # codex absent
    rc = relay.cmd_claim(_args(kind="plan", in_reply_to=None, corrects=None,
                               project=None, pair_id=None))
    assert rc == 2
    assert list((session_dir / ".draft").glob("*.md")) == []
