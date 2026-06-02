# agent-ledger

`agent-relay` connects interactive Claude Code and Codex CLI sessions through
an append-only shared file ledger so they can cross-review work without
writing API glue. The protocol is markdown + sidecars, so other agents can
participate too. Current: **v1.1.0**.

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
ln -s "$PWD/skills/agent-relay/bin/relay" ~/.local/bin/relay
ln -s "$PWD/skills/agent-relay" ~/.codex/skills/agent-relay
ln -s "$PWD/skills/agent-relay" ~/.claude/skills/agent-relay
```

For a two-machine setup (each side has its own checkout), copy or `scp`
the same bits onto the other host and symlink the skill into its
`~/.codex/skills/` or `~/.agents/skills/`.

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
