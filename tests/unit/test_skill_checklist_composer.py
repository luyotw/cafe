"""Tests for skill-sourced checklist composition."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from cafe.agents.manager import AgentManager
from cafe.skills.bridge import load_skill_reference
from cafe.skills.checklist_composer import (
    compose_declared_checklist,
    generate_develop_checklist,
    generate_plan_checklist,
    generate_pr_checklist,
    generate_review_checklist,
    generate_spec_checklist,
)
from cafe.skills.loader import SkillLoader

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "checklists"

REQUIRED_SKILL_REFERENCES = {
    "spec_first": [
        "execution_steps_iteration_1.md",
        "important_notes_iteration_4_plus.md",
        "dod_instruction.md",
        "xml_questions_instruction.md",
    ],
    "spec_revise": ["execution_steps_iteration_n.md"],
    "plan": [
        "execution_steps_iteration_1.md",
        "execution_steps_iteration_n.md",
        "xml_questions_instruction.md",
    ],
    "develop": [
        "execution_steps_normal.md",
        "execution_steps_correction.md",
        "xml_questions_instruction.md",
    ],
    "review": ["execution_steps.md"],
    "pr": [
        "execution_steps_iteration_1.md",
        "execution_steps_iteration_n.md",
        "comments_organization_steps.md",
    ],
}

GOLDEN_RUNNERS = {
    "spec_iter1": lambda path: generate_spec_checklist(
        iteration=1,
        agent_name="Roger",
        current_spec_file=".cafe/issues/test/spec/iteration_001/output.md",
        prev_spec_file=None,
        checklist_file_path=path,
        questions_xml_file=".cafe/issues/test/spec/iteration_001/questions.xml",
        template_mode="manual",
    ),
    "spec_iter2": lambda path: generate_spec_checklist(
        iteration=2,
        agent_name="Roger",
        current_spec_file=".cafe/issues/test/spec/iteration_002/output.md",
        prev_spec_file=".cafe/issues/test/spec/iteration_001/output.md",
        checklist_file_path=path,
        template_mode="manual",
    ),
    "spec_iter4": lambda path: generate_spec_checklist(
        iteration=4,
        agent_name="Roger",
        current_spec_file=".cafe/issues/test/spec/iteration_004/output.md",
        prev_spec_file=".cafe/issues/test/spec/iteration_003/output.md",
        checklist_file_path=path,
        template_mode="manual",
    ),
    "plan_iter1": lambda path: generate_plan_checklist(
        agent_name="Nick",
        plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
        spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
        checklist_file_path=path,
        iteration=1,
        template_mode="manual",
        questions_xml_file=".cafe/issues/test/plan/iteration_001/questions.xml",
    ),
    "plan_iter2": lambda path: generate_plan_checklist(
        agent_name="Nick",
        plan_file_path=".cafe/issues/test/plan/iteration_002/output.md",
        spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
        checklist_file_path=path,
        iteration=2,
        prev_plan_file=".cafe/issues/test/plan/iteration_001/output.md",
        template_mode="manual",
    ),
    "spec_iter1_with_basic_principles": lambda path: generate_spec_checklist(
        iteration=1,
        agent_name="Roger",
        current_spec_file=".cafe/issues/test/spec/iteration_001/output.md",
        prev_spec_file=None,
        checklist_file_path=path,
        basic_principles="- Keep implementation minimal\n- Prefer existing utilities",
        questions_xml_file=".cafe/issues/test/spec/iteration_001/questions.xml",
        template_mode="manual",
    ),
    "plan_iter1_with_basic_principles": lambda path: generate_plan_checklist(
        agent_name="Nick",
        plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
        spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
        checklist_file_path=path,
        iteration=1,
        template_mode="manual",
        basic_principles="- Keep checklist output stable\n- Keep user impact explicit",
        questions_xml_file=".cafe/issues/test/plan/iteration_001/questions.xml",
    ),
    "develop_normal": lambda path: generate_develop_checklist(
        agent_name="Nick",
        spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
        plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
        develop_file=None,
        checklist_file_path=path,
        correction_mode=False,
        output_file=".cafe/issues/test/develop/iteration_001/output.md",
    ),
    "develop_correction": lambda path: generate_develop_checklist(
        agent_name="Nick",
        spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
        plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
        develop_file=None,
        checklist_file_path=path,
        correction_mode=True,
        feedback_file_path=".cafe/issues/test/review/iteration_001/output.md",
        output_file=".cafe/issues/test/develop/iteration_001/output.md",
    ),
    "review": lambda path: generate_review_checklist(
        agent_name="Alice",
        spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
        review_file_path=".cafe/issues/test/review/iteration_001/output.md",
        base_branch="develop",
        checklist_file_path=path,
        plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
    ),
    "review_pr_todo": lambda path: generate_review_checklist(
        agent_name="Alice",
        spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
        review_file_path=".cafe/issues/test/review/iteration_001/output.md",
        base_branch="develop",
        checklist_file_path=path,
        plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
        pr_todo_list_file_path=".cafe/issues/test/pr/iteration_001/output.md",
    ),
    "pr_iter1": lambda path: generate_pr_checklist(
        agent_name="Nick",
        spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
        plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
        pr_file=".cafe/issues/test/pr/iteration_001/output.md",
        checklist_file_path=path,
        iteration=1,
    ),
    "pr_iter2": lambda path: generate_pr_checklist(
        agent_name="Nick",
        spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
        plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
        pr_file=".cafe/issues/test/pr/iteration_002/output.md",
        checklist_file_path=path,
        iteration=2,
        prev_pr_file=".cafe/issues/test/pr/iteration_001/output.md",
    ),
}


# These are the normal workflow cases.  Keep the legacy-wrapper snapshots below
# as compatibility coverage for their extra arguments, but exercise the
# production declarative composer against the same snapshots as the source of
# truth for normal phase execution.
PRODUCTION_GOLDEN_CASES = {
    "spec_iter1": {
        "skill": "cafe-spec",
        "agent": "Roger",
        "role": "pm",
        "iteration": 1,
        "context": {
            "output_file": ".cafe/issues/test/spec/iteration_001/output.md",
            "previous_output_file": "",
            "questions_xml_file": ".cafe/issues/test/spec/iteration_001/questions.xml",
        },
    },
    "spec_iter2": {
        "skill": "cafe-spec",
        "agent": "Roger",
        "role": "pm",
        "iteration": 2,
        "context": {
            "output_file": ".cafe/issues/test/spec/iteration_002/output.md",
            "previous_output_file": ".cafe/issues/test/spec/iteration_001/output.md",
        },
    },
    "spec_iter4": {
        "skill": "cafe-spec",
        "agent": "Roger",
        "role": "pm",
        "iteration": 4,
        "context": {
            "output_file": ".cafe/issues/test/spec/iteration_004/output.md",
            "previous_output_file": ".cafe/issues/test/spec/iteration_003/output.md",
            "iteration": "4",
        },
    },
    "plan_iter1": {
        "skill": "cafe-plan",
        "agent": "Nick",
        "role": "developer",
        "iteration": 1,
        "context": {
            "spec_file": ".cafe/issues/test/spec/iteration_001/output.md",
            "output_file": ".cafe/issues/test/plan/iteration_001/output.md",
            "previous_output_file": "",
            "questions_xml_file": ".cafe/issues/test/plan/iteration_001/questions.xml",
        },
    },
    "plan_iter2": {
        "skill": "cafe-plan",
        "agent": "Nick",
        "role": "developer",
        "iteration": 2,
        "context": {
            "spec_file": ".cafe/issues/test/spec/iteration_001/output.md",
            "output_file": ".cafe/issues/test/plan/iteration_002/output.md",
            "previous_output_file": ".cafe/issues/test/plan/iteration_001/output.md",
        },
    },
    "develop_normal": {
        "skill": "cafe-develop",
        "agent": "Nick",
        "role": "developer",
        "iteration": 1,
        "context": {
            "spec_file": ".cafe/issues/test/spec/iteration_001/output.md",
            "plan_file": ".cafe/issues/test/plan/iteration_001/output.md",
            "output_file": ".cafe/issues/test/develop/iteration_001/output.md",
        },
    },
    "develop_correction": {
        "skill": "cafe-develop",
        "agent": "Nick",
        "role": "developer",
        "iteration": 1,
        "feedback": True,
        "context": {
            "spec_file": ".cafe/issues/test/spec/iteration_001/output.md",
            "plan_file": ".cafe/issues/test/plan/iteration_001/output.md",
            "feedback_file": ".cafe/issues/test/review/iteration_001/output.md",
            "output_file": ".cafe/issues/test/develop/iteration_001/output.md",
        },
    },
    "review": {
        "skill": "cafe-review",
        "agent": "Alice",
        "role": "reviewer",
        "iteration": 1,
        "context": {
            "spec_file": ".cafe/issues/test/spec/iteration_001/output.md",
            "plan_file": ".cafe/issues/test/plan/iteration_001/output.md",
            "output_file": ".cafe/issues/test/review/iteration_001/output.md",
            "base_branch": "develop",
        },
    },
    "pr_iter1": {
        "skill": "cafe-pr",
        "agent": "Nick",
        "role": "developer",
        "iteration": 1,
        "context": {
            "spec_file": ".cafe/issues/test/spec/iteration_001/output.md",
            "plan_file": ".cafe/issues/test/plan/iteration_001/output.md",
            "output_file": ".cafe/issues/test/pr/iteration_001/output.md",
        },
    },
    "pr_iter2": {
        "skill": "cafe-pr",
        "agent": "Nick",
        "role": "developer",
        "iteration": 2,
        "context": {
            "spec_file": ".cafe/issues/test/spec/iteration_001/output.md",
            "plan_file": ".cafe/issues/test/plan/iteration_001/output.md",
            "output_file": ".cafe/issues/test/pr/iteration_002/output.md",
            "previous_output_file": ".cafe/issues/test/pr/iteration_001/output.md",
        },
    },
}


def _builtin_agent_path(cls, name, role, **_kw: object) -> str:
    """Always resolve to builtin agent files (ignores local overrides)."""
    return f"src/cafe/data/agents/{role}/{name}.md"


def _normalized_checklist(content: str) -> str:
    """Ignore formatting-only blank-line differences across legacy wrappers."""
    return re.sub(r"\n{2,}", "\n\n", content).rstrip() + "\n"


@pytest.mark.parametrize("case_name", json.loads((FIXTURES_DIR / "manifest.json").read_text(encoding="utf-8")))
def test_golden_checklist_matches_fixture(case_name: str, tmp_path: Path) -> None:
    """Composed checklists stay equivalent to the pre-migration golden snapshots."""
    saved = AgentManager.get_agent_file_path
    AgentManager.get_agent_file_path = classmethod(_builtin_agent_path)  # type: ignore[assignment]
    try:
        output_path = tmp_path / f"{case_name}.md"
        GOLDEN_RUNNERS[case_name](output_path)
        expected = (FIXTURES_DIR / f"{case_name}.md").read_text(encoding="utf-8")
        actual = output_path.read_text(encoding="utf-8")
        if actual != expected:
            for i, (a, e) in enumerate(zip(actual.splitlines(), expected.splitlines())):
                if a != e:
                    raise AssertionError(
                        f"First diff at line {i}:\n  actual:   {a!r}\n  expected: {e!r}"
                    )
            raise AssertionError(
                f"Line count differs: actual={len(actual.splitlines())} expected={len(expected.splitlines())}"
            )
    finally:
        AgentManager.get_agent_file_path = saved


@pytest.mark.parametrize("case_name", sorted(PRODUCTION_GOLDEN_CASES))
def test_production_composer_golden_checklist_matches_fixture(
    case_name: str, tmp_path: Path
) -> None:
    """The workflow runtime's declarative composer preserves golden output."""
    case = PRODUCTION_GOLDEN_CASES[case_name]
    saved = AgentManager.get_agent_file_path
    AgentManager.get_agent_file_path = classmethod(_builtin_agent_path)  # type: ignore[assignment]
    try:
        output_path = tmp_path / f"production-{case_name}.md"
        assert compose_declared_checklist(
            skill_name=case["skill"],
            contract=SkillLoader().get_workflow_contract(case["skill"]),
            agent_name=case["agent"],
            role=case["role"],
            checklist_file_path=output_path,
            iteration=case["iteration"],
            context=case["context"],
            artifacts={},
            feedback=case.get("feedback", False),
            template_mode="manual",
        )
        actual = _normalized_checklist(output_path.read_text(encoding="utf-8"))
        expected = _normalized_checklist(
            (FIXTURES_DIR / f"{case_name}.md").read_text(encoding="utf-8")
        )
        assert actual == expected
    finally:
        AgentManager.get_agent_file_path = saved


