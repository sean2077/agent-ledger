"""`awb wait` — poll for replies/<agent>.submitted, verify, publish .sha256 + .ready.

The agent writes `<agent>.md` and touches `<agent>.submitted`. awb does
the rest: format check, sha256, atomic publish of .ready (the consumer
sentinel). Consumers only look at .ready.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from awb import atomic, ledger, ssh_tmux

VERDICT_RE = re.compile(r"^\s*Verdict\s*:\s*(approve|needs-change|blocked)\b", re.IGNORECASE | re.MULTILINE)


class WaitError(Exception):
    pass


class FormatInvalid(WaitError):
    pass


def reply_paths(session_path: Path, round_n: int, agent: str) -> dict[str, Path]:
    r = session_path / f"r{round_n}" / "replies"
    return {
        "md": r / f"{agent}.md",
        "submitted": r / f"{agent}.submitted",
        "sha256": r / f"{agent}.md.sha256",
        "ready": r / f"{agent}.ready",
    }


def _format_ok(text: str) -> tuple[bool, str]:
    if not VERDICT_RE.search(text):
        return False, "missing `Verdict: approve|needs-change|blocked` line"
    return True, "ok"


def verify_and_publish(session_path: Path, round_n: int, agent: str) -> dict[str, Path]:
    p = reply_paths(session_path, round_n, agent)
    if not p["submitted"].exists():
        raise WaitError(f"no .submitted sentinel at {p['submitted']}")
    if not p["md"].exists():
        raise WaitError(f"submitted but reply missing: {p['md']}")
    text = p["md"].read_text(encoding="utf-8", errors="replace")
    ok, detail = _format_ok(text)
    if not ok:
        archive = session_path / "archive" / f"r{round_n}-{agent}-malformed-{int(time.time())}.md"
        archive.parent.mkdir(parents=True, exist_ok=True)
        p["md"].rename(archive)
        try:
            p["submitted"].unlink()
        except FileNotFoundError:
            pass
        raise FormatInvalid(f"reply format invalid: {detail} (archived to {archive})")
    sha = ssh_tmux.local_sha256(str(p["md"]))
    atomic.atomic_write_text(p["sha256"], f"{sha}  {p['md'].name}\n", mode=0o644)
    atomic.atomic_write_bytes(p["ready"], b"", mode=0o644)
    # remove submitted; ready supersedes it
    try:
        p["submitted"].unlink()
    except FileNotFoundError:
        pass
    return p


def wait_for(
    session: ledger.Session,
    round_n: int,
    agent: str,
    *,
    timeout_s: int = 600,
    poll_s: float = 2.0,
    now: callable | None = None,
) -> dict[str, Path]:
    if session.path is None:
        raise WaitError("session has no path")
    rnd = session.round(round_n)
    target = rnd.target(agent)
    if target.is_terminal():
        raise WaitError(f"target {agent} already terminal: {target.state}")

    now = now or time.monotonic
    deadline = now() + timeout_s
    p = reply_paths(session.path, round_n, agent)
    while True:
        if p["ready"].exists():
            target.state = "reply_present"
            ledger.save(session)
            return p
        if p["submitted"].exists() and p["md"].exists():
            paths = verify_and_publish(session.path, round_n, agent)
            target.state = "reply_present"
            ledger.save(session)
            return paths
        if now() >= deadline:
            target.state = "timed_out"
            target.reason = f"no .submitted within {timeout_s}s"
            ledger.save(session)
            raise WaitError(f"timed out waiting for {agent} reply ({timeout_s}s)")
        time.sleep(poll_s)


def cmd_wait(args) -> int:
    from awb.session import _resolve_session
    try:
        session = _resolve_session(args.ledger, args.project, args.session)
    except ledger.LedgerError as exc:
        print(f"awb wait: {exc}", file=sys.stderr)
        return 2
    round_n = args.round or session.current_round

    targets = [args.target] if args.target else [
        t.agent for t in session.round(round_n).required_targets()
        if not t.is_terminal()
    ]
    if not targets:
        print("awb wait: no non-terminal required targets to wait for", file=sys.stderr)
        return 2

    exit_code = 0
    for agent in targets:
        try:
            paths = wait_for(
                session, round_n, agent,
                timeout_s=args.timeout, poll_s=args.poll,
            )
        except FormatInvalid as exc:
            print(f"awb wait: {agent}: {exc}", file=sys.stderr)
            target = session.round(round_n).target(agent)
            target.state = "failed"
            target.reason = str(exc)
            ledger.save(session)
            ledger.append_event(
                session,
                {"actor": "awb", "event": "reply.malformed", "round": round_n, "target": agent},
                command="awb wait",
            )
            exit_code = 2
            continue
        except WaitError as exc:
            print(f"awb wait: {agent}: {exc}", file=sys.stderr)
            ledger.append_event(
                session,
                {"actor": "awb", "event": "reply.timeout", "round": round_n, "target": agent},
                command="awb wait",
            )
            exit_code = 2
            continue

        ledger.append_event(
            session,
            {
                "actor": "awb", "event": "reply.imported", "round": round_n,
                "target": agent, "sha256": paths["sha256"].read_text().split()[0],
            },
            command="awb wait",
        )
        print(f"verified r{round_n}/{agent}: {paths['md'].name} -> ready")
    return exit_code
