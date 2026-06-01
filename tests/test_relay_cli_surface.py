"""CLI surface regressions for current pair/binding commands."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
RELAY = ROOT / "skills" / "agent-relay" / "bin" / "relay"


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("RELAY_") or key in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
            env.pop(key, None)
    return env


@pytest.mark.parametrize("argv", [
    ["bootstrap", "--help"],
    ["status", "--help"],
    ["claim", "--help"],
    ["draft", "--help"],
    ["publish", "--help"],
    ["close", "--help"],
    ["pairs", "--help"],
    ["pairs", "archive", "--help"],
    ["pairs", "restore", "--help"],
    ["pair", "--help"],
    ["sync", "--help"],
    ["doctor", "--help"],
    ["wait", "--help"],
    ["heartbeat", "--help"],
    ["hooks", "--help"],
    ["issue", "--help"],
])
def test_current_top_level_commands_remain_available(argv):
    res = subprocess.run(
        [sys.executable, str(RELAY), *argv],
        cwd=ROOT,
        env=_clean_env(),
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert "usage:" in res.stdout


@pytest.mark.parametrize("argv", [
    ["status", "--session-id", "20260531-old"],
    ["sessions", "list"],
    ["next-seq"],
    ["init", "--role", "same-host"],
    ["init", "--peer", "claude"],
])
def test_removed_legacy_cli_surfaces_fail_closed(argv):
    res = subprocess.run(
        [sys.executable, str(RELAY), *argv],
        cwd=ROOT,
        env=_clean_env(),
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 2
    assert "usage:" in res.stderr