@pytest.mark.parametrize(
    ("skill_name", "reference_names"),
    [(skill, refs) for skill, refs in REQUIRED_SKILL_REFERENCES.items()],
)
def test_builtin_skill_checklist_references_exist(skill_name: str, reference_names: list[str]) -> None:
    for ref_name in reference_names:
        content = load_skill_reference(skill_name, ref_name)
        assert content.strip(), f"{skill_name}/{ref_name} must be non-empty"


def test_spec_iteration_1_reference_mentions_images_directory() -> None:
    content = load_skill_reference("spec_first", "execution_steps_iteration_1.md")
    assert "spec/images/" in content
    assert "UI/UX" in content or "visual context" in content


def test_spec_dod_instruction_skill_reference_contract() -> None:
    instruction = load_skill_reference("spec_first", "dod_instruction.md")
    assert "DoD" in instruction
    assert "functional" in instruction.lower()
    assert "Acceptance Criteria" in instruction
    assert "**DoD:**" in instruction
    assert "checkbox" in instruction
    assert "CAFE_" not in instruction


def test_develop_correction_checklist_uses_feedback_file_path(tmp_path: Path) -> None:
    checklist_path = tmp_path / "checklist.md"
    feedback = ".cafe/issues/test/review/iteration_001/output.md"

    generate_develop_checklist(
        agent_name="David",
        spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
        plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
        develop_file=None,
        checklist_file_path=checklist_path,
        correction_mode=True,
        feedback_file_path=feedback,
    )

    content = checklist_path.read_text(encoding="utf-8")
    assert feedback in content
    assert "Read feedback todo list" in content
    assert "{review_file_path}" not in content
    assert "{pr_feedback_file_path}" not in content


