"""Static docs/CLI consistency checks for agent-relay."""

from pathlib import Path

import relay


ROOT = Path(__file__).resolve().parent.parent


def test_relay_version_matches_readme():
    """M1: relay __version__ matches the v0.x.y substring in README.md."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"v{relay.__version__}" in readme


def test_first_brief_does_not_teach_needs_change_frontmatter():
    """M2: first-brief.md does not instruct setting an off-protocol status."""
    text = (ROOT / "skills/agent-relay/templates/first-brief.md").read_text(encoding="utf-8")
    assert "status: needs-change" not in text


def test_skill_mentions_multiple_active_session_stop_rule():
    """m5: SKILL.md documents the recovery path for multiple active sessions."""
    text = (ROOT / "skills/agent-relay/SKILL.md").read_text(encoding="utf-8")
    assert "multiple active sessions" in text
    assert "relay sessions list" in text


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


def test_legacy_role_templates_removed_in_v06():
    """v0.6: envrc.host.example and envrc.remote.example were deleted.
    Only envrc.same-host.example and envrc.dispatcher.example remain."""
    templates_dir = ROOT / "skills/agent-relay/templates"
    assert (templates_dir / "envrc.same-host.example").is_file()
    assert (templates_dir / "envrc.dispatcher.example").is_file()
    assert not (templates_dir / "envrc.host.example").exists()
    assert not (templates_dir / "envrc.remote.example").exists()
    # Same-host template MUST be RELAY_SYNC-first, no RELAY_ROLE.
    same_host = (templates_dir / "envrc.same-host.example").read_text(encoding="utf-8")
    assert "RELAY_SYNC=none" in same_host
    assert "RELAY_ROLE" not in same_host


def test_dispatcher_template_does_not_reference_deleted_v05_templates():
    """v0.6 post-commit-review seq 2 Blocker 1.

    The dispatcher .envrc template gets copied into every new project by
    `relay init`. It must NOT instruct users to copy envrc.host.example /
    envrc.remote.example — those files were deleted in v0.6, so any new
    user following the dispatcher's guidance is sent to non-existent
    paths.
    """
    template = (ROOT / "skills/agent-relay/templates/envrc.dispatcher.example").read_text(encoding="utf-8")
    forbidden = [
        "envrc.host.example",
        "envrc.remote.example",
        "envrc.{host,remote}.example",
    ]
    for needle in forbidden:
        assert needle not in template, (
            f"envrc.dispatcher.example still references deleted v0.5 path {needle!r}; "
            "update the comments/warning to point at `relay init --role same-host` "
            "or `--author/--peer/--sync`"
        )


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
