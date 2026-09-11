"""Tests for skill-sourced checklist composition."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from cafe.agents.manager import AgentManager
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.bridge import load_skill_reference
from cafe.skills.checklist_composer import (
    compose_declared_checklist,
    generate_develop_checklist,
    generate_plan_checklist,
    generate_pr_checklist,
    generate_review_checklist,
    generate_spec_checklist,
    select_checklist_variant,
)
from cafe.skills.contracts import SkillWorkflowContract
from cafe.skills.loader import SkillLoader

pytestmark = pytest.mark.usefixtures("cached_builtin_playbook_models")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "checklists"
DEVELOP_BASIC_PRINCIPLES = load_skill_reference("cafe-develop", "basic_principles.md")
REVIEW_BASIC_PRINCIPLES = load_skill_reference("cafe-review", "basic_principles.md")

REQUIRED_SKILL_REFERENCES = {
    "spec_first": [
        "execution_steps_iteration_1.md",
        "important_notes_iteration_4_plus.md",
        "dod_instruction.md",
        "dod_instruction_composed.md",
        "dod_instruction_after_notes_composed.md",
        "important_notes_iteration_4_plus_composed.md",
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
        "normal_plan_context.md",
        "normal_plan_verification.md",
        "correction_plan_context.md",
        "correction_plan_test_list.md",
        "xml_questions_instruction.md",
    ],
    "review": [
        "execution_steps.md",
        "execution_preflight.md",
        "execution_risk_assessment.md",
        "execution_acceptance_closure.md",
        "execution_first_pass.md",
        "execution_correction.md",
        "execution_convergence.md",
        "execution_exit_audit.md",
        "execution_finalize.md",
        "feedback_instruction.md",
        "spec_read_instruction.md",
        "plan_read_instruction.md",
        "spec_comparison_instruction.md",
    ],
    "pr": [
        "execution_steps_iteration_1.md",
        "execution_steps_iteration_n.md",
        "comments_organization_steps.md",
        "spec_read_instruction.md",
        "plan_read_instruction.md",
        "pr_spec_context.md",
        "pr_plan_context.md",
        "review_feedback_instruction.md",
    ],
}


def test_checklist_variant_honors_declared_arbitrary_step() -> None:
    contract = SkillWorkflowContract.model_validate(
        {
            "checklist": {
                "variants": [
                    {"when": {"step": "assemble"}, "sections": [{"reference": "assemble.md"}]},
                    {"when": {}, "sections": [{"reference": "fallback.md"}]},
                ]
            }
        }
    )

    selected = select_checklist_variant(
        contract,
        step="assemble",
        iteration=1,
        artifacts={},
        feedback=False,
    )

    assert selected.when.step == "assemble"


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
        basic_principles=DEVELOP_BASIC_PRINCIPLES,
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
        basic_principles=DEVELOP_BASIC_PRINCIPLES,
        output_file=".cafe/issues/test/develop/iteration_001/output.md",
    ),
    "review": lambda path: generate_review_checklist(
        agent_name="Alice",
        spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
        review_file_path=".cafe/issues/test/review/iteration_001/output.md",
        base_branch="develop",
        checklist_file_path=path,
        plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
        basic_principles=REVIEW_BASIC_PRINCIPLES,
    ),
    "review_pr_todo": lambda path: generate_review_checklist(
        agent_name="Alice",
        spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
        review_file_path=".cafe/issues/test/review/iteration_001/output.md",
        base_branch="develop",
        checklist_file_path=path,
        plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
        pr_todo_list_file_path=".cafe/issues/test/pr/iteration_001/output.md",
        basic_principles=REVIEW_BASIC_PRINCIPLES,
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
# as compatibility coverage for their extra arguments. Production cases use
# the same snapshots, with explicit expected omissions for optional artifacts.
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
        "omitted_optional_lines": (
            "[ ] Read PR feedback in (not available) (if exists) to see user feedback and requests",
        ),
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


def _builtin_agent_file(cls, name, role, **_kw: object) -> tuple[str, str]:
    """Provide stable golden-fixture guidance at the agent reader boundary."""
    path = _builtin_agent_path(cls, name, role)
    source = Path(path)
    content = source.read_text(encoding="utf-8") if source.is_file() else ""
    return path, content


@pytest.mark.parametrize(
    "case_name", json.loads((FIXTURES_DIR / "manifest.json").read_text(encoding="utf-8"))
)
def test_golden_checklist_matches_fixture(case_name: str, tmp_path: Path) -> None:
    """Composed checklists stay equivalent to the pre-migration golden snapshots."""
    saved = AgentManager.read_agent_file
    AgentManager.read_agent_file = classmethod(_builtin_agent_file)  # type: ignore[assignment]
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
                "Line count differs: "
                f"actual={len(actual.splitlines())} "
                f"expected={len(expected.splitlines())}"
            )
    finally:
        AgentManager.read_agent_file = saved


@pytest.mark.parametrize("case_name", sorted(PRODUCTION_GOLDEN_CASES))
def test_production_composer_golden_checklist_matches_fixture(
    case_name: str, tmp_path: Path
) -> None:
    """The workflow runtime's declarative composer preserves golden output."""
    case = PRODUCTION_GOLDEN_CASES[case_name]
    saved = AgentManager.read_agent_file
    AgentManager.read_agent_file = classmethod(_builtin_agent_file)  # type: ignore[assignment]
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
        actual = output_path.read_text(encoding="utf-8")
        expected = (FIXTURES_DIR / f"{case_name}.md").read_text(encoding="utf-8")
        for line in case.get("omitted_optional_lines", ()):
            expected = expected.replace(f"{line}\n", "")
        assert actual == expected
    finally:
        AgentManager.read_agent_file = saved


