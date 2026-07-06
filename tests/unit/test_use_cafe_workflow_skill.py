"""Tests for bundled use-cafe-workflow skill guidance."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_use_cafe_workflow_skill_guides_alignment_checkpoint_delegation() -> None:
    skill = (
        PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "use-cafe-workflow" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "## Alignment Checkpoints" in skill
    assert "resolve the checkpoint on behalf of the user" in skill
    assert "explicit JSON decision payload" in skill
    assert "Plain text must not be used for alignment approval" in skill
    assert "Do not write `strategic_documents_updated`" in skill
    assert "stop and ask the user" in skill
