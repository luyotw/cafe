"""Tests for legacy phase/checklist integration with SkillLoader."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from cafe.phases.plan_phase import PlanPhase
from cafe.utils.checklist_generator import generate_plan_checklist


def test_generate_plan_checklist_prefers_skill_reference(tmp_path: Path) -> None:
    checklist_path = tmp_path / "checklist.md"

    with patch(
        "cafe.utils.checklist_generator.try_load_skill_reference",
        return_value="## Checklist\n\n[ ] Skill-backed checklist item\n",
    ):
        generate_plan_checklist(
            agent_name="Nick",
            plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
            spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
            checklist_file_path=checklist_path,
            template_mode="manual",
        )

    content = checklist_path.read_text(encoding="utf-8")
    assert "Skill-backed checklist item" in content


def test_plan_phase_prompt_includes_skill_body(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")

    git_ops = MagicMock()
    git_ops.get_current_branch.return_value = "test-issue"

    phase = PlanPhase(
        agent_manager=MagicMock(),
        permission_handler=MagicMock(),
        git_ops=git_ops,
        spec_file=str(spec_file),
        interactive=False,
    )
    phase.iteration = 1

    with patch("cafe.skills.bridge.try_load_skill_body", return_value="Skill prompt injected"):
        prompt = phase._generate_prompt("")

    assert "Skill prompt injected" in prompt
