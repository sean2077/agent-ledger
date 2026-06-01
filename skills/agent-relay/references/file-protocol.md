# agent-relay file protocol

> Source spec for the `relay` CLI implementation. v1.0.0; session schema v3.
> The binding 1.0 frozen-contract and compatibility policy is in §15.

## 1. Directory layout

```
.shared/
  _relay/
    .sentinel                          # bootstrap creates on the mount; presence proves mount alive
    bindings/                          # per-instance pair bindings (v0.13); see §13
      claude-<digest>.json             # one file per instance -> its current pair
  _archive/                            # `relay pairs archive` moves terminated pair dirs here (v0.16)
    <pair-slug>/                       # same on-disk layout as a live pair; skipped by every pair scan
  <pair-slug>/                         # YYYYMMDD-<topic>  (a "pair" = up to 2 instances)
    session.json                       # minimal pair metadata, see §3
    CLOSED                             # written by `relay close`; contains close meta
    README.md                          # written by bootstrap; human description
    .draft/                            # hidden; `relay claim` writes here
      001-codex-plan.md
    001-codex-plan.md                  # `relay publish` moves from .draft here
    001-codex-plan.md.sha256
    001-codex-plan.ready
    002-claude-review.md
    002-claude-review.md.sha256
    002-claude-review.ready
    ...
    archive/                           # pair-INTERNAL; future supersede support (current CLI never writes it). Not the top-level _archive/ above.
```

**Slug rules**:

- `pair-slug`: `YYYYMMDD-<topic>`, topic same rules. Single date prefix; the day is local time of `relay bootstrap`.
- `project`: kept only as `session.json.project` metadata. It is not a directory level.

**Hidden vs visible**:

- `.draft/` is hidden; peers and `relay status` do not list its contents.
- All other files under the pair dir are visible artifacts.

## 2. File naming

`NNN-<author>-<kind>.md`

- `NNN`: three-digit zero-padded sequence number, starting at `001`. Allows up to 999 artifacts per pair; if you hit the ceiling, open a new pair.
- `<author>`: short identifier of the writer. Recommended: `codex`, `claude`. Free-form ASCII + digits.
- `<kind>`: short identifier of the artifact type. Vocabulary in §4.

Examples: `001-codex-plan.md`, `015-claude-correction.md`.

Sidecars for published files only:

- `NNN-<author>-<kind>.md.sha256` — single line `<hex>  NNN-<author>-<kind>.md\n` (sha256sum format).
- `NNN-<author>-<kind>.ready` — zero-byte sentinel.

Drafts have no sidecars.

## 3. session.json schema

```json
{
  "schema_version": 3,
  "project": "<project-slug>",
  "session_id": "<YYYYMMDD-topic>",
  "title": "...",
  "state": "active",
  "created_at": "2026-05-27T13:30:00+08:00",
  "closed_at": null,
  "close_reason": null,
  "participants": ["codex", "claude"]
}
```

**Field reference**:

| field | type | required | notes |
|---|---|---|---|
| `schema_version` | int | yes | currently `3`; bump on breaking changes |
| `project` | str | yes | project slug for audit/display only; not a path component |
| `session_id` | str | yes | pair-slug (historical field name) |
| `title` | str | yes | human description |
| `state` | enum | yes | `"active"` or `"closed"` |
| `created_at` | ISO 8601 | yes | bootstrap time |
| `closed_at` | ISO 8601 \| null | yes | null while active; filled by `relay close` |
| `close_reason` | str \| null | yes | null while active; free-form by `relay close --reason` |
| `participants` | str[] | yes | agent identities expected to write in this pair |

Per-file frontmatter (§4) carries the round structure; `session.json` itself stays minimal.

## 4. Per-file frontmatter

Every published `NNN-<author>-<kind>.md` starts with YAML frontmatter:

```yaml
---
seq: 4
author: codex
peer: claude
kind: fix
status: ready
created: 2026-05-27T15:04:00+08:00
in_reply_to: 3
prompt_for_next: |
  - Specific actionable instructions for {peer}
  - Each line is one item
sync_needed: false
touched_paths:
  - relay/cli.py
corrects: null
---
```

### 4.1 Field reference

