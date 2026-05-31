"""Static docs/CLI consistency checks for agent-relay."""

from pathlib import Path

import relay


ROOT = Path(__file__).resolve().parent.parent


DELETED_V05_ENVRC_TEMPLATES = [
    "envrc.host.example",
    "envrc.remote.example",
    "envrc.{host,remote}.example",
]


def assert_no_deleted_envrc_template_refs(text: str, label: str) -> None:
    for needle in DELETED_V05_ENVRC_TEMPLATES:
        assert needle not in text, (
            f"{label} still references deleted v0.5 path {needle!r}; "
            "update guidance to use `relay init --same-host` or "
            "`relay init --sync rsync`"
        )


def test_relay_version_matches_readme():
    """M1: relay __version__ matches the v0.x.y substring in README.md."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"v{relay.__version__}" in readme


def test_hook_version_matches_relay_version():
    """Hook dispatcher release version stays aligned with the CLI."""
    hook = (ROOT / "skills/agent-relay/hooks/relay-hook.py").read_text(encoding="utf-8")
    assert f'VERSION = "{relay.__version__}"' in hook


def test_skill_mentions_multiple_active_pair_recovery():
    """SKILL.md documents the recovery path for multiple active pairs."""
    text = (ROOT / "skills/agent-relay/SKILL.md").read_text(encoding="utf-8")
    assert "multiple active pairs" in text
    assert "relay pairs list" in text


def test_skill_documents_pair_commands():
    """v0.13: SKILL.md teaches the instance/pair binding commands."""
    text = (ROOT / "skills/agent-relay/SKILL.md").read_text(encoding="utf-8")
    # SKILL uses "$RELAY" in code blocks and bare `relay` in prose, so match the
    # command names without pinning the executable prefix.
    assert "pair ensure" in text
    assert "pair join" in text
    assert "relay whoami" in text


def test_relay_source_retires_active_session_marker():
    """v0.13: the global .active-session marker writers are fully removed; the
    binding-aware resolver and join_pair replace them."""
    src = (ROOT / "skills/agent-relay/bin/relay").read_text(encoding="utf-8")
    assert "write_active_marker" not in src
    assert "def resolve_active_pair" in src
    assert "def join_pair" in src


def test_file_protocol_documents_binding_registry():
    """v0.13: file-protocol describes the per-instance binding registry."""
    text = (ROOT / "skills/agent-relay/references/file-protocol.md").read_text(encoding="utf-8")
    assert "bindings/" in text
    assert "binding-key" in text
    assert "instance" in text


def test_file_protocol_uses_pair_vocabulary_not_retired_surface():
    """v0.14/v0.15 (codex seq 9): the protocol reference must use the pair
    command surface, not the retired `relay sessions list` / `--session-id`,
    and must not present `$RELAY_AUTHOR` as the runtime identity (author
    auto-detects; RELAY_AUTHOR is only a custom-agent override)."""
    text = (ROOT / "skills/agent-relay/references/file-protocol.md").read_text(encoding="utf-8")
    assert "relay sessions list" not in text
    assert "--session-id" not in text
    assert "$RELAY_AUTHOR" not in text
    assert "Clear `.shared/.active-session`" not in text


def test_force_reason_frontmatter_field_is_documented():
    """R1: force-publish audit field is listed in the protocol field table."""
    text = (ROOT / "skills/agent-relay/references/file-protocol.md").read_text(encoding="utf-8")
    assert "| `force_reason` | str | no |" in text
    assert "recorded as `force_reason: TEXT`" in text


def test_readme_leads_with_claude_codex_hook():
    """D6: README first paragraph names both Claude Code and Codex CLI."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    first_section = readme.split("## ")[0]
    # Whitespace-tolerant — README hard-wraps lines.
    normalized = " ".join(first_section.split())
    assert "Claude Code" in normalized
    assert "Codex CLI" in normalized
    assert "without writing API glue" in normalized


