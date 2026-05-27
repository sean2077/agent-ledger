# agent-relay file protocol

> Source spec for the `relay` CLI implementation. v1 (2026-05-27).
> Derived from plan v3 §1 (`~/.claude/plans/misty-gliding-muffin.md`) +
> r6/decision.md (9 patches absorbed) + r7/decision.md (terminal-state correction).

## 1. Directory layout

```
.shared/
  _relay/
    .sentinel                          # bootstrap creates on the mount; presence proves mount alive
  <project-slug>/
    <session-slug>/                    # YYYYMMDD-<topic>
      session.json                     # minimal session metadata, see §3
      CLOSED                           # written by `relay close`; contains close meta
      README.md                        # written by bootstrap; human description
      .draft/                          # hidden; `relay claim` writes here
        001-codex-plan.md
      001-codex-plan.md                # `relay publish` moves from .draft here
      001-codex-plan.md.sha256
      001-codex-plan.ready
      002-claude-review.md
      002-claude-review.md.sha256
      002-claude-review.ready
      ...
      archive/                         # v1.1 supersede uses this; v1 does not write here
```

**Slug rules**:

- `project-slug`: lowercase ASCII + digits + `-`, ≤ 48 chars. Default = `basename $(git rev-parse --show-toplevel)`.
- `session-slug`: `YYYYMMDD-<topic>`, topic same rules. Single date prefix; the day is local time of `relay bootstrap`.

**Hidden vs visible**:

- `.draft/` is hidden; peers and `relay status` do not list its contents.
- All other files under the session dir are visible artifacts.

## 2. File naming

`NNN-<author>-<kind>.md`

- `NNN`: three-digit zero-padded sequence number, starting at `001`. Allows up to 999 artifacts per session; if you hit the ceiling, open a new session.
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
  "schema_version": 2,
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
| `schema_version` | int | yes | bump on breaking changes; v1 = `2` |
| `project` | str | yes | project-slug |
| `session_id` | str | yes | session-slug |
| `title` | str | yes | human description |
| `state` | enum | yes | **only `"active"` or `"closed"`** — no `abandoned`/etc. |
| `created_at` | ISO 8601 | yes | bootstrap time |
| `closed_at` | ISO 8601 \| null | yes | null while active; filled by `relay close` |
| `close_reason` | str \| null | yes | null while active; free-form by `relay close --reason` |
| `participants` | str[] | yes | agent identities expected to write in this session |

