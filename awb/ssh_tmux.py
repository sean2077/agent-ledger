"""Subprocess helpers for SSH (ControlMaster) and tmux.

All command construction uses argv lists — never shell concatenation —
so untrusted strings (task ids, paths) cannot inject. Inject a fake
runner in tests via `runner=...`.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Sequence


@dataclass
class ProcResult:
    returncode: int
    stdout: str
    stderr: str

    def ok(self) -> bool:
        return self.returncode == 0


Runner = Callable[[Sequence[str]], ProcResult]


def _real_runner(argv: Sequence[str]) -> ProcResult:
    p = subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        check=False,
    )
    return ProcResult(p.returncode, p.stdout, p.stderr)


@dataclass
class SSHConfig:
    host: str
    options: list[str] = field(default_factory=lambda: [
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3",
        "-o", "BatchMode=yes",
    ])

    def argv(self, remote_cmd: Sequence[str]) -> list[str]:
        return ["ssh", *self.options, self.host, "--", *remote_cmd]


def ssh_run(cfg: SSHConfig, remote_cmd: Sequence[str], *, runner: Runner = _real_runner) -> ProcResult:
    return runner(cfg.argv(remote_cmd))


def remote_exists(cfg: SSHConfig, path: str, *, runner: Runner = _real_runner) -> bool:
    return ssh_run(cfg, ["test", "-e", path], runner=runner).ok()


def remote_sha256(cfg: SSHConfig, path: str, *, runner: Runner = _real_runner) -> str | None:
    r = ssh_run(cfg, ["sha256sum", path], runner=runner)
    if not r.ok():
        return None
    return r.stdout.strip().split()[0] if r.stdout.strip() else None


def local_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# --- tmux ----------------------------------------------------------------


@dataclass
class TmuxConfig:
    ssh: SSHConfig
    session_name: str
    socket_path: str | None = None  # e.g. ~/.tmux/awb.sock

    def tmux_argv(self, args: Sequence[str]) -> list[str]:
        out = ["tmux"]
        if self.socket_path:
            out += ["-S", self.socket_path]
        out += list(args)
        return out


def tmux_has_session(cfg: TmuxConfig, *, runner: Runner = _real_runner) -> bool:
    cmd = cfg.tmux_argv(["has-session", "-t", cfg.session_name])
    return ssh_run(cfg.ssh, cmd, runner=runner).ok()


def tmux_send_keys(
    cfg: TmuxConfig,
    text: str,
    *,
    enter: bool = True,
    runner: Runner = _real_runner,
) -> ProcResult:
    if "\n" in text or "\r" in text:
        raise ValueError("send_keys text must not contain newlines (use enter=True)")
    parts: list[str] = []
    parts += cfg.tmux_argv(["send-keys", "-t", cfg.session_name, "-l", "--", text])
    res = ssh_run(cfg.ssh, parts, runner=runner)
    if not res.ok() or not enter:
        return res
    return ssh_run(
        cfg.ssh, cfg.tmux_argv(["send-keys", "-t", cfg.session_name, "Enter"]), runner=runner
    )


def tmux_capture_pane(
    cfg: TmuxConfig, *, lines: int = 2000, runner: Runner = _real_runner
) -> ProcResult:
    return ssh_run(
        cfg.ssh,
        cfg.tmux_argv(["capture-pane", "-p", "-t", cfg.session_name, "-S", f"-{lines}"]),
        runner=runner,
    )
