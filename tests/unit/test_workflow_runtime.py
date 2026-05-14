"""Tests for the blackboard-first workflow runtime."""

import json
from pathlib import Path

import pytest

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.workflow_models import StepExecutionResult, StepInterrupted
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime


def _write_baton(issue_dir: Path, *, from_step: str, to_owner: str, to_step: str, intent: str) -> None:
    (issue_dir / "next_step.txt").write_text(
        json.dumps(
            {
                "version": 1,
                "from_step": from_step,
                "to_owner": to_owner,
                "to_step": to_step,
                "intent": intent,
                "status_code": "",
                "created_at": "2026-04-26T23:00:00+08:00",
                "source": "test",
            }
        ),
        encoding="utf-8",
    )


def test_runtime_blocks_pr_done_without_publish_receipt(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-pr"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "pr": {"skill": "spec_first", "role": "developer", "on": {"await_agent": "_done"}},
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        _write_baton(issue_dir, from_step="pr", to_owner="done", to_step="done", intent="workflow_complete")
        return StepExecutionResult(response="done", artifacts={"pr_result": "p1"})

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="pr")

    assert result.completed is False
    assert result.final_step == "pr"
    assert result.final_status_code == "MISSING_CAPABILITY_RECEIPT"
    blackboard = BlackboardStore(issue_dir).load_or_create("pr")
    assert blackboard.current_step == "pr"


def test_runtime_completes_pr_when_publish_receipt_exists(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-pr"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "pr": {"skill": "spec_first", "role": "developer", "on": {"await_agent": "_done"}},
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        _write_baton(issue_dir, from_step="pr", to_owner="done", to_step="done", intent="workflow_complete")
        return StepExecutionResult(
            response="done",
            artifacts={"pr_result": "p1"},
            events=[{"type": "pr_synced", "url": "https://github.com/test/repo/pull/240"}],
        )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="pr")

    assert result.completed is True
    assert result.final_step == "pr"
    assert result.final_status_code == "BATON_WORKFLOW_COMPLETE"


def test_runtime_completes_pr_when_capability_receipt_success_exists(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-pr-cap"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "pr": {"skill": "spec_first", "role": "developer", "on": {"await_agent": "_done"}},
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        _write_baton(issue_dir, from_step="pr", to_owner="done", to_step="done", intent="workflow_complete")
        return StepExecutionResult(
            response="done",
            artifacts={"pr_result": "p1"},
            events=[
                {
                    "type": "capability_receipt",
                    "capability": "cafe.pr.publish",
                    "success": True,
                    "correlation_id": "x",
                    "category": None,
                    "code": None,
                }
            ],
        )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="pr")

    assert result.completed is True
    assert result.final_status_code == "BATON_WORKFLOW_COMPLETE"


def test_runtime_delegates_non_pr_steps_to_legacy_runner(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-spec"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {
                "skill": "spec_first",
                "role": "pm",
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object):
        return ("confirmed", {})

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="spec")

    assert result.completed is True
    assert result.final_step == "spec"
    assert result.final_status_code == "confirmed"


def test_runtime_rejects_legacy_text_baton_in_core_path(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "strict-baton"
    issue_dir.mkdir(parents=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "default",
                "current_step": "spec",
                "artifacts": {},
                "events": [],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )
    (issue_dir / "next_step.txt").write_text("spec\n", encoding="utf-8")
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {
                "skill": "spec_first",
                "role": "pm",
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object):
        return ("confirmed", {})

    with pytest.raises(ValueError, match="Invalid baton contract payload"):
        runtime = BlackboardWorkflowRuntime(
            issue_dir=issue_dir,
            playbook=playbook,
            executor=executor,
        )
        runtime.run()


def test_runtime_hands_off_to_pr_runtime_boundary(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-boundary"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "review": {
                "skill": "spec_first",
                "role": "reviewer",
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "pr"},
            },
            "pr": {
                "skill": "spec_first",
                "role": "developer",
                "on": {"await_agent": "_done"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object):
        if step_name == "review":
            return ("confirmed", {})
        raise AssertionError("pr should not execute in the legacy portion")

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="review")

    assert result.completed is False
    assert result.final_step == "review"
    assert result.final_status_code == "confirmed"
    blackboard = BlackboardStore(issue_dir).load_or_create("review")
    assert blackboard.current_step == "pr"