def test_skill_preamble_leads_with_claude_codex_hook():
    """D6: SKILL.md preamble names both Claude Code and Codex CLI."""
    text = (ROOT / "skills/agent-relay/SKILL.md").read_text(encoding="utf-8")
    body = text.split("---\n", 2)[-1]
    preamble = body.split("\n## ")[0]
    normalized = " ".join(preamble.split())
    assert "Claude Code" in normalized
    assert "Codex CLI" in normalized
    assert "API-key orchestrator" in normalized


def test_docs_why_exists_and_carries_caveats():
    """D5/D6: docs/why.md is the long-form home for billing/limits caveats."""
    why = (ROOT / "docs/why.md")
    assert why.is_file(), "docs/why.md must exist for the longer take"
    text = why.read_text(encoding="utf-8")
    # No hard-coded cost numbers (e.g., "$0.01" or "10000 tokens"); we keep
    # qualitative only. A weak check, but catches the most common mistake.
    assert "$0." not in text  # no dollar-decimal cost numbers
    # Required caveat markers.
    assert "subscription" in text.lower()
    assert "ChatGPT" in text
    assert "Claude" in text
    normalized = " ".join(
        line.removeprefix("> ").strip()
        for line in text.splitlines()
    )
    assert "no central long-running orchestrator daemon" in normalized
    assert "manual/user-driven" in normalized
    assert "optional hooks" in normalized.lower()


def test_legacy_role_templates_removed():
    """v0.6 deleted envrc.host/remote.example; v0.14 retired the
    envrc.same-host.example (author auto-detects, same-host needs no env).
    Only the dispatcher template remains."""
    templates_dir = ROOT / "skills/agent-relay/templates"
    assert (templates_dir / "envrc.dispatcher.example").is_file()
    assert not (templates_dir / "envrc.same-host.example").exists()
    assert not (templates_dir / "envrc.host.example").exists()
    assert not (templates_dir / "envrc.remote.example").exists()
    # The dispatcher must not carry the retired RELAY_ROLE or the v0.13
    # per-terminal RELAY_AUTHOR-override guidance.
    dispatcher = (templates_dir / "envrc.dispatcher.example").read_text(encoding="utf-8")
    assert "RELAY_ROLE" not in dispatcher
    assert "export RELAY_AUTHOR=claude" not in dispatcher


def test_dispatcher_template_does_not_reference_deleted_v05_templates():
    """v0.6 post-commit-review seq 2 Blocker 1.

    The dispatcher .envrc template gets copied into every new project by
    `relay init`. It must NOT instruct users to copy envrc.host.example /
    envrc.remote.example — those files were deleted in v0.6, so any new
    user following the dispatcher's guidance is sent to non-existent
    paths.
    """
    template = (ROOT / "skills/agent-relay/templates/envrc.dispatcher.example").read_text(encoding="utf-8")
    assert_no_deleted_envrc_template_refs(template, "envrc.dispatcher.example")


def test_dispatcher_template_carries_v014_setup_guidance():
    """v0.14: the committed dispatcher template (the source of truth — the root
    `.envrc` is a per-user gitignored copy) must point at the current setup
    commands and drop the retired per-terminal RELAY_AUTHOR / --author/--peer
    flow. This is the committed artifact; the gitignored root .envrc is not."""
    text = (ROOT / "skills/agent-relay/templates/envrc.dispatcher.example").read_text(encoding="utf-8")
    assert_no_deleted_envrc_template_refs(text, "envrc.dispatcher.example")
    assert "relay init --same-host" in text
    assert "relay init --sync rsync" in text
    # Retired guidance must be gone.
    assert "export RELAY_AUTHOR=claude" not in text
    assert "--author <name> --peer <name>" not in text


def test_first_brief_template_removed_from_surface():
    """C7: first-brief.md was never wired into the CLI or skill surface."""
    assert not (ROOT / "skills/agent-relay/templates/first-brief.md").exists()


