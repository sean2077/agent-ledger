"""Atomic primitives + frontmatter parsing/dumping."""

import json
import threading
from pathlib import Path

import pytest

import relay


def test_atomic_write_text_creates_and_overwrites(tmp_path: Path):
    p = tmp_path / "out.txt"
    relay.atomic_write_text(p, "hello")
    assert p.read_text() == "hello"
    relay.atomic_write_text(p, "world")
    assert p.read_text() == "world"


def test_atomic_no_tmp_leftover(tmp_path: Path):
    p = tmp_path / "x.txt"
    relay.atomic_write_text(p, "ok")
    leftovers = [f for f in tmp_path.iterdir() if ".relay-" in f.name]
    assert leftovers == []


def test_atomic_creates_parent_dirs(tmp_path: Path):
    p = tmp_path / "a" / "b" / "c.txt"
    relay.atomic_write_text(p, "y")
    assert p.read_text() == "y"


def test_atomic_mode_0600(tmp_path: Path):
    p = tmp_path / "sec.txt"
    relay.atomic_write_text(p, "x")
    assert (p.stat().st_mode & 0o777) == 0o600


def test_atomic_json_roundtrip(tmp_path: Path):
    p = tmp_path / "data.json"
    relay.atomic_write_json(p, {"a": [1, 2], "b": None})
    assert json.loads(p.read_text()) == {"a": [1, 2], "b": None}


def test_concurrent_writes_no_leftover(tmp_path: Path):
    target = tmp_path / "race.txt"
    barrier = threading.Barrier(8)

    def w(i: int):
        barrier.wait()
        relay.atomic_write_text(target, f"v{i}")

    threads = [threading.Thread(target=w, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert target.read_text().startswith("v")
    assert [f for f in tmp_path.iterdir() if ".relay-" in f.name] == []


def test_sha256_of_file(tmp_path: Path):
    p = tmp_path / "f"
    p.write_bytes(b"hello world")
    import hashlib
    assert relay.sha256_of_file(p) == hashlib.sha256(b"hello world").hexdigest()


# --- frontmatter -----------------------------------------------------------

def test_frontmatter_roundtrip_basic():
    fm = {
        "seq": 4, "author": "codex", "peer": "claude", "kind": "fix",
        "status": "ready",
        "prompt_for_next": "do the thing\nsecond line\n",
        "sync_needed": False, "touched_paths": ["a.py", "b.py"],
        "corrects": None,
    }
    body = "# title\n\nbody here\n"
    text = relay.dump_frontmatter(fm, body)
    parsed_fm, parsed_body = relay.parse_frontmatter(text)
    assert parsed_fm["seq"] == 4
    assert parsed_fm["sync_needed"] is False
    assert parsed_fm["touched_paths"] == ["a.py", "b.py"]
    assert parsed_fm["corrects"] is None
    assert parsed_fm["prompt_for_next"].strip() == "do the thing\nsecond line"
    assert parsed_body.strip() == body.strip()


def test_frontmatter_missing_raises():
    with pytest.raises(ValueError, match="no frontmatter"):
        relay.parse_frontmatter("just body, no frontmatter\n")


def test_frontmatter_handles_null_and_bool():
    text = "---\na: null\nb: true\nc: false\nd: 42\n---\n"
    fm, _ = relay.parse_frontmatter(text)
    assert fm == {"a": None, "b": True, "c": False, "d": 42}


def test_frontmatter_empty_list():
    text = "---\nitems: []\n---\nbody\n"
    fm, _ = relay.parse_frontmatter(text)
    assert fm["items"] == []


def test_frontmatter_multiline_preserves_relative_indent():
    text = "---\nprompt_for_next: |\n  - one\n  - two\n  with sub\n---\nbody\n"
    fm, _ = relay.parse_frontmatter(text)
    assert fm["prompt_for_next"].startswith("- one")
    assert "two" in fm["prompt_for_next"]
