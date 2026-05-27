"""Tests for principles-aware scope guard in spec and review (#323)."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_spec_skill_reads_strategic_context() -> None:
    spec_skill = (
        PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "spec" / "SKILL.md"
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
        / "spec"
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


def test_review_checklist_has_anti_over_engineering_section() -> None:
    review_steps = (
        PROJECT_ROOT
        / "src"
        / "cafe"
        / "data"
        / "skills"
        / "review"
        / "references"
        / "execution_steps.md"
    ).read_text(encoding="utf-8")

    assert "Anti-Over-Engineering Review" in review_steps
    assert "Dependency hygiene" in review_steps
    assert "Layering and speculative abstractions" in review_steps
    assert "Explicit cross-component contracts" in review_steps