def test_skill_relay_lookup_guidance_documents_priority_contract():
    """F5: SKILL.md documents the supported lookup priority without requiring
    relay to be globally installed on PATH."""
    text = (ROOT / "skills/agent-relay/SKILL.md").read_text(encoding="utf-8")
    hook_src = (ROOT / "skills/agent-relay/hooks/relay-hook.py").read_text(encoding="utf-8")
    for needle in (
        'bins: ["bash"]',
        "the skill runtime does not expose a",
        "portable `$SKILL_DIR`",
        "Priority: explicit `RELAY_BIN`",
        "project-local",
        "skill installs",
        "this repo's",
        "`skills/agent-relay/bin/relay`",
        "`PATH`",
        "common per-user skill installs",
        "older global",
        "symlink cannot shadow",
        "${RELAY_BIN:-}",
        "$ROOT/.agents/skills/agent-relay/bin/relay",
        "$ROOT/.claude/skills/agent-relay/bin/relay",
        "$ROOT/.codex/skills/agent-relay/bin/relay",
        "$ROOT/skills/agent-relay/bin/relay",
        "$(command -v relay 2>/dev/null)",
        "$HOME/.codex/skills/agent-relay/bin/relay",
        "$HOME/.claude/skills/agent-relay/bin/relay",
        "$HOME/.agents/skills/agent-relay/bin/relay",
        'ln -s "$PWD/skills/agent-relay/bin/relay" ~/.local/bin/relay',
    ):
        assert needle in text
    assert 'bins: ["relay", "bash"]' not in text
    for needle in (
        '".agents/skills/agent-relay/bin/relay"',
        '".claude/skills/agent-relay/bin/relay"',
        '".codex/skills/agent-relay/bin/relay"',
        '"skills/agent-relay/bin/relay"',
    ):
        assert needle in hook_src


def test_skill_documents_terminal_timeout_publish_command():
    """F4: user-blocking relay questions must publish a timed_out artifact."""
    text = (ROOT / "skills/agent-relay/SKILL.md").read_text(encoding="utf-8")
    assert '@user:' in text
    assert '"$RELAY" publish "$DRAFT" --status timed_out' in text


def test_skill_escalation_marker_is_line_start_not_substring():
    """Finding (codex seq 4): the @user: break trigger must be line-start,
    not arbitrary substring — otherwise an artifact that merely mentions
    @user: (e.g. 'do not escalate to @user: unless') false-positives and
    undercuts the un-interrupted auto-loop. Lock in the line-start rule and
    forbid the old broad-substring wording."""
    text = (ROOT / "skills/agent-relay/SKILL.md").read_text(encoding="utf-8")
    # Required: the step-10 rule must describe line-start semantics.
    assert "line whose trimmed text starts with `@user:`" in text
    # The marker mentioned mid-sentence must be explicitly called out as a
    # non-trigger.
    assert "not** an escalation" in text or "not an escalation" in text
    # Forbidden: the old substring phrasing must be gone from the break rule.
    assert "body contains literal `@user:`" not in text


def test_skill_documents_optional_parallel_wait_mode_as_advanced_only():
    """Q3: background wait is documented as optional, not the default path."""
    text = (ROOT / "skills/agent-relay/SKILL.md").read_text(encoding="utf-8")
    assert "Optional advanced: parallel wait mode" in text
    assert "The default remains the blocking" in text
    assert "Do not edit files, claim drafts, publish, sync, or close while the wait is pending" in text


def test_hook_protocol_timeout_and_path_canonicalization_match_dispatcher():
    """F6/F7: hook protocol mirrors the dispatcher timeout and path base order."""
    text = (ROOT / "skills/agent-relay/references/hook-protocol.md").read_text(encoding="utf-8")
    src = (ROOT / "skills/agent-relay/hooks/relay-hook.py").read_text(encoding="utf-8")
    assert "Subprocess timeout 8s" in text
    assert "timeout: int = 8" in src
    assert "slower than 5s" in text
    assert "`payload.cwd`, then" in text
    assert "`CLAUDE_PROJECT_DIR`, then" in text
    assert "hook process cwd" in text
    normalized = " ".join(text.split())
    assert "does not shell out to git" in normalized
    pretooluse_section = text.split("### 4.2 `PreToolUse`", 1)[1].split("### 4.3", 1)[0]
    assert "git root" not in pretooluse_section


def test_hook_protocol_fingerprint_documents_status_sourced_drafts():
    """C6: fingerprint docs must not imply hooks inspect peer draft bodies."""
    text = (ROOT / "skills/agent-relay/references/hook-protocol.md").read_text(encoding="utf-8")
    assert "draft names come from `relay status`" in text
    assert "not by directly listing peer `.draft/` contents" in text