| field | type | required | notes |
|---|---|---|---|
| `seq` | int | yes | matches filename prefix (e.g., file `004-codex-fix.md` ⇒ `seq: 4`) |
| `author` | str | yes | who wrote this artifact |
| `peer` | str | yes | intended reader / next actor |
| `kind` | enum | yes | see §4.2 |
| `status` | enum | yes | see §4.3 |
| `created` | ISO 8601 | yes | publish time, with timezone |
| `in_reply_to` | int \| null | no | seq of the artifact this responds to; default = seq-1 |
| `prompt_for_next` | str (multiline) | yes | substantive instructions for peer; `publish` rejects placeholders like `"TODO: ..."` |
| `sync_needed` | bool | yes | true if peer must `relay sync pull` (host) or wait for host to push (remote) before acting |
| `touched_paths` | str[] | no | list of non-`.shared` paths the author modified |
| `corrects` | int \| null | no | seq of the artifact this corrects (forward-only); see §6 |
| `force_reason` | str | no | present iff publish used `--force`; explains the override reason |

### 4.2 `kind` vocabulary

Free-form short ASCII, but the well-known values are:

- `plan` — initial proposal or work outline
- `review` — feedback / cross-review on a peer's prior artifact
- `fix` — code or doc change accompanying the artifact
- `note` — informal update or status
- `question` — request for clarification from peer
- `decision` — final synthesis closing a topic
- `correction` — forward-only correction to a prior artifact (sets `corrects`)
- `addendum` — supplement to a prior artifact (sets `corrects`)

### 4.3 `status` vocabulary + terminal semantics

| status | meaning | pair-active impact | publish writes? |
|---|---|---|---|
| `draft` | in `.draft/`, not yet published | does not count | ✗ (publish writes `ready`) |
| `ready` | published, awaiting peer | **keeps pair active** | ✓ (default) |
| `closed` | this artifact concluded by author | **terminal** (no peer action expected) | ✓ (e.g., final decision) |
| `cancelled` | author/user withdrew | **terminal** | ✓ |
| `failed` | author or peer recorded that the artifact or requested work is broken and should not continue | **terminal** | ✓ |
| `timed_out` | long elapsed time without peer response | **terminal** | ✓ |

**All four terminal statuses (`closed`/`cancelled`/`failed`/`timed_out`) signal that THIS artifact no longer requires peer response.** A local `relay publish` validation failure leaves the draft in place and does not create a `failed` artifact.

## 5. Pair-active rule (CLI must hard-code this)

```
pair is active ⟺
    session.json.state == "active"
    AND no CLOSED sentinel file exists in pair dir
    AND (
        no published files yet (just bootstrapped)
        OR latest published file's status NOT IN {closed, cancelled, failed, timed_out}
    )
```

"Latest published file" = highest `seq` among `*.md` files with companion `.ready` sentinel.

`relay status` evaluates this; `relay close` independently can move the pair to closed via state transition or sentinel.

If more than one pair is active, `relay status`, `relay claim`, and `relay close` without `--pair-id` (and without a resolvable instance binding) refuse. `relay pairs list` is the discovery fallback and must not fail merely because zero or multiple pairs are active.

## 6. Append-only invariant

**Once a file is published, neither its content nor its frontmatter may be modified.** This includes status — you cannot edit `status: ready` to `status: closed` post hoc.

Two protocol-clean ways to change something already published:

### 6.1 Forward-only correction

Write a new artifact with the next available `seq`:

```yaml
---
seq: 9
author: claude
peer: codex
kind: correction
status: ready
created: 2026-05-27T16:00:00+08:00
in_reply_to: 4
corrects: 4
prompt_for_next: |
  Re-read file 9 in place of file 4's recommendations on X.
...
---
```

The new file references the old via `corrects`. Both files remain on disk. Readers should respect the latest correction chain.

### 6.2 Status transition via new artifact

If you need to mark something as closed/cancelled/failed/timed_out, write a *new* artifact (often `kind: decision` or `kind: note`) declaring that fact. Don't edit the original.

## 7. Concurrency

### 7.1 Sequence allocation

Sequence allocation is internal: `relay claim` derives the next NNN as `max(seqs in pair + .draft) + 1` (or `001` if empty) and reserves it atomically. There is no public "reserve a seq ahead of time" command — pre-reserving would race the append-only allocator.

`relay claim`:

