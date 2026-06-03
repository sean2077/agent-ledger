"""relay statusline — compact 'which pair / whose turn' line.

Covers the render primitive (binding-scoped + pure-read + fail-quiet), the
state→text formatter for each lifecycle state, and the Claude-only install that
never clobbers a user's own statusLine. Codex has no command-backed statusline
(openai/codex#20140), so install is claude-only and the live surface there is
`--watch` / the Stop hook.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import relay

RELAY = Path(relay.__file__)  # the loaded relay script, for subprocess-level tests


def _clean_subprocess_env(**extra):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("RELAY_")
           and k not in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")}
    env.update(extra)
    return env


# ---------------------------------------------------------------------------
# helpers (mirror tests/test_relay_wait.py)
# ---------------------------------------------------------------------------


def _be(monkeypatch, author, window):
    monkeypatch.setenv("RELAY_AUTHOR", author)
    monkeypatch.setenv("RELAY_AGENT_SESSION_ID", window)


def _init_shared(monkeypatch, tmp_path):
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
    monkeypatch.setenv("RELAY_SHARED_ROOT", str(shared))
    return shared


def _bootstrap(monkeypatch, tmp_path, topic="t"):
    """codex creates + binds; peer = claude."""
    _init_shared(monkeypatch, tmp_path)
    _be(monkeypatch, "codex", "codex-window")
    relay.cmd_bootstrap(type("A", (), {"topic": topic, "title": None})())
    return relay.resolve_active_pair(relay.load_env())


def _publish_artifact(session, *, seq, author, peer, kind="review",
                      status="ready", prompt="do real things\n", body="ok body"):
    fm = {
        "seq": seq, "author": author, "peer": peer, "kind": kind,
        "status": status, "created": relay.now_iso(), "in_reply_to": None,
        "prompt_for_next": prompt, "sync_needed": False, "touched_paths": [],
        "corrects": None,
    }
    name = f"{seq:03d}-{author}-{kind}.md"
    md = session / name
    md.write_text(relay.dump_frontmatter(fm, f"\n{body}\n"))
    sha = relay.sha256_of_file(md)
    (session / f"{name}.sha256").write_text(f"{sha}  {name}\n")
    (session / f"{seq:03d}-{author}-{kind}.ready").touch()
    return md


def _sl_args(**kw):
    base = {"watch": False, "json": False, "interval": None, "pair_id": None,
            "no_color": True, "agent_session_id": None}
    base.update(kw)
    return type("A", (), base)()


def _render_json(**kw):
    """Call the render path in --json mode and return the parsed payload."""
    return relay.cmd_statusline(_sl_args(json=True, **kw))


# ---------------------------------------------------------------------------
# formatter unit tests (pure function, no fs)
# ---------------------------------------------------------------------------


def _fmt(**st):
    base = {"bound": True, "pair": "20260603-auth", "peer": "codex", "me": "claude",
            "state": "waiting", "seq": 7, "kind": "plan", "status": "ready",
            "artifact_author": "claude", "artifact_peer": "codex", "extra": None}
    base.update(st)
    return relay._statusline_format(base, color=False)


def test_format_your_move():
    line = _fmt(state="your_move", seq=8, kind="review")
    assert "YOUR MOVE" in line
    assert "auth" in line and "← codex" in line and "#8 review" in line
    # date prefix is stripped for brevity
    assert "20260603-" not in line


def test_format_waiting_with_peer_writing():
    line = _fmt(state="waiting", seq=7, kind="plan", extra="peer writing")
    assert "→ codex" in line and "waiting" in line
    assert "#7 plan" in line and "peer writing" in line


def test_format_peer_stale_paused_decision_terminal():
    assert "peer stale?" in _fmt(state="peer_stale")
    assert "@user" in _fmt(state="paused", status="timed_out")
    assert "decision" in _fmt(state="decision", seq=9, kind="decision")
    assert "closed" in _fmt(state="closed", status="closed")
    assert "failed" in _fmt(state="failed", status="failed")


def test_format_neutral_watcher_has_no_me():
    line = _fmt(state="neutral", me=None, peer=None, seq=4,
                artifact_author="codex", artifact_peer="claude", kind="fix")
    assert "codex→claude" in line and "#4" in line


def test_format_unbound_is_empty():
    assert relay._statusline_format({"bound": False}, color=False) == ""
    assert relay._statusline_format({}, color=False) == ""


def test_color_toggle():
    loud = {"bound": True, "pair": "x", "peer": "codex", "me": "claude",
            "state": "your_move", "seq": 1, "kind": "review", "status": "ready",
            "artifact_author": "codex", "artifact_peer": "claude", "extra": None}
    assert "\033[" in relay._statusline_format(loud, color=True)
    assert "\033[" not in relay._statusline_format(loud, color=False)


# ---------------------------------------------------------------------------
# render integration — binding-scoped state machine
# ---------------------------------------------------------------------------


def test_render_waiting_when_i_published_last(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    _publish_artifact(session, seq=1, author="codex", peer="claude", kind="plan")
    capsys.readouterr()
    _be(monkeypatch, "codex", "codex-window")
    rc = _render_json()
    payload = json.loads(capsys.readouterr().out.strip())
    assert rc == 0
    assert payload["bound"] is True
    assert payload["pair"] == session.name
    assert payload["state"] == "waiting"
    assert payload["peer"] == "claude"
    assert "→ claude" in payload["text"] and "waiting" in payload["text"]


def test_render_your_move_when_peer_published_last(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    _publish_artifact(session, seq=1, author="claude", peer="codex", kind="review")
    capsys.readouterr()
    _be(monkeypatch, "codex", "codex-window")
    _render_json()
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["state"] == "your_move"
    assert "YOUR MOVE" in payload["text"]


def test_render_paused_decision_and_terminal(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    _be(monkeypatch, "codex", "codex-window")

    _publish_artifact(session, seq=1, author="codex", peer="claude",
                      kind="question", status="timed_out")
    capsys.readouterr()
    _render_json()
    assert json.loads(capsys.readouterr().out.strip())["state"] == "paused"

    _publish_artifact(session, seq=2, author="codex", peer="claude", kind="decision")
    capsys.readouterr()
    _render_json()
    assert json.loads(capsys.readouterr().out.strip())["state"] == "decision"

    (session / "CLOSED").write_text('reason = "done"\n')
    capsys.readouterr()
    _render_json()
    assert json.loads(capsys.readouterr().out.strip())["state"] == "closed"


def test_render_fresh_pair_no_artifacts(monkeypatch, tmp_path, capsys):
    session = _bootstrap(monkeypatch, tmp_path)
    capsys.readouterr()
    _be(monkeypatch, "codex", "codex-window")
    _render_json()
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["state"] == "fresh"
    assert "new pair" in payload["text"]


# ---------------------------------------------------------------------------
# binding scope + pure-read invariant (issue 20260601T182646-2920d5b9)
# ---------------------------------------------------------------------------


def test_unbound_session_renders_empty_and_creates_no_binding(monkeypatch, tmp_path, capsys):
    """A window bound to NO pair must not be shown the lone active pair, and the
    render path must never create/mutate a binding (pure read)."""
    session = _bootstrap(monkeypatch, tmp_path)  # binds codex only
    _publish_artifact(session, seq=1, author="codex", peer="claude")
    shared = relay.load_env().shared_root
    capsys.readouterr()

    _be(monkeypatch, "claude", "claude-unbound-window")  # never joined
    # text mode: nothing printed
    rc = relay.cmd_statusline(_sl_args())
    assert rc == 0
    assert capsys.readouterr().out.strip() == ""
    # json mode: explicitly unbound
    _render_json()
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["bound"] is False
    assert payload["state"] == "unbound"
    # pure read: no binding file was written for this unbound instance
    bpath = relay.binding_path(shared, "claude", "claude-unbound-window")
    assert not bpath.exists()


def test_pair_id_overrides_binding(monkeypatch, tmp_path, capsys):
    """--pair-id renders a specific pair even from an unbound window."""
    session = _bootstrap(monkeypatch, tmp_path)
    _publish_artifact(session, seq=1, author="claude", peer="codex")
    capsys.readouterr()
    _be(monkeypatch, "codex", "some-other-window")  # not the bound window
    _render_json(pair_id=session.name)
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["pair"] == session.name
    assert payload["state"] == "your_move"


# ---------------------------------------------------------------------------
# fail-quiet
# ---------------------------------------------------------------------------


def test_fail_quiet_on_missing_shared_root(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    for k in list(os.environ):
        if k.startswith("RELAY_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RELAY_AUTHOR", "codex")
    monkeypatch.setenv("RELAY_AGENT_SESSION_ID", "w")
    monkeypatch.setenv("RELAY_SHARED_ROOT", str(tmp_path / "does-not-exist"))
    rc = relay.cmd_statusline(_sl_args())
    assert rc == 0
    assert capsys.readouterr().out.strip() == ""


# ---------------------------------------------------------------------------
# install / uninstall / doctor (Claude settings.json, single-slot protection)
# ---------------------------------------------------------------------------


def _claude_cfg(home):
    return home / ".claude" / "settings.json"


def _install_args(**kw):
    base = {"target": "claude", "dry_run": False, "force": False}
    base.update(kw)
    return type("A", (), base)()


def test_install_into_empty_writes_status_line(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    rc = relay.cmd_statusline_install(_install_args())
    assert rc == 0
    cfg = json.loads(_claude_cfg(tmp_path).read_text())
    assert cfg["statusLine"]["type"] == "command"
    assert " statusline" in cfg["statusLine"]["command"]
    assert relay._statusline_is_managed(cfg["statusLine"])


def test_install_refuses_to_clobber_existing(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg_path = _claude_cfg(tmp_path)
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(json.dumps({"statusLine": {"type": "command",
                                                   "command": "my-ccusage-thing"}}))
    rc = relay.cmd_statusline_install(_install_args())
    out = capsys.readouterr().out
    assert rc == 3
    assert "NOT overwriting" in out
    assert "statusline" in out  # prints the compose recipe
    # the user's statusLine is untouched
    assert json.loads(cfg_path.read_text())["statusLine"]["command"] == "my-ccusage-thing"


def test_install_force_replaces_then_uninstall_removes(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg_path = _claude_cfg(tmp_path)
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(json.dumps({"statusLine": {"type": "command",
                                                   "command": "mine"},
                                    "other": 1}))
    assert relay.cmd_statusline_install(_install_args(force=True)) == 0
    cfg = json.loads(cfg_path.read_text())
    assert relay._statusline_is_managed(cfg["statusLine"])
    assert cfg["other"] == 1  # unrelated keys preserved

    # uninstall removes only the managed statusLine, leaves the rest
    assert relay.cmd_statusline_uninstall(_install_args()) == 0
    cfg = json.loads(cfg_path.read_text())
    assert "statusLine" not in cfg
    assert cfg["other"] == 1


def test_uninstall_leaves_foreign_statusline(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg_path = _claude_cfg(tmp_path)
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(json.dumps({"statusLine": {"type": "command",
                                                   "command": "not-relay"}}))
    rc = relay.cmd_statusline_uninstall(_install_args())
    assert rc == 0
    assert json.loads(cfg_path.read_text())["statusLine"]["command"] == "not-relay"


def test_install_dry_run_writes_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    rc = relay.cmd_statusline_install(_install_args(dry_run=True))
    assert rc == 0
    assert not _claude_cfg(tmp_path).exists()


def test_install_codex_target_is_rejected(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    rc = relay.cmd_statusline_install(_install_args(target="codex"))
    out = capsys.readouterr().out
    assert rc == 2
    assert "--watch" in out  # points users at the Codex-side fallback


def test_doctor_reports_install_state(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    # settings.json exists but has no statusLine yet
    cfg_path = _claude_cfg(tmp_path)
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text("{}")
    assert relay.cmd_statusline_doctor(type("A", (), {})()) == 0
    out = capsys.readouterr().out
    assert "no statusLine configured" in out
    assert "openai/codex#20140" in out  # always names the Codex limitation
    # after install: relay-managed present
    relay.cmd_statusline_install(_install_args())
    capsys.readouterr()
    assert relay.cmd_statusline_doctor(type("A", (), {})()) == 0
    assert "relay-managed statusLine present" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# fail-quiet / no-hang at the process boundary (codex seq 2 must-fix)
# ---------------------------------------------------------------------------


def test_statusline_never_hangs_on_half_open_stdin(tmp_path):
    """A piped-but-no-payload stdin (a writer that holds the pipe OPEN without
    sending EOF, e.g. `sleep 5 | relay statusline`) must NOT block the footer:
    `sys.stdin.read()` would wait for EOF, so the bounded select-read must let
    the process exit on its own, well under the host's ~1s statusline budget."""
    shared = tmp_path / ".shared"
    (shared / "_relay").mkdir(parents=True)
    (shared / "_relay" / ".sentinel").touch()
    env = _clean_subprocess_env(RELAY_SHARED_ROOT=str(shared),
                                RELAY_AUTHOR="codex", RELAY_AGENT_SESSION_ID="w")
    proc = subprocess.Popen(
        [sys.executable, str(RELAY), "statusline", "--no-color"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, cwd=str(tmp_path),
    )
    try:
        # Deliberately DO NOT write or close proc.stdin -> the child sees a
        # half-open pipe with no data and no EOF. communicate() would close it,
        # so we wait() directly and read stdout only after the child exits.
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        pytest.fail("relay statusline hung on a half-open stdin")
    out = proc.stdout.read().decode()
    try:
        proc.stdin.close()
    except OSError:
        pass
    assert proc.returncode == 0
    assert out.strip() == ""  # no binding for this instance -> empty footer


def test_statusline_fail_quiet_outside_any_repo(tmp_path):
    """Run outside a git repo with no RELAY_SHARED_ROOT: helpers hard-exit
    (`SystemExit`, not a plain Exception) on no shared root, but the footer must
    still degrade to an empty line at exit 0 — never a rc-2 error in the bar."""
    proc = subprocess.run(
        [sys.executable, str(RELAY), "statusline", "--no-color"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=_clean_subprocess_env(), cwd=str(tmp_path), timeout=10,
    )
    assert proc.returncode == 0
    assert proc.stdout.decode().strip() == ""
