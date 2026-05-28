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


def test_legacy_role_alias_documented_with_horizon():
    """D4: deprecation horizon for RELAY_ROLE is mentioned somewhere (README or template)."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    same_host = (ROOT / "skills/agent-relay/templates/envrc.same-host.example").read_text(encoding="utf-8")
    host_legacy = (ROOT / "skills/agent-relay/templates/envrc.host.example").read_text(encoding="utf-8")
    # Same-host template MUST be RELAY_SYNC-first, no RELAY_ROLE.
    assert "RELAY_SYNC=none" in same_host
    assert "RELAY_ROLE" not in same_host
    # Legacy host template still works but advertises the new option.
    assert "RELAY_SYNC" in host_legacy
    assert "deprecat" in host_legacy.lower()