def test_runtime_single_step_executes_non_pr_locally(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-single"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "develop": {
                "skill": "spec_first",
                "role": "developer",
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        return StepExecutionResult(
            response="confirmed",
            artifacts={"develop_result": "d1"},
            status_code="confirmed",
        )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="develop", single_step=True)

    assert result.completed is True
    assert result.final_step == "develop"
    assert result.final_status_code == "confirmed"
    blackboard = BlackboardStore(issue_dir).load_or_create("develop")
    assert blackboard.current_step == "done"
    assert blackboard.handoff_contract is not None
    assert blackboard.handoff_contract.to_owner == HandoffOwner.DONE
    assert blackboard.handoff_contract.intent == HandoffIntent.WORKFLOW_COMPLETE
    assert blackboard.artifacts["develop_result"].path == "d1"


def test_runtime_single_step_executes_pr_without_legacy_runner(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-pr-single"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "pr": {"skill": "spec_first", "role": "developer", "on": {"await_agent": "_done"}},
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        _write_baton(issue_dir, from_step="pr", to_owner="done", to_step="done", intent="workflow_complete")
        return StepExecutionResult(
            response="done",
            artifacts={"pr_result": "p1"},
            events=[{"type": "pr_synced", "url": "https://github.com/test/repo/pull/240"}],
        )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="pr", single_step=True)

    assert result.completed is True
    assert result.final_step == "pr"
    assert result.final_status_code == "BATON_WORKFLOW_COMPLETE"
    blackboard = BlackboardStore(issue_dir).load_or_create("pr")
    assert blackboard.current_step == "done"
    assert blackboard.handoff_contract is not None
    assert blackboard.handoff_contract.to_owner == HandoffOwner.DONE
    assert blackboard.artifacts["pr_result"].path == "p1"


def test_runtime_single_step_legacy_transition_uses_single_step_labels(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-single-transition"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {
                "skill": "spec_first",
                "role": "pm",
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "plan"},
            },
            "plan": {
                "skill": "spec_first",
                "role": "developer",
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        if step_name != "spec":
            raise AssertionError("single-step should only execute one step")
        return StepExecutionResult(
            response="confirmed",
            artifacts={},
            status_code="confirmed",
        )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="spec", single_step=True)

    assert result.completed is False
    assert result.final_step == "spec"
    assert result.final_status_code == "confirmed"
    blackboard = BlackboardStore(issue_dir).load_or_create("spec")
    assert blackboard.current_step == "plan"
    transition_events = [e for e in blackboard.events if e.event_type == "transition"]
    assert transition_events[-1].data["runtime"] == "single_step"
    single_completed = [e for e in blackboard.events if e.event_type == "single_step_completed"]
    assert single_completed[-1].data["runtime"] == "single_step"


def test_runtime_single_step_baton_transition_uses_single_step_labels(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-single-baton-transition"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "pr": {
                "skill": "spec_first",
                "role": "developer",
                "on": {"await_agent": "review"},
            },
            "review": {
                "skill": "spec_first",
                "role": "reviewer",
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        _write_baton(issue_dir, from_step="pr", to_owner="agent", to_step="review", intent="await_agent")
        return StepExecutionResult(response="done", artifacts={"pr_result": "p1"})

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="pr", single_step=True)

    assert result.completed is False
    assert result.final_step == "pr"
    blackboard = BlackboardStore(issue_dir).load_or_create("pr")
    assert blackboard.current_step == "review"
    transition_events = [e for e in blackboard.events if e.event_type == "transition"]
    assert transition_events[-1].data["runtime"] == "single_step"
    single_completed = [e for e in blackboard.events if e.event_type == "single_step_completed"]
    assert single_completed[-1].data["runtime"] == "single_step"


