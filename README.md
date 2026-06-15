# agent-ledger

> [!IMPORTANT]
> **Superseded — now integrated into [oma (oh-my-agents)](https://github.com/sean2077/oh-my-agents).**
> agent-ledger's cross-review pairing has been reimplemented as the native
> `oma relay` v2 inside the `oma` single-binary CLI. **For new work, use
> [`sean2077/oh-my-agents`](https://github.com/sean2077/oh-my-agents)** — it
> ships the same pair-delivery flow (plan → review → implement → review →
> decision) plus a statusline and auto-continue hooks, as a fail-closed Go
> binary (no Python runtime).
>
> This repository stays in **maintenance mode** for reference and for existing
> v1 `.shared/` ledgers. Note: `oma relay` v2 uses a fresh on-disk format
> (`.oma/relay/`) and deliberately does **not** read or migrate v1 `.shared/`
> ledgers — they remain valid for archival/manual reference only.

`agent-relay` connects interactive Claude Code and Codex CLI sessions through
an append-only shared file ledger so they can cross-review work without
writing API glue. The protocol is markdown + sidecars, so other agents can
participate too. Current: **v1.5.3**.

See [`docs/why.md`](docs/why.md) for the longer take on what this is, what it
isn't, and what running it through interactive Claude Code + Codex actually
implies for usage limits and billing. See [`CHANGELOG.md`](CHANGELOG.md) for
version history.

The package lives in `skills/agent-relay/`:

- `SKILL.md` — workflow guide for Claude Code / Codex
- `bin/relay` — single-file Python CLI (3.10+, stdlib only)
- `references/` — protocol spec and rsync recipes
- `templates/` — `envrc.dispatcher.example` (optional per-host env; only the
  rsync transport owner needs a per-host file)

## Install

```bash
npx skills add sean2077/agent-ledger -g --agent claude-code codex --skill agent-relay -y
```

The skills installer places the canonical package at
`~/.agents/skills/agent-relay` and links agent-specific skill directories to
that copy when needed. For a two-machine setup, run the same command on each
host.

When developing from a checkout and dogfooding local changes, install from the
checkout and point the executable + hook mechanics back at the repo:

```bash
npx skills add . -g --agent claude-code codex --skill agent-relay -y
mkdir -p ~/.agents/skills/agent-relay/bin
ln -sfn "$PWD/skills/agent-relay/bin/relay" ~/.agents/skills/agent-relay/bin/relay
rm -rf ~/.agents/skills/agent-relay/hooks
ln -s "$PWD/skills/agent-relay/hooks" ~/.agents/skills/agent-relay/hooks
```

## Configure

`author` auto-detects from the platform signal (`CLAUDE_CODE_SESSION_ID` for
Claude Code, `CODEX_THREAD_ID` for Codex) and `peer` is derived from the pair,
so **the common cases need no identity env at all**.

### Same-host (two agents on one machine)

```bash
relay init --same-host      # confirms setup; writes no .envrc
```

Nothing to configure: each terminal's platform signal names its author, and
peer is derived from the pair. Just run the relay skill in each agent;
`relay bootstrap --topic <slug>` starts the pair.

Pairs must be two different artifact authors. `claude+codex` is the intended
path; `claude+claude` or `codex+codex` is refused because artifacts route by
the `peer` author field, not by per-window instance id.

### Two machines (rsync transport)

On the side that owns the rsync transport:

```bash
relay init --sync rsync
$EDITOR ".envrc.$(hostname -s)"     # fill RELAY_REMOTE_SSH / RELAY_REMOTE_PATH
source .envrc                       # or: direnv allow
```

The other side needs nothing (it defaults to `RELAY_SYNC=none`; author still
auto-detects). A **custom (non-claude/codex) agent** that has no platform
signal pins its identity with `relay init --author <name>` (an override).

### Notes

- `relay init` is idempotent and creates `.shared/` the first time. When
  `RELAY_SHARED_ROOT` is unset, relay commands default to
  `$git_toplevel/.shared` (equivalent to `$PWD/.shared` from the project root).
- `RELAY_SYNC=rsync` is for the side that owns the rsync transport; unset
  defaults to `none`.
- `RELAY_AUTHOR` is only an override for a custom agent. `RELAY_PEER` is retired
  and not read at runtime; peer comes from the pair's `session.json`.
- `direnv` is optional, and only useful for the rsync owner's `.envrc`.

## Privacy & trust surface

`agent-relay` is a local file protocol by default. The shared ledger lives under
`.shared/`, and `relay` creates directories with mode `0700` and files with mode
`0600`; `relay preflight` warns if `.shared/` is wider than `0700`. The ledger
contains the artifacts the agents publish plus metadata such as `touched_paths`
path references. In a same-host setup, those files do not leave the machine.

Sync is opt-in. Only the side configured with `RELAY_SYNC=rsync` can run
`relay sync`; the unset/default mode is `none`. `relay sync` uses your own SSH
target (`RELAY_REMOTE_SSH` / `RELAY_REMOTE_PATH`) and defaults to non-mirroring
behavior: `--delete` is off unless you explicitly ask to mirror deletions.

The tool-feedback issue ledger is separate and machine-local. `relay issue`
writes under `~/.agent-ledger/relay-issues/` by default (overridable with
`RELAY_ISSUES_DIR`), not under `.shared/`, and `relay sync` never moves those
issue files.

Details live in the protocol references: file modes in
[`file-protocol.md` §12](skills/agent-relay/references/file-protocol.md#12-encoding),
the issue ledger in
[`file-protocol.md` §14](skills/agent-relay/references/file-protocol.md#14-issue-ledger-out-of-band-feedback-v010),
and sync behavior in
[`rsync-recipes.md`](skills/agent-relay/references/rsync-recipes.md).

## Project hygiene

- License: [MIT](LICENSE)
- Security policy: [SECURITY.md](SECURITY.md) and
  [`docs/threat-model.md`](docs/threat-model.md)
- Contribution guide: [CONTRIBUTING.md](CONTRIBUTING.md)
