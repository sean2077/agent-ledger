"""Relay-created file and directory permissions."""

import os
import subprocess
from pathlib import Path

import relay


def _clear_relay_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("RELAY_"):
            monkeypatch.delenv(key, raising=False)


def _args(**kw):
    return type("A", (), kw)()


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def _fill_draft(draft: Path) -> None:
    fm, _ = relay.parse_frontmatter(draft.read_text(encoding="utf-8"))
    fm["prompt_for_next"] = "review file-mode contract\n"
    draft.write_text(relay.dump_frontmatter(fm, "\nbody.\n"), encoding="utf-8")


def test_relay_created_files_are_0600_and_dirs_are_0700(monkeypatch, tmp_path, capsys):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    monkeypatch.chdir(repo)
    shared = repo / ".shared"
    issues_dir = tmp_path / "issues"

    _clear_relay_env(monkeypatch)
    monkeypatch.setenv("RELAY_SHARED_ROOT", str(shared))
    monkeypatch.setenv("RELAY_ISSUES_DIR", str(issues_dir))
    monkeypatch.setenv("RELAY_AUTHOR", "codex")
    monkeypatch.setenv("RELAY_AGENT_SESSION_ID", "mode-test-codex")

    assert relay.cmd_init(_args(same_host=False, author=None, sync=None)) == 0
    assert relay.cmd_bootstrap(
        _args(topic="modes", title=None, peer=None, force=False)
    ) == 0
    session = next(shared.glob("20*-modes"))
    binding = relay.binding_path(shared, "codex", "mode-test-codex")
    capsys.readouterr()

    assert binding.exists()
    assert _mode(binding) == 0o600

    assert relay.cmd_claim(
        _args(
            kind="plan",
            in_reply_to=None,
            corrects=None,
            project=None,
            pair_id=None,
            no_heartbeat=True,
        )
    ) == 0
    draft = Path(capsys.readouterr().out.strip())
    _fill_draft(draft)
    assert relay.cmd_publish(
        _args(draft_path=str(draft), status=None, force=False, force_reason=None)
    ) == 0
    published = Path(capsys.readouterr().out.strip())

    assert relay.cmd_close(
        _args(reason="mode contract verified", outcome=None, project=None, pair_id=None)
    ) == 0
    capsys.readouterr()
    assert relay.cmd_issue_add(
        _args(
            title="mode issue",
            severity=None,
            area=None,
            body=None,
            body_file=None,
        )
    ) == 0
    issue_file = Path(capsys.readouterr().out.strip())

    files_0600 = [
        shared / "_relay" / ".sentinel",
        session / "session.json",
        session / "README.md",
        published,
        session / f"{published.name}.sha256",
        session / f"{published.stem}.ready",
        session / "CLOSED",
        issue_file,
    ]
    for path in files_0600:
        assert path.exists(), path
        assert _mode(path) == 0o600, path

    dirs_0700 = [
        shared,
        shared / "_relay",
        session,
        session / ".draft",
        issue_file.parent,
    ]
    for path in dirs_0700:
        assert path.exists(), path
        assert _mode(path) == 0o700, path
