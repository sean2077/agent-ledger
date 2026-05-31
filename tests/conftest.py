"""Pytest configuration: load `relay` (script with no .py extension) as a module."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

RELAY_PATH = (
    Path(__file__).resolve().parent.parent / "skills" / "agent-relay" / "bin" / "relay"
)

if "relay" not in sys.modules:
    loader = importlib.machinery.SourceFileLoader("relay", str(RELAY_PATH))
    spec = importlib.util.spec_from_loader("relay", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["relay"] = mod  # must be set before exec for dataclass()
    loader.exec_module(mod)

import relay  # noqa: E402  (loaded above into sys.modules)


@pytest.fixture(autouse=True)
def _relay_test_env(monkeypatch):
    """Deterministic baseline for every test:

    - Clear `CLAUDE_CODE_SESSION_ID` / `CODEX_THREAD_ID`: the shell running
      pytest under Claude Code (or Codex) exports them, and current identity derives
      `author` from those platform signals, an ambient value would override the
      RELAY_AUTHOR a test sets. Tests covering platform detection set these
      explicitly (after this fixture runs).
    - Disable `claim` auto-heartbeat: from v0.15 `relay claim` auto-starts a
      renewal-file heartbeat daemon. We flip the module-level seam
      `relay._CLAIM_HEARTBEAT_AUTOSTART` to False (NOT an env var — many test
      helpers clear all `RELAY_*`, which would defeat an env gate and let claim
      spawn a real daemon mid-test). The dedicated claim-auto-heartbeat tests
      flip it back on and stub the spawn."""
    for var in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(relay, "_CLAIM_HEARTBEAT_AUTOSTART", False)
