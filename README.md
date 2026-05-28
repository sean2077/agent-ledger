# agent-ledger

`agent-relay` connects interactive Claude Code and Codex CLI sessions through
an append-only shared file ledger so they can cross-review work without
writing API glue. The protocol is markdown + sidecars, so other agents can
participate too. Current: **v0.5.0** (pre-1.0).

See [`docs/why.md`](docs/why.md) for the longer take on what this is, what it
isn't, and what running it through interactive Claude Code + Codex actually
implies for usage limits and billing.

The package lives in `skills/agent-relay/`:

- `SKILL.md` — workflow guide for Claude Code / Codex
- `bin/relay` — single-file Python CLI (3.10+, stdlib only)
- `references/` — protocol spec and rsync recipes
- `templates/` — env and brief templates

## Install

Same machine (recommended for v0.5+: both agents in two terminals on one host):

```bash
ln -s "$PWD/skills/agent-relay/bin/relay" ~/.local/bin/relay
ln -s "$PWD/skills/agent-relay" ~/.codex/skills/agent-relay
ln -s "$PWD/skills/agent-relay" ~/.claude/skills/agent-relay
```

Two machines with rsync (legacy `host`/`remote` topology):

```bash
# on the rsync side
ln -s "$PWD/skills/agent-relay/bin/relay" ~/.local/bin/relay
ln -s "$PWD/skills/agent-relay" ~/.codex/skills/agent-relay

# on the other side
scp skills/agent-relay/bin/relay other:~/.local/bin/relay
ssh other chmod +x ~/.local/bin/relay
ssh other ln -s /path/on/other/skills/agent-relay ~/.agents/skills/agent-relay
```

## Configure

The repo ships a committed dispatcher `.envrc` that sources
`.envrc.$(hostname -s)`. Each machine (or each terminal, for same-host
setups) maintains its own per-host file (gitignored). `relay init`
bootstraps it from the appropriate template:

```bash
# same-host setup: two agents on one machine (recommended)
relay init --role same-host
source .envrc                           # or `direnv allow` — defaults to RELAY_AUTHOR=codex

# in the OTHER terminal (the one running Claude Code), override identity
# before sourcing so both agents see the same .envrc file but get
# distinct AUTHOR/PEER values:
export RELAY_AUTHOR=claude
source .envrc                           # or `direnv reload`

# two-machine setup (legacy RELAY_ROLE pair, still supported)
relay init --role host                  # on the rsync side
relay init --role remote                # on the other side
```

The same-host template reads `RELAY_AUTHOR` from the calling shell
(defaulting to `codex`) and derives `RELAY_PEER` from it — one file
serves both terminals, no editing between launches. Same-host setups
also set `RELAY_SYNC=none` (no rsync involved). Two-machine setups set
`RELAY_SYNC=rsync` on the side that owns the transport;
`RELAY_ROLE=host|remote` is still accepted as an alias for one more
release but emits a deprecation warn in `relay preflight`.

`relay init` is idempotent: re-running won't overwrite a customized
`.envrc.<hostname>` or rewrite `.shared/_relay/.sentinel`. It also creates
`.shared/` (default `$git_toplevel/.shared`) the first time. Both per-host
files coexist on the shared mount; the dispatcher picks the right one by
hostname. Works with or without direnv.
