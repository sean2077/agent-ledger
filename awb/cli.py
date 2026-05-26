"""awb CLI entry point.

Subcommands dispatch into dedicated modules. New commands register here.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from awb import (
    __version__,
    doctor,
    importer,
    pack as pack_mod,
    send as send_mod,
    session,
    synthesize as synth_mod,
    wait as wait_mod,
)


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
        "--target", action="append", metavar="AGENT",
        help="Required target agent for r1 (repeat for multiple).",
    )
    p_new.add_argument("--ssh-host", default=None)
    p_new.add_argument("--tmux-session", default=None)
    p_new.add_argument("--remote-root", default=None, help="Ledger path on the remote machine.")
    p_new.add_argument("--tmux-socket", default=None)

    # status
    p_status = sub.add_parser("status", help="Show session state.")
    p_status.add_argument("--project", default=None)
    p_status.add_argument("--session", default=None)
    p_status.add_argument("--json", action="store_true")

    # send
    p_send = sub.add_parser("send", help="Send a prompt trigger to remote agent.")
    p_send.add_argument("--project", default=None)
    p_send.add_argument("--session", default=None)
    p_send.add_argument("--round", type=int, default=None)
    p_send.add_argument("--target", required=True, metavar="AGENT")

    # wait
    p_wait = sub.add_parser("wait", help="Wait for agent reply, verify, publish .ready.")
    p_wait.add_argument("--project", default=None)
    p_wait.add_argument("--session", default=None)
    p_wait.add_argument("--round", type=int, default=None)
    p_wait.add_argument(
        "--target", default=None,
        help="Specific agent (default: all non-terminal required targets)",
    )
    p_wait.add_argument("--timeout", type=int, default=600)
    p_wait.add_argument("--poll", type=float, default=2.0)

    # pack
    p_pack = sub.add_parser("pack", help="Bundle repo context + generate prompts for the active round.")
    p_pack.add_argument("--project", default=None)
    p_pack.add_argument("--session", default=None)
    p_pack.add_argument("--round", type=int, default=None)
    p_pack.add_argument("--repo", default=".", type=Path, help="Repo to pack (default: cwd).")
    p_pack.add_argument("--max-bundle-bytes", type=int, default=pack_mod.DEFAULT_BUNDLE_BYTES)
    p_pack.add_argument("--max-file-bytes", type=int, default=pack_mod.DEFAULT_FILE_BYTES)
    p_pack.add_argument("--max-file-count", type=int, default=pack_mod.DEFAULT_FILE_COUNT)
    p_pack.add_argument("--include-untracked", action="store_true")
    p_pack.add_argument("--allow-risk", action="store_true",
                        help="Proceed even if secret scan finds matches.")
    p_pack.add_argument("--dry-run", action="store_true")

    # import
    p_imp = sub.add_parser("import", help="Import an external reply file into the ledger.")
    p_imp.add_argument("--project", default=None)
    p_imp.add_argument("--session", default=None)
    p_imp.add_argument("--round", type=int, default=None)
    p_imp.add_argument("--from", dest="from_", required=True, metavar="AGENT")
    p_imp.add_argument("path", help="Local path to the reply file to import.")
    p_imp.add_argument("--replace", action="store_true",
                       help="Archive existing reply before importing.")

    # synthesize
    p_syn = sub.add_parser("synthesize", help="Generate decision.draft.md, or --publish to close round.")
    p_syn.add_argument("--project", default=None)
    p_syn.add_argument("--session", default=None)
    p_syn.add_argument("--round", type=int, default=None)
    p_syn.add_argument("--publish", action="store_true",
                       help="Promote draft to decision.md, close round, open next.")
    p_syn.add_argument("--no-open", action="store_true",
                       help="With --publish: do not open a next round.")
    p_syn.add_argument("--next-target", action="append", default=None,
                       metavar="AGENT",
                       help="Targets for next round (default: same as current).")

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

    if args.cmd == "send":
        return send_mod.cmd_send(args)

    if args.cmd == "wait":
        return wait_mod.cmd_wait(args)

    if args.cmd == "pack":
        return pack_mod.cmd_pack(args)

    if args.cmd == "import":
        return importer.cmd_import(args)

    if args.cmd == "synthesize":
        return synth_mod.cmd_synthesize(args)

    parser.error(f"unknown command: {args.cmd}")
    return 2  # unreachable


if __name__ == "__main__":
    sys.exit(main())
