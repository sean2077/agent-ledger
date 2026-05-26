import json
from pathlib import Path

from awb import cli


def test_session_new_creates_layout(tmp_path: Path, capsys):
    rc = cli.main([
        "--ledger", str(tmp_path),
        "session", "new", "demo",
        "--project", "p", "--target", "claude", "--target", "gpt55",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "created session" in out
    # Find created dir
    project_dirs = list((tmp_path / "p").iterdir())
    assert len(project_dirs) == 1
    sp = project_dirs[0]
    assert (sp / "session.json").exists()
    assert (sp / "r1" / "prompts").is_dir()
    assert (sp / "events.ndjson").exists()


def test_status_default_finds_single_session(tmp_path: Path, capsys):
    cli.main([
        "--ledger", str(tmp_path),
        "session", "new", "only", "--project", "p", "--target", "claude",
    ])
    capsys.readouterr()
    rc = cli.main(["--ledger", str(tmp_path), "status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "only" in out and "claude" in out


def test_status_json(tmp_path: Path, capsys):
    cli.main([
        "--ledger", str(tmp_path),
        "session", "new", "only", "--project", "p", "--target", "claude",
    ])
    capsys.readouterr()
    cli.main(["--ledger", str(tmp_path), "status", "--json"])
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["project"] == "p"
    assert "1" in data["rounds"]
    assert data["rounds"]["1"]["targets"][0]["agent"] == "claude"


def test_status_ambiguous_requires_args(tmp_path: Path, capsys):
    cli.main(["--ledger", str(tmp_path), "session", "new", "a", "--project", "p", "--target", "claude"])
    cli.main(["--ledger", str(tmp_path), "session", "new", "b", "--project", "p", "--target", "claude"])
    capsys.readouterr()
    rc = cli.main(["--ledger", str(tmp_path), "status"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "multiple sessions" in err


def test_invalid_slug_returns_2(tmp_path: Path, capsys):
    rc = cli.main([
        "--ledger", str(tmp_path),
        "session", "new", "BAD SLUG", "--project", "p", "--target", "claude",
    ])
    assert rc == 2
    assert "slug must match" in capsys.readouterr().err
