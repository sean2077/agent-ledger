# Contributing

Thanks for improving `agent-relay`. The project optimizes for a practical,
drop-in tool that two interactive agents can use without an API-key
orchestrator.

## Before You Start

Read [`AGENTS.md`](AGENTS.md) first. It is the authoritative agent-facing
contract for this repo and defines source-of-truth boundaries, safety rules,
verification requirements, and commit-message expectations.

For protocol or workflow context, also read:

- [`docs/why.md`](docs/why.md)
- [`skills/agent-relay/SKILL.md`](skills/agent-relay/SKILL.md)
- [`skills/agent-relay/references/file-protocol.md`](skills/agent-relay/references/file-protocol.md)
- [`skills/agent-relay/references/hook-protocol.md`](skills/agent-relay/references/hook-protocol.md)

## Development Principles

- Keep `skills/agent-relay/bin/relay` as a single Python file using the
  standard library only.
- Prefer fail-closed behavior at identity, artifact, and publish boundaries.
- Keep diffs small, reviewable, and reversible.
- Update the right source of truth when behavior changes: `AGENTS.md` for
  current agent-facing rules, `file-protocol.md` for durable on-disk contracts,
  and `CHANGELOG.md` for version history.
- Do not edit published `.shared/*.md` artifacts that have `.ready` sidecars;
  append a correction artifact instead.

## Validation

Run the full test suite before calling code work done:

```bash
python -m pytest -q
```

For focused changes, run the relevant targeted tests first, then the full suite.
Protocol, parser, identity, publish, heartbeat, hook, or sync changes need
regression coverage for both the success path and the fail-closed path.

## Commits

Use the Lore commit protocol documented in [`AGENTS.md`](AGENTS.md). Commit
messages should explain why the change was made and include the required
trailers, including what was tested and what was not tested.
