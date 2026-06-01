"""Subprocess onboarding smoke for the real relay CLI.

This is intentionally higher level than the unit tests: it runs the checked-out
`bin/relay` through `subprocess` with clean per-instance environments, so the
test covers argparse, `__main__` dispatch, default shared-root discovery, exit
codes, stdout/stderr, and published triads as a new user would see them.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _resolve_relay() -> Path:
    """Mirror the skill's checked-out-binary preference for tests."""
    candidates = [
        os.environ.get("RELAY_BIN"),
        ROOT / ".agents" / "skills" / "agent-relay" / "bin" / "relay",
        ROOT / ".claude" / "skills" / "agent-relay" / "bin" / "relay",
        ROOT / ".codex" / "skills" / "agent-relay" / "bin" / "relay",
        ROOT / "skills" / "agent-relay" / "bin" / "relay",
        shutil.which("relay"),
        Path.home() / ".codex" / "skills" / "agent-relay" / "bin" / "relay",
        Path.home() / ".claude" / "skills" / "agent-relay" / "bin" / "relay",
        Path.home() / ".agents" / "skills" / "agent-relay" / "bin" / "relay",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_file():
            return path
    raise AssertionError("could not resolve relay binary")


RELAY = _resolve_relay()


def _new_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "onboarding-project"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    return repo


def _env(tmp_path: Path, author: str, session_id: str) -> dict[str, str]:
    return {
        "HOME": str(tmp_path / f"home-{session_id}"),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONIOENCODING": "utf-8",
        "RELAY_SYNC": "none",
        "RELAY_AUTHOR": author,
        "RELAY_AGENT_SESSION_ID": session_id,
        "RELAY_CLAIM_NO_HEARTBEAT": "1",
    }


def _run(repo: Path, env: dict[str, str], *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RELAY), *argv],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _pair_dir(repo: Path) -> Path:
    pairs = [
        p for p in (repo / ".shared").iterdir()
        if p.is_dir() and p.name != "_relay"
    ]
    assert len(pairs) == 1
    return pairs[0]


def _assert_triad(md: Path) -> None:
    assert md.is_file()
    assert (md.parent / f"{md.name}.sha256").is_file()
    assert (md.parent / f"{md.stem}.ready").is_file()


def _draft_set(
    repo: Path,
    env: dict[str, str],
    draft: Path,
    tmp_path: Path,
    *,
    body: str,
    prompt: str,
) -> subprocess.CompletedProcess[str]:
    body_file = tmp_path / f"{draft.stem}-body.md"
    prompt_file = tmp_path / f"{draft.stem}-prompt.txt"
    body_file.write_text(body, encoding="utf-8")
    prompt_file.write_text(prompt, encoding="utf-8")
    return _run(
        repo,
        env,
        "draft",
        "set",
        str(draft),
        "--body-file",
        str(body_file),
        "--prompt-for-next-file",
        str(prompt_file),
    )