No `rounds[]`, no `targets[]`, no `events[]` (that was r5 awb's overhead; r7 simplification). Per-file frontmatter (§4) is the round structure.

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

| status | meaning | session-active impact | publish writes? |
|---|---|---|---|
| `draft` | in `.draft/`, not yet published | does not count | ✗ (publish writes `ready`) |
| `ready` | published, awaiting peer | **keeps session active** | ✓ (default) |
| `closed` | this artifact concluded by author | **terminal** (no peer action expected) | ✓ (e.g., final decision) |
| `cancelled` | author/user withdrew | **terminal** | ✓ |
| `failed` | publish validation failed; or peer flagged content as broken | **terminal** | ✓ |
| `timed_out` | long elapsed time without peer response | **terminal** | ✓ |

**All four terminal statuses (`closed`/`cancelled`/`failed`/`timed_out`) signal that THIS artifact no longer requires peer response.** This closes the ghost-peer loophole flagged in GPT r4 review.

## 5. Session-active rule (CLI must hard-code this)

```
session is active ⟺
    session.json.state == "active"
    AND no CLOSED sentinel file exists in session dir
    AND (
        no published files yet (just bootstrapped)
        OR latest published file's status NOT IN {closed, cancelled, failed, timed_out}
        OR latest published file's prompt_for_next demands further peer action
    )
```

"Latest published file" = highest `seq` among `*.md` files with companion `.ready` sentinel.

`relay status` evaluates this; `relay close` independently can move session to closed via state transition or sentinel.

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

### 6.3 supersede (v1.1, not v1)

Deferred. v1 cannot move already-published files. If a published file is truly broken, use correction.

## 7. Concurrency

### 7.1 Sequence allocation

`relay next-seq` is advisory only. It returns `max(seqs in session + .draft) + 1`, or `001` if empty.

`relay claim`:

1. Compute next seq via §7.1.
2. Write `.draft/NNN-<author>-<kind>.md` with frontmatter scaffold (`status: draft`, `prompt_for_next: "TODO: ..."`, etc.).
3. If `.draft/NNN-*.md` already exists from another concurrent claim, increment seq once and retry.
4. On second failure, exit 1 and ask user to resolve.

`relay publish`:

1. Validate the draft (see §8).
2. Atomically rename `.draft/NNN-*.md` → `NNN-*.md`.
3. If the published path is already taken (extremely rare), increment seq once and retry.
4. On second failure, exit 1.

### 7.2 No locks

POSIX same-directory `rename(2)` is atomic. Sequence collisions are extremely rare with human-driven turns. No `fcntl` advisory locks, no `mkdir` lease locks — protocol relies on rename atomicity and the seq retry.

## 8. Publish validation

`relay publish <draft-path>` rejects the draft (returns non-zero, leaves draft in place) if any of:

- File does not exist or is not under `.draft/`.
- Frontmatter cannot be parsed.
- Any required field (§4.1) is missing.
- `status` ≠ `draft` (drafts must be in draft state going into publish).
- `prompt_for_next` contains the placeholder marker `TODO:` (case-sensitive substring).
- `prompt_for_next` is empty or whitespace-only.
- Body (everything after the frontmatter close `---`) is empty or whitespace-only.

On success:

1. Frontmatter `status` flipped to `ready` (or honoring the author's choice of `closed/cancelled/failed/timed_out` if explicitly set).
2. Frontmatter `created` filled with current ISO 8601 + timezone.
3. Atomic rename to the published path.
4. Compute sha256, write `<published>.sha256`.
5. Touch `<published>.ready`.
6. fsync the session dir.

## 9. Close semantics

`relay close --reason "..." [--outcome <verdict>]`:

1. Refuse if a `CLOSED` file already exists.
2. Write `CLOSED` (TOML body):

   ```toml
   reason = "..."
   outcome = "approve | needs-change | blocked | <free-form>"
   closed_by = "<RELAY_AUTHOR>"
   closed_at = "2026-05-27T18:00:00+08:00"
   ```

3. Update `session.json`: `state = "closed"`, `closed_at = <now>`, `close_reason = <reason>`.
4. **Do not modify any `NNN-*.md` file.** Append-only.

If user wants a final synthesis on record, they should `relay claim --kind decision` first, fill body and `prompt_for_next`, `relay publish`, then `relay close`.

## 10. Sidecar verification

A `published` artifact is **consumable** only when:

- `NNN-*.md` exists
- `NNN-*.md.sha256` exists and matches `sha256sum NNN-*.md`
- `NNN-*.ready` exists (zero bytes is fine)

Consumers (peer's `relay status`, peer reading) should treat artifacts without all three as "in progress / not visible". `relay status` filters out incomplete triads.

`.draft/*.md` deliberately has no sidecar — visibility is by absence of sidecar.

## 11. Reserved file names

Inside a session directory, these names are reserved by the protocol:

- `session.json`
- `CLOSED`
- `README.md`
- `.draft/`
- `archive/`
- `*.sha256`, `*.ready`

User must not write files matching these names by hand outside the CLI flow.

## 12. Encoding

All text files UTF-8. Frontmatter YAML uses the basic subset (scalars, lists, multiline scalars via `|`). The CLI's parser is a minimal handwritten one — do not depend on YAML features like anchors, references, or tags.

Line endings: LF only.

POSIX file modes: `relay` creates files with `0600` and dirs with `0700` to align with the `RELAY_SHARED_ROOT` permission policy. `preflight` warns if `.shared/` is not `0700`.
