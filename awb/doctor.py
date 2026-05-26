"""Filesystem behavior probes for an awb ledger path.

Tests whether the path provides the semantics the file protocol depends on,
regardless of what's behind it (mount / real dir / symlink — user's problem).

Probes:
  tmp_rename       same-directory tmp + os.replace publishes atomically
  mtime_monotonic  mtime does not regress after rewrite
  symlink_ops      create / readlink / rename a symlink
  sha256_stable    sha256 of the same bytes is stable across reopen
  fsync_barrier    fsync returns and readback matches written bytes
  posix_mode       directory mode matches policy (default 0700)

Each probe is independent; one failure does not abort the rest.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Callable


@dataclasses.dataclass
class ProbeResult:
    name: str
    status: str  # "pass" | "warn" | "fail"
    detail: str
    elapsed_ms: int

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _payload() -> bytes:
    return b"awb-doctor-" + os.urandom(16).hex().encode()


def _run(name: str, fn: Callable[[Path], tuple[str, str]], root: Path) -> ProbeResult:
    start = time.monotonic()
    try:
        status, detail = fn(root)
    except Exception as exc:
        return ProbeResult(name, "fail", f"{type(exc).__name__}: {exc}", _ms(start))
    return ProbeResult(name, status, detail, _ms(start))


def _tmp_rename(root: Path) -> tuple[str, str]:
    target = root / ".awb-doctor-rename.target"
    tmp = root / ".awb-doctor-rename.tmp"
    try:
        payload = _payload()
        tmp.write_bytes(payload)
        with open(tmp, "rb") as f:
            os.fsync(f.fileno())
        os.replace(tmp, target)
        seen = target.read_bytes()
        if seen != payload:
            return "fail", f"readback mismatch: wrote {len(payload)}B, read {len(seen)}B"
        return "pass", "tmp+rename atomic; readback matches"
    finally:
        for p in (tmp, target):
            try:
                p.unlink()
            except FileNotFoundError:
                pass


def _mtime_monotonic(root: Path) -> tuple[str, str]:
    target = root / ".awb-doctor-mtime"
    try:
        target.write_bytes(b"v1")
        m1 = target.stat().st_mtime_ns
        time.sleep(0.05)
        target.write_bytes(b"v2")
        m2 = target.stat().st_mtime_ns
        if m2 < m1:
            return "fail", f"mtime regressed: {m1} -> {m2}"
        if m2 == m1:
            return "warn", f"mtime unchanged after rewrite ({m1}); coarse time resolution"
        return "pass", f"mtime advanced {m2 - m1}ns"
    finally:
        try:
            target.unlink()
        except FileNotFoundError:
            pass


def _symlink_ops(root: Path) -> tuple[str, str]:
    target = root / ".awb-doctor-symlink-target"
    link = root / ".awb-doctor-symlink"
    link2 = root / ".awb-doctor-symlink.renamed"
    try:
        target.write_bytes(b"linkme")
        try:
            os.symlink(target.name, link)
        except OSError as exc:
            return "fail", f"symlink create unsupported: {exc}"
        read = os.readlink(link)
        if read != target.name:
            return "fail", f"readlink mismatch: expected {target.name!r}, got {read!r}"
        os.rename(link, link2)
        if not link2.is_symlink():
            return "fail", "rename did not preserve symlink"
        if os.readlink(link2) != target.name:
            return "fail", "readlink after rename mismatched"
        return "pass", "create / readlink / rename all work"
    finally:
        for p in (link, link2, target):
            try:
                if p.is_symlink() or p.exists():
                    p.unlink()
            except (FileNotFoundError, IsADirectoryError):
                pass


def _sha256_stable(root: Path) -> tuple[str, str]:
    # ~10 KB to bypass any cache shortcut that special-cases tiny files
    target = root / ".awb-doctor-sha256"
    try:
        payload = _payload() * 512
        target.write_bytes(payload)
        h1 = hashlib.sha256(target.read_bytes()).hexdigest()
        h2 = hashlib.sha256(target.read_bytes()).hexdigest()
        if h1 != h2:
            return "fail", f"hash mismatch across reopen: {h1[:12]}.. vs {h2[:12]}.."
        return "pass", f"sha256 stable across reopen ({h1[:16]}..)"
    finally:
        try:
            target.unlink()
        except FileNotFoundError:
            pass


def _fsync_barrier(root: Path) -> tuple[str, str]:
    target = root / ".awb-doctor-fsync"
    try:
        payload = _payload()
        with open(target, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        seen = target.read_bytes()
        if seen != payload:
            return "fail", "post-fsync readback mismatch"
        return "pass", "fsync returns; readback matches"
    finally:
        try:
            target.unlink()
        except FileNotFoundError:
            pass


def _posix_mode(root: Path, target_mode: int = 0o700) -> tuple[str, str]:
    actual = stat.S_IMODE(root.stat().st_mode)
    if actual == target_mode:
        return "pass", f"mode {actual:04o} matches target {target_mode:04o}"
    if actual & 0o002:
        return "fail", f"mode {actual:04o} world-writable"
    if actual & 0o007:
        return "warn", f"mode {actual:04o} world-readable; target {target_mode:04o}"
    if actual & 0o077:
        return "warn", f"mode {actual:04o} group-accessible; target {target_mode:04o}"
    return "warn", f"mode {actual:04o} differs from target {target_mode:04o}"


_FS_PROBES: list[tuple[str, Callable[[Path], tuple[str, str]]]] = [
    ("tmp_rename", _tmp_rename),
    ("mtime_monotonic", _mtime_monotonic),
    ("symlink_ops", _symlink_ops),
    ("sha256_stable", _sha256_stable),
    ("fsync_barrier", _fsync_barrier),
]


def run(root: Path, as_json: bool = False, posix_target: int = 0o700) -> int:
    if not root.exists():
        print(f"awb doctor: path does not exist: {root}", file=sys.stderr)
        return 2
    if not root.is_dir():
        print(f"awb doctor: not a directory: {root}", file=sys.stderr)
        return 2

    root = root.absolute()

    results: list[ProbeResult] = [_run(name, fn, root) for name, fn in _FS_PROBES]

    start = time.monotonic()
    try:
        status, detail = _posix_mode(root, posix_target)
    except Exception as exc:
        status, detail = "fail", f"{type(exc).__name__}: {exc}"
    results.append(ProbeResult("posix_mode", status, detail, _ms(start)))

    exit_code = 0
    for r in results:
        if r.status == "fail":
            exit_code = 2
            break
        if r.status == "warn" and exit_code < 1:
            exit_code = 1

    if as_json:
        out = {
            "path": str(root),
            "posix_target": f"0o{posix_target:o}",
            "exit_code": exit_code,
            "results": [r.to_dict() for r in results],
        }
        print(json.dumps(out, indent=2))
    else:
        icon = {"pass": "[ok]  ", "warn": "[warn]", "fail": "[fail]"}
        print(f"awb doctor :: {root}")
        print(f"posix policy: 0o{posix_target:o}")
        print()
        for r in results:
            print(f"  {icon[r.status]} {r.name:18s} ({r.elapsed_ms:>5d}ms)  {r.detail}")
        print()
        summary = {"pass": 0, "warn": 0, "fail": 0}
        for r in results:
            summary[r.status] += 1
        print(
            f"summary: {summary['pass']} pass, {summary['warn']} warn, "
            f"{summary['fail']} fail (exit {exit_code})"
        )

    return exit_code
