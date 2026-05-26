"""Ledger data model: session.json schema, targets[], round IO.

Source of truth is session.json (CLI-only writer). events.ndjson is the
audit log. Files live at:

  .shared/<project>/<session_id>/
    session.json
    brief.md
    events.ndjson
    latest -> r<n>           (convenience symlink; not authority)
    r<n>/                    (rounds; see round_dir())
    archive/  logs/  locks/

Round directory layout is created/inspected by round_dir() and friends.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from awb import atomic, events, locks

SCHEMA_VERSION = 1

TARGET_STATES = {
    "pending",
    "sent",
    "reply_present",
    "cancelled",
    "timed_out",
    "failed",
}
TARGET_TERMINAL = {"reply_present", "cancelled", "timed_out", "failed"}

ROUND_STATES = {"active", "closed", "aborted"}
SESSION_STATES = {"draft", "active", "done", "blocked"}

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")


class LedgerError(Exception):
    pass


@dataclass
class Target:
    agent: str
    required: bool = True
    state: str = "pending"
    requested_at: Optional[str] = None
    deadline_at: Optional[str] = None
    cancelled_at: Optional[str] = None
    reason: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Target":
        return cls(
            agent=d["agent"],
            required=bool(d.get("required", True)),
            state=d.get("state", "pending"),
            requested_at=d.get("requested_at"),
            deadline_at=d.get("deadline_at"),
            cancelled_at=d.get("cancelled_at"),
            reason=d.get("reason"),
        )

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "required": self.required,
            "state": self.state,
            "requested_at": self.requested_at,
            "deadline_at": self.deadline_at,
            "cancelled_at": self.cancelled_at,
            "reason": self.reason,
        }

    def is_terminal(self) -> bool:
        return self.state in TARGET_TERMINAL


@dataclass
class Round:
    number: int
    state: str = "active"
    opened_at: Optional[str] = None
    decided_at: Optional[str] = None
    note: Optional[str] = None
    targets: list[Target] = field(default_factory=list)

    @classmethod
    def from_dict(cls, number: int, d: dict) -> "Round":
        targets: list[Target] = []
        if "targets" in d:
            targets = [Target.from_dict(t) for t in d["targets"]]
        elif "agents" in d:  # legacy: r1-r4 used a flat string list
            targets = [
                Target(agent=name, required=True, state="reply_present")
                for name in d["agents"]
            ]
        return cls(
            number=number,
            state=d.get("state", "active"),
            opened_at=d.get("opened_at"),
            decided_at=d.get("decided_at"),
            note=d.get("note"),
            targets=targets,
        )

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "opened_at": self.opened_at,
            "decided_at": self.decided_at,
            "note": self.note,
            "targets": [t.to_dict() for t in self.targets],
        }

    def required_targets(self) -> list[Target]:
        return [t for t in self.targets if t.required]

    def all_required_terminal(self) -> bool:
        req = self.required_targets()
        return bool(req) and all(t.is_terminal() for t in req)

    def target(self, agent: str) -> Target:
        for t in self.targets:
            if t.agent == agent:
                return t
        raise LedgerError(f"round r{self.number} has no target {agent!r}")


@dataclass
class Remote:
    ssh_host: Optional[str] = None
    tmux_session: Optional[str] = None
    remote_root: Optional[str] = None
    tmux_socket: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict | None) -> "Remote":
        d = d or {}
        return cls(
            ssh_host=d.get("ssh_host"),
            tmux_session=d.get("tmux_session"),
            remote_root=d.get("remote_root"),
            tmux_socket=d.get("tmux_socket"),
        )

    def to_dict(self) -> dict:
        return {
            "ssh_host": self.ssh_host,
            "tmux_session": self.tmux_session,
            "remote_root": self.remote_root,
            "tmux_socket": self.tmux_socket,
        }


@dataclass
class Session:
    project: str
    session_id: str
    title: str
    state: str = "active"
    current_round: int = 1
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    rounds: dict[int, Round] = field(default_factory=dict)
    remote: Remote = field(default_factory=Remote)
    path: Optional[Path] = None

    @classmethod
    def from_dict(cls, d: dict, path: Path | None = None) -> "Session":
        rounds_raw = d.get("rounds", {})
        rounds = {int(k): Round.from_dict(int(k), v) for k, v in rounds_raw.items()}
        return cls(
            project=d["project"],
            session_id=d["session_id"],
            title=d.get("title", d["session_id"]),
            state=d.get("state", "active"),
            current_round=int(d.get("current_round", 1)),
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
            rounds=rounds,
            remote=Remote.from_dict(d.get("remote")),
            path=path,
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "project": self.project,
            "session_id": self.session_id,
            "title": self.title,
            "state": self.state,
            "current_round": self.current_round,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "remote": self.remote.to_dict(),
            "rounds": {str(n): r.to_dict() for n, r in sorted(self.rounds.items())},
        }

    def round(self, n: int) -> Round:
        if n not in self.rounds:
            raise LedgerError(f"session has no round r{n}")
        return self.rounds[n]

    def active_round(self) -> Round:
        return self.round(self.current_round)


# --- paths ----------------------------------------------------------------


def session_dir(ledger_root: Path, project: str, session_id: str) -> Path:
    return Path(ledger_root) / project / session_id


def round_dir(session_path: Path, n: int) -> Path:
    return Path(session_path) / f"r{n}"


def session_json_path(session_path: Path) -> Path:
    return Path(session_path) / "session.json"


def events_path(session_path: Path) -> Path:
    return Path(session_path) / "events.ndjson"


def lock_dir(session_path: Path) -> Path:
    return Path(session_path) / "locks" / "session.lock"


# --- IO -------------------------------------------------------------------


def load(session_path: Path) -> Session:
    session_path = Path(session_path)
    sj = session_json_path(session_path)
    if not sj.exists():
        raise LedgerError(f"no session.json at {sj}")
    data = json.loads(sj.read_text())
    return Session.from_dict(data, path=session_path)


def save(session: Session) -> None:
    if session.path is None:
        raise LedgerError("session has no path")
    session.updated_at = events.now_iso()
    atomic.atomic_write_json(session_json_path(session.path), session.to_dict())


# --- factory --------------------------------------------------------------


def create_session(
    ledger_root: Path,
    project: str,
    slug: str,
    title: str,
    target_agents: Iterable[str],
    *,
    when: str | None = None,
    remote: Remote | None = None,
) -> Session:
    if not SLUG_RE.match(slug):
        raise LedgerError(f"slug must match {SLUG_RE.pattern}, got {slug!r}")
    when = when or events.now_iso()
    date_prefix = when[:10].replace("-", "")
    session_id = f"{date_prefix}-{slug}"
    sp = session_dir(ledger_root, project, session_id)
    if sp.exists():
        raise LedgerError(f"session dir already exists: {sp}")

    for sub in ("archive", "logs", "locks"):
        (sp / sub).mkdir(parents=True, exist_ok=True)

    r1 = Round(
        number=1,
        state="active",
        opened_at=when,
        targets=[
            Target(agent=a, required=True, state="pending", requested_at=when)
            for a in target_agents
        ],
    )
    for sub in ("prompts", "replies", "context"):
        (round_dir(sp, 1) / sub).mkdir(parents=True, exist_ok=True)

    session = Session(
        project=project,
        session_id=session_id,
        title=title,
        state="active",
        current_round=1,
        created_at=when,
        updated_at=when,
        rounds={1: r1},
        remote=remote or Remote(),
        path=sp,
    )
    save(session)
    _update_latest(sp, 1)
    return session


def _update_latest(session_path: Path, n: int) -> None:
    latest = Path(session_path) / "latest"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
    except OSError:
        return
    try:
        latest.symlink_to(f"r{n}")
    except OSError:
        pass


def open_next_round(
    session: Session,
    target_agents: Iterable[str],
    *,
    note: str | None = None,
    when: str | None = None,
) -> Round:
    if session.path is None:
        raise LedgerError("session has no path")
    when = when or events.now_iso()
    next_n = max(session.rounds) + 1 if session.rounds else 1
    sp = session.path
    for sub in ("prompts", "replies", "context"):
        (round_dir(sp, next_n) / sub).mkdir(parents=True, exist_ok=True)
    new_round = Round(
        number=next_n,
        state="active",
        opened_at=when,
        note=note,
        targets=[
            Target(agent=a, required=True, state="pending", requested_at=when)
            for a in target_agents
        ],
    )
    session.rounds[next_n] = new_round
    session.current_round = next_n
    save(session)
    _update_latest(sp, next_n)
    return new_round


# --- session-locked event append -----------------------------------------


def append_event(session: Session, event: dict, *, command: str = "awb") -> dict:
    if session.path is None:
        raise LedgerError("session has no path")
    with locks.hold(lock_dir(session.path), command=command, lease_secs=60):
        return events.append(events_path(session.path), event)