def test_runtime_single_step_pause_does_not_emit_workflow_paused_event(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-single-step-pause"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {
                "skill": "spec_first",
                "role": "pm",
                "valid_intents": ["ready_for_review", "confirmed"],
                "on": {"confirm_output": "spec", "await_agent": "_done"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        return StepExecutionResult(
            response="ready_for_review",
            artifacts={},
            status_code="ready_for_review",
            auto_continue=False,
        )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="spec", single_step=True)

    assert result.completed is False
    assert result.final_status_code == "ready_for_review"
    blackboard = BlackboardStore(issue_dir).load_or_create("spec")
    assert blackboard.current_step == "user"
    pause_events = [e for e in blackboard.events if e.event_type == "workflow_paused"]
    assert pause_events == []


def test_runtime_legacy_step_uses_default_transition_when_status_missing(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-default"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {
                "skill": "spec_first",
                "role": "pm",
                "valid_intents": ["confirmed"],
                "on": {"default": "plan"},
            },
            "plan": {
                "skill": "spec_first",
                "role": "developer",
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            },
        },
    }
    calls: list[str] = []

    def executor(step_name: str, step_def: dict, state: object):
        calls.append(step_name)
        if step_name == "spec":
            return ("no explicit cafe code here", {})
        return ("confirmed", {})

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="spec")

    assert result.completed is True
    assert result.final_step == "plan"
    assert result.final_status_code == "confirmed"
    assert calls == ["spec", "plan"]


def test_runtime_legacy_step_honors_review_confirmed_advance(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-review-advance"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "review": {
                "skill": "spec_first",
                "role": "reviewer",
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "review", "default": "pr"},
            },
            "pr": {
                "skill": "spec_first",
                "role": "developer",
                "on": {"await_agent": "_done"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        if step_name == "review":
            return StepExecutionResult(
                response="confirmed",
                artifacts={},
                status_code="confirmed",
                events=[{"type": "review_confirmed_advance"}],
            )
        _write_baton(issue_dir, from_step="pr", to_owner="done", to_step="done", intent="workflow_complete")
        return StepExecutionResult(
            response="done",
            artifacts={"pr_result": "p1"},
            events=[{"type": "pr_synced", "url": "https://github.com/test/repo/pull/240"}],
        )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="review")

    assert result.completed is False
    assert result.final_step == "review"
    assert result.final_status_code == "confirmed"
    blackboard = BlackboardStore(issue_dir).load_or_create("review")
    assert blackboard.current_step == "pr"


def test_runtime_resumes_from_blackboard_current_step(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-resume"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {
                "skill": "spec_first",
                "role": "pm",
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "plan"},
            },
            "plan": {
                "skill": "spec_first",
                "role": "developer",
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            },
        },
    }
    executed_steps: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        executed_steps.append(step_name)
        return StepExecutionResult(
            response="confirmed",
            artifacts={},
            status_code="confirmed",
        )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    runtime.blackboard_store.set_current_step(runtime.blackboard, "plan")
    runtime.blackboard_store.update_handoff_contract(
        runtime.blackboard,
        from_step="spec",
        to_owner=HandoffOwner.AGENT,
        to_step="plan",
        intent=HandoffIntent.AWAIT_AGENT,
        status_code="confirmed",
        source="test.resume",
    )
    result = runtime.run(max_transitions=5)

    assert result.completed is True
    assert result.final_step == "plan"
    assert executed_steps == ["plan"]


def test_runtime_records_done_handoff_for_non_pr_terminal_transition(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "done-transition"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "review": {
                "skill": "spec_first",
                "role": "reviewer",
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "done"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object):
        return ("confirmed", {})

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="review")

    assert result.completed is True
    assert result.final_status_code == "confirmed"
    blackboard = BlackboardStore(issue_dir).load_or_create("review")
    assert blackboard.current_step == "done"
    assert blackboard.handoff_contract is not None
    assert blackboard.handoff_contract.to_owner.value == "done"
    assert blackboard.handoff_contract.intent.value == "workflow_complete"
    assert blackboard.events[-1].event_type == "workflow_completed"