def test_file_protocol_version_and_failed_status_wording_are_current():
    """C4/C5: file protocol version and failed-status semantics stay current."""
    text = (ROOT / "skills/agent-relay/references/file-protocol.md").read_text(encoding="utf-8")
    header = text.split("\n\n", 2)[1]
    assert f"v{relay.__version__}" in header
    assert "session schema v3" in header
    assert "v0.3.0" not in header
    failed_line = next(line for line in text.splitlines() if line.startswith("| `failed` |"))
    assert "publish validation failed" not in failed_line
    assert "author or peer recorded" in failed_line
    assert "does not create a `failed` artifact" in text


def test_file_protocol_describes_v09_exclusive_reservation_publish():
    """Finding 4 (codex seq 2): file-protocol.md must describe the shipped
    v0.9 publish semantics (exclusive O_CREAT|O_EXCL reservation, sidecars-
    last, incomplete-triad invisibility) — not the stale v0.8 atomic-rename
    story that no longer matches `atomic_reserve_text`."""
    text = (ROOT / "skills/agent-relay/references/file-protocol.md").read_text(encoding="utf-8")
    # New semantics must be present.
    assert "O_CREAT|O_EXCL" in text
    assert "incomplete-triad" in text.lower() or "incomplete triad" in text.lower()
    assert "invisible to protocol-compliant readers" in text
    # The stale claim that publish works by renaming the draft must be gone
    # from the publish-steps section (rename may still be MENTIONED as the
    # pre-v0.9 form, but must not be stated as the current success path).
    assert "Atomically rename `.draft/NNN-*.md`" not in text
    assert "Atomic rename to the published path." not in text


def test_file_protocol_documents_corrects_kind_restriction():
    """Finding 3 (codex seq 2): the spec must say only correction/addendum
    may carry `corrects`, and publish enforces positive-int < seq."""
    text = (ROOT / "skills/agent-relay/references/file-protocol.md").read_text(encoding="utf-8")
    assert "may not carry it" in text or "may not carry" in text
    assert "self/future" in text or "less than the artifact" in text


def test_changelog_documents_v06_migration():
    """v0.6 ships with a CHANGELOG that includes the migration table."""
    cl = (ROOT / "CHANGELOG.md")
    assert cl.is_file()
    text = cl.read_text(encoding="utf-8")
    assert "0.6.0" in text
    # Mapping table must appear (the only way for users to migrate).
    assert "RELAY_SYNC=rsync" in text
    assert "RELAY_SYNC=none" in text
    assert "RELAY_ROLE" in text  # the old name is named explicitly


def test_v07_error_exits_carry_recovery_hints():
    """v0.7 PR5: every flagged error exit in relay must include a concrete
    next-step hint. Grep the relay source for each known message and assert
    a recovery keyword is present nearby."""
    src = (ROOT / "skills/agent-relay/bin/relay").read_text(encoding="utf-8")
    # Each row: (needle that locates the exit, keyword that must be in the
    # same message block — "relay <verb>" recovery hint or known command).
    checks = [
        ("could not allocate sequence after 10 attempts", "relay doctor"),
        ("could not allocate published path after 10 attempts", "relay doctor"),
        ("heartbeat already running for", "relay heartbeat stop --force"),
        ("cannot derive renewal path", "RELAY_PROJECT"),
        ("owner not alive at start", "--owner-kind renewal-file"),
        ("refusing to publish into inactive pair", "relay bootstrap"),
        ("RELAY_REMOTE_SSH and RELAY_REMOTE_PATH must be set", "relay sync push --dry-run"),
        ("is not a valid slug", "set RELAY_PROJECT"),  # bootstrap bad-slug hint
    ]
    for needle, hint in checks:
        idx = src.find(needle)
        assert idx >= 0, f"could not locate error exit {needle!r} in relay source"
        # Look at a ~400-char window starting at the needle to find the hint
        # (covers multi-line print(...) calls).
        window = src[idx:idx + 400]
        assert hint in window, (
            f"error exit {needle!r} is missing recovery hint {hint!r}; "
            "v0.7 PR5 requires every stderr exit to spell out the next step."
        )


