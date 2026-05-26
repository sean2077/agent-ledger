import json
import os
import threading
from pathlib import Path

from awb import atomic


def test_write_creates_file(tmp_path: Path):
    target = tmp_path / "out.txt"
    atomic.atomic_write_text(target, "hello")
    assert target.read_text() == "hello"


def test_write_overwrites_existing(tmp_path: Path):
    target = tmp_path / "out.txt"
    target.write_text("old")
    atomic.atomic_write_text(target, "new")
    assert target.read_text() == "new"


def test_no_tmp_leftover_on_success(tmp_path: Path):
    target = tmp_path / "out.txt"
    atomic.atomic_write_text(target, "ok")
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".out.txt.awb-")]
    assert leftovers == []


def test_mode_is_applied(tmp_path: Path):
    target = tmp_path / "secret.txt"
    atomic.atomic_write_text(target, "x", mode=0o600)
    assert (target.stat().st_mode & 0o777) == 0o600


def test_creates_parent_dirs(tmp_path: Path):
    target = tmp_path / "a" / "b" / "c.txt"
    atomic.atomic_write_text(target, "y")
    assert target.read_text() == "y"


def test_json_roundtrip(tmp_path: Path):
    target = tmp_path / "obj.json"
    atomic.atomic_write_json(target, {"a": 1, "b": [2, 3]})
    assert json.loads(target.read_text()) == {"a": 1, "b": [2, 3]}


def test_concurrent_writes_one_winner(tmp_path: Path):
    target = tmp_path / "race.txt"
    barrier = threading.Barrier(8)
    results = []

    def w(i: int):
        barrier.wait()
        atomic.atomic_write_text(target, f"value-{i}")
        results.append(i)

    threads = [threading.Thread(target=w, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 8
    # last writer wins; we just require the file is one valid payload
    content = target.read_text()
    assert content.startswith("value-")
    # no temp files leftover
    assert [p for p in tmp_path.iterdir() if ".awb-" in p.name] == []
