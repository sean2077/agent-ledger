"""`awb import` — atomically publish an externally-produced reply into the ledger.

Used for the GPT-5.5 Pro Web path: user downloads/copies the reply text
to a local file, then `awb import --from gpt55 path/to/reply.md` copies
it into r<n>/replies/gpt55.md, runs the same verify+publish that wait does.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from awb import atomic, ledger, wait


class ImportError_(Exception):
    pass


def import_reply(
    session: ledger.Session,
    round_n: int,
    agent: str,
    source: Path,
    *,
    replace: bool = False,
) -> dict[str, Path]:
    if session.path is None:
        raise ImportError_("session has no path")
    rnd = session.round(round_n)
    target = rnd.target(agent)
    source = Path(source)
    if not source.exists():
        raise ImportError_(f"source not found: {source}")
    if not source.is_file():
        raise ImportError_(f"source is not a file: {source}")

    p = wait.reply_paths(session.path, round_n, agent)

    if p["md"].exists() and not replace:
        raise ImportError_(
            f"reply already present: {p['md']}; use --replace to archive existing"
        )
    if p["md"].exists() and replace:
        import time
        arch = session.path / "archive" / f"r{round_n}-{agent}-superseded-{int(time.time())}.md"
        arch.parent.mkdir(parents=True, exist_ok=True)
        p["md"].rename(arch)
        for sidecar in ("sha256", "ready"):
            ap = (session.path / f"r{round_n}" / "replies" / f"{agent}.{sidecar}")
            if sidecar == "sha256":
                ap = session.path / f"r{round_n}" / "replies" / f"{agent}.md.sha256"
            try:
                ap.unlink()
            except FileNotFoundError:
                pass

    # Stage via atomic write into the destination directory
    data = source.read_bytes()
    atomic.atomic_write_bytes(p["md"], data, mode=0o600)
    atomic.atomic_write_bytes(p["submitted"], b"", mode=0o600)
    out = wait.verify_and_publish(session.path, round_n, agent)
    target.state = "reply_present"
    ledger.save(session)
    return out


def cmd_import(args) -> int:
    from awb.session import _resolve_session
    try:
        session = _resolve_session(args.ledger, args.project, args.session)
    except ledger.LedgerError as exc:
        print(f"awb import: {exc}", file=sys.stderr)
        return 2

    round_n = args.round or session.current_round
    try:
        paths = import_reply(
            session, round_n, args.from_, Path(args.path),
            replace=args.replace,
        )
    except (ImportError_, wait.WaitError, ledger.LedgerError) as exc:
        print(f"awb import: {exc}", file=sys.stderr)
        return 2

    ledger.append_event(
        session,
        {
            "actor": "awb", "event": "reply.imported", "round": round_n,
            "target": args.from_,
            "source": str(args.path),
            "sha256": paths["sha256"].read_text().split()[0],
        },
        command="awb import",
    )
    print(f"imported r{round_n}/{args.from_} from {args.path}")
    return 0
