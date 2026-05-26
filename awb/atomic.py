"""Atomic same-directory tmp + rename publisher.

Always writes to a sibling temp file in the target's own directory so
that os.replace stays inside one filesystem and remains atomic.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path


def atomic_write_bytes(target: Path, data: bytes, *, mode: int = 0o600) -> None:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{target.name}.awb-{os.getpid()}-{uuid.uuid4().hex[:8]}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        os.replace(tmp, target)
        _fsync_dir(target.parent)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def atomic_write_text(target: Path, text: str, *, mode: int = 0o600) -> None:
    atomic_write_bytes(target, text.encode("utf-8"), mode=mode)


def atomic_write_json(target: Path, obj, *, mode: int = 0o600, indent: int = 2) -> None:
    atomic_write_text(target, json.dumps(obj, indent=indent, ensure_ascii=False) + "\n", mode=mode)


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_DIRECTORY)
    except (OSError, AttributeError):
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
