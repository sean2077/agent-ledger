"""relay init — idempotent first-run filesystem setup."""

import os
from pathlib import Path

import pytest

import relay


def _isolated_env(monkeypatch, **kwargs):
    for k in list(os.environ):
        if k.startswith("RELAY_"):
            monkeypatch.delenv(k, raising=False)
    for k, v in kwargs.items():
        monkeypatch.setenv(k, v)


def test_init_creates_full_layout_from_scratch(monkeypatch, tmp_path, capsys):
    shared = tmp_path / ".shared"
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(shared))
    rc = relay.cmd_init(type("A", (), {})())
    assert rc == 0
    assert shared.is_dir()
    assert (shared / "_relay").is_dir()
    assert (shared / "_relay" / ".sentinel").is_file()
    out = capsys.readouterr().out
    assert "created" in out
    assert str(shared) in out


def test_init_is_idempotent(monkeypatch, tmp_path, capsys):
    shared = tmp_path / ".shared"
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(shared))
    assert relay.cmd_init(type("A", (), {})()) == 0
    sentinel_before = (shared / "_relay" / ".sentinel").read_bytes()
    capsys.readouterr()  # drain
    rc = relay.cmd_init(type("A", (), {})())
    assert rc == 0
    out = capsys.readouterr().out
    assert "already initialized" in out
    assert (shared / "_relay" / ".sentinel").read_bytes() == sentinel_before


def test_init_creates_dirs_with_0700_mode(monkeypatch, tmp_path):
    shared = tmp_path / ".shared"
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(shared))
    assert relay.cmd_init(type("A", (), {})()) == 0
    assert (shared.stat().st_mode & 0o777) == 0o700
    assert ((shared / "_relay").stat().st_mode & 0o777) == 0o700


def test_init_fills_in_missing_sentinel_only(monkeypatch, tmp_path, capsys):
    """If RELAY_SHARED_ROOT exists but sentinel is missing, init only adds the sentinel."""
    shared = tmp_path / ".shared"
    shared.mkdir(mode=0o700)
    (shared / "_relay").mkdir(mode=0o700)
    _isolated_env(monkeypatch, RELAY_SHARED_ROOT=str(shared))
    rc = relay.cmd_init(type("A", (), {})())
    assert rc == 0
    assert (shared / "_relay" / ".sentinel").is_file()
    out = capsys.readouterr().out
    assert ".sentinel" in out


def test_init_fails_without_shared_root_env(monkeypatch, capsys):
    _isolated_env(monkeypatch)
    rc = relay.cmd_init(type("A", (), {})())
    assert rc == 2
    assert "RELAY_SHARED_ROOT" in capsys.readouterr().err
