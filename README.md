# agent-ledger

A skill + CLI for relaying work between a local Codex CLI agent (host) and a
remote interactive Claude Code agent (remote). Current: **v0.3.1** (pre-1.0).

The package lives in `skills/agent-relay/`:

- `SKILL.md` — workflow guide for Claude Code / Codex
- `bin/relay` — single-file Python CLI (3.10+, stdlib only)
- `references/` — protocol spec and rsync recipes
- `templates/` — env and brief templates

## Install

Host (Codex side):

```bash
ln -s "$PWD/skills/agent-relay/bin/relay" ~/.local/bin/relay
ln -s "$PWD/skills/agent-relay" ~/.codex/skills/agent-relay
```

Remote (Claude Code side):

```bash
scp skills/agent-relay/bin/relay remote:~/.local/bin/relay
ssh remote chmod +x ~/.local/bin/relay
ssh remote ln -s /path/on/remote/skills/agent-relay ~/.agents/skills/agent-relay
```

## Configure

The repo ships a committed dispatcher `.envrc` that sources
`.envrc.$(hostname -s)`. Each machine maintains its own per-host file
(gitignored):

```bash
# on host
cp skills/agent-relay/templates/envrc.host.example   ".envrc.$(hostname -s)"
$EDITOR ".envrc.$(hostname -s)"                # fill RELAY_REMOTE_SSH/PATH
source .envrc                                  # or `direnv allow`

# on remote
cp skills/agent-relay/templates/envrc.remote.example ".envrc.$(hostname -s)"
source .envrc
```

Both per-host files coexist on the shared mount; the dispatcher picks the
right one by hostname. Works with or without direnv.