def test_runtime_pauses_for_non_pr_transition_to_user(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "user-transition"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "review": {
                "skill": "spec_first",
                "role": "reviewer",
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "user"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object):
        return ("confirmed", {})

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="review")

    assert result.completed is False
    assert result.final_status_code == "confirmed"
    blackboard = BlackboardStore(issue_dir).load_or_create("review")
    assert blackboard.current_step == "user"
    assert blackboard.handoff_contract is not None
    assert blackboard.handoff_contract.to_owner.value == "user"
    assert blackboard.handoff_contract.to_step == "user"
    assert blackboard.handoff_contract.intent.value == "manual_handoff"
    assert blackboard.events[-1].event_type == "workflow_paused"


def test_runtime_records_status_code_invalid_event(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "invalid-status"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {
                "skill": "spec_first",
                "role": "pm",
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object):
        return ("ready_for_review", {})

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="spec")

    assert result.completed is False
    assert result.final_status_code == "INVALID_STATUS_CODE"
    blackboard = BlackboardStore(issue_dir).load_or_create("spec")
    invalid_events = [e for e in blackboard.events if e.event_type == "status_code_invalid"]
    assert invalid_events
    latest = invalid_events[-1].data
    assert latest["invalid_intents"] == ["ready_for_review"]
    assert latest["allowed_status_codes"] == ["confirmed"]
    assert latest["runtime"] == "legacy_until_boundary"


def test_runtime_prefers_step_baton_over_missing_status_text(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "missing-status-with-baton"
    issue_dir.mkdir(parents=True, exist_ok=True)
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {
                "skill": "spec_first",
                "role": "pm",
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object):
        # Simulate what a real step executor does: write a handoff
        # contract pointing to "user" with confirm_output intent.
        store = BlackboardStore(issue_dir)
        store.update_handoff_contract(
            state,
            from_step="spec",
            to_owner=HandoffOwner.USER,
            to_step="user",
            intent=HandoffIntent.CONFIRM_OUTPUT,
            source="test.executor",
        )
        return ("plain response without status token", {})

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="spec")

    assert result.completed is False
    assert result.final_status_code == "BATON_CONFIRM_OUTPUT"
    blackboard = BlackboardStore(issue_dir).load_or_create("spec")
    missing_events = [e for e in blackboard.events if e.event_type == "status_code_missing"]
    assert missing_events == []
    assert blackboard.current_step == "user"
    completed_events = [e for e in blackboard.events if e.event_type == "step_completed"]
    assert completed_events[-1].data["status_code"] == "BATON_CONFIRM_OUTPUT"


def test_runtime_prefers_step_baton_over_invalid_status_text(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "invalid-status-with-baton"
    issue_dir.mkdir(parents=True, exist_ok=True)
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {
                "skill": "spec_first",
                "role": "pm",
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "plan"},
            },
            "plan": {
                "skill": "plan",
                "role": "developer",
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object):
        if step_name == "spec":
            store = BlackboardStore(issue_dir)
            store.update_handoff_contract(
                state,
                from_step="spec",
                to_owner=HandoffOwner.AGENT,
                to_step="plan",
                intent=HandoffIntent.AWAIT_AGENT,
                status_code="confirmed",
                source="test.executor",
            )
            return ("ready_for_review", {})
        return ("confirmed", {})

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="spec")

    assert result.completed is True
    assert result.final_step == "plan"
    blackboard = BlackboardStore(issue_dir).load_or_create("spec")
    invalid_events = [e for e in blackboard.events if e.event_type == "status_code_invalid"]
    assert invalid_events == []
    transitions = [e for e in blackboard.events if e.event_type == "transition"]
    assert transitions[0].data["source"] == "baton"


def test_runtime_status_code_missing_no_handoff_contract(tmp_path: Path) -> None:
    """When the agent omits a status code and no handoff contract exists, the runtime still pauses."""
    issue_dir = tmp_path / ".cafe" / "issues" / "missing-no-handoff"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {
                "skill": "spec_first",
                "role": "pm",
                "valid_intents": ["need_clarification"],
                "on": {"need_clarification": "spec"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object):
        # No handoff contract written — agent produced nothing useful.
        return ("plain response without status token", {})

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="spec")

    # No handoff contract → baton fallback not possible → still pauses
    assert result.completed is False
    assert result.final_status_code == "NO_STATUS_CODE"
    blackboard = BlackboardStore(issue_dir).load_or_create("spec")
    missing_events = [e for e in blackboard.events if e.event_type == "status_code_missing"]
    assert missing_events
    # No baton_fallback key when fallback was not possible
    assert "baton_fallback" not in missing_events[-1].data