def test_spec_checklist_includes_dod_instruction(tmp_path: Path) -> None:
    checklist_path = tmp_path / "checklist.md"
    generate_spec_checklist(
        iteration=1,
        agent_name="Roger",
        current_spec_file=".cafe/issues/test/spec/iteration_001/output.md",
        prev_spec_file=None,
        checklist_file_path=checklist_path,
    )
    content = checklist_path.read_text(encoding="utf-8")
    assert "Definition of Done" in content


def test_spec_and_develop_xml_question_instruction_requires_need_clarification() -> None:
    spec_instruction = load_skill_reference("spec_first", "xml_questions_instruction.md")
    develop_instruction = load_skill_reference("cafe-develop", "xml_questions_instruction.md")
    assert "intent=need_clarification" in spec_instruction
    assert "intent=need_clarification" in develop_instruction


def test_generate_checklist_with_basic_principles_includes_checklist_section(tmp_path: Path) -> None:
    checklist_path = tmp_path / "checklist.md"
    generate_plan_checklist(
        agent_name="David",
        plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
        spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
        checklist_file_path=checklist_path,
        iteration=1,
        template_mode="manual",
        basic_principles="- Keep scope\n- Follow existing conventions",
    )
    content = checklist_path.read_text(encoding="utf-8")

    assert "## Basic Principles" in content
    assert "[ ] Keep scope" in content
    assert "[ ] Follow existing conventions" in content


