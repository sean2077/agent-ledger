# Why agent-relay

> **TL;DR.** `agent-relay` lets an interactive Claude Code session and an
> interactive Codex CLI session cross-review each other through an
> append-only file ledger. The relay itself requires no API keys, no
> central long-running orchestrator daemon, and no additional per-call API
> billing path. Usage of the underlying tools remains subject to whatever
> limits and billing terms those tools have.

This page is the longer take. The README and SKILL.md keep it short on
purpose; this is where the caveats live.

## What the tool actually is

Two things and only two things:

1. A small Python CLI (`relay`) that does mechanical operations on a shared
   directory — atomic writes, sequence numbers, frontmatter validation,
   rsync wrapping, heartbeat sidecars.
2. A skill (`agent-relay`) that teaches an interactive coding agent how
   to use that CLI to cross-review with a peer over a shared file ledger.

There is no orchestrator process, no message bus, no central scheduler. The
filesystem is the substrate. Each side reads what the other published,
does work, publishes a response. The baseline workflow is manual/user-driven:
the human pokes each side via the agent's existing interactive UI. Optional
hooks can assist continuation by surfacing relay state, but they do not become
a central orchestrator.

## Why it exists

Modern coding agents like Claude Code and Codex CLI are individually
strong. Putting two of them in conversation — one drafts, the other
reviews, then they swap — produces better work than either alone. But the
obvious way to wire that up has costs:

- Building an orchestrator on top of the **Anthropic** and **OpenAI APIs**
  means standing up infrastructure (API keys, retries, prompt scaffolding,
  rate-limit handling) and paying per-call token billing for every
  back-and-forth.
- Going headless inside one of these tools (subprocess automation, screen
  scraping) gives up the interactive review surface that makes them
  useful in the first place — diffs, plan mode, tool gating, etc.

`agent-relay` skips both paths. The two agents stay in their **interactive
sessions**. The relay between them is just markdown files in a shared
directory. The CLI handles the boring atomic-write / sequence-number /
sidecar parts so neither agent can corrupt the ledger.

## What "no API tokens burned" does **not** mean

The phrasing matters because it's easy to read into.

- **What's true.** The relay protocol itself does not call the Anthropic
  or OpenAI API. There is no API key in `RELAY_*` env vars. No per-call
  billing path is introduced by running the relay.
- **What's not true.** "Free" or "unlimited" is *not* the claim. The
  agents on each end of the relay still consume whatever resource the
  user is paying for to run those agents.

Concretely:

- **Interactive Claude Code** can be backed by a Claude.ai subscription
  (Pro / Max) or by an Anthropic Console / API account. The subscription
  path applies subscription-level usage limits; the API path applies
  per-token billing. See Anthropic's
  [Claude Code overview](https://docs.anthropic.com/en/docs/claude-code/overview)
  and
  [costs page](https://docs.anthropic.com/en/docs/claude-code/costs).
- **Interactive Codex CLI** can run against a ChatGPT subscription or
  against the OpenAI API. OpenAI documents that Codex is included with
  ChatGPT plans and lists the Codex CLI as a supported client; API usage
  is billed separately from ChatGPT plans. See
  [Codex in ChatGPT (official article)](https://help.openai.com/en/articles/11369540-icodex-in-chatgpt)
  and
  [API pricing](https://openai.com/api/pricing/).

So the honest statement is: *the relay itself doesn't add a billing
boundary*. Whether running an N-round cross-review costs you money depends
entirely on how you're paying for the two interactive tools. Always
verify current vendor terms — pricing and inclusion details change
independently and faster than this document.

## Setup topologies

Two axes, often confused but actually independent: **how many machines**
(same-host vs two-machine) and **what's on the filesystem** (shape A
vs shape B).

### How many machines?

- **Same-host.** One machine, one checkout, two terminals. Both agents
  see the exact same files via the same kernel. `RELAY_SYNC=none` on
  both sides; no rsync involved. This is the default recommendation
  and what `relay init --same-host` sets up. Identity needs no env: each
  terminal's author auto-detects from its platform signal
  (`CLAUDE_CODE_SESSION_ID` / `CODEX_THREAD_ID`).
- **Two-machine.** Two boxes. One side runs Codex CLI, the other runs
  Claude Code. The ledger has to be reachable from both. Combine with
  one of the two filesystem shapes below.

### What's on the filesystem? (shape A vs shape B)

`relay preflight` detects the shape by checking whether the git project
root is itself a fuse mount. The two shapes diverge only on
"how do edits propagate":

- **Shape A — project root IS the mount.** One side has the project
  checkout; the other side mounts it via sshfs and operates in-place.
  Both sides write to the same filesystem; rsync is structurally
  inapplicable because there is only one copy of the project. Relay
  defaults to `RELAY_SYNC=none` when the variable is unset.
- **Shape B — two project copies.** Each side has its own checkout;
  the side that owns the rsync transport sets `RELAY_SYNC=rsync` and
  runs `relay sync push` / `pull` to keep them in step. Requires
  `RELAY_REMOTE_SSH` + `RELAY_REMOTE_PATH`.

### How they combine

| | Shape A (root is mount) | Shape B (two project copies) |
|---|---|---|
| Same-host | not meaningful — only one machine | not meaningful — only one machine |
| Two-machine | both sides `SYNC=none` by default | one side `SYNC=rsync`, other side defaults to `SYNC=none` |

Same-host is its own bucket: shape doesn't apply because there's nothing
to mount or sync. Shape A and shape B are exclusive to the two-machine
case.

Only the rsync owner needs an explicit `RELAY_SYNC=rsync`. All other
sides default to `RELAY_SYNC=none`.

For the quickstart-level privacy and trust summary, see
[`README.md`](../README.md#privacy--trust-surface).

## Why a file ledger beats copy-paste

The ledger isn't just a glorified chat log. The protocol enforces enough
structure to make peer review actually useful:

- **Sequence numbers** make ordering unambiguous; collisions are rejected
  loudly instead of silently squashed.
- **Frontmatter `kind`** (`plan`, `review`, `fix`, `decision`, ...) lets
  each side know what the other expects, and lets the SKILL break the
  auto-loop on terminal kinds.
- **`prompt_for_next`** is an explicit instruction block. The peer's
  next turn has a brief, not just history to read.
- **Append-only + sidecar `.sha256` + `.ready`** make the ledger
  auditable: every artifact is content-addressed, and a missing
  `.ready` means publish failed (don't trust the file).
- **Heartbeat + renewal-file protocol** lets the waiting side distinguish
  a live peer from stale relay state without trusting timeout alone.

None of these prevent the user from intervening — they're guardrails for
the bots, not gates on the human.

## What this tool is **not**

- Not a multi-agent framework. It coordinates exactly two interactive
  sessions at a time; bringing in a third agent works mechanically but
  isn't the design target.
- Not a substitute for code review by a human. Two agents reviewing each
  other catch many issues but miss the kinds humans catch in seconds.
- Not a CI/CD pipeline. Sync is `rsync`, not a publish-and-deploy
  workflow.
- Not vendor-blessed. Anthropic and OpenAI ship their own coding tools;
  this is a third-party way of bridging them via the file system.
