"""Tests for plan template architecture sections and review enforcement (#322)."""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLAN_TEMPLATES_DIR = (
    PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "plan" / "assets" / "templates"
)
PLAN_SKILL = PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "plan" / "SKILL.md"
PLAN_EXEC_STEPS = (
    PROJECT_ROOT
    / "src"
    / "cafe"
    / "data"
    / "skills"
    / "plan"
    / "references"
    / "execution_steps_iteration_1.md"
)
REVIEW_EXEC_STEPS = (
    PROJECT_ROOT
    / "src"
    / "cafe"
    / "data"
    / "skills"
    / "review"
    / "references"
    / "execution_steps.md"
)
FIXTURES_PLANS = PROJECT_ROOT / "tests" / "fixtures" / "plans"

REQUIRED_HEADINGS = ("Negative space", "Layering map", "Dependency ADR")


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


def test_plan_skill_requires_three_sections_before_handoff() -> None:
    skill = PLAN_SKILL.read_text(encoding="utf-8")
    assert "Layering map" in skill
    assert "confirm" in skill.lower() or "確認" in skill


def test_plan_execution_steps_require_architecture_sections() -> None:
    steps = PLAN_EXEC_STEPS.read_text(encoding="utf-8")
    assert "Negative space" in steps
    assert "Layering map" in steps
    assert "Dependency ADR" in steps


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