def test_runtime_pauses_ready_for_review_with_confirm_output_intent(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "ready-for-review-pause"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {
                "skill": "spec_first",
                "role": "pm",
                "valid_intents": ["ready_for_review", "confirmed"],
                "on": {"confirm_output": "spec", "await_agent": "_done"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        return StepExecutionResult(
            response="ready_for_review",
            artifacts={},
            status_code="ready_for_review",
            auto_continue=False,
        )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="spec")

    assert result.completed is False
    assert result.final_status_code == "ready_for_review"
    blackboard = BlackboardStore(issue_dir).load_or_create("spec")
    assert blackboard.current_step == "user"
    assert blackboard.handoff_contract is not None
    assert blackboard.handoff_contract.to_owner == HandoffOwner.USER
    assert blackboard.handoff_contract.intent == HandoffIntent.CONFIRM_OUTPUT


def test_runtime_continues_when_auto_continue_is_true(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "auto-continue"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {
                "skill": "spec_first",
                "role": "pm",
                "valid_intents": ["need_clarification", "confirmed"],
                "on": {
                    "need_clarification": "spec",
                    "await_agent": "_done",
                },
            },
        },
    }
    call_count = 0

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return StepExecutionResult(
                response="need_clarification",
                artifacts={},
                status_code="need_clarification",
                auto_continue=True,
            )
        return StepExecutionResult(
            response="confirmed",
            artifacts={},
            status_code="confirmed",
        )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="spec", max_transitions=5)

    assert result.completed is True
    assert call_count == 2
    blackboard = BlackboardStore(issue_dir).load_or_create("spec")
    pause_events = [e for e in blackboard.events if e.event_type == "workflow_paused"]
    assert not pause_events


def test_runtime_emits_expected_runtime_labels_per_path(tmp_path: Path) -> None:
    # legacy -> boundary_handoff
    issue_dir_legacy = tmp_path / ".cafe" / "issues" / "runtime-labels-legacy"
    playbook_legacy = {
        "playbook": {"id": "default"},
        "steps": {
            "review": {
                "skill": "spec_first",
                "role": "reviewer",
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "pr"},
            },
            "pr": {"skill": "spec_first", "role": "developer", "on": {"await_agent": "_done"}},
        },
    }

    def legacy_executor(step_name: str, step_def: dict, state: object):
        return ("confirmed", {})

    legacy_runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir_legacy,
        playbook=playbook_legacy,
        executor=legacy_executor,
    )
    legacy_runtime.run(start_step="review")
    legacy_state = BlackboardStore(issue_dir_legacy).load_or_create("review")
    review_started = [
        e for e in legacy_state.events if e.event_type == "step_started" and e.data.get("step") == "review"
    ]
    review_completed = [
        e for e in legacy_state.events if e.event_type == "step_completed" and e.data.get("step") == "review"
    ]
    boundary_transition = [
        e
        for e in legacy_state.events
        if e.event_type == "transition"
        and e.data.get("from") == "review"
        and e.data.get("to") == "pr"
    ]
    assert review_started[-1].data["runtime"] == "legacy_until_boundary"
    assert review_completed[-1].data["runtime"] == "legacy_until_boundary"
    assert boundary_transition[-1].data["runtime"] == "boundary_handoff"

    # baton-driven
    issue_dir_pr = tmp_path / ".cafe" / "issues" / "runtime-labels-pr"
    playbook_pr = {
        "playbook": {"id": "default"},
        "steps": {
            "pr": {"skill": "spec_first", "role": "developer", "on": {"await_agent": "_done"}},
        },
    }

    def pr_executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        _write_baton(issue_dir_pr, from_step="pr", to_owner="done", to_step="done", intent="workflow_complete")
        return StepExecutionResult(
            response="done",
            artifacts={},
            events=[{"type": "pr_synced"}],
        )

    pr_runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir_pr,
        playbook=playbook_pr,
        executor=pr_executor,
    )
    pr_runtime.run(start_step="pr")
    pr_state = BlackboardStore(issue_dir_pr).load_or_create("pr")
    pr_started = [e for e in pr_state.events if e.event_type == "step_started"]
    pr_completed = [e for e in pr_state.events if e.event_type == "step_completed"]
    pr_workflow_done = [e for e in pr_state.events if e.event_type == "workflow_completed"]
    assert pr_started[-1].data["runtime"] == "blackboard"
    assert pr_completed[-1].data["runtime"] == "blackboard"
    assert pr_workflow_done[-1].data["runtime"] == "blackboard"

    # single_step
    issue_dir_single = tmp_path / ".cafe" / "issues" / "runtime-labels-single"
    playbook_single = {
        "playbook": {"id": "default"},
        "steps": {
            "develop": {
                "skill": "spec_first",
                "role": "developer",
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            },
        },
    }

    def single_executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        return StepExecutionResult(
            response="confirmed",
            artifacts={},
            status_code="confirmed",
        )

    single_runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir_single,
        playbook=playbook_single,
        executor=single_executor,
    )
    single_runtime.run(start_step="develop", single_step=True)
    single_state = BlackboardStore(issue_dir_single).load_or_create("develop")
    single_started = [e for e in single_state.events if e.event_type == "step_started"]
    single_completed = [e for e in single_state.events if e.event_type == "single_step_completed"]
    single_done = [e for e in single_state.events if e.event_type == "workflow_completed"]
    assert single_started[-1].data["runtime"] == "single_step"
    assert single_completed[-1].data["runtime"] == "single_step"
    assert single_done[-1].data["runtime"] == "single_step"