def test_onboarding_happy_path_real_cli(tmp_path):
    repo = _new_repo(tmp_path)
    claude = _env(tmp_path, "claude", "claude-window")
    codex = _env(tmp_path, "codex", "codex-window")

    res = _run(repo, claude, "init")
    assert res.returncode == 0, res.stderr
    assert "created:" in res.stdout
    assert "RELAY_SHARED_ROOT not set" in res.stderr
    assert (repo / ".shared" / "_relay" / ".sentinel").is_file()

    res = _run(repo, claude, "preflight")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "env.RELAY_SHARED_ROOT" in res.stdout
    assert "summary:" in res.stdout

    res = _run(repo, claude, "bootstrap", "--topic", "smoke")
    assert res.returncode == 0, res.stderr
    assert "created pair:" in res.stdout
    pair = _pair_dir(repo)
    session = json.loads((pair / "session.json").read_text(encoding="utf-8"))
    assert session["participants"] == ["claude", "codex"]

    res = _run(repo, claude, "claim", "--kind", "plan")
    assert res.returncode == 0, res.stderr
    claude_draft = Path(res.stdout.strip())
    assert claude_draft.parent == pair / ".draft"

    res = _draft_set(
        repo,
        claude,
        claude_draft,
        tmp_path,
        body="## Plan\n\nReview this onboarding smoke.\n",
        prompt="Review the plan and publish a response.\n",
    )
    assert res.returncode == 0, res.stderr

    res = _run(repo, claude, "publish", str(claude_draft))
    assert res.returncode == 0, res.stderr
    plan_md = Path(res.stdout.strip())
    _assert_triad(plan_md)
    assert not claude_draft.exists()

    res = _run(repo, codex, "pair", "ensure", "--json")
    assert res.returncode == 0, res.stderr
    ensure = json.loads(res.stdout)
    assert ensure["action"] == "joined"
    assert ensure["pair"] == pair.name

    res = _run(repo, codex, "status", "--json")
    assert res.returncode == 0, res.stderr
    status = json.loads(res.stdout)
    assert status["bound_pair"] == pair.name
    assert status["next_seq"] == 2
    assert len(status["published"]) == 1
    assert status["published"][0]["peer"] == "codex"

    res = _run(repo, codex, "wait", "--timeout", "1", "--poll", "1")
    assert res.returncode == 0, res.stderr
    assert Path(res.stdout.strip()) == plan_md

    res = _run(repo, codex, "claim", "--kind", "review", "--in-reply-to", "1")
    assert res.returncode == 0, res.stderr
    codex_draft = Path(res.stdout.strip())
    assert codex_draft.parent == pair / ".draft"

    res = _draft_set(
        repo,
        codex,
        codex_draft,
        tmp_path,
        body="## Review\n\nLooks good from the smoke path.\n",
        prompt="Inspect the two-artifact smoke result.\n",
    )
    assert res.returncode == 0, res.stderr

    res = _run(repo, codex, "publish", str(codex_draft))
    assert res.returncode == 0, res.stderr
    review_md = Path(res.stdout.strip())
    _assert_triad(review_md)
    assert not codex_draft.exists()

    res = _run(repo, claude, "status", "--json")
    assert res.returncode == 0, res.stderr
    status = json.loads(res.stdout)
    assert status["bound_pair"] == pair.name
    assert status["next_seq"] == 3
    assert [item["seq"] for item in status["published"]] == [1, 2]
    assert status["published"][1]["path"] == review_md.name


def test_onboarding_claim_before_binding_has_recovery_hint(tmp_path):
    repo = _new_repo(tmp_path)
    claude = _env(tmp_path, "claude", "claude-window")
    stray_codex = _env(tmp_path, "codex", "stray-codex-window")

    assert _run(repo, claude, "init").returncode == 0
    assert _run(repo, claude, "bootstrap", "--topic", "smoke").returncode == 0
    pair = _pair_dir(repo)

    res = _run(repo, stray_codex, "claim", "--kind", "plan")

    assert res.returncode == 2
    assert "no bound pair for this instance" in res.stderr
    assert "relay pair ensure" in res.stderr
    assert "relay bootstrap --topic <slug>" in res.stderr
    assert list((pair / ".draft").glob("*.md")) == []


def test_onboarding_publish_rejects_placeholder_prompt(tmp_path):
    repo = _new_repo(tmp_path)
    claude = _env(tmp_path, "claude", "claude-window")

    assert _run(repo, claude, "init").returncode == 0
    assert _run(repo, claude, "bootstrap", "--topic", "smoke").returncode == 0
    pair = _pair_dir(repo)
    claim = _run(repo, claude, "claim", "--kind", "plan")
    assert claim.returncode == 0, claim.stderr
    draft = Path(claim.stdout.strip())

    res = _run(repo, claude, "publish", str(draft))

    assert res.returncode == 2
    assert "TODO:" in res.stderr
    assert draft.exists()
    assert list(pair.glob("[0-9][0-9][0-9]-*.md")) == []


def test_onboarding_status_without_pair_has_bootstrap_hint(tmp_path):
    repo = _new_repo(tmp_path)
    claude = _env(tmp_path, "claude", "claude-window")

    assert _run(repo, claude, "init").returncode == 0
    res = _run(repo, claude, "status")

    assert res.returncode == 2
    assert "no active pair" in res.stderr
    assert "relay bootstrap --topic <slug>" in res.stderr
