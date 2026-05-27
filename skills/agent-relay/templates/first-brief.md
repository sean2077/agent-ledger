# Relay session brief template

When bootstrapping a new relay session, the first artifact should establish what the session is for.

Typical pattern: `relay claim --kind plan` then fill the body with the brief.

## Suggested body structure

```markdown
## Goal

What this session is supposed to accomplish, in one sentence.

## Context

- Repo: <name + commit>
- Driver of the change: bug report / feature request / refactor / etc.
- Constraints: timeline, dependencies, hard limits

## Open questions

- ...
- ...

## Proposed approach

1. ...
2. ...
3. ...
```

## What to put in `prompt_for_next`

Direct, actionable instructions for the peer. Examples for a first plan handoff:

```yaml
prompt_for_next: |
  - Review the proposed approach against the constraints above.
  - Flag any of the open questions that block starting work.
  - If approach is sound, respond with kind: review and concrete refinements.
  - If approach is wrong, respond with kind: review, keep frontmatter status ready,
    and write the rejection plus alternative in the body.
```