def test_runtime_chains_pr_need_changes_through_develop_to_review(tmp_path: Path) -> None:
    """PR (NEEDS_CHANGES) → develop → review with baton updates (issue-225 plan Test 4.2)."""
    issue_dir = tmp_path / ".cafe" / "issues" / "e2e-chain-225"
    issue_dir.mkdir(parents=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "current_step": "pr",
                "playbook_id": "default",
                "artifacts": {},
                "events": [],
                "decisions": [],
                "handoff_summary": "",
            }
        ),
        encoding="utf-8",
    )
    _write_baton(issue_dir, from_step="pr", to_owner="agent", to_step="pr", intent="await_agent")

    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "pr": {"skill": "spec_first", "role": "developer", "assignee_type": "agent", "on": {}},
            "develop": {
                "skill": "develop",
                "role": "developer",
                "assignee_type": "agent",
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "review"},
            },
            "review": {
                "skill": "review",
                "role": "reviewer",
                "assignee_type": "agent",
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            },
        },
    }

    calls: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        calls.append(step_name)
        store = BlackboardStore(issue_dir)
        if step_name == "pr":
            store.update_handoff_contract(
                state,
                from_step="pr",
                to_owner=HandoffOwner.AGENT,
                to_step="develop",
                intent=HandoffIntent.AWAIT_AGENT,
                status_code="needs_changes",
                source="test",
            )
            return StepExecutionResult(response="todos", artifacts={}, status_code="needs_changes")
        if step_name == "develop":
            store.update_handoff_contract(
                state,
                from_step="develop",
                to_owner=HandoffOwner.AGENT,
                to_step="review",
                intent=HandoffIntent.AWAIT_AGENT,
                status_code="confirmed",
                source="test",
            )
            return StepExecutionResult(response="done", artifacts={}, status_code="confirmed")
        if step_name == "review":
            store.update_handoff_contract(
                state,
                from_step="review",
                to_owner=HandoffOwner.USER,
                to_step="user",
                intent=HandoffIntent.MANUAL_HANDOFF,
                status_code="confirmed",
                source="test",
            )
            return StepExecutionResult(response="lgtm", artifacts={}, status_code="confirmed")
        raise AssertionError(f"unexpected step {step_name}")

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="pr", max_transitions=15)

    assert calls == ["pr", "develop", "review"]
    assert result.completed is False
    blackboard = BlackboardStore(issue_dir).load_or_create("pr")
    assert blackboard.current_step == "user"
    transitions = [e for e in blackboard.events if e.event_type == "transition"]
    assert any(e.data.get("to") == "develop" for e in transitions)
    assert any(e.data.get("to") == "review" for e in transitions)


