"""Integration tests for default-playbook develop clarification (mocked executor)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cafe.core.blackboard import BlackboardStore
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.playbooks.loader import PlaybookLoader


def _load_default_playbook() -> dict:
    return PlaybookLoader().load("standard")


def _run_until_settled(
    *,
    issue_dir: Path,
    playbook: dict,
    executor,
    start_step: str = "develop",
    max_transitions: int = 30,
):
    """Drive runtime through boundary handoffs until complete or user pause."""
    last_result = None
    pending_start: str | None = start_step
    for _ in range(8):
        runner = BlackboardWorkflowRuntime(
            issue_dir=issue_dir,
            playbook=playbook,
            executor=executor,
        )
        last_result = runner.run(start_step=pending_start, max_transitions=max_transitions)
        latest = BlackboardStore(issue_dir).load_or_create(
            str(playbook.get("entry_point") or next(iter(playbook["steps"].keys()))),
            playbook_id=str(playbook["playbook"]["id"]),
        )
        if last_result.completed:
            return last_result
        if latest.current_step in {"user", "done"}:
            return last_result
        pending_start = latest.current_step
    return last_result


def _write_pr_done_baton(issue_dir: Path) -> None:
    from cafe.core.blackboard import HandoffIntent, HandoffOwner

    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")
    store.update_handoff_contract(
        state,
        from_step="pr",
        to_owner=HandoffOwner.DONE,
        to_step="done",
        intent=HandoffIntent.WORKFLOW_COMPLETE,
        status_code="confirmed",
        source="test.executor",
    )


def test_develop_need_clarification_pauses_at_user(tmp_path: Path) -> None:
    """Develop need_clarification with auto_continue=False pauses workflow at user step."""
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-develop-clarify-pause"
    playbook = _load_default_playbook()

    def executor(step_name: str, step_def: dict, state) -> StepExecutionResult:
        if step_name == "develop":
            return StepExecutionResult(
                response="need_clarification",
                artifacts={},
                status_code="need_clarification",
                auto_continue=False,
            )
        return StepExecutionResult(
            response="confirmed",
            artifacts={str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"},
            status_code="confirmed",
        )

    runner = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runner.run(start_step="develop", max_transitions=10)

    assert result.completed is False
    blackboard = BlackboardStore(issue_dir).load_or_create("spec")
    assert blackboard.current_step == "user"
    pause_events = [e for e in blackboard.events if e.event_type == "workflow_paused"]
    assert pause_events


def test_develop_clarification_then_confirmed_reaches_review(tmp_path: Path) -> None:
    """Develop need_clarification (auto_continue) then confirmed advances toward review."""
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-develop-clarify-review"
    playbook = _load_default_playbook()
    develop_calls = 0

    def executor(step_name: str, step_def: dict, state) -> StepExecutionResult:
        nonlocal develop_calls
        if step_name == "develop":
            develop_calls += 1
            if develop_calls == 1:
                return StepExecutionResult(
                    response="need_clarification",
                    artifacts={},
                    status_code="need_clarification",
                    auto_continue=True,
                )
        events = []
        if step_name == "pr":
            _write_pr_done_baton(issue_dir)
            events.append({"type": "pr_synced", "url": "https://example.com/pr/1"})
        return StepExecutionResult(
            response="confirmed",
            artifacts={str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"},
            status_code="confirmed",
            events=events,
        )

    result = _run_until_settled(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
        start_step="develop",
    )

    assert result.completed is True
    assert develop_calls == 2
    blackboard = BlackboardStore(issue_dir).load_or_create("spec")
    review_started = [
        e
        for e in blackboard.events
        if e.event_type == "step_started" and e.data.get("step") == "review"
    ]
    assert review_started
