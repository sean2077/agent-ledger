"""relay issue — machine-global feedback ledger (v0.10)."""

import json
import os
from pathlib import Path

import pytest

import relay


def _isolated_env(monkeypatch, issues_dir: Path, **kwargs):
    for k in list(os.environ):
        if k.startswith("RELAY_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RELAY_ISSUES_DIR", str(issues_dir))
    for k, v in kwargs.items():
        monkeypatch.setenv(k, v)


def _add(**kw):
    base = {"title": None, "severity": None, "area": None,
            "body": None, "body_file": None}
    base.update(kw)
    return type("A", (), base)()


def _list(**kw):
    base = {"status": None, "area": None, "json": False}
    base.update(kw)
    return type("A", (), base)()


# ---------------------------------------------------------------------------
# storage location
# ---------------------------------------------------------------------------

def test_issues_dir_defaults_to_home_agent_ledger(monkeypatch):
    for k in list(os.environ):
        if k.startswith("RELAY_"):
            monkeypatch.delenv(k, raising=False)
    expected = Path.home() / ".agent-ledger" / "relay-issues"
    assert relay.issues_dir() == expected


def test_issues_dir_honors_override(monkeypatch, tmp_path):
    monkeypatch.setenv("RELAY_ISSUES_DIR", str(tmp_path / "custom"))
    assert relay.issues_dir() == tmp_path / "custom"


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------

def test_issue_add_writes_file_with_frontmatter(monkeypatch, tmp_path, capsys):
    d = tmp_path / "issues"
    _isolated_env(monkeypatch, d, RELAY_AUTHOR="claude")
    rc = relay.cmd_issue_add(_add(title="publish swallowed detail",
                                   severity="major", area="cli",
                                   body="two publishers race, loser gets bare exit 2"))
    assert rc == 0
    path = Path(capsys.readouterr().out.strip())
    assert path.exists() and path.parent == d
    fm, body = relay.parse_frontmatter(path.read_text())
    assert fm["title"] == "publish swallowed detail"
    assert fm["severity"] == "major"
    assert fm["area"] == "cli"
    assert fm["reporter"] == "claude"
    assert fm["status"] == "open"
    assert fm["resolved_at"] is None
    assert "two publishers race" in body


def test_issue_add_requires_title(monkeypatch, tmp_path, capsys):
    _isolated_env(monkeypatch, tmp_path / "issues")
    rc = relay.cmd_issue_add(_add(title="   "))
    assert rc == 2
    assert "title" in capsys.readouterr().err.lower()


def test_issue_add_defaults_severity_minor_area_other(monkeypatch, tmp_path, capsys):
    d = tmp_path / "issues"
    _isolated_env(monkeypatch, d, RELAY_AUTHOR="codex")
    rc = relay.cmd_issue_add(_add(title="small thing"))
    assert rc == 0
    fm, _ = relay.parse_frontmatter(Path(capsys.readouterr().out.strip()).read_text())
    assert fm["severity"] == "minor"
    assert fm["area"] == "other"


def test_issue_add_rejects_bad_severity_and_area(monkeypatch, tmp_path, capsys):
    _isolated_env(monkeypatch, tmp_path / "issues")
    assert relay.cmd_issue_add(_add(title="x", severity="catastrophic")) == 2
    assert "severity" in capsys.readouterr().err.lower()
    assert relay.cmd_issue_add(_add(title="x", area="frontend")) == 2
    assert "area" in capsys.readouterr().err.lower()


def test_issue_add_rejects_body_and_body_file_together(monkeypatch, tmp_path, capsys):
    _isolated_env(monkeypatch, tmp_path / "issues")
    bf = tmp_path / "b.txt"; bf.write_text("x")
    rc = relay.cmd_issue_add(_add(title="x", body="inline", body_file=str(bf)))
    assert rc == 2
    assert "at most one" in capsys.readouterr().err


def test_issue_add_body_defaults_to_title(monkeypatch, tmp_path, capsys):
    d = tmp_path / "issues"
    _isolated_env(monkeypatch, d, RELAY_AUTHOR="claude")
    relay.cmd_issue_add(_add(title="just a title"))
    fm_path = Path(capsys.readouterr().out.strip())
    _, body = relay.parse_frontmatter(fm_path.read_text())
    assert body.strip() == "just a title"


def test_issue_add_reporter_unknown_without_author(monkeypatch, tmp_path, capsys):
    d = tmp_path / "issues"
    _isolated_env(monkeypatch, d)  # no RELAY_AUTHOR
    relay.cmd_issue_add(_add(title="anon"))
    fm, _ = relay.parse_frontmatter(Path(capsys.readouterr().out.strip()).read_text())
    assert fm["reporter"] == "unknown"
    assert fm["session"] is None  # no active session resolvable


# ---------------------------------------------------------------------------
# list / show / resolve
# ---------------------------------------------------------------------------

def _seed(monkeypatch, d, capsys, n):
    ids = []
    for i in range(n):
        relay.cmd_issue_add(_add(title=f"issue {i}", area="cli"))
        ids.append(Path(capsys.readouterr().out.strip()).stem)
    return ids


