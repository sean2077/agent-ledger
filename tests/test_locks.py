import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from awb import locks


def test_acquire_creates_dir_and_owner(tmp_path: Path):
    ld = tmp_path / "task.lock"
    owner = locks.acquire(ld, command="t1", lease_secs=60)
    assert ld.is_dir()
    data = json.loads((ld / "owner.json").read_text())
    assert data["command"] == "t1"
    assert data["pid"] == owner.pid


def test_second_acquire_raises_held(tmp_path: Path):
    ld = tmp_path / "task.lock"
    locks.acquire(ld, command="first")
    with pytest.raises(locks.LockHeld) as exc:
        locks.acquire(ld, command="second")
    assert exc.value.owner["command"] == "first"


def test_release_makes_lock_available(tmp_path: Path):
    ld = tmp_path / "task.lock"
    locks.acquire(ld, command="a")
    locks.release(ld)
    assert not ld.exists()
    locks.acquire(ld, command="b")  # should succeed


def test_break_stale_refuses_live_lock(tmp_path: Path):
    ld = tmp_path / "task.lock"
    locks.acquire(ld, command="x", lease_secs=600)
    with pytest.raises(locks.LockHeld):
        locks.break_stale(ld)


def test_break_stale_removes_expired_lock(tmp_path: Path):
    ld = tmp_path / "task.lock"
    locks.acquire(ld, command="x", lease_secs=1)
    # Manually fast-forward expiry
    owner = json.loads((ld / "owner.json").read_text())
    owner["expires_at"] = (
        (datetime.now(timezone.utc) - timedelta(seconds=10))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    (ld / "owner.json").write_text(json.dumps(owner))
    prior = locks.break_stale(ld)
    assert prior["command"] == "x"
    assert not ld.exists()


def test_break_stale_force(tmp_path: Path):
    ld = tmp_path / "task.lock"
    locks.acquire(ld, command="x", lease_secs=600)
    prior = locks.break_stale(ld, force=True)
    assert prior["command"] == "x"


def test_hold_context_releases(tmp_path: Path):
    ld = tmp_path / "task.lock"
    with locks.hold(ld, command="cm") as owner:
        assert ld.is_dir()
        assert owner.command == "cm"
    assert not ld.exists()


def test_hold_releases_on_exception(tmp_path: Path):
    ld = tmp_path / "task.lock"
    with pytest.raises(RuntimeError):
        with locks.hold(ld, command="cm"):
            raise RuntimeError("boom")
    assert not ld.exists()
