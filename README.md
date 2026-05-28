# agent-ledger

`agent-relay` connects interactive Claude Code and Codex CLI sessions through
an append-only shared file ledger so they can cross-review work without
writing API glue. The protocol is markdown + sidecars, so other agents can
participate too. Current: **v0.8.0** (pre-1.0).

See [`docs/why.md`](docs/why.md) for the longer take on what this is, what it
isn't, and what running it through interactive Claude Code + Codex actually
implies for usage limits and billing. See [`CHANGELOG.md`](CHANGELOG.md) for
version history (note: v0.6 removed the `RELAY_ROLE=host|remote` legacy
alias — see the migration table there).

The package lives in `skills/agent-relay/`:

- `SKILL.md` — workflow guide for Claude Code / Codex
- `bin/relay` — single-file Python CLI (3.10+, stdlib only)
- `references/` — protocol spec and rsync recipes
- `templates/` — env templates (only `envrc.same-host.example` and
  `envrc.dispatcher.example` since v0.6)

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

The repo ships a committed dispatcher `.envrc` that sources
`.envrc.$(hostname -s)`. Each machine (or each terminal, for same-host
setups) maintains its own per-host file (gitignored). Pick one path:

### Same-host (two agents on one machine)

```bash
relay init --role same-host
source .envrc                       # codex terminal (default identity)
# in the OTHER terminal:
export RELAY_AUTHOR=claude
source .envrc                       # claude terminal
```

The same-host template reads `RELAY_AUTHOR` from the calling shell
(defaulting to `codex`) and derives `RELAY_PEER` from it — one file
serves both terminals, no editing between launches.

### Explicit per-side flags (any topology)

```bash
# on the side that owns rsync transport (or any side in a same-machine setup)
relay init --author codex --peer claude --sync rsync
$EDITOR ".envrc.$(hostname -s)"     # fill RELAY_REMOTE_SSH/PATH
source .envrc

# on the other side
relay init --author claude --peer codex --sync none
source .envrc
```

This path renders `.envrc.<hostname>` inline from the flags (no template
copy). Use it whenever same-host isn't enough — two machines with rsync,
or any scenario where you want to pin the identity / sync pair
explicitly. Mutually exclusive with `--role same-host`.

### Notes

- `relay init` is idempotent. Re-running won't overwrite a customized
  `.envrc.<hostname>` or rewrite `.shared/_relay/.sentinel`. It also
  creates `.shared/` (default `$git_toplevel/.shared`) the first time.
- `RELAY_SYNC=rsync` is for the side that owns the rsync transport;
  the other side uses `RELAY_SYNC=none`. Preflight infers `none` when
  the project root is itself a fuse mount (shape A).
- Works with or without direnv.
