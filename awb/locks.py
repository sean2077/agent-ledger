"""mkdir-style lease locks.

Cross-host safe: lock is a directory whose creation is atomic. Owner
metadata is written *after* successful mkdir. PID-based liveness checks
are only valid for same-host acquirers and only used to inform stale
break decisions.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class LockError(Exception):
    pass


class LockHeld(LockError):
    def __init__(self, lock_dir: Path, owner: dict | None) -> None:
        self.lock_dir = lock_dir
        self.owner = owner
        msg = f"lock held: {lock_dir}"
        if owner:
            msg += f" by {owner.get('user')}@{owner.get('host')} pid {owner.get('pid')} until {owner.get('expires_at')}"
        super().__init__(msg)


@dataclass
class LockOwner:
    host: str
    pid: int
    user: str
    created_at: str
    expires_at: str
    command: str

    def to_dict(self) -> dict:
        return {
            "host": self.host,
            "pid": self.pid,
            "user": self.user,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "command": self.command,
        }


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _make_owner(command: str, lease_secs: int) -> LockOwner:
    now = _now_utc()
    return LockOwner(
        host=socket.gethostname(),
        pid=os.getpid(),
        user=os.environ.get("USER", "unknown"),
        created_at=_iso(now),
        expires_at=_iso(now.replace(microsecond=0) + _td(lease_secs)),
        command=command,
    )


def _td(secs: int):
    from datetime import timedelta
    return timedelta(seconds=secs)


def _read_owner(lock_dir: Path) -> dict | None:
    owner_file = lock_dir / "owner.json"
    try:
        return json.loads(owner_file.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _is_expired(owner: dict) -> bool:
    try:
        exp = datetime.fromisoformat(owner["expires_at"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return False
    return _now_utc() >= exp


def acquire(lock_dir: Path, *, command: str, lease_secs: int = 600) -> LockOwner:
    lock_dir = Path(lock_dir)
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.mkdir(lock_dir, mode=0o700)
    except FileExistsError:
        owner = _read_owner(lock_dir)
        raise LockHeld(lock_dir, owner)
    owner = _make_owner(command, lease_secs)
    from awb.atomic import atomic_write_json
    atomic_write_json(lock_dir / "owner.json", owner.to_dict())
    return owner


def release(lock_dir: Path) -> None:
    lock_dir = Path(lock_dir)
    owner_file = lock_dir / "owner.json"
    try:
        owner_file.unlink()
    except FileNotFoundError:
        pass
    try:
        os.rmdir(lock_dir)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise LockError(f"failed to release {lock_dir}: {exc}") from exc


def break_stale(lock_dir: Path, *, force: bool = False) -> dict | None:
    """Remove a lock if it has expired (or if force=True). Returns prior owner."""
    lock_dir = Path(lock_dir)
    owner = _read_owner(lock_dir)
    if owner is None:
        try:
            os.rmdir(lock_dir)
        except FileNotFoundError:
            return None
        except OSError:
            pass
        return None
    if not force and not _is_expired(owner):
        raise LockHeld(lock_dir, owner)
    release(lock_dir)
    return owner


@contextlib.contextmanager
def hold(lock_dir: Path, *, command: str, lease_secs: int = 600):
    owner = acquire(lock_dir, command=command, lease_secs=lease_secs)
    try:
        yield owner
    finally:
        release(lock_dir)