1. Compute next seq via §7.1.
2. Write `.draft/NNN-<author>-<kind>.md` with frontmatter scaffold (`status: draft`, `prompt_for_next: "TODO: ..."`, etc.).
3. If `.draft/NNN-*.md` already exists from another concurrent claim, increment seq and retry. Up to 10 total attempts with `random.uniform(0.01, 0.05)` jitter between attempts after the first.
4. After exhausting 10 attempts, exit 2. The stderr message points at `relay doctor` so the operator can inspect for abandoned drafts before retrying.

`relay publish` (v0.9 exclusive-reservation semantics):

1. Validate the draft (see §8).
2. **Reserve the final `.md` path exclusively** via `open(..., O_CREAT|O_EXCL)` (`atomic_reserve_text`), write the rendered content into the reserved fd, and fsync. This is the concurrency primitive — a second publisher racing the same seq gets `FileExistsError`, never a silent clobber. (v0.8 used tmp+`os.replace`, which is atomic but *unconditional*: two publishers could both pass an `exists()` check and the later `os.replace` would overwrite the earlier `.md`. Fixed in v0.9.)
3. On `FileExistsError`, increment seq and retry, emitting a `seq NNN taken by concurrent publisher, retrying as NNN` line to stderr so the bump is observable. Up to 10 total attempts with `random.uniform(0.05, 0.20)` jitter.
4. Write sidecars **after** the visible `.md`: compute sha256 → `<published>.md.sha256`, then touch `<published>.ready`, then fsync the pair dir. The original draft is unlinked once the triad is in place.
5. After exhausting 10 attempts, exit 2 with the `relay doctor` recovery hint.

**Incomplete-triad invisibility.** Between step 2 and step 4 the `.md` exists on disk without its `.sha256`/`.ready` siblings. This partial state is **invisible to protocol-compliant readers**: `list_published()` (and everything that funnels through it — `status`, wait, pairs-list, latest-artifact helpers) returns a `.md` only when both `.ready` and `.md.sha256` exist AND the re-hashed `.md` matches the recorded digest. A reader MUST gate on `.ready` + sha256 match; it MUST NOT treat a bare `NNN-*.md` as published.

### 7.2 No locks

Exclusive `O_CREAT|O_EXCL` final-path reservation is the only concurrency primitive; sequence collisions are resolved by the seq-bump retry above. No `fcntl` advisory locks, no `mkdir` lease locks. (Pre-v0.9 the protocol relied on `rename(2)` atomicity instead; the reservation form additionally prevents the unconditional-overwrite race.)

## 8. Publish validation

`relay publish <draft-path>` rejects the draft (returns non-zero, leaves draft in place) if any of:

- File does not exist or is not under `.draft/`.
- Frontmatter cannot be parsed.
- Any required field (§4.1) is missing.
- `status` ≠ `draft` (drafts must be in draft state going into publish).
- `prompt_for_next` contains the placeholder marker `TODO:` (case-sensitive substring).
- `prompt_for_next` is empty or whitespace-only.
- Body (everything after the frontmatter close `---`) is empty or whitespace-only.

- `corrects` is set on a kind that may not carry it. Only `correction` (required) and `addendum` (optional) may set `corrects`; any other kind with a non-null `corrects` is rejected. A non-null `corrects` must be a positive int strictly less than the artifact's own `seq` (no self/future references).
- The resolved author (auto-detected from the platform signal, or `RELAY_AUTHOR` for a custom agent) is unresolved, or does not equal the draft's `author`. Publish is the authorship boundary and fails closed when identity is missing or mismatched.

On success:

