"""Tests for principles-aware scope guard in spec and review (#323)."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_spec_skill_reads_strategic_context() -> None:
    spec_skill = (
        PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "cafe-spec" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "strategic_context.yaml" in spec_skill, (
        "Spec skill must instruct the PM to consult strategic_context.yaml"
    )
    assert "documents.principles.path" in spec_skill
    assert "out_of_mandate" in spec_skill
    assert "status: exists" in spec_skill
    assert "status: missing" in spec_skill or "missing" in spec_skill, (
        "Spec skill must define graceful degradation when principles file is absent"
    )


def test_spec_default_template_has_principles_alignment_section() -> None:
    template = (
        PROJECT_ROOT
        / "src"
        / "cafe"
        / "data"
        / "skills"
        / "cafe-spec"
        / "assets"
        / "templates"
        / "default.md"
    ).read_text(encoding="utf-8")

    assert "Principles Alignment" in template
    assert "do-not-do list or red lines" in template
    assert "declared capability or roadmap stage" in template
    assert "completion criterion would be missing" in template
    # Graceful degradation hint: agents should know when to leave the section blank
    assert "Leave blank" in template or "leave blank" in template


def test_plan_skill_reads_strategic_context_for_architecture_sections() -> None:
    plan_skill = (
        PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "cafe-plan" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "strategic_context.yaml" in plan_skill
    assert "documents.principles.path" in plan_skill
    assert "Negative space" in plan_skill
    assert "Dependency ADR" in plan_skill
    assert "30 days" in plan_skill


def test_review_checklist_has_anti_over_engineering_section() -> None:
    review_steps = (
        PROJECT_ROOT
        / "src"
        / "cafe"
        / "data"
        / "skills"
        / "cafe-review"
        / "references"
        / "execution_steps.md"
    ).read_text(encoding="utf-8")

    assert "Anti-Over-Engineering Review" in review_steps
    assert "Dependency hygiene" in review_steps
    assert "Layering and speculative abstractions" in review_steps
    assert "Explicit cross-component contracts" in review_steps
    assert "Dependency ADR vs manifest diff" in review_steps
    assert "undeclared" in review_steps.lower()
    assert "30 days" in review_steps


def test_plan_and_review_checklists_require_minimal_sufficient_design() -> None:
    skills = PROJECT_ROOT / "src" / "cafe" / "data" / "skills"
    plan_first = (
        skills / "cafe-plan" / "references" / "execution_steps_iteration_1.md"
    ).read_text(encoding="utf-8")
    plan_revise = (
        skills / "cafe-plan" / "references" / "execution_steps_iteration_n.md"
    ).read_text(encoding="utf-8")
    review = (
        skills / "cafe-review" / "references" / "execution_steps.md"
    ).read_text(encoding="utf-8")

    assert "scope is sufficient but not excessive" in plan_first
    assert "design is sufficient but not excessive" in plan_revise
    assert "without speculative scope, unnecessary complexity, abstractions" in plan_first
    assert "without speculative scope, unnecessary complexity, abstractions" in plan_revise
    assert "smallest design that satisfies the approved requirements" in review