def test_runtime_normalizes_legacy_baton_written_by_pr_agent(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "legacy-pr-handoff"
    issue_dir.mkdir(parents=True)
    _write_baton(issue_dir, from_step="pr", to_owner="agent", to_step="pr", intent="await_agent")
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "pr": {"skill": "spec_first", "role": "developer", "assignee_type": "agent", "on": {}},
            "develop": {
                "skill": "develop",
                "role": "developer",
                "assignee_type": "agent",
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            },
        },
    }

    calls: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == "pr":
            (issue_dir / "next_step.txt").write_text("develop\n", encoding="utf-8")
            return StepExecutionResult(response="todo", artifacts={}, status_code="needs_changes")
        if step_name == "develop":
            store = BlackboardStore(issue_dir)
            store.update_handoff_contract(
                state,
                from_step="develop",
                to_owner=HandoffOwner.DONE,
                to_step="done",
                intent=HandoffIntent.WORKFLOW_COMPLETE,
                status_code="confirmed",
                source="test",
            )
            return StepExecutionResult(response="done", artifacts={}, status_code="confirmed")
        raise AssertionError(f"unexpected step {step_name}")

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="pr", max_transitions=5)

    assert calls == ["pr", "develop"]
    assert result.completed is True
    baton = json.loads((issue_dir / "next_step.txt").read_text(encoding="utf-8"))
    assert baton["from_step"] == "develop"
    assert baton["to_step"] == "done"


def test_runtime_handles_keyboard_interrupt(tmp_path: Path) -> None:
    """KeyboardInterrupt during step execution records step_interrupted event and returns INTERRUPTED result."""
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-interrupt"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {"skill": "spec_first", "role": "developer", "on": {"await_agent": "plan"}},
            "plan": {"skill": "plan", "role": "developer", "on": {"await_agent": "_done"}},
        },
    }

    call_count = 0

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        nonlocal call_count
        call_count += 1
        if step_name == "spec" and call_count == 1:
            raise KeyboardInterrupt()
        return StepExecutionResult(
            response="done",
            artifacts={},
            status_code="confirmed",
        )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )

    result = runtime.run(start_step="spec", max_transitions=5)

    # Should return INTERRUPTED result, not raise
    assert result.completed is False
    assert result.final_status_code.startswith("INTERRUPTED")
    assert result.final_step == "spec"

    # Verify event was recorded
    bb = BlackboardStore(issue_dir).load_or_create("spec", playbook_id="default")
    interrupted_events = [e for e in bb.events if e.event_type == "step_interrupted"]
    assert len(interrupted_events) == 1
    msg = json.loads(interrupted_events[0].message) if isinstance(interrupted_events[0].message, str) else interrupted_events[0].message
    assert msg["step"] == "spec"


def test_runtime_handles_agent_execution_error(tmp_path: Path) -> None:
    """AgentExecutionError (e.g. rate_limit) records step_interrupted event and returns INTERRUPTED result."""
    from cafe.agents.executor import AgentExecutionError

    issue_dir = tmp_path / ".cafe" / "issues" / "demo-agent-error"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {"skill": "spec_first", "role": "developer", "on": {"await_agent": "plan"}},
            "plan": {"skill": "plan", "role": "developer", "on": {"await_agent": "_done"}},
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        if step_name == "spec":
            raise AgentExecutionError("Rate limit exceeded", error_type="rate_limit")
        return StepExecutionResult(response="done", artifacts={}, status_code="confirmed")

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )

    result = runtime.run(start_step="spec", max_transitions=5)

    assert result.completed is False
    assert "agent_rate_limit" in result.final_status_code
    assert result.final_step == "spec"

    bb = BlackboardStore(issue_dir).load_or_create("spec", playbook_id="default")
    interrupted_events = [e for e in bb.events if e.event_type == "step_interrupted"]
    assert len(interrupted_events) == 1
    msg = json.loads(interrupted_events[0].message) if isinstance(interrupted_events[0].message, str) else interrupted_events[0].message
    assert msg["step"] == "spec"
    assert msg["reason"] == "agent_rate_limit"
