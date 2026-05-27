# agent-ledger

A skill + CLI for relaying work between a local Codex CLI agent (host) and a
remote interactive Claude Code agent (remote), through a shared `.shared/`
directory plus host-side rsync.

## v0.2.0+: skill + `relay` single-file CLI

Active implementation lives in `skills/agent-relay/`:

- `skills/agent-relay/SKILL.md` — workflow guide for Claude Code / Codex
- `skills/agent-relay/bin/relay` — single-file Python CLI (3.10+, stdlib only)
- `skills/agent-relay/references/` — full protocol spec and rsync recipes
- `skills/agent-relay/templates/` — env and brief templates
- `tests/test_relay_*.py` — pytest coverage

Install (host):

```bash
ln -s "$PWD/skills/agent-relay/bin/relay" ~/.local/bin/relay
ln -s "$PWD/skills/agent-relay" ~/.codex/skills/agent-relay
```

Install (remote, where Claude Code runs):

```bash
scp skills/agent-relay/bin/relay remote:~/.local/bin/relay
ssh remote chmod +x ~/.local/bin/relay
ssh remote ln -s /path/on/remote/skills/agent-relay ~/.agents/skills/agent-relay
```

Configure (per project): the repo ships a committed dispatcher `.envrc` that
sources `.envrc.$(hostname -s)`. Each machine maintains its own per-host
file (gitignored):

```bash
# on host (Codex side)
cp skills/agent-relay/templates/envrc.host.example   ".envrc.$(hostname -s)"
$EDITOR ".envrc.$(hostname -s)"                # fill RELAY_REMOTE_SSH/PATH
source .envrc                                  # or `direnv allow` if installed

# on remote (Claude Code side)
cp skills/agent-relay/templates/envrc.remote.example ".envrc.$(hostname -s)"
source .envrc
```

Both per-host files coexist on the shared mount; the dispatcher picks the
right one by hostname. Works with or without direnv.

## v0.1.x: `awb` (deprecated)

The previous implementation was a heavier Python package with tmux+SSH wakeup,
target-state orchestration, and a half-implemented autopilot scaffold. After
two GPT-5.5 cross-review rounds (`r6`/`r7` in
`.shared/agent-workbench-design/20260523-architecture-design/`), it was
superseded by the lighter `relay` design. The legacy code is preserved at git
tag **`awb-v0.1.0`**:

```bash
git show awb-v0.1.0           # commit metadata
git checkout awb-v0.1.0       # recover the old tree
git log -- awb/               # history of the deprecated path
```

## Design discussions

`.shared/agent-workbench-design/20260523-architecture-design/` contains the
full multi-round design conversation between Claude Opus and GPT-5.5@Codex
that led to this layout: r1-r2 initial designs and cross-reviews, r3 Claude's
directory schema, r4 GPT's mandatory schema patches, r5 awb implementation,
r6 design pivot to skill+CLI, r7 final correction of terminal-state semantics.
