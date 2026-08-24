"""Runtime journeys for the reviewed direct-development playbook."""

from __future__ import annotations

from pathlib import Path

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


def _direct_runtime_playbook() -> dict:
    playbook = PlaybookLoader().load("direct", strict=True)
    playbook["steps"]["pr"]["capability_requests"] = []
    playbook["steps"]["pr"]["behavior"] = {"completion": "status_code"}
    return playbook


def test_direct_happy_path_always_reviews_before_pr(tmp_path: Path) -> None:
    issue_dir = tmp_path / "happy"
    calls: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == "pr":
            _finish_pr(issue_dir)
        return StepExecutionResult(response="confirmed", artifacts={}, status_code="confirmed")

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_direct_runtime_playbook(),
        executor=executor,
    ).run(start_step="develop")

    assert result.completed is True
    assert calls == ["develop", "review", "pr"]


def test_direct_develop_manual_handoff_cannot_bypass_review(tmp_path: Path) -> None:
    issue_dir = tmp_path / "develop-retry"
    calls: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == "develop" and calls.count("develop") == 1:
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
        playbook=_direct_runtime_playbook(),
        executor=executor,
    ).run(start_step="develop")

    assert result.completed is True
    assert calls == ["develop", "develop", "review", "pr"]


def test_direct_review_correction_repeats_develop_and_review(tmp_path: Path) -> None:
    issue_dir = tmp_path / "review-correction"
    calls: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == "review" and calls.count("review") == 1:
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
        playbook=_direct_runtime_playbook(),
        executor=executor,
    ).run(start_step="review")

    assert result.completed is True
    assert calls == ["review", "develop", "review", "pr"]


def test_direct_pr_correction_repeats_develop_and_review(tmp_path: Path) -> None:
    issue_dir = tmp_path / "pr-correction"
    calls: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == "pr" and calls.count("pr") == 1:
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
        playbook=_direct_runtime_playbook(),
        executor=executor,
    ).run(start_step="pr")

    assert result.completed is True
    assert calls == ["pr", "develop", "review", "pr"]


def test_direct_review_permission_resumes_before_pr(tmp_path: Path) -> None:
    issue_dir = tmp_path / "review-permission"
    calls: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == "review" and calls.count("review") == 1:
            return StepExecutionResult(
                response="need_permission",
                artifacts={},
                status_code="need_permission",
                auto_continue=True,
            )
        if step_name == "pr":
            _finish_pr(issue_dir)
        return StepExecutionResult(response="confirmed", artifacts={}, status_code="confirmed")

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_direct_runtime_playbook(),
        executor=executor,
    ).run(start_step="review")

    assert result.completed is True
    assert calls == ["review", "review", "pr"]
