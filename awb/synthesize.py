"""`awb synthesize` — produce decision.draft.md from round replies, optionally publish.

`awb synthesize`               → writes r<n>/decision.draft.md (template).
`awb synthesize --publish`     → atomic rename to decision.md, close round,
                                  open r<n+1> with the SAME targets unless
                                  --no-open / --next-target.

Publish refuses if any required target isn't terminal.
"""

from __future__ import annotations

import sys
from pathlib import Path

from awb import atomic, events, ledger


class SynthesizeError(Exception):
    pass


DRAFT_TEMPLATE = """\
# r{round} decision (DRAFT)

> 自动生成模板。请由 codex / 人填充实质决策，然后用 `awb synthesize --publish` 提交。
> 项目：{project} / 会话：{session_id}
> 生成时间：{when}

## Verdict

`approve | needs-change | blocked`

## 本轮 reply 综述

{reply_summary}

## 已采纳

- ...

## 已拒绝 / 改写

- ...

## Open Items

- ...

## 下一轮建议

- targets：{next_targets_suggestion}
- 重点：

## Confidence

`low | medium | high`
"""


def _reply_summary(session: ledger.Session, round_n: int) -> str:
    rdir = session.path / f"r{round_n}" / "replies"
    rnd = session.round(round_n)
    lines: list[str] = []
    for t in rnd.targets:
        ready = rdir / f"{t.agent}.ready"
        md = rdir / f"{t.agent}.md"
        sha = rdir / f"{t.agent}.md.sha256"
        if not ready.exists() or not md.exists():
            lines.append(f"- {t.agent} ({t.state}): no reply")
            continue
        first = ""
        for ln in md.read_text(encoding="utf-8", errors="replace").splitlines():
            ln = ln.strip()
            if ln:
                first = ln
                break
        sha_value = ""
        if sha.exists():
            sha_value = sha.read_text().strip().split()[0][:12]
        lines.append(f"- {t.agent} ({t.state}, sha {sha_value}): {first}")
    return "\n".join(lines) if lines else "- (no targets)"


def write_draft(session: ledger.Session, round_n: int) -> Path:
    if session.path is None:
        raise SynthesizeError("session has no path")
    session.round(round_n)  # raises if missing
    draft = session.path / f"r{round_n}" / "decision.draft.md"
    text = DRAFT_TEMPLATE.format(
        round=round_n,
        project=session.project,
        session_id=session.session_id,
        when=events.now_iso(),
        reply_summary=_reply_summary(session, round_n),
        next_targets_suggestion=", ".join(t.agent for t in session.round(round_n).targets),
    )
    atomic.atomic_write_text(draft, text, mode=0o600)
    return draft


def publish(
    session: ledger.Session,
    round_n: int,
    *,
    open_next: bool = True,
    next_targets: list[str] | None = None,
) -> tuple[Path, ledger.Round | None]:
    if session.path is None:
        raise SynthesizeError("session has no path")
    rnd = session.round(round_n)
    if not rnd.all_required_terminal():
        non = [t.agent for t in rnd.required_targets() if not t.is_terminal()]
        raise SynthesizeError(
            f"cannot publish: required targets not terminal: {non}"
        )

    draft = session.path / f"r{round_n}" / "decision.draft.md"
    decided = session.path / f"r{round_n}" / "decision.md"
    if decided.exists():
        raise SynthesizeError(f"decision.md already exists: {decided}")
    if not draft.exists():
        raise SynthesizeError(
            f"no draft to publish at {draft}; run `awb synthesize` first"
        )

    # atomic rename within the round dir (same filesystem)
    import os
    os.replace(draft, decided)
    rnd.state = "closed"
    rnd.decided_at = events.now_iso()

    new_round: ledger.Round | None = None
    if open_next:
        targets = next_targets or [t.agent for t in rnd.targets if t.required]
        new_round = ledger.open_next_round(session, targets, note="auto-opened after decision")
    else:
        ledger.save(session)
    return decided, new_round


# --- CLI -----------------------------------------------------------------


def cmd_synthesize(args) -> int:
    from awb.session import _resolve_session
    try:
        session = _resolve_session(args.ledger, args.project, args.session)
    except ledger.LedgerError as exc:
        print(f"awb synthesize: {exc}", file=sys.stderr)
        return 2
    round_n = args.round or session.current_round

    if not args.publish:
        try:
            draft = write_draft(session, round_n)
        except SynthesizeError as exc:
            print(f"awb synthesize: {exc}", file=sys.stderr)
            return 2
        ledger.append_event(
            session,
            {"actor": "awb", "event": "decision.draft.written", "round": round_n,
             "path": str(draft.relative_to(session.path))},
            command="awb synthesize",
        )
        print(f"wrote draft: {draft}")
        return 0

    try:
        decided, nxt = publish(
            session, round_n,
            open_next=not args.no_open,
            next_targets=args.next_target or None,
        )
    except SynthesizeError as exc:
        print(f"awb synthesize: {exc}", file=sys.stderr)
        return 2
    ledger.append_event(
        session,
        {"actor": "awb", "event": "decision.published", "round": round_n,
         "path": str(decided.relative_to(session.path))},
        command="awb synthesize --publish",
    )
    ledger.append_event(
        session,
        {"actor": "awb", "event": "round.closed", "round": round_n},
        command="awb synthesize --publish",
    )
    print(f"published: {decided}")
    if nxt:
        ledger.append_event(
            session,
            {"actor": "awb", "event": "round.opened", "round": nxt.number,
             "targets": [t.agent for t in nxt.targets]},
            command="awb synthesize --publish",
        )
        print(f"opened r{nxt.number} with targets: {[t.agent for t in nxt.targets]}")
    return 0
