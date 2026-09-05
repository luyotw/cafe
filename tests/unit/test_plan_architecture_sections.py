"""Tests for plan template architecture sections and review enforcement (#322)."""

from pathlib import Path

import pytest

from cafe.core.human_tasks import HumanTaskCompletion
from cafe.core.questions_schema import Question
from cafe.ui.interactive_qa import _format_answers

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLAN_TEMPLATES_DIR = (
    PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "cafe-plan" / "assets" / "templates"
)
PLAN_SKILL = PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "cafe-plan" / "SKILL.md"
PLAN_EXEC_STEPS = (
    PROJECT_ROOT
    / "src"
    / "cafe"
    / "data"
    / "skills"
    / "cafe-plan"
    / "references"
    / "execution_steps_iteration_1.md"
)
PLAN_REVISION_STEPS = (
    PROJECT_ROOT
    / "src"
    / "cafe"
    / "data"
    / "skills"
    / "cafe-plan"
    / "references"
    / "execution_steps_iteration_n.md"
)
REVIEW_EXEC_STEPS = (
    PROJECT_ROOT
    / "src"
    / "cafe"
    / "data"
    / "skills"
    / "cafe-review"
    / "references"
    / "execution_steps.md"
)
FIXTURES_PLANS = PROJECT_ROOT / "tests" / "fixtures" / "plans"

REQUIRED_HEADINGS = (
    "Confirmed Implementation Approach",
    "Negative space",
    "Layering map",
    "Dependency ADR",
)