def test_declared_checklist_uses_readable_builtin_agent_path_outside_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A composed checklist points to builtin guidance the phase can open."""
    working_dir = tmp_path / "working"
    working_dir.mkdir()
    monkeypatch.chdir(working_dir)
    monkeypatch.setattr(
        Path,
        "home",
        classmethod(lambda cls: tmp_path / "empty-home"),
    )
    case = PRODUCTION_GOLDEN_CASES["develop_normal"]
    checklist = tmp_path / "develop.md"

    assert compose_declared_checklist(
        skill_name=case["skill"],
        contract=SkillLoader().get_workflow_contract(case["skill"]),
        agent_name="David",
        role="developer",
        checklist_file_path=checklist,
        iteration=case["iteration"],
        context=case["context"],
        artifacts={},
        feedback=False,
        template_mode="manual",
    )

    agent_file, _ = AgentManager.read_agent_file("David", "developer")
    agent_path = Path(agent_file)
    assert agent_path.is_absolute()
    assert agent_path.is_file()
    assert agent_file in checklist.read_text(encoding="utf-8")


def test_review_correction_runtime_composes_planless_closure_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        AgentManager,
        "get_agent_file_path",
        classmethod(_builtin_agent_path),
    )
    output_path = tmp_path / "review-correction-planless.md"

    assert compose_declared_checklist(
        skill_name="cafe-review",
        contract=SkillLoader().get_workflow_contract("cafe-review"),
        agent_name="Richard",
        role="reviewer",
        checklist_file_path=output_path,
        iteration=2,
        context={
            "output_file": ".cafe/issues/test/review/iteration_002/output.md",
            "base_branch": "develop",
        },
        artifacts={
            "code": ".cafe/issues/test/develop/iteration_002/output.md",
            "workflow_feedback": ".cafe/issues/test/workflow-feedback.md",
        },
        feedback=True,
        template_mode="manual",
    )

    checklist = output_path.read_text(encoding="utf-8")
    correction_heading = "## Discovery Correction Review"
    risk_heading = "## Triggered Risk Assessment"
    matrix_heading = "## Acceptance Closure"
    exit_heading = "## Exit Audit"
    assert correction_heading in checklist
    assert risk_heading in checklist
    assert matrix_heading in checklist
    assert exit_heading in checklist
    assert (
        checklist.index(correction_heading)
        < checklist.index(risk_heading)
        < checklist.index(matrix_heading)
        < checklist.index(exit_heading)
    )
    assert "derive a bounded planless baseline" in checklist
    assert "request clarification instead of guessing" in checklist
    assert "production path" in checklist
    assert "map each complete boundary" in checklist
    assert "closed_fresh" in checklist
    assert "closed_reused" in checklist
    assert "Correction Impact Set" in checklist
    assert "do not rerun probes for rows validly carried as `closed_reused`" in checklist
    assert "Carried Evidence Summary" in checklist
    assert "Cumulative Seam Coverage Summary" in checklist
    assert "strictest downstream schema, journal, recovery, and reader limits" in checklist
    assert "decision-bound direct or fallback input" in checklist
    assert "actual automatic consumer" in checklist
    assert "`closed_reused` can never pass" not in checklist
    assert "map to plan journeys/invariants" not in checklist
    assert "exact copy only when mandated in the spec" not in checklist
    assert "naming its production path" not in checklist
    assert "need_permission" not in checklist
    assert "{spec_" not in checklist
    assert "{plan_" not in checklist


def _review_checkbox_count(content: str) -> int:
    return len(re.findall(r"^\[ \]", content, flags=re.MULTILINE))


def _normalized_checklist_source(content: str) -> str:
    return "\n".join(line.rstrip() for line in content.strip().splitlines())


def test_review_legacy_aggregate_matches_first_pass_modules() -> None:
    review_root = PROJECT_ROOT / "src/cafe/data/skills/cafe-review/references"
    aggregate = (review_root / "execution_steps.md").read_text(encoding="utf-8")
    expected = "\n\n".join(
        (review_root / name).read_text(encoding="utf-8").strip()
        for name in (
            "execution_preflight.md",
            "execution_risk_assessment.md",
            "execution_first_pass.md",
            "execution_acceptance_closure.md",
            "execution_exit_audit.md",
            "execution_finalize.md",
        )
    )

    assert _normalized_checklist_source(aggregate) == _normalized_checklist_source(expected)


def test_review_composed_checklists_stay_within_budget_and_keep_role_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_file = tmp_path / "BudgetReviewer.md"
    agent_file.write_text(
        "---\nname: BudgetReviewer\ndescription: Budget test reviewer\n---\n\n"
        "You are a review budget fixture. Your behavioral guidelines are as follows:\n\n"
        "- Check behavior rigorously\n"
        "- Keep findings relevant\n"
        "- Prefer independent evidence\n"
        "- Communicate concisely\n"
        "- Avoid speculative findings\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        AgentManager,
        "get_agent_file_path",
        classmethod(lambda cls, *_args, **_kwargs: str(agent_file)),
    )
    contract = SkillLoader().get_workflow_contract("cafe-review")
    assert contract.checklist is not None
    assert contract.checklist.include_role_guidance is True

    planned_context = {
        "output_file": ".cafe/issues/test/review/output.md",
        "base_branch": "develop",
        "spec_file": ".cafe/issues/test/spec/output.md",
        "plan_file": ".cafe/issues/test/plan/output.md",
        "feedback_file": ".cafe/issues/test/review/previous.md",
    }
    cases = (
        ("first-planned", 1, planned_context, False),
        ("discovery-correction-planned", 3, planned_context, True),
        ("convergence-correction-planned", 4, planned_context, True),
        (
            "correction-planless",
            2,
            {
                "output_file": ".cafe/issues/test/review/output.md",
                "base_branch": "develop",
                "feedback_file": ".cafe/issues/test/review/previous.md",
            },
            True,
        ),
    )

    for name, iteration, context, feedback in cases:
        output_path = tmp_path / f"{name}.md"
        assert compose_declared_checklist(
            skill_name="cafe-review",
            contract=contract,
            agent_name="BudgetReviewer",
            role="reviewer",
            checklist_file_path=output_path,
            iteration=iteration,
            context=context,
            artifacts={},
            feedback=feedback,
            template_mode="manual",
        )
        checklist = output_path.read_text(encoding="utf-8")
        assert _review_checkbox_count(checklist) <= 28
        assert checklist.count("## Agent Guidelines Checklist") == 1
        assert checklist.split("## Agent Guidelines Checklist", 1)[1].count("[ ]") == 5
        assert checklist.count("## Triggered Risk Assessment") == 1
        assert "## Acceptance Closure" in checklist
        assert checklist.count("## Exit Audit") == 1
        assert "## Finalize Review" in checklist
        assert "Cumulative Seam Coverage Summary" in checklist
        assert "using a `limit + 1` failure case" in checklist
        assert "actual automatic consumer" in checklist
        assert "need_permission" not in checklist
        if iteration == 1:
            assert "## First-Pass Behavior Review" in checklist
            assert "## Discovery Correction Review" not in checklist
            assert "## Convergence Review" not in checklist
        elif iteration <= 3:
            assert "## Discovery Correction Review" in checklist
            assert "## Convergence Review" not in checklist
            assert "## First-Pass Behavior Review" not in checklist
            assert "Correction Impact Set" in checklist
            assert "do not rerun probes for rows validly carried as `closed_reused`" in checklist
        else:
            assert "## Convergence Review" in checklist
            assert "## Discovery Correction Review" not in checklist
            assert "## First-Pass Behavior Review" not in checklist
            assert "newly evidenced `Impact: Critical`" in checklist
            assert "Convert every other newly discovered Important or Minor" in checklist
        assert "## Follow-up Proposals" in checklist

    legacy_path = tmp_path / "legacy-pr-todo.md"
    generate_review_checklist(
        agent_name="BudgetReviewer",
        spec_file_path=planned_context["spec_file"],
        review_file_path=planned_context["output_file"],
        base_branch="develop",
        checklist_file_path=legacy_path,
        pr_feedback_file_path=planned_context["feedback_file"],
        plan_file_path=planned_context["plan_file"],
        pr_todo_list_file_path=".cafe/issues/test/pr/output.md",
        basic_principles=REVIEW_BASIC_PRINCIPLES,
    )
    legacy = legacy_path.read_text(encoding="utf-8")
    assert _review_checkbox_count(legacy) <= 28
    assert "## PR Todo List Check" in legacy
    assert legacy.split("## Agent Guidelines Checklist", 1)[1].count("[ ]") == 5


@pytest.mark.parametrize(
    ("playbook_id", "step_name", "available_artifacts"),
    [
        ("hotfix", "review", {"code": "code.md"}),
        ("hotfix", "pr", {"code": "code.md", "review_feedback": "review.md"}),
        (
            "simple",
            "pr",
            {"spec": "spec.md", "code": "code.md", "qa_feedback": "qa.md"},
        ),
        (
            "tdd",
            "pr",
            {
                "spec": "spec.md",
                "plan": "plan.md",
                "code": "code.md",
                "review_feedback": "review.md",
            },
        ),
        (
            "standard-qa",
            "pr",
            {
                "spec": "spec.md",
                "plan": "plan.md",
                "code": "code.md",
                "review_feedback": "review.md",
                "qa_feedback": "qa.md",
            },
        ),
    ],
)
def test_short_builtin_playbook_checklists_compose_with_declared_artifact_scope(
    playbook_id: str,
    step_name: str,
    available_artifacts: dict[str, str],
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Shipped short workflows omit unavailable optional checklist instructions."""
    agent_file = tmp_path / "agent.md"
    agent_file.write_text(
        "---\nname: Ada\ndescription: test\n---\n\nTest guidance.\n",
        encoding="utf-8",
    )
    step = PlaybookLoader().load_model(playbook_id).model.steps[step_name]
    scope = set(step.input_artifacts or [])
    context = {
        "output_file": "output.md",
        "base_branch": "main",
        **{
            f"{artifact}_file": path
            for artifact, path in available_artifacts.items()
            if artifact in scope and artifact in {"spec", "plan", "review_feedback"}
        },
    }
    monkeypatch.setattr(
        "cafe.skills.checklist_composer.AgentManager.get_agent_file_path",
        lambda *_: str(agent_file),
    )
    checklist = tmp_path / f"{playbook_id}-{step_name}.md"
    assert compose_declared_checklist(
        skill_name=step.skill,
        contract=SkillLoader().get_workflow_contract(step.skill),
        agent_name="Ada",
        role=step.role,
        checklist_file_path=checklist,
        iteration=1,
        context=context,
        artifacts={
            name: available_artifacts[name] for name in scope if name in available_artifacts
        },
    )

    content = checklist.read_text(encoding="utf-8")
    assert "{spec_file}" not in content
    assert "{plan_file}" not in content
    if "spec" not in scope:
        assert "Read the requirements specification" not in content
    if "plan" not in scope:
        assert "Read the implementation plan" not in content
    if "review_feedback" in available_artifacts:
        assert "Read the latest review feedback at review.md" in content
        assert "use its `## Follow-up Proposals` section as the only proposal source" in content


@pytest.mark.parametrize(
    ("skill_name", "reference_names"),
    [(skill, refs) for skill, refs in REQUIRED_SKILL_REFERENCES.items()],
)
def test_builtin_skill_checklist_references_exist(
    skill_name: str, reference_names: list[str]
) -> None:
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


def test_generate_checklist_with_basic_principles_includes_checklist_section(
    tmp_path: Path,
) -> None:
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
        agent_name="Richard",
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
            "- Valid list item\nNot a list item\n* wrong bullet marker\n- Another valid list item"
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
    assert "mandatory user-owned HumanTask" in normalized_skill
    assert "mandatory HumanTask gates" in spec
    assert "The stop contract is step-level" in normalized_skill
    assert "Planned user confirmation gate" in spec
    assert "skill-only pause" in spec
    assert "playbook-only gate" in spec
    assert "`confirm_output: <current-step>`" in normalized_spec
    assert "`need_clarification`、`need_permission`、`alignment_checkpoint`" in spec
    assert "同一 phase 內的多階段 checkpoint" in spec
    assert "mandatory user-owned" in normalized_skill
    assert "durable stage evidence" in normalized_skill
    assert "first-entry/resume routing" in normalized_skill
    assert "final `confirm_output`" in normalized_skill
