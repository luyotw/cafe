"""Runtime journeys for declarative QA playbooks."""

from __future__ import annotations

from pathlib import Path

import pytest

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.playbooks.loader import PlaybookLoader


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
    return playbook


@pytest.mark.parametrize("name", ["standard-qa", "tdd-qa"])
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
def test_corrections_repeat_develop_review_and_qa(tmp_path: Path, origin: str) -> None:
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
    assert calls[correction : correction + 3] == ["develop", "review", "qa"]


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
