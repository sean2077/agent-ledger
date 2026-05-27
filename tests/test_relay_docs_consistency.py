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
