"""CLI handlers for `awb session new` and `awb status`."""

from __future__ import annotations

import sys
from pathlib import Path

from awb import ledger


def cmd_session_new(args) -> int:
    remote = ledger.Remote(
        ssh_host=getattr(args, "ssh_host", None),
        tmux_session=getattr(args, "tmux_session", None),
        remote_root=getattr(args, "remote_root", None),
        tmux_socket=getattr(args, "tmux_socket", None),
    )
    try:
        session = ledger.create_session(
            ledger_root=args.ledger,
            project=args.project,
            slug=args.slug,
            title=args.title or args.slug,
            target_agents=args.target or (),
            remote=remote,
        )
    except ledger.LedgerError as exc:
        print(f"awb session new: {exc}", file=sys.stderr)
        return 2

    ledger.append_event(
        session,
        {
            "actor": "awb",
            "event": "session.created",
            "session_id": session.session_id,
            "project": session.project,
        },
        command="awb session new",
    )
    ledger.append_event(
        session,
        {
            "actor": "awb",
            "event": "round.opened",
            "round": 1,
            "targets": [t.agent for t in session.round(1).targets],
        },
        command="awb session new",
    )

    print(f"created session: {session.path}")
    print(f"  current_round = r{session.current_round}")
    print(f"  targets       = {[t.agent for t in session.round(1).targets] or '(none)'}")
    return 0


def cmd_status(args) -> int:
    try:
        session = _resolve_session(args.ledger, args.project, args.session)
    except ledger.LedgerError as exc:
        print(f"awb status: {exc}", file=sys.stderr)
        return 2

    if args.json:
        import json
        print(json.dumps(session.to_dict(), indent=2, ensure_ascii=False))
        return 0

    print(f"session: {session.project}/{session.session_id}")
    print(f"  title:   {session.title}")
    print(f"  state:   {session.state}")
    print(f"  current: r{session.current_round}")
    print(f"  path:    {session.path}")
    print()
    print("rounds:")
    for n, r in sorted(session.rounds.items()):
        marker = " <-- active" if n == session.current_round else ""
        print(f"  r{n} [{r.state}]{marker}")
        for t in r.targets:
            req = "*" if t.required else " "
            print(f"    {req} {t.agent:12s} {t.state}")
        if r.note:
            print(f"    note: {r.note}")
    return 0


def _resolve_session(ledger_root: Path, project: str | None, session_id: str | None) -> ledger.Session:
    ledger_root = Path(ledger_root)
    if project and session_id:
        return ledger.load(ledger.session_dir(ledger_root, project, session_id))
    # Discover: if there's exactly one project with one session, use it.
    if not ledger_root.exists():
        raise ledger.LedgerError(f"ledger root does not exist: {ledger_root}")
    candidates: list[Path] = []
    for proj_dir in sorted(ledger_root.iterdir()):
        if not proj_dir.is_dir():
            continue
        if project and proj_dir.name != project:
            continue
        for sess_dir in sorted(proj_dir.iterdir()):
            if (sess_dir / "session.json").exists():
                if session_id and sess_dir.name != session_id:
                    continue
                candidates.append(sess_dir)
    if not candidates:
        raise ledger.LedgerError("no sessions found; pass --project and --session")
    if len(candidates) > 1:
        names = ", ".join(f"{p.parent.name}/{p.name}" for p in candidates)
        raise ledger.LedgerError(f"multiple sessions; pass --project/--session: {names}")
    return ledger.load(candidates[0])
