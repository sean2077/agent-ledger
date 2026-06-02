"""Process-level claim/publish concurrency regressions."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import relay


ROOT = Path(__file__).resolve().parent.parent
RELAY_PATH = ROOT / "skills" / "agent-relay" / "bin" / "relay"


def _bootstrap_repo(monkeypatch, tmp_path: Path, topic: str) -> tuple[Path, Path]:
    repo = tmp_path / topic
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    shared = repo / ".shared"
    (shared / "_relay").mkdir(parents=True)
    (shared / "_relay" / ".sentinel").touch()
    monkeypatch.chdir(repo)
    monkeypatch.setenv("RELAY_SHARED_ROOT", str(shared))
    monkeypatch.setenv("RELAY_AUTHOR", "codex")
    monkeypatch.setenv("RELAY_AGENT_SESSION_ID", "codex-main")
    rc = relay.cmd_bootstrap(type("A", (), {
        "topic": topic,
        "title": None,
        "peer": "claude",
        "force": False,
    })())
    assert rc == 0
    pairs = [
        p for p in shared.iterdir()
        if p.is_dir() and not p.name.startswith("_")
    ]
    assert len(pairs) == 1
    return repo, pairs[0]


def _child_env(session: Path, *, agent_session_id: str) -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("RELAY_") or key in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
            env.pop(key, None)
    env.update({
        "RELAY_PATH": str(RELAY_PATH),
        "RELAY_SHARED_ROOT": str(session.parent),
        "RELAY_AUTHOR": "codex",
        "RELAY_AGENT_SESSION_ID": agent_session_id,
        "RELAY_CLAIM_NO_HEARTBEAT": "1",
        "PAIR_ID": session.name,
    })
    return env


def _write_worker(tmp_path: Path, name: str, source: str) -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


def _wait_for_ready(ready_dir: Path, count: int) -> None:
    deadline = time.time() + 10
    while time.time() < deadline:
        if len(list(ready_dir.glob("*.ready"))) >= count:
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {count} workers at {ready_dir}")


def _collect(procs: list[subprocess.Popen[str]]) -> list[subprocess.CompletedProcess[str]]:
    results = []
    for proc in procs:
        stdout, stderr = proc.communicate(timeout=20)
        results.append(subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr))
    return results


def test_multi_process_claim_race_allocates_unique_drafts(monkeypatch, tmp_path):
    repo, session = _bootstrap_repo(monkeypatch, tmp_path, "claim-race")
    ready_dir = tmp_path / "claim-ready"
    ready_dir.mkdir()
    start_file = tmp_path / "claim-start"
    worker = _write_worker(tmp_path, "claim_worker.py", """
        from __future__ import annotations
        import importlib.machinery
        import importlib.util
        import os
        import sys
        import time
        from pathlib import Path

        relay_path = os.environ["RELAY_PATH"]
        loader = importlib.machinery.SourceFileLoader("relay_worker", relay_path)
        spec = importlib.util.spec_from_loader("relay_worker", loader)
        relay = importlib.util.module_from_spec(spec)
        sys.modules["relay_worker"] = relay
        loader.exec_module(relay)

        ready_dir = Path(os.environ["READY_DIR"])
        start_file = Path(os.environ["START_FILE"])
        original_latest_seq = relay.latest_seq

        def blocked_latest_seq(session):
            (ready_dir / f"{os.environ['RELAY_AGENT_SESSION_ID']}.ready").touch()
            while not start_file.exists():
                time.sleep(0.005)
            return original_latest_seq(session)

        relay.latest_seq = blocked_latest_seq
        args = type("A", (), {
            "kind": "plan",
            "in_reply_to": None,
            "corrects": None,
            "project": None,
            "pair_id": os.environ["PAIR_ID"],
            "no_heartbeat": True,
        })()
        raise SystemExit(relay.cmd_claim(args))
    """)

    count = 6
    procs: list[subprocess.Popen[str]] = []
    for idx in range(count):
        env = _child_env(session, agent_session_id=f"codex-claim-{idx}")
        env["READY_DIR"] = str(ready_dir)
        env["START_FILE"] = str(start_file)
        procs.append(subprocess.Popen(
            [sys.executable, str(worker)],
            cwd=repo,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ))

    _wait_for_ready(ready_dir, count)
    start_file.touch()
    results = _collect(procs)

    assert all(r.returncode == 0 for r in results), [(r.returncode, r.stderr) for r in results]
    drafts = [Path(r.stdout.strip()) for r in results]
    assert len({p.name for p in drafts}) == count
    assert sorted(int(p.name[:3]) for p in drafts) == list(range(1, count + 1))
    assert [p for p in (session / ".draft").iterdir() if ".relay-" in p.name] == []


def test_concurrent_publish_creates_unique_triads_without_leftovers(monkeypatch, tmp_path):
    repo, session = _bootstrap_repo(monkeypatch, tmp_path, "publish-race")
    draft_dir = session / ".draft"
    draft_dir.mkdir(exist_ok=True)
    draft = draft_dir / "001-codex-plan.md"
    fm = {
        "seq": 1,
        "author": "codex",
        "peer": "claude",
        "kind": "plan",
        "status": "draft",
        "created": relay.now_iso(),
        "in_reply_to": None,
        "prompt_for_next": "review the concurrent publish\n",
        "sync_needed": False,
        "touched_paths": [],
        "corrects": None,
    }
    draft.write_text(relay.dump_frontmatter(fm, "\nbody.\n"), encoding="utf-8")
    ready_dir = tmp_path / "publish-ready"
    ready_dir.mkdir()
    start_file = tmp_path / "publish-start"
    worker = _write_worker(tmp_path, "publish_worker.py", """
        from __future__ import annotations
        import importlib.machinery
        import importlib.util
        import os
        import sys
        import time
        from pathlib import Path

        relay_path = os.environ["RELAY_PATH"]
        loader = importlib.machinery.SourceFileLoader("relay_worker", relay_path)
        spec = importlib.util.spec_from_loader("relay_worker", loader)
        relay = importlib.util.module_from_spec(spec)
        sys.modules["relay_worker"] = relay
        loader.exec_module(relay)

        draft_path = Path(os.environ["DRAFT_PATH"])
        cached_fm, cached_body = relay._validate_draft(draft_path)
        ready_dir = Path(os.environ["READY_DIR"])
        start_file = Path(os.environ["START_FILE"])

        def blocked_validate(path):
            (ready_dir / f"{os.environ['RELAY_AGENT_SESSION_ID']}.ready").touch()
            while not start_file.exists():
                time.sleep(0.005)
            return dict(cached_fm), cached_body

        relay._validate_draft = blocked_validate
        args = type("A", (), {
            "draft_path": str(draft_path),
            "status": None,
            "force": False,
            "force_reason": None,
            "project": None,
            "session_id": None,
        })()
        raise SystemExit(relay.cmd_publish(args))
    """)

    count = 4
    procs: list[subprocess.Popen[str]] = []
    for idx in range(count):
        env = _child_env(session, agent_session_id=f"codex-publish-{idx}")
        env["READY_DIR"] = str(ready_dir)
        env["START_FILE"] = str(start_file)
        env["DRAFT_PATH"] = str(draft)
        procs.append(subprocess.Popen(
            [sys.executable, str(worker)],
            cwd=repo,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ))

    _wait_for_ready(ready_dir, count)
    start_file.touch()
    results = _collect(procs)

    assert all(r.returncode == 0 for r in results), [(r.returncode, r.stderr) for r in results]
    published = [Path(r.stdout.strip()) for r in results]
    assert sorted(int(p.name[:3]) for p in published) == list(range(1, count + 1))
    assert len({p.name for p in published}) == count
    for path in published:
        assert path.exists()
        assert (session / f"{path.name}.sha256").exists()
        assert (session / f"{path.stem}.ready").exists()
    assert [p.name for p in relay.list_published(session)] == [
        f"{idx:03d}-codex-plan.md" for idx in range(1, count + 1)
    ]
    assert list(draft_dir.glob("*.md")) == []
    assert [p for p in session.iterdir() if ".relay-" in p.name] == []
