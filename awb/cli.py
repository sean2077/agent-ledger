"""awb CLI entry point.

Subcommands dispatch into dedicated modules. New commands register here.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from awb import __version__, doctor, session


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="awb",
        description="Agent workbench CLI for multi-agent ledger collaboration.",
    )
    parser.add_argument("--version", action="version", version=f"awb {__version__}")
    parser.add_argument(
        "--ledger",
        default=Path(".shared"),
        type=Path,
        help="Ledger root directory (default: .shared).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="<command>")

    # doctor
    p_doctor = sub.add_parser(
        "doctor",
        help="Run filesystem behavior probes on a ledger path.",
        description=doctor.__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_doctor.add_argument(
        "path",
        nargs="?",
        default=None,
        type=Path,
        help="Path to probe (default: --ledger).",
    )
    p_doctor.add_argument("--json", action="store_true")
    p_doctor.add_argument("--posix-target", default="0700", metavar="MODE")

    # session
    p_sess = sub.add_parser("session", help="Session lifecycle commands.")
    sess_sub = p_sess.add_subparsers(dest="session_cmd", required=True, metavar="<subcommand>")

    p_new = sess_sub.add_parser("new", help="Create a new session.")
    p_new.add_argument("slug", help="Session slug; lowercase [a-z0-9-], <=48 chars.")
    p_new.add_argument("--project", required=True, help="Project name (top-level dir).")
    p_new.add_argument("--title", default=None, help="Human-readable title.")
    p_new.add_argument(
        "--target",
        action="append",
        metavar="AGENT",
        help="Required target agent for r1 (repeat for multiple).",
    )

    # status
    p_status = sub.add_parser("status", help="Show session state.")
    p_status.add_argument("--project", default=None)
    p_status.add_argument("--session", default=None)
    p_status.add_argument("--json", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "doctor":
        try:
            posix_target = int(args.posix_target, 8)
        except ValueError:
            parser.error(f"--posix-target must be octal, got {args.posix_target!r}")
        path = args.path or args.ledger
        return doctor.run(path, as_json=args.json, posix_target=posix_target)

    if args.cmd == "session":
        if args.session_cmd == "new":
            return session.cmd_session_new(args)

    if args.cmd == "status":
        return session.cmd_status(args)

    parser.error(f"unknown command: {args.cmd}")
    return 2  # unreachable


if __name__ == "__main__":
    sys.exit(main())