@pytest.mark.parametrize("template_name", ["default.md", "simple.md", "bug.md"])
def test_plan_templates_contain_required_architecture_headings(
    template_name: str,
) -> None:
    content = (PLAN_TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
    for heading in REQUIRED_HEADINGS:
        assert heading in content, f"{template_name} must include section: {heading}"


def test_plan_skill_reads_strategic_context_for_principles() -> None:
    skill = PLAN_SKILL.read_text(encoding="utf-8")
    assert "strategic_context.yaml" in skill
    assert "documents.principles.path" in skill
    assert "Negative space" in skill
    assert "Dependency ADR" in skill


def test_plan_skill_requires_four_sections_before_handoff() -> None:
    skill = PLAN_SKILL.read_text(encoding="utf-8")
    assert "Layering map" in skill
    assert "confirm" in skill.lower() or "確認" in skill


def test_plan_execution_steps_require_architecture_sections() -> None:
    steps = PLAN_REVISION_STEPS.read_text(encoding="utf-8")
    assert "Confirmed Implementation Approach" in steps
    assert "Negative space" in steps
    assert "Layering map" in steps
    assert "Dependency ADR" in steps


def test_plan_uses_same_phase_solution_alignment_checkpoint() -> None:
    skill = PLAN_SKILL.read_text(encoding="utf-8")
    first = PLAN_EXEC_STEPS.read_text(encoding="utf-8")
    later = PLAN_REVISION_STEPS.read_text(encoding="utf-8")

    assert "<!-- plan-stage: solution-alignment -->" in skill
    assert "<!-- plan-stage: detailed-plan -->" in skill
    assert "<!-- plan-stage: solution-alignment -->" in first
    assert "<!-- plan-stage: solution-alignment -->" in later
    assert "<!-- plan-stage: detailed-plan -->" in later
    assert "同一 phase" in skill
    assert "solution_direction_confirmation" in skill
    assert "`solution_direction_confirmation:` answer" in skill
    assert "local legacy" in skill
    assert "完全 相等" in " ".join(skill.split())
    assert "Plan confirmation answer" in skill
    assert "localized" in first
    assert "localized expected answer" in later
    assert "substring" in skill
    assert "不得另建 phase" in skill
    assert "do not write a Test List" in first
    assert "do not infer stage from iteration number" in later
    assert "only contains the confirmation phrase as a substring" in later
    xml_instruction = (
        PLAN_SKILL.parent / "references" / "xml_questions_instruction.md"
    ).read_text(encoding="utf-8")
    assert "do not route to `develop`" in xml_instruction


def test_solution_alignment_question_is_self_contained_and_has_one_option() -> None:
    first = PLAN_EXEC_STEPS.read_text(encoding="utf-8")
    xml_instruction = (
        PLAN_SKILL.parent / "references" / "xml_questions_instruction.md"
    ).read_text(encoding="utf-8")

    combined = first + xml_instruction
    assert "Recommended Direction" in combined
    assert "Will Do" in combined
    assert "Will Not Do" in combined
    assert "Key Trade-offs" in combined
    assert "exactly one explicit option" in combined
    assert "do not add an Other option" in combined
    assert "solution_direction_confirmation" in xml_instruction


@pytest.mark.parametrize(
    "confirmation_answer",
    ["Confirm this direction and draft the Plan", "確認此方案並開始撰寫 Plan"],
)
def test_solution_confirmation_matches_the_real_human_task_projection(
    confirmation_answer: str,
) -> None:
    completion = HumanTaskCompletion(
        task_id="clarification-answers",
        answers={
            "solution_direction_confirmation": (confirmation_answer,),
        },
    )

    assert completion.agent_input() == (
        f"solution_direction_confirmation: {confirmation_answer}"
    )
    local_input = _format_answers(
        [
            Question(
                id="solution_direction_confirmation",
                title="建議方向：採用最小修改。是否確認？",
                options=[confirmation_answer],
            )
        ],
        {0: confirmation_answer},
    )
    assert local_input == (
        "Q1: 建議方向：採用最小修改。是否確認？\n"
        f"A1: {confirmation_answer}"
    )
    skill = PLAN_SKILL.read_text(encoding="utf-8")
    later = PLAN_REVISION_STEPS.read_text(encoding="utf-8")
    for content in (skill, later):
        assert "solution_direction_confirmation:" in content
        assert "Q1:" in content and "A1:" in content
        assert "substring" in content


def test_plan_skill_discovers_usage_context_before_unset_architecture() -> None:
    skill = PLAN_SKILL.read_text(encoding="utf-8")

    assert "預設 user" in skill
    assert "questions.xml" in skill
    assert "已有答案不得重問" in skill
    assert "個人電腦或 NAS 24 小時開機" in skill
    assert "需要另行確認" in skill


@pytest.mark.parametrize("steps_path", [PLAN_EXEC_STEPS, PLAN_REVISION_STEPS])
def test_plan_checklists_enforce_nontechnical_architecture_discovery(
    steps_path: Path,
) -> None:
    steps = steps_path.read_text(encoding="utf-8")

    assert "non-technical by default" in steps
    assert "plain-language usage questions" in steps
    assert "always-on personal computer/NAS" in steps
    assert "deploy an external service" in steps


def test_review_checklist_manifest_diff_vs_dependency_adr() -> None:
    steps = REVIEW_EXEC_STEPS.read_text(encoding="utf-8")
    lowered = steps.lower()
    assert "dependency adr" in lowered
    assert "develop" in lowered
    assert "undeclared" in lowered or "not declared" in lowered or "not in" in lowered


def test_review_checklist_flags_recent_majors_within_30_days() -> None:
    steps = REVIEW_EXEC_STEPS.read_text(encoding="utf-8")
    assert "30" in steps
    assert "major" in steps.lower()


def test_fixture_two_tab_plan_declines_router_in_negative_space() -> None:
    sample = (FIXTURES_PLANS / "two_tab_negative_space.md").read_text(encoding="utf-8")
    assert "Negative space" in sample
    assert "router" in sample.lower()


def test_fixture_plan_with_dependency_includes_adr_paragraph() -> None:
    sample = (FIXTURES_PLANS / "with_dependency_adr.md").read_text(encoding="utf-8")
    assert "Dependency ADR" in sample
    assert "why" in sample.lower() or "alternatives" in sample.lower()
