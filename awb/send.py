"""`awb send` — verify prompt visible remotely, then send a short tmux trigger."""

from __future__ import annotations

import sys
from pathlib import Path

from awb import atomic, ledger, ssh_tmux
from awb.ssh_tmux import Runner, SSHConfig, TmuxConfig, _real_runner


class SendError(Exception):
    pass


def remote_prompt_path(remote_root: str, session_id: str, project: str, round_n: int, agent: str) -> str:
    return f"{remote_root.rstrip('/')}/{project}/{session_id}/r{round_n}/prompts/{agent}.md"


def send(
    session: ledger.Session,
    round_n: int,
    agent: str,
    *,
    runner: Runner = _real_runner,
    log_path: Path | None = None,
) -> None:
    if session.path is None:
        raise SendError("session has no path")

    rnd = session.round(round_n)
    target = rnd.target(agent)
    if target.state in ledger.TARGET_TERMINAL:
        raise SendError(f"target {agent} already terminal: {target.state}")

    rcfg = session.remote
    if not rcfg.ssh_host or not rcfg.tmux_session or not rcfg.remote_root:
        raise SendError(
            "session.remote.ssh_host / tmux_session / remote_root must be set"
        )

    prompt_local = session.path / f"r{round_n}" / "prompts" / f"{agent}.md"
    if not prompt_local.exists():
        raise SendError(f"local prompt missing: {prompt_local}")

    prompt_remote = remote_prompt_path(
        rcfg.remote_root, session.session_id, session.project, round_n, agent
    )

    ssh = SSHConfig(host=rcfg.ssh_host)
    if not ssh_tmux.remote_exists(ssh, prompt_remote, runner=runner):
        raise SendError(f"remote prompt not visible: {prompt_remote}")

    tmux = TmuxConfig(
        ssh=ssh, session_name=rcfg.tmux_session, socket_path=rcfg.tmux_socket
    )
    if not ssh_tmux.tmux_has_session(tmux, runner=runner):
        raise SendError(
            f"tmux session {rcfg.tmux_session!r} not running on {rcfg.ssh_host} "
            "(start interactive Claude there first)"
        )

    # Trigger format: short single-line path. Agent must be primed to read it.
    trigger = prompt_remote
    res = ssh_tmux.tmux_send_keys(tmux, trigger, enter=True, runner=runner)
    if not res.ok():
        raise SendError(f"tmux send-keys failed: {res.stderr.strip()}")

    if log_path:
        atomic.atomic_write_text(
            log_path,
            f"send {agent} r{round_n}\n"
            f"  trigger:  {trigger}\n"
            f"  rc:       {res.returncode}\n"
            f"  stdout:   {res.stdout!r}\n"
            f"  stderr:   {res.stderr!r}\n",
        )

    target.state = "sent"
    ledger.save(session)


def cmd_send(args) -> int:
    from awb.session import _resolve_session
    try:
        session = _resolve_session(args.ledger, args.project, args.session)
    except ledger.LedgerError as exc:
        print(f"awb send: {exc}", file=sys.stderr)
        return 2
    round_n = args.round or session.current_round
    log_path = (
        session.path / "logs" / f"ssh-send-r{round_n}-{args.target}.log"
        if session.path else None
    )
    try:
        send(session, round_n, args.target, log_path=log_path)
    except (SendError, ledger.LedgerError) as exc:
        print(f"awb send: {exc}", file=sys.stderr)
        return 2

    ledger.append_event(
        session,
        {
            "actor": "awb",
            "event": "prompt.sent",
            "round": round_n,
            "target": args.target,
        },
        command="awb send",
    )
    print(f"sent r{round_n}/{args.target}: trigger sent to tmux")
    return 0