def test_review_and_pr_checklists_include_basic_principles(tmp_path: Path) -> None:
    review_path = tmp_path / "review_checklist.md"
    pr_path = tmp_path / "pr_checklist.md"

    generate_review_checklist(
        agent_name="Alice",
        spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
        review_file_path=".cafe/issues/test/review/iteration_001/output.md",
        base_branch="develop",
        checklist_file_path=review_path,
        plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
        basic_principles="- Review with repository scope",
    )
    generate_pr_checklist(
        agent_name="Nick",
        spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
        plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
        pr_file=".cafe/issues/test/pr/iteration_001/output.md",
        checklist_file_path=pr_path,
        basic_principles="- Keep PR description factual",
    )

    review_content = review_path.read_text(encoding="utf-8")
    pr_content = pr_path.read_text(encoding="utf-8")

    assert "## Basic Principles" in review_content
    assert "[ ] Review with repository scope" in review_content
    assert "## Basic Principles" in pr_content
    assert "[ ] Keep PR description factual" in pr_content


def test_generate_checklist_ignores_non_bullet_basic_principles_lines(tmp_path: Path) -> None:
    checklist_path = tmp_path / "checklist.md"
    generate_develop_checklist(
        agent_name="David",
        spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
        plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
        develop_file=None,
        checklist_file_path=checklist_path,
        correction_mode=False,
        output_file=".cafe/issues/test/develop/iteration_001/output.md",
        basic_principles=(
            "- Valid list item\n"
            "Not a list item\n"
            "* wrong bullet marker\n"
            "- Another valid list item"
        ),
    )

    content = checklist_path.read_text(encoding="utf-8")

    assert "Not a list item" not in content
    assert "wrong bullet marker" not in content
    assert "[ ] Valid list item" in content
    assert "[ ] Another valid list item" in content


