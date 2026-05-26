"""awb CLI entry point.

Subcommands dispatch into dedicated modules. New commands register here.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from awb import __version__, doctor


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="awb",
        description="Agent workbench CLI for multi-agent ledger collaboration.",
    )
    parser.add_argument(
        "--version", action="version", version=f"awb {__version__}"
    )
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="<command>")

    p_doctor = sub.add_parser(
        "doctor",
        help="Run filesystem behavior probes on a ledger path.",
        description=doctor.__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_doctor.add_argument(
        "path",
        nargs="?",
        default=Path(".shared"),
        type=Path,
        help="Path to probe (default: .shared)",
    )
    p_doctor.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON output."
    )
    p_doctor.add_argument(
        "--posix-target",
        default="0700",
        metavar="MODE",
        help="Expected POSIX mode of the ledger root (octal, default: 0700).",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "doctor":
        try:
            posix_target = int(args.posix_target, 8)
        except ValueError:
            parser.error(f"--posix-target must be octal, got {args.posix_target!r}")
        return doctor.run(
            args.path, as_json=args.json, posix_target=posix_target
        )

    parser.error(f"unknown command: {args.cmd}")
    return 2  # unreachable


if __name__ == "__main__":
    sys.exit(main())