def test_file_protocol_documents_ten_retry_behavior():
    """references/file-protocol.md §7.1 must describe the 10-attempt retry
    (not the old "second failure" wording). Codex cross-review found this
    drift on 2026-05-29."""
    text = (ROOT / "skills/agent-relay/references/file-protocol.md").read_text(encoding="utf-8")
    # Forbidden old phrasing
    assert "second failure" not in text, (
        "file-protocol.md still describes the old 2-retry behavior; "
        "v0.7 widened claim/publish to 10 attempts"
    )
    assert "increment seq once and retry" not in text
    # Required new phrasing
    assert "10 attempts" in text
    assert "relay doctor" in text


def test_skill_opening_does_not_call_protocol_a_no_autopilot_loop():
    """Finding 10: SKILL.md L11 used to say 'there is no autopilot loop',
    contradicting step 10's auto-loop semantics. The rewrite must not
    re-introduce that phrasing or any equivalent denial of the loop."""
    text = (ROOT / "skills/agent-relay/SKILL.md").read_text(encoding="utf-8")
    bad_phrases = [
        "there is no autopilot loop",
        "no autopilot",
        "not an autopilot",
    ]
    for needle in bad_phrases:
        assert needle.lower() not in text.lower(), (
            f"SKILL.md still contains misleading phrase {needle!r}; "
            "the relay does run an auto-loop via `relay wait` between "
            "rule-based break triggers — keep the framing honest"
        )
    # Required: the new opening must signal user-bootstrapped + auto-converging.
    assert "auto-converging" in text or "auto-loop" in text


def test_skill_does_not_imply_relay_role_inference_fallback():
    """Finding C2: SKILL.md once implied RELAY_ROLE participated in sync
    resolution. RELAY_ROLE is fully retired; wording must not resurrect it."""
    text = (ROOT / "skills/agent-relay/SKILL.md").read_text(encoding="utf-8")
    # The exact old phrasing
    bad = "neither `RELAY_SYNC` nor `RELAY_ROLE` is set"
    assert bad not in text, (
        f"SKILL.md still implies RELAY_ROLE as an inference fallback "
        f"({bad!r}); RELAY_ROLE must stay inert."
    )
    # RELAY_ROLE must not appear anywhere in the skill surface anymore.
    assert "RELAY_ROLE" not in text, (
        "SKILL.md still mentions the retired RELAY_ROLE var"
    )


def test_envrc_renderer_explains_unconditional_unset():
    """Finding C3: the unset RELAY_REMOTE_SSH/PATH line in the rendered
    .envrc should carry a one-line explanation; a bare `unset` confuses
    users whose parent shell already set those vars."""
    src = (ROOT / "skills/agent-relay/bin/relay").read_text(encoding="utf-8")
    idx = src.find("unset RELAY_REMOTE_SSH")
    assert idx > 0, "renderer no longer emits the unset line — keep the explanation in sync"
    # Look at the ~400 chars preceding the unset for the comment block.
    window = src[max(0, idx - 400):idx]
    assert "refuses cleanly" in window or "stale rsync" in window, (
        "unset line must carry a comment explaining why it fires "
        "unconditionally (so users don't think their env was clobbered "
        "by mistake)"
    )


def test_v07_wait_hint_paths_include_doctor_or_sessions():
    """relay wait resolver-fail and claim resolver-fail hints must mention
    `relay pairs list` (the discovery command) and `relay bootstrap`."""
    src = (ROOT / "skills/agent-relay/bin/relay").read_text(encoding="utf-8")
    # Both wait and claim wrap resolve_active_pair with a hint block.
    for verb in ("relay wait:", "relay claim:"):
        idx = src.find(verb)
        # Find the resolver-fail hint block by searching for "no active pair" near verb.
        nas_idx = src.find("no active pair", idx)
        assert nas_idx > 0, f"{verb} missing resolver-fail handler for no-active-pair"
        window = src[nas_idx:nas_idx + 400]
        assert "relay pairs list" in window
        assert "relay bootstrap" in window