1. Frontmatter `status` flipped to `ready` (or honoring the author's choice of `closed/cancelled/failed/timed_out` if explicitly set).
2. Frontmatter `created` filled with current ISO 8601 + timezone.
3. Exclusive `O_CREAT|O_EXCL` reservation of the published `.md` path, content written + fsync'd (see §7.1 for the concurrency rationale and the incomplete-triad invisibility guarantee).
4. Compute sha256, write `<published>.md.sha256`.
5. Touch `<published>.ready`.
6. fsync the pair dir; unlink the draft.

`relay publish` refuses inactive pairs by default. The escape hatch is limited to terminal append-only notes: `--force --force-reason TEXT --status <closed|cancelled|failed|timed_out>`. The override reason is recorded as `force_reason: TEXT` in the published frontmatter.

## 9. Close semantics

`relay close --reason "..." [--outcome <verdict>]`:

1. Refuse if a `CLOSED` file already exists.
2. Write `CLOSED` (TOML body):

   ```toml
   reason = "..."
   outcome = "approve | needs-change | blocked | <free-form>"
   closed_by = "<resolved author>"
   closed_at = "2026-05-27T18:00:00+08:00"
   ```

3. Update `session.json`: `state = "closed"`, `closed_at = <now>`, `close_reason = <reason>`.
4. Drop the closing instance's binding; peer-side stale bindings self-heal on next resolve.
5. **Do not modify any `NNN-*.md` file.** Append-only.

If user wants a final synthesis on record, they should `relay claim --kind decision` first, fill body and `prompt_for_next`, `relay publish`, then `relay close`.

## 10. Sidecar verification

A `published` artifact is **consumable** only when:

- `NNN-*.md` exists
- `NNN-*.md.sha256` exists and matches `sha256sum NNN-*.md`
- `NNN-*.ready` exists (zero bytes is fine)

Consumers (peer's `relay status`, peer reading) should treat artifacts without
all three as "in progress / not visible". `relay status` filters out incomplete
triads. `relay doctor` reports incomplete triads and orphan publish sidecars;
`relay doctor --fix --older-than <duration>` may remove only old, owner-safe
published-position leftovers, and it must preserve any triad whose matching
draft has a fresh heartbeat.

`.draft/*.md` deliberately has no sidecar — visibility is by absence of sidecar.

## 11. Reserved file names

**Inside a pair directory**, these names are reserved by the protocol:

- `session.json`
- `CLOSED`
- `README.md`
- `.draft/`
- `archive/` — pair-INTERNAL, reserved for future supersede support; the current CLI never writes it
- `*.sha256`, `*.ready`

**Directly under `.shared/`** (top level), these directory names are reserved and skipped when enumerating pairs:

- `_relay/` — binding registry + mount sentinel (§13)
- `_archive/` — whole pair dirs moved aside by `relay pairs archive` (v0.16). Distinct from the pair-internal `archive/` above; the leading underscore marks it a relay-managed top-level area, not a pair.

User must not write files matching these names by hand outside the CLI flow.

## 12. Encoding

All text files UTF-8. Frontmatter YAML uses the basic subset (scalars, lists, multiline scalars via `|`). The CLI's parser is a minimal handwritten one — do not depend on YAML features like anchors, references, or tags.

Line endings: LF only.

POSIX file modes: `relay` creates files with `0600` and dirs with `0700` to align with the `RELAY_SHARED_ROOT` permission policy. `preflight` warns if `.shared/` is not `0700`.

## 13. Instance bindings and multi-pair recovery (v0.13)

A **pair** is the collaboration unit (`.shared/<pair-slug>/`) holding at most **two instances**. An **instance** is one running agent session, identified by `<author>:<agent-session-id>` where the agent session id comes from `CLAUDE_CODE_SESSION_ID` (Claude Code), `CODEX_THREAD_ID` (Codex), or a per-terminal fallback. The agent session id is used ONLY by the binding layer — published artifacts still carry the bare `author`, so the artifact protocol is unchanged.

Each instance records which pair it is in as one file under `.shared/_relay/bindings/<binding-key>.json`, where `binding-key = <author>-<sha256(full agent-session-id)[:24]>` (the FULL id is hashed, never a truncated prefix — time-prefixed ids would otherwise collide). This per-instance binding replaces the single global `.active-session` marker, so two same-host instances each track their own current pair. Fields: `schema_version` (1), `instance_id` (short display form), `author`, `agent_session_id` (full), `pair_slug`, `bound_at`, `last_seen` (ISO 8601, refreshed best-effort + throttled).

`resolve_active_pair()` resolution order:

```
explicit --pair-id > this instance's binding (if it still names an active pair)
  > the sole active pair > else refuse, listing candidates ("multiple active pairs").
```

**Strict mode (`require_binding=True`).** Passive automation that resolves *on
behalf of one instance* — the Stop hook — calls `relay status --require-binding`
(and `relay wait --require-binding`). Write-boundary commands such as `relay
claim` also use strict resolution. Strict mode keeps the first two steps but
DISABLES the sole-active fallback: with no `--pair-id` and no live binding it
yields no pair — `status` emits a non-actionable payload (`bound_pair: null`,
`session: null`, empty `published`/`drafts`, `is_active: false`) at exit 0;
`wait` refuses with a non-zero exit; `claim` refuses before creating `.draft/`
content. This is the identity boundary that stops an unbound session from being
pulled or written into the lone active pair (issues
20260601T182646-2920d5b9 and 20260601T200726-e5da21d8). Bare interactive
`relay status` keeps the fallback.

`relay status --json` always includes **`bound_pair`** — the pair slug this
instance is bound to (`null` when unbound), consistent with `relay whoami`. It
reflects the binding regardless of how `session` was resolved (an explicit
`--pair-id` can differ from the binding), so a reader can distinguish a real
binding from a convenience resolution.

A binding whose pair is gone/inactive is dropped (self-healed) on resolve. `relay bootstrap` binds its creator; `relay close` and terminal `relay publish --status ...` drop the closing instance's binding. `relay pair join <slug>` / `relay pair leave` bind/unbind explicitly; `relay pair ensure` is the smart resolver (use binding → auto-join the sole compatible pair → else report `choose` / `bootstrap` / `full`).

**Capacity & recovery.** A pair holds 2 instance slots, derived from binding files (no lock → no deadlock). A full pair with a *stale* slot (`last_seen` older than `RELAY_RENEWAL_STALE_THRESHOLD`, default 3600s) is reclaimable. Racing joiners may transiently exceed 2 bindings; this breaks no invariant (artifacts never use `instance_id`) and self-corrects. `relay doctor [--fix]` reports/removes stale bindings — files only, never signaling a PID.

**Same-agent limitation.** Because artifacts route by `author` (the `peer` field), two instances of the SAME agent (claude+claude) cannot be addressed within one pair. `join` / `ensure` therefore refuse a pair that already holds a live same-author instance. Real same-agent pairing would require an artifact-routing change (e.g. a `peer_instance` field) and is out of scope.

`preflight` reports binding health under the check name `pair.binding`: bound→active pair = pass; no binding = pass (or **warn** if >1 active pair forces a choice); binding→inactive pair = **warn** (recoverable); unsupported binding or session schema = **warn** with `unsupported_schema` (not recoverable by cleanup). A pre-v0.13 `.active-session` file left on disk is inert; `relay doctor` surfaces it as informational.

`relay pairs list` lists every pair with its category (`active`/`terminal`/`closed`/`inactive`/`unsupported_schema`/`invalid_schema`), bound instances, and open slots.

## 14. Issue ledger (out-of-band feedback, v0.10)

Separate from the relay pair ledger above. The issue ledger is a
**user-local machine** store where agents record problems they hit
*while using the relay tool itself*, so a developer can triage them
later.

- **Location**: `~/.agent-ledger/relay-issues/`, overridable via
  `RELAY_ISSUES_DIR`. Deliberately NOT under `.shared/` (per-pair,
  gitignored, ephemeral) and NOT under any repo — issues accrue across
  every project and pair for this user on this machine. **`relay
  sync` never moves issues**: they stay on the host that filed them, so
  each developer triages their own machine's ledger.
- **One file per issue**: `<id>.md` where `id` is
  `YYYYMMDDThhmmss-<8hex>` (sortable, collision-resistant).
- **Safety**: `show`/`resolve` accept only `[0-9A-Za-z-]` id tokens (or
  a leading prefix), validated before any path is composed, so a
  reference can never escape the store via `..`, absolute paths, or
  glob metacharacters. An ambiguous prefix is reported distinctly from
  "not found". Unreadable/corrupt `*.md` files are surfaced by `list`
  (stderr warning + `unreadable` array in `--json`, exit 1), never
  silently dropped.
- **Frontmatter**: `id`, `created`, `reporter` (the resolved author —
  auto-detected from the platform signal, or `RELAY_AUTHOR` for a custom
  agent; `unknown` if unresolved), `project` (sanitized, best-effort),
  `pair` (active pair slug if resolvable, else null), `severity` (`minor`/`major`),
  `area` (`cli`/`hooks`/`docs`/`protocol`/`tests`/`build`/`other`),
  `title`, `status` (`open`/`resolved`), `resolved_at`, `resolution`.
- **Mutability**: unlike published pair artifacts, issues are a
  mutable tracker — `relay issue resolve` rewrites the file in place.
  They carry no `.ready`/`.sha256` sidecars and are not append-only.

Commands: `relay issue add --title T [--severity S] [--area A]
[--body TEXT | --body-file PATH|-]`, `relay issue list [--status
open|resolved|all] [--area A] [--json]`, `relay issue show <id|prefix>
[--json]`, `relay issue resolve <id|prefix> [--note "fixed in <sha>"]`.

## 15. Frozen contract & compatibility policy (1.0)

> Normative for 1.0 and later. This section governs every section above: it
> names the on-disk surfaces that 1.0 **freezes** and the only ways they may
> change after 1.0. At the 1.0.0 tag this contract is **binding**.

### 15.1 What 1.0 freezes

The following are the **frozen contract surfaces**. A conforming reader at any
1.x version MUST be able to read state written by 1.0:

| Surface | Frozen shape | Spec |
|---|---|---|
| Pair directory layout | `.shared/<pair-slug>/`, `.draft/`, top-level `_relay/` + `_archive/` | §1, §11 |
| Artifact filename | `NNN-<author>-<kind>.md` (3-digit zero-padded seq) | §2 |
| `session.json` | schema **v3**: required keys + `participants` (exactly two) | §3 |
| Frontmatter | required fields, `kind` vocabulary, `status` vocabulary + terminal set | §4, §4.1-§4.3 |
| Publish triad | `<base>.md` + `<base>.md.sha256` + `<base>.ready`; consumable only as a complete, hash-matching triad | §8, §10 |
| Sequence semantics | monotonic per pair; collisions rejected, never squashed | §7.1 |
| Append-only | published `.md` never mutated; corrections forward-only via new artifact | §6 |
| Binding registry | `_relay/bindings/<author>-<sha256(full-id)[:24]>.json`, `schema_version: 1` | §13 |
| Two-participant invariant | a pair has exactly two participants; routing is `author -> peer` | §3, §5, §13, §15.3 |

### 15.2 How the contract may change after 1.0

- **Additive, backward-compatible** changes are allowed within 1.x **without** a
  schema bump: new optional frontmatter fields, new `kind` values, new reserved
  top-level `_`-prefixed dirs, new sidecar kinds, provided a 1.0 reader that
  ignores the unknown still behaves correctly. New required fields are NOT
  additive.
- **Breaking** changes (removing/renaming a required field, changing a value's
  meaning, altering the triad, changing the binding-key derivation, changing the
  `session.json` shape incompatibly) require **either**:
  1. a **schema bump**: increment `session.json.schema_version` (and/or the
     binding `schema_version`) with the new reader accepting both old and new,
     **or**
  2. an **explicit migration**: a documented, idempotent `relay` step that
     rewrites old state to the new shape, fronted by a clear refusal when an
     un-migrated ledger is opened by a reader that needs the new shape.
- Silent breaking changes are forbidden post-1.0. The pre-1.0 "hard-remove
  deprecated, no compat shim" hygiene (see `CHANGELOG.md`) **ends at 1.0**.

Readers enforce the schema contract at read time. Missing or non-integer
`schema_version` is `invalid_schema`; a session or binding schema greater than
the compiled-in supported version is `unsupported_schema` and operational
commands hard-refuse with an upgrade message; a lower schema also refuses until
an explicit adapter or migration exists. Read-only commands (`preflight`,
`whoami`, `pairs list`, and `doctor`) may report these diagnostics, but must not
mutate unsupported records, even when `doctor --fix` is requested.

### 15.3 Forward-read guarantee (what the gate tests)

The compatibility promise is concretely: **a newer `relay` reads a 1.0 ledger.**
The 1.0 gate ships **one canonical 1.0 fixture**: a frozen pair tree containing a
`session.json` v3 file, a published-artifact triad chain, a binding file, and an
archived pair. A forward-read test asserts `relay status`, `relay wait`, and
`relay doctor` parse it and report a recoverable state. Multi-version
*historical* corpora (v0.9/v0.13/v0.15/v0.16) are explicitly **out of the 1.0
gate** (1.x compatibility enhancement): pre-1.0 made no compatibility promise,
so only the 1.0-forward direction is contractual.