def test_issue_list_open_only_by_default(monkeypatch, tmp_path, capsys):
    d = tmp_path / "issues"
    _isolated_env(monkeypatch, d, RELAY_AUTHOR="claude")
    ids = _seed(monkeypatch, d, capsys, 3)
    # resolve one
    relay.cmd_issue_resolve(type("A", (), {"id": ids[0], "note": "done"})())
    capsys.readouterr()
    rc = relay.cmd_issue_list(_list())  # default open
    assert rc == 0
    out = capsys.readouterr().out
    assert ids[0] not in out  # resolved hidden
    assert ids[1] in out and ids[2] in out


def test_issue_list_all_and_json(monkeypatch, tmp_path, capsys):
    d = tmp_path / "issues"
    _isolated_env(monkeypatch, d, RELAY_AUTHOR="claude")
    ids = _seed(monkeypatch, d, capsys, 2)
    capsys.readouterr()
    rc = relay.cmd_issue_list(_list(status="all", json=True))
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    assert {r["id"] for r in rows} == set(ids)


def test_issue_list_empty(monkeypatch, tmp_path, capsys):
    d = tmp_path / "issues"
    _isolated_env(monkeypatch, d)
    rc = relay.cmd_issue_list(_list())
    assert rc == 0
    assert "no open issues" in capsys.readouterr().out


def test_issue_resolve_sets_status_and_note(monkeypatch, tmp_path, capsys):
    d = tmp_path / "issues"
    _isolated_env(monkeypatch, d, RELAY_AUTHOR="claude")
    relay.cmd_issue_add(_add(title="fixme", area="docs"))
    issue_id = Path(capsys.readouterr().out.strip()).stem
    rc = relay.cmd_issue_resolve(type("A", (), {"id": issue_id, "note": "fixed in deadbeef"})())
    assert rc == 0
    fm, _ = relay.parse_frontmatter((d / f"{issue_id}.md").read_text())
    assert fm["status"] == "resolved"
    assert fm["resolved_at"] is not None
    assert fm["resolution"] == "fixed in deadbeef"


def test_issue_resolve_by_unique_prefix(monkeypatch, tmp_path, capsys):
    d = tmp_path / "issues"
    _isolated_env(monkeypatch, d, RELAY_AUTHOR="claude")
    relay.cmd_issue_add(_add(title="prefixable"))
    full = Path(capsys.readouterr().out.strip()).stem
    prefix = full[:11]  # YYYYMMDDThh
    rc = relay.cmd_issue_resolve(type("A", (), {"id": prefix, "note": None})())
    # may be ambiguous if multiple in same hour; this test only seeds one
    assert rc == 0
    fm, _ = relay.parse_frontmatter((d / f"{full}.md").read_text())
    assert fm["status"] == "resolved"


def test_issue_show_missing_returns_2(monkeypatch, tmp_path, capsys):
    _isolated_env(monkeypatch, tmp_path / "issues")
    rc = relay.cmd_issue_show(type("A", (), {"id": "nope"})())
    assert rc == 2
    assert "no issue matching" in capsys.readouterr().err


def test_issue_resolve_missing_returns_2(monkeypatch, tmp_path, capsys):
    _isolated_env(monkeypatch, tmp_path / "issues")
    rc = relay.cmd_issue_resolve(type("A", (), {"id": "nope", "note": None})())
    assert rc == 2
    assert "no issue matching" in capsys.readouterr().err


def test_issue_list_area_filter(monkeypatch, tmp_path, capsys):
    d = tmp_path / "issues"
    _isolated_env(monkeypatch, d, RELAY_AUTHOR="claude")
    relay.cmd_issue_add(_add(title="cli one", area="cli"))
    relay.cmd_issue_add(_add(title="docs one", area="docs"))
    capsys.readouterr()
    relay.cmd_issue_list(_list(area="docs"))
    out = capsys.readouterr().out
    assert "docs one" in out
    assert "cli one" not in out


# ---------------------------------------------------------------------------
# CLI parser wiring (end-to-end via build_parser)
# ---------------------------------------------------------------------------

def test_issue_subcommands_are_wired(monkeypatch, tmp_path):
    parser = relay.build_parser()
    # add
    ns = parser.parse_args(["issue", "add", "--title", "t", "--area", "cli"])
    assert ns.func is relay.cmd_issue_add
    assert ns.title == "t" and ns.area == "cli"
    # list
    ns = parser.parse_args(["issue", "list", "--status", "all", "--json"])
    assert ns.func is relay.cmd_issue_list
    assert ns.status == "all" and ns.json is True
    # show
    ns = parser.parse_args(["issue", "show", "abc"])
    assert ns.func is relay.cmd_issue_show and ns.id == "abc"
    # resolve
    ns = parser.parse_args(["issue", "resolve", "abc", "--note", "n"])
    assert ns.func is relay.cmd_issue_resolve and ns.note == "n"
