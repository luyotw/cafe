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


def test_use_cafe_workflow_skill_guides_phase_confirmation_contract() -> None:
    skill = (
        PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "use-cafe-workflow" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "## Phase Confirmation Contract" in skill
    assert "driver policy for `confirm_output` approvals" in skill
    assert "it is not currently parsed by CAFE runtime" in skill
    assert "- `user_required`: `spec`, `plan`" in skill
    assert "- `agent_confirmable`: empty" in skill
    assert "not for develop/review/PR completion" in skill
    assert "field-wise merge" in skill
    assert "If a step appears in both lists, `user_required` wins" in skill
    assert "A step missing from both lists defaults to `user_required`" in skill


def test_use_cafe_workflow_skill_protects_issue_overrides() -> None:
    skill = (
        PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "use-cafe-workflow" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "### Issue overrides are opt-in only" in skill
    assert "The `issues:` section is protected" in skill
    assert "do not write to this section" in skill
    assert "Do not create `issues.<issue-name>` just because" in skill
    assert "Do not store workflow progress, baton state, phase outputs" in skill
    assert "leave `issues:` untouched unless the user explicitly requested" in skill
