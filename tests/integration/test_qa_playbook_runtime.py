"""Focused runtime journeys for declarative QA playbooks."""

from __future__ import annotations

from pathlib import Path

import pytest

from cafe.core.blackboard import (
    ArtifactEntry,
    ArtifactKind,
    BlackboardStore,
    HandoffIntent,
    HandoffOwner,
)
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.playbooks.loader import PlaybookLoader
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor

pytestmark = pytest.mark.usefixtures("cached_builtin_playbook_models")


def _finish_pr(issue_dir: Path) -> None:
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("pr")
    store.update_handoff_contract(
        state,
        from_step="pr",
        to_owner=HandoffOwner.DONE,
        to_step="done",
        intent=HandoffIntent.WORKFLOW_COMPLETE,
        status_code="confirmed",
        source="test.executor",
    )


def _runtime_playbook(name: str) -> dict:
    playbook = PlaybookLoader().load(name, strict=True)
    playbook["steps"]["pr"]["capability_requests"] = []
    playbook["steps"]["pr"]["behavior"] = {"completion": "status_code"}
    playbook["steps"]["pr"]["on"].pop("confirm_output", None)
    playbook["steps"]["pr"]["on"]["workflow_complete"] = "_done"
    return playbook


@pytest.mark.parametrize("name", ["direct-qa", "standard-qa", "tdd-qa"])
def test_qa_happy_path_reaches_pr(tmp_path: Path, name: str) -> None:
    issue_dir = tmp_path / name
    calls: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == "pr":
            _finish_pr(issue_dir)
        return StepExecutionResult(response="confirmed", artifacts={}, status_code="confirmed")

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_runtime_playbook(name),
        executor=executor,
    ).run(start_step="review")

    assert result.completed is True
    assert calls == ["review", "qa", "pr"]


@pytest.mark.parametrize("origin", ["review", "qa", "pr"])
def test_every_correction_repeats_develop_review_and_qa(tmp_path: Path, origin: str) -> None:
    issue_dir = tmp_path / f"correction-{origin}"
    calls: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == origin and calls.count(origin) == 1:
            return StepExecutionResult(
                response="needs_changes",
                artifacts={},
                status_code="needs_changes",
            )
        if step_name == "pr":
            _finish_pr(issue_dir)
        return StepExecutionResult(response="confirmed", artifacts={}, status_code="confirmed")

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_runtime_playbook("standard-qa"),
        executor=executor,
    ).run(start_step=origin)

    assert result.completed is True
    correction = calls.index("develop")
    assert calls[correction : correction + 4] == ["develop", "review", "qa", "pr"]


def test_blocked_qa_resumes_in_qa_before_pr(tmp_path: Path) -> None:
    issue_dir = tmp_path / "blocked-qa"
    calls: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == "qa" and calls.count("qa") == 1:
            return StepExecutionResult(
                response="need_clarification",
                artifacts={},
                status_code="need_clarification",
                auto_continue=True,
            )
        if step_name == "pr":
            _finish_pr(issue_dir)
        return StepExecutionResult(response="confirmed", artifacts={}, status_code="confirmed")

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_runtime_playbook("standard-qa"),
        executor=executor,
    ).run(start_step="qa")

    assert result.completed is True
    assert calls == ["qa", "qa", "pr"]


def test_simple_qa_failure_repeats_develop_and_qa_before_pr(tmp_path: Path) -> None:
    issue_dir = tmp_path / "simple-correction"
    calls: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == "qa" and calls.count("qa") == 1:
            return StepExecutionResult(
                response="needs_changes",
                artifacts={},
                status_code="needs_changes",
            )
        if step_name == "pr":
            _finish_pr(issue_dir)
        return StepExecutionResult(response="confirmed", artifacts={}, status_code="confirmed")

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_runtime_playbook("simple"),
        executor=executor,
    ).run(start_step="develop")

    assert result.completed is True
    assert calls == ["develop", "qa", "develop", "qa", "pr"]


def test_simple_no_change_correction_returns_to_qa_before_pr(tmp_path: Path) -> None:
    issue_dir = tmp_path / "simple-no-change-correction"
    calls: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == "qa" and calls.count("qa") == 1:
            return StepExecutionResult(
                response="needs_changes",
                artifacts={},
                status_code="needs_changes",
            )
        if step_name == "develop":
            return StepExecutionResult(
                response="no_changes_needed",
                artifacts={},
                status_code="no_changes_needed",
            )
        if step_name == "pr":
            _finish_pr(issue_dir)
        return StepExecutionResult(response="confirmed", artifacts={}, status_code="confirmed")

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_runtime_playbook("simple"),
        executor=executor,
    ).run(start_step="qa")

    assert result.completed is True
    assert calls == ["qa", "develop", "qa", "pr"]


@pytest.mark.parametrize("origin", ["qa", "pr"])
def test_subagent_qa_corrections_repeat_reviewed_development_and_acceptance(
    tmp_path: Path, origin: str
) -> None:
    issue_dir = tmp_path / f"subagent-correction-{origin}"
    calls: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == origin and calls.count(origin) == 1:
            return StepExecutionResult(
                response="needs_changes", artifacts={}, status_code="needs_changes"
            )
        if step_name == "pr":
            _finish_pr(issue_dir)
        return StepExecutionResult(response="confirmed", artifacts={}, status_code="confirmed")

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_runtime_playbook("subagent-flow-qa"),
        executor=executor,
    ).run(start_step=origin)

    assert result.completed is True
    correction = calls.index("develop")
    assert calls[correction : correction + 3] == ["develop", "qa", "pr"]


@pytest.mark.parametrize("sender", ["spec_plan", "qa"])
def test_subagent_qa_projects_only_the_current_correction_source(
    tmp_path: Path, sender: str
) -> None:
    store = BlackboardStore(tmp_path / "issue")
    state = store.load_or_create("develop")
    store.update_handoff_contract(
        state,
        from_step=sender,
        to_owner=HandoffOwner.AGENT,
        to_step="develop",
        intent=HandoffIntent.AWAIT_AGENT,
        source="test.current_sender",
    )
    artifacts = {}
    for name, owner, item_id, todo_source in (
        ("plan", "spec_plan", "PLAN-001", "plan"),
        ("qa_feedback", "qa", "QA-001", "qa"),
    ):
        report = tmp_path / f"{name}.md"
        report.write_text(
            "## Todo List\n"
            f"- [ ] `{item_id}` — Source: `{todo_source}` — Work: repair acceptance — "
            "Closure: requested journey passes — Evidence: targeted acceptance check\n",
            encoding="utf-8",
        )
        artifacts[name] = ArtifactEntry(
            name=name,
            kind=ArtifactKind.DOCUMENT,
            version=1,
            updated_by=owner,
            path=str(report),
        )

    resolved = GenericWorkflowStepExecutor._add_causal_todo_artifact(
        artifacts,
        state,
        playbook=PlaybookLoader().load("subagent-flow-qa", strict=True),
    )

    if sender == "qa":
        assert resolved["causal_todo"] is artifacts["qa_feedback"]
    else:
        assert "causal_todo" not in resolved
    assert resolved["plan"] is artifacts["plan"]