def test_write_cafe_phase_spec_documents_basic_principles_reference() -> None:
    spec = Path("src/cafe/data/skills/write-cafe-phase/references/skill-spec.md").read_text(
        encoding="utf-8"
    )
    skill = Path("src/cafe/data/skills/write-cafe-phase/SKILL.md").read_text(encoding="utf-8")

    assert "references/basic_principles.md" in spec
    assert "## Basic Principles" in spec
    assert "references/execution_steps_*.md" in spec
    assert "agent 檔 guidelines" in spec
    assert "references/basic_principles.md" in skill


def test_write_cafe_phase_requires_playbook_declared_confirmation_gates() -> None:
    spec = Path("src/cafe/data/skills/write-cafe-phase/references/skill-spec.md").read_text(
        encoding="utf-8"
    )
    skill = Path("src/cafe/data/skills/write-cafe-phase/SKILL.md").read_text(encoding="utf-8")
    normalized_spec = " ".join(spec.split())
    normalized_skill = " ".join(skill.split())

    assert "## Planned User Confirmation Gates" in skill
    assert "the bound playbook step must declare `on.confirm_output`" in normalized_skill
    assert "Neither side alone is a complete contract" in normalized_skill
    assert "cafe playbook confirmation-gates <id>" in skill
    assert "The stop contract is step-level" in normalized_skill
    assert "Planned user confirmation gate" in spec
    assert "skill-only pause" in spec
    assert "playbook-only gate" in spec
    assert "`confirm_output: <current-step>`" in normalized_spec
    assert "`need_clarification`、`need_permission`、`alignment_checkpoint`" in spec
