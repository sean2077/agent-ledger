"""Pytest configuration: load `relay` (script with no .py extension) as a module."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

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
