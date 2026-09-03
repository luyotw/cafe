"""Tests for the blackboard-first workflow runtime."""

import json
import multiprocessing
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.core.human_tasks import HumanTaskBinding, HumanTaskDecision, HumanTaskPolicy
from cafe.core.workflow_models import BatonRejected, StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.playbooks.loader import PlaybookLoader
from cafe.ui.human_tasks import resolve_step_human_task

pytestmark = pytest.mark.usefixtures("cached_builtin_playbook_models")


def test_observer_wakes_once_after_a_phase_transition(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "observer-transition"
    events: list[dict[str, object]] = []
    playbook = {
        "playbook": {"id": "observer"},
        "steps": {
            "spec": {
                "skill": "spec",
                "role": "pm",
                "on": {"await_agent": "develop"},
            },
            "develop": {
                "skill": "develop",
                "role": "developer",
                "on": {"await_agent": "_done"},
            },
        },
    }

    def executor(step_name: str, _step: dict, _state: object) -> StepExecutionResult:
        if step_name == "spec":
            _write_baton(
                issue_dir,
                from_step="spec",
                to_owner="agent",
                to_step="develop",
                intent="await_agent",
            )
        else:
            _write_baton(
                issue_dir,
                from_step="develop",
                to_owner="done",
                to_step="done",
                intent="workflow_complete",
            )
        return StepExecutionResult(response="", artifacts={})

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
        workflow_event_observer=events.append,
    ).run(start_step="spec")

    assert result.completed is True
    assert [event["event_type"] for event in events] == [
        "phase_terminal",
        "workflow_completed",
    ]
    assert events[0]["step"] == "spec"
    assert events[1]["step"] == "develop"


def test_observer_failure_never_blocks_workflow_advancement(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "observer-failure"
    playbook = {
        "playbook": {"id": "observer"},
        "steps": {"spec": {"skill": "spec", "role": "pm", "on": {"await_agent": "_done"}}},
    }

    def executor(_step_name: str, _step: dict, _state: object) -> StepExecutionResult:
        _write_baton(
            issue_dir,
            from_step="spec",
            to_owner="done",
            to_step="done",
            intent="workflow_complete",
        )
        return StepExecutionResult(response="", artifacts={})

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
        workflow_event_observer=lambda _event: (_ for _ in ()).throw(OSError("offline")),
    ).run(start_step="spec")

    assert result.completed is True
    events = BlackboardStore(issue_dir).load_or_create("spec").events
    assert any(event.event_type == "workflow_observer_dispatch_failed" for event in events)


def test_observer_diagnostic_failure_never_blocks_workflow_advancement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "observer-diagnostic-failure"
    playbook = {
        "playbook": {"id": "observer"},
        "steps": {"spec": {"skill": "spec", "role": "pm", "on": {"await_agent": "_done"}}},
    }

    def executor(_step_name: str, _step: dict, _state: object) -> StepExecutionResult:
        _write_baton(
            issue_dir,
            from_step="spec",
            to_owner="done",
            to_step="done",
            intent="workflow_complete",
        )
        return StepExecutionResult(response="", artifacts={})

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
        workflow_event_observer=lambda _event: (_ for _ in ()).throw(OSError("offline")),
    )
    original_record = runtime.blackboard_store.record_event

    def record_event(state, event_type, payload):
        if event_type == "workflow_observer_dispatch_failed":
            raise OSError("disk unavailable")
        return original_record(state, event_type, payload)

    monkeypatch.setattr(runtime.blackboard_store, "record_event", record_event)
    assert runtime.run(start_step="spec").completed is True


def test_pause_without_completed_phase_still_wakes_observer(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "observer-pause"
    events: list[dict[str, object]] = []
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook={
            "playbook": {"id": "observer"},
            "steps": {"spec": {"skill": "spec", "role": "pm", "on": {}}},
        },
        executor=lambda *_args: None,
        workflow_event_observer=events.append,
    )

    result = runtime._emit_pause(
        current_step="spec", status_code="ITERATION_LIMIT_REACHED", runtime="test", reason="limit"
    )

    assert result.final_status_code == "ITERATION_LIMIT_REACHED"
    assert events == [
        {
            "workflow_id": runtime.blackboard.workflow_id,
            "issue": "observer-pause",
            "event_type": "workflow_interruption",
            "step": "spec",
            "status_code": "ITERATION_LIMIT_REACHED",
            "reason": "limit",
        }
    ]


def _notify_human_task_in_process(
    issue_dir_value: str,
    rendezvous: object,
    result_queue: object,
) -> None:
    """Run one stale notification claimant in an independent process."""
    import cafe.core.workflow_runtime as runtime_mod

    dispatched = False

    def _dispatch(**_kwargs: object) -> SimpleNamespace:
        nonlocal dispatched
        dispatched = True
        time.sleep(0.25)
        return SimpleNamespace(receipt={"capability": "cafe.slack.human_task", "success": True})

    try:
        issue_dir = Path(issue_dir_value)
        runtime_mod.load_capability_registry = lambda _dirs: {}
        runtime_mod.default_capability_definition_dirs = lambda _root: []
        runtime_mod.run_capability_request = _dispatch
        runtime = BlackboardWorkflowRuntime(
            issue_dir=issue_dir,
            playbook=PlaybookLoader().load("standard"),
            executor=lambda *_args: None,
        )
        task = HumanTaskRecordStore(issue_dir).tasks()[0]
        rendezvous.wait(timeout=10)
        runtime._notify_new_human_task(task)
    except BaseException as exc:
        result_queue.put(("error", repr(exc)))
        return
    result_queue.put(("ok", dispatched))


def _write_baton(
    issue_dir: Path,
    *,
    from_step: str,
    to_owner: str,
    to_step: str,
    intent: str,
    status_code: str = "",
    source: str = "test",
) -> None:
    (issue_dir / "next_step.txt").write_text(
        json.dumps(
            {
                "version": 1,
                "from_step": from_step,
                "to_owner": to_owner,
                "to_step": to_step,
                "intent": intent,
                "status_code": status_code,
                "created_at": "2026-04-26T23:00:00+08:00",
                "source": source,
            }
        ),
        encoding="utf-8",
    )


def _write_iteration_evidence(
    issue_dir: Path,
    step: str,
    *,
    output: str = "# done\n",
    checklist: str = "- [x] done\n",
    questions: str | None = None,
) -> Path:
    iteration_dir = issue_dir / step / "iteration_001"
    iteration_dir.mkdir(parents=True, exist_ok=True)
    (iteration_dir / "iteration.json").write_text(
        json.dumps({"iteration": 1, "timestamp": "2026-04-26T23:00:00+08:00"}),
        encoding="utf-8",
    )
    (iteration_dir / "output.md").write_text(output, encoding="utf-8")
    (iteration_dir / "checklist.md").write_text(checklist, encoding="utf-8")
    if questions is not None:
        (iteration_dir / "questions.xml").write_text(questions, encoding="utf-8")
    return iteration_dir


def test_runtime_rejects_undeclared_alignment_baton_before_routing(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "driver-owned-alignment-baton"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "develop": {
                "skill": "develop",
                "role": "developer",
                "on": {"await_agent": "_done"},
            }
        },
    }
    calls = 0

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            _write_baton(
                issue_dir,
                from_step=step_name,
                to_owner="user",
                to_step="user",
                intent="alignment_checkpoint",
            )
        else:
            _write_baton(
                issue_dir,
                from_step=step_name,
                to_owner="done",
                to_step="done",
                intent="workflow_complete",
            )
        return StepExecutionResult(response="", artifacts={})

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    ).run(start_step="develop")

    assert result.completed is True
    assert calls == 2
    blackboard = BlackboardStore(issue_dir).load_or_create("develop")
    rejected = [event for event in blackboard.events if event.event_type == "baton_rejected"]
    assert rejected[-1].data["field"] == "intent"
    assert rejected[-1].data["invalid_value"] == "alignment_checkpoint"


def test_runtime_rejects_undeclared_alignment_legacy_status(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "driver-owned-alignment-status"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "develop": {
                "skill": "develop",
                "role": "developer",
                "on": {"await_agent": "_done"},
            }
        },
    }

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=lambda *_: StepExecutionResult(
            response="alignment_checkpoint",
            artifacts={},
        ),
    ).run(start_step="develop")

    assert result.completed is False
    assert result.final_status_code == "INVALID_STATUS_CODE"
    blackboard = BlackboardStore(issue_dir).load_or_create("develop")
    assert blackboard.current_step == "develop"


def test_runtime_blocks_pr_done_without_publish_receipt(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-pr"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("pr:\n  auto_create: true\n", encoding="utf-8")
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "pr": {
                "skill": "spec_first",
                "role": "developer",
                "behavior": {"completion": "baton", "publish_confirmation": True},
                "capability_requests": ["cafe.pr.publish"],
                "on": {"confirm_output": "pr", "workflow_complete": "_done"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        _write_baton(
            issue_dir, from_step="pr", to_owner="done", to_step="done", intent="workflow_complete"
        )
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


def test_runtime_missing_pr_config_does_not_require_publish_receipt(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-local-pr"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "pr": {
                "skill": "spec_first",
                "role": "developer",
                "behavior": {"completion": "baton", "publish_confirmation": True},
                "capability_requests": ["cafe.pr.publish"],
                "on": {"await_agent": "_done"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        _write_baton(
            issue_dir,
            from_step="pr",
            to_owner="done",
            to_step="done",
            intent="workflow_complete",
        )
        return StepExecutionResult(response="done", artifacts={"pr_result": "local"})

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    ).run(start_step="pr")

    assert result.completed is True


def test_runtime_completes_pr_when_publish_receipt_exists(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-pr"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "pr": {
                "skill": "spec_first",
                "role": "developer",
                "behavior": {"completion": "baton", "publish_confirmation": True},
                "capability_requests": ["cafe.pr.publish"],
                "on": {"await_agent": "_done"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        _write_baton(
            issue_dir, from_step="pr", to_owner="done", to_step="done", intent="workflow_complete"
        )
        return StepExecutionResult(
            response="done",
            artifacts={"pr_result": "p1"},
            events=[{"type": "pr_synced", "url": "https://github.com/test/repo/pull/240"}],
        )

    observer_events: list[dict[str, object]] = []
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
        workflow_event_observer=observer_events.append,
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
            "pr": {
                "skill": "spec_first",
                "role": "developer",
                "behavior": {"completion": "baton", "publish_confirmation": True},
                "capability_requests": ["cafe.pr.publish"],
                "on": {"await_agent": "_done"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        _write_baton(
            issue_dir, from_step="pr", to_owner="done", to_step="done", intent="workflow_complete"
        )
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


def test_runtime_blocks_declared_capability_step_without_receipt(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-capability-step"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "publish": {
                "skill": "spec_first",
                "role": "developer",
                "capability_requests": ["demo.publish"],
                "on": {"await_agent": "_done"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        _write_baton(
            issue_dir,
            from_step="publish",
            to_owner="done",
            to_step="done",
            intent="workflow_complete",
        )
        return StepExecutionResult(response="done", artifacts={"publish_result": "p1"})

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="publish")

    assert result.completed is False
    assert result.final_step == "publish"
    assert result.final_status_code == "MISSING_CAPABILITY_RECEIPT"
    blackboard = BlackboardStore(issue_dir).load_or_create("publish")
    assert blackboard.current_step == "publish"
    blocked_events = [
        event for event in blackboard.events if event.event_type == "workflow_blocked"
    ]
    assert blocked_events[-1].data["missing_capabilities"] == ["demo.publish"]


def test_runtime_pauses_on_distinct_capability_approval_task(tmp_path: Path) -> None:
    """Test List integration 1/7: approval pending routes to user, not alignment."""
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-capability-approval"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "publish": {
                "skill": "spec_first",
                "role": "developer",
                "capability_requests": ["demo.publish"],
                "on": {"await_agent": "_done"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        _write_baton(
            issue_dir,
            from_step="publish",
            to_owner="done",
            to_step="done",
            intent="workflow_complete",
        )
        return StepExecutionResult(
            response="done",
            artifacts={"publish_result": "p1"},
            events=[
                {
                    "type": "capability_approval_pending",
                    "capability": "demo.publish",
                    "task_id": "approval-task",
                    "request_fingerprint": "fingerprint",
                }
            ],
        )

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    ).run(start_step="publish")

    assert result.final_status_code == "CAPABILITY_APPROVAL_PENDING"
    assert result.detail == "approval-task"
    blackboard = BlackboardStore(issue_dir).load_or_create("publish")
    assert blackboard.current_step == "user"
    assert blackboard.handoff_contract is not None
    assert blackboard.handoff_contract.intent.value == "manual_handoff"


def test_runtime_completes_declared_capability_step_with_receipt(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-capability-step-success"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "publish": {
                "skill": "spec_first",
                "role": "developer",
                "capability_requests": ["demo.publish"],
                "on": {"await_agent": "_done"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        _write_baton(
            issue_dir,
            from_step="publish",
            to_owner="done",
            to_step="done",
            intent="workflow_complete",
        )
        return StepExecutionResult(
            response="done",
            artifacts={"publish_result": "p1"},
            events=[
                {
                    "type": "capability_receipt",
                    "capability": "demo.publish",
                    "success": True,
                    "correlation_id": "generic-ok",
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
    result = runtime.run(start_step="publish")

    assert result.completed is True
    assert result.final_step == "publish"
    assert result.final_status_code == "BATON_WORKFLOW_COMPLETE"


def test_runtime_reports_pr_publish_failures_as_publish_error(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-pr-publish-error"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "pr": {"skill": "spec_first", "role": "developer", "on": {"await_agent": "_done"}},
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        raise RuntimeError(
            "PR sync script failed: Error: cannot sync PR with uncommitted changes.\n"
            "Commit or stash changes first, then run cafe make again."
        )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="pr")

    assert result.completed is False
    assert result.final_step == "pr"
    assert result.final_status_code == "INTERRUPTED:publish_error"
    assert result.detail is not None
    assert "cannot sync PR with uncommitted changes" in result.detail

    blackboard = BlackboardStore(issue_dir).load_or_create("pr")
    contract = BlackboardStore(issue_dir).load_handoff_contract(
        blackboard,
        allowed_steps=["pr"],
    )
    assert blackboard.current_step == "pr"
    assert contract.from_step == "pr"
    assert contract.to_owner == HandoffOwner.AGENT
    assert contract.to_step == "pr"
    event = blackboard.events[-3]
    assert event.event_type == "step_interrupted"
    assert event.data["reason"] == "publish_error"


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


def test_runtime_retries_stale_invalid_baton_from_startup(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "stale-invalid-baton"
    issue_dir.mkdir(parents=True)
    _write_baton(
        issue_dir,
        from_step="spec",
        to_owner="user",
        to_step="user",
        intent="await_user_qa",
    )
    playbook = {
        "playbook": {"id": "default"},
        "entry_point": "spec",
        "steps": {
            "spec": {
                "skill": "spec_first",
                "role": "pm",
                "on": {"need_clarification": "spec", "await_agent": "_done"},
            },
        },
    }
    prompts: list[str] = []
    retry_flags: list[bool] = []

    def executor(
        step_name: str,
        step_def: dict,
        state: object,
        *,
        extra_prompt: str | None = None,
        same_invocation_retry: bool = False,
    ) -> StepExecutionResult:
        prompts.append(extra_prompt or "")
        retry_flags.append(same_invocation_retry)
        _write_baton(
            issue_dir,
            from_step="spec",
            to_owner="user",
            to_step="user",
            intent="need_clarification",
        )
        payload = json.loads((issue_dir / "next_step.txt").read_text(encoding="utf-8"))
        payload["status_code"] = "need_clarification"
        (issue_dir / "next_step.txt").write_text(json.dumps(payload), encoding="utf-8")
        return StepExecutionResult(response="", artifacts={})

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run()

    assert result.completed is False
    assert result.final_step == "spec"
    assert result.final_status_code == "need_clarification"
    assert prompts
    assert "await_user_qa" in prompts[0]
    assert "need_clarification" in prompts[0]
    assert retry_flags == [False]


def test_runtime_rejects_legacy_text_baton_in_core_path(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "strict-baton"
    issue_dir.mkdir(parents=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "standard",
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
                "behavior": {"completion": "baton", "publish_confirmation": True},
                "capability_requests": ["cafe.pr.publish"],
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
        _write_baton(
            issue_dir, from_step="pr", to_owner="done", to_step="done", intent="workflow_complete"
        )
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


def test_runtime_preserves_strict_done_baton_metadata_after_reload(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-pr-strict-done"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "issue.yaml").write_text("pr:\n  auto_create: false\n", encoding="utf-8")
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "pr": {"skill": "spec_first", "role": "developer", "on": {"await_agent": "_done"}},
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        (issue_dir / "next_step.txt").write_text(
            json.dumps(
                {
                    "version": 1,
                    "to_owner": "done",
                    "to_step": "done",
                    "intent": "workflow_complete",
                }
            ),
            encoding="utf-8",
        )
        return StepExecutionResult(response="done", artifacts={"pr_result": "p1"})

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="pr", single_step=True)

    assert result.completed is True
    assert result.final_step == "pr"
    assert result.final_status_code == "BATON_WORKFLOW_COMPLETE"

    reloaded = BlackboardStore(issue_dir).load_or_create("done")
    assert reloaded.current_step == "done"
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.from_step == "pr"
    assert reloaded.handoff_contract.source == "baton"
    assert reloaded.handoff_contract.to_owner == HandoffOwner.DONE


def test_runtime_done_baton_status_overrides_phase_parser_status(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-pr-status"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "issue.yaml").write_text("pr:\n  auto_create: false\n", encoding="utf-8")
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "pr": {"skill": "spec_first", "role": "developer", "on": {"await_agent": "_done"}},
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        (issue_dir / "next_step.txt").write_text(
            json.dumps(
                {
                    "version": 1,
                    "to_owner": "done",
                    "to_step": "done",
                    "intent": "workflow_complete",
                }
            ),
            encoding="utf-8",
        )
        return StepExecutionResult(
            response="done",
            artifacts={"pr_result": "p1"},
            status_code="need_clarification",
        )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="pr", single_step=True)

    assert result.completed is True
    assert result.final_status_code == "BATON_WORKFLOW_COMPLETE"


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
        _write_baton(
            issue_dir, from_step="pr", to_owner="agent", to_step="review", intent="await_agent"
        )
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


def test_runtime_legacy_step_stays_on_same_step_when_status_missing(tmp_path: Path) -> None:
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

    assert result.completed is False
    assert result.final_step == "spec"
    assert result.final_status_code == "NO_STATUS_CODE"
    assert calls == ["spec"]


def test_runtime_ignores_stale_baton_when_status_missing(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-stale-baton"
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

    blackboard = BlackboardStore(issue_dir).load_or_create("spec")
    BlackboardStore(issue_dir).update_handoff_contract(
        blackboard,
        from_step="plan",
        to_owner=HandoffOwner.AGENT,
        to_step="plan",
        intent=HandoffIntent.AWAIT_AGENT,
        status_code="confirmed",
        source="test.stale_baton",
    )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=lambda *_args, **_kwargs: ("plain response without status token", {}),
    )
    assert runtime._resolve_next_step_from_handoff(current_step="spec") is None


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
                "behavior": {"completion": "baton", "publish_confirmation": True},
                "capability_requests": ["cafe.pr.publish"],
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
        _write_baton(
            issue_dir, from_step="pr", to_owner="done", to_step="done", intent="workflow_complete"
        )
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


def test_runtime_review_confirmed_routes_to_pr_without_legacy_class(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "review-confirmed"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "review": {
                "skill": "review",
                "role": "reviewer",
                "valid_intents": ["confirmed", "needs_changes"],
                "on": {
                    "await_agent": "pr",
                    "manual_handoff": "develop",
                    "need_clarification": "review",
                },
            },
            "develop": {
                "skill": "develop",
                "role": "developer",
                "on": {"await_agent": "review"},
            },
            "pr": {
                "skill": "pr",
                "role": "developer",
                "behavior": {"completion": "baton", "publish_confirmation": True},
                "capability_requests": ["cafe.pr.publish"],
                "on": {"await_agent": "_done"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        assert step_name == "review"
        return StepExecutionResult(
            response="confirmed",
            artifacts={"review_feedback": "review-output.md"},
            status_code="confirmed",
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
    assert blackboard.handoff_contract is not None
    assert blackboard.handoff_contract.to_owner == HandoffOwner.AGENT
    assert blackboard.handoff_contract.to_step == "pr"
    assert blackboard.handoff_contract.intent == HandoffIntent.AWAIT_AGENT
    assert blackboard.artifacts["review_feedback"].path == "review-output.md"


def test_runtime_review_needs_changes_routes_to_develop_without_legacy_class(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "review-needs-changes"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "review": {
                "skill": "review",
                "role": "reviewer",
                "valid_intents": ["confirmed", "needs_changes"],
                "on": {
                    "await_agent": "pr",
                    "manual_handoff": "develop",
                    "need_clarification": "review",
                },
            },
            "develop": {
                "skill": "develop",
                "role": "developer",
                "on": {"await_agent": "review"},
            },
            "pr": {
                "skill": "pr",
                "role": "developer",
                "on": {"await_agent": "_done"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        assert step_name == "review"
        return StepExecutionResult(
            response="needs_changes",
            artifacts={"review_feedback": "review-output.md"},
            status_code="needs_changes",
        )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="review", single_step=True)

    assert result.completed is False
    assert result.final_step == "review"
    assert result.final_status_code == "needs_changes"
    blackboard = BlackboardStore(issue_dir).load_or_create("review")
    assert blackboard.current_step == "develop"
    assert blackboard.handoff_contract is not None
    assert blackboard.handoff_contract.to_owner == HandoffOwner.AGENT
    assert blackboard.handoff_contract.to_step == "develop"
    assert blackboard.handoff_contract.intent == HandoffIntent.AWAIT_AGENT
    assert blackboard.artifacts["review_feedback"].path == "review-output.md"


def test_runtime_review_preserves_agent_written_downstream_baton(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "review-agent-baton"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "review": {
                "skill": "review",
                "role": "reviewer",
                "valid_intents": ["confirmed", "needs_changes"],
                "on": {
                    "await_agent": "pr",
                    "manual_handoff": "develop",
                    "need_clarification": "review",
                },
            },
            "develop": {
                "skill": "develop",
                "role": "developer",
                "on": {"await_agent": "review"},
            },
            "pr": {
                "skill": "pr",
                "role": "developer",
                "on": {"await_agent": "_done"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        assert step_name == "review"
        _write_baton(
            issue_dir,
            from_step="review",
            to_owner="agent",
            to_step="develop",
            intent="await_agent",
            status_code="needs_changes",
            source="review.agent",
        )
        return StepExecutionResult(
            response="confirmed",
            artifacts={"review_feedback": "review-output.md"},
            status_code="confirmed",
        )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="review", single_step=True)

    assert result.completed is False
    assert result.final_step == "review"
    assert result.final_status_code == "needs_changes"
    blackboard = BlackboardStore(issue_dir).load_or_create("review")
    assert blackboard.current_step == "develop"
    assert blackboard.handoff_contract is not None
    assert blackboard.handoff_contract.to_step == "develop"
    assert blackboard.handoff_contract.source == "review.agent"


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


def test_continuous_runtime_executes_after_realigning_stale_current_step(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-stale-current-step"
    issue_dir.mkdir(parents=True)
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {"skill": "spec_first", "role": "pm", "on": {"await_agent": "plan"}},
            "plan": {
                "skill": "spec_first",
                "role": "developer",
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            },
        },
    }
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "current_step": "spec",
                "playbook_id": "standard",
                "artifacts": {},
                "events": [],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )
    _write_baton(
        issue_dir,
        from_step="spec",
        to_owner=HandoffOwner.AGENT,
        to_step="plan",
        intent=HandoffIntent.AWAIT_AGENT,
        status_code="confirmed",
    )
    executed_steps: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        executed_steps.append(step_name)
        return StepExecutionResult(response="confirmed", artifacts={}, status_code="confirmed")

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )

    result = runtime.run(max_transitions=5)

    assert result.completed is True
    assert result.final_step == "plan"
    assert executed_steps == ["plan"]
    blackboard = BlackboardStore(issue_dir).load_or_create("spec")
    assert blackboard.current_step == "done"
    realigned_events = [
        event for event in blackboard.events if event.event_type == "runtime_position_realigned"
    ]
    assert realigned_events[-1].data["previous_current_step"] == "spec"
    assert realigned_events[-1].data["resolved_step"] == "plan"

    next_runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    resumed = next_runtime.run(max_transitions=5)

    assert resumed.completed is True
    assert resumed.final_step == "plan"
    assert executed_steps == ["plan"]


def test_single_step_reports_a_stale_handoff_realign_without_executing_it(tmp_path: Path) -> None:
    issue_dir = tmp_path / "single-step-realign"
    playbook = {
        "playbook": {"id": "single-step-realign"},
        "steps": {
            "spec": {"skill": "spec_first", "role": "pm", "on": {"await_agent": "plan"}},
            "plan": {"skill": "spec_first", "role": "developer", "on": {"await_agent": "_done"}},
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")
    store.update_handoff_contract(
        state,
        from_step="spec",
        to_owner=HandoffOwner.AGENT,
        to_step="plan",
        intent=HandoffIntent.AWAIT_AGENT,
        status_code="confirmed",
        source="test",
    )
    executed: list[str] = []
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=lambda step, *_args: executed.append(step)
        or StepExecutionResult(response="confirmed", artifacts={}, status_code="confirmed"),
    )

    result = runtime.run(single_step=True)

    assert result.completed is False
    assert result.final_step == "spec"
    assert result.final_status_code == "confirmed"
    assert executed == []


def test_replay_resets_attempt_cycle_when_transition_event_survives_first(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / "transition-attempt-reset"
    playbook = {
        "playbook": {"id": "transition-attempt-reset"},
        "steps": {
            "spec": {
                "skill": "spec_first",
                "role": "pm",
                "max_attempts_per_cycle": 1,
                "on": {"await_agent": "plan"},
            },
            "plan": {
                "skill": "spec_first",
                "role": "developer",
                "on": {"await_agent": "_done"},
            },
        },
    }
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=lambda *_args: StepExecutionResult(
            response="await_agent", artifacts={}, status_code="await_agent"
        ),
    )

    with pytest.raises(RuntimeError, match="crash after transition event"):
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(
                runtime,
                "_reset_step_attempts_after_successful_advance",
                lambda **_kwargs: (_ for _ in ()).throw(
                    RuntimeError("crash after transition event")
                ),
            )
            runtime.run(start_step="spec")

    crashed = BlackboardStore(issue_dir).load_or_create("spec")
    assert crashed.current_step == "spec"
    assert crashed.step_attempt_counts == {"spec": 1}

    replay = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=lambda *_args: pytest.fail("replay must not execute the completed step"),
    ).run(single_step=True)

    recovered = BlackboardStore(issue_dir).load_or_create("spec")
    assert replay.final_step == "spec"
    assert recovered.current_step == "plan"
    assert recovered.step_attempt_counts == {}
    assert any(event.event_type == "step_attempt_count_reset" for event in recovered.events)


def test_replay_does_not_overwrite_a_newer_target_user_handoff(tmp_path: Path) -> None:
    issue_dir = tmp_path / "transition-newer-target-handoff"
    playbook = {
        "playbook": {"id": "transition-newer-target-handoff"},
        "steps": {
            "spec": {"skill": "spec_first", "role": "pm", "on": {"await_agent": "plan"}},
            "plan": {"skill": "spec_first", "role": "developer", "on": {"await_agent": "_done"}},
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")
    store.record_event(
        state,
        "transition",
        {
            "from": "spec",
            "to": "plan",
            "status_code": "await_agent",
            "transition_id": "transition-458-old",
        },
    )
    store.set_current_step(state, "plan")
    store.update_handoff_contract(
        state,
        from_step="plan",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.NEED_CLARIFICATION,
        status_code="need_clarification",
        source="test.newer_target_handoff",
    )
    executed: list[str] = []

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=lambda step, *_args: executed.append(step)
        or StepExecutionResult(response="await_agent", artifacts={}, status_code="await_agent"),
    ).run(single_step=True)

    recovered = BlackboardStore(issue_dir).load_or_create("spec")
    assert result.final_status_code == "need_clarification"
    assert executed == []
    assert recovered.current_step == "user"
    assert recovered.handoff_contract.from_step == "plan"
    assert recovered.handoff_contract.to_owner is HandoffOwner.USER
    assert recovered.handoff_contract.intent is HandoffIntent.NEED_CLARIFICATION
    assert not any(event.event_type == "transition_recovered" for event in recovered.events)


def test_runtime_resumes_to_user_wait_from_handoff_contract(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-user-wait-contract"
    issue_dir.mkdir(parents=True)
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "develop": {"skill": "develop", "role": "developer", "on": {"await_agent": "review"}},
            "review": {"skill": "review", "role": "reviewer", "on": {"await_agent": "_done"}},
        },
    }
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "current_step": "develop",
                "playbook_id": "standard",
                "artifacts": {},
                "events": [],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )
    _write_baton(
        issue_dir,
        from_step="develop",
        to_owner="user",
        to_step="user",
        intent="need_clarification",
        status_code="need_clarification",
    )

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        raise AssertionError("user-owned baton should not execute an agent step")

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )

    result = runtime.run(max_transitions=5)

    assert result.completed is False
    assert result.final_step == "develop"
    assert result.final_status_code == "need_clarification"
    blackboard = BlackboardStore(issue_dir).load_or_create("develop")
    assert blackboard.current_step == "user"


def test_runtime_resumes_to_done_from_handoff_contract(tmp_path: Path) -> None:
    cafe_dir = tmp_path / ".cafe"
    issue_dir = cafe_dir / "issues" / "demo-done-contract"
    issue_dir.mkdir(parents=True)
    (cafe_dir / "active_issue").write_text("demo-done-contract\n", encoding="utf-8")
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "pr": {"skill": "pr", "role": "developer", "on": {"await_agent": "_done"}},
        },
    }
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "current_step": "pr",
                "playbook_id": "standard",
                "artifacts": {},
                "events": [],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )
    _write_baton(
        issue_dir,
        from_step="pr",
        to_owner="done",
        to_step="done",
        intent="workflow_complete",
    )

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        raise AssertionError("done-owned baton should not execute an agent step")

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )

    result = runtime.run(max_transitions=5)

    assert result.completed is True
    assert result.final_step == "pr"
    assert result.final_status_code == "BATON_WORKFLOW_COMPLETE"
    blackboard = BlackboardStore(issue_dir).load_or_create("pr")
    assert blackboard.current_step == "done"
    assert not (cafe_dir / "active_issue").exists()


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


def test_emit_complete_clears_matching_active_issue_marker(tmp_path: Path) -> None:
    cafe_dir = tmp_path / ".cafe"
    issue_dir = cafe_dir / "issues" / "done-issue"
    issue_dir.mkdir(parents=True)
    (cafe_dir / "active_issue").write_text("done-issue\n", encoding="utf-8")
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
    assert not (cafe_dir / "active_issue").exists()


def test_emit_complete_does_not_clear_non_matching_active_issue_marker(tmp_path: Path) -> None:
    cafe_dir = tmp_path / ".cafe"
    issue_dir = cafe_dir / "issues" / "done-issue"
    issue_dir.mkdir(parents=True)
    (cafe_dir / "active_issue").write_text("other-issue\n", encoding="utf-8")
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
    runtime.run(start_step="review")

    assert (cafe_dir / "active_issue").read_text(encoding="utf-8").strip() == "other-issue"


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
                "valid_intents": ["confirmed", "ready_for_review"],
                "on": {
                    "await_agent": "_done",
                    "confirm_output": "spec",
                },
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


def test_runtime_revision_materializes_a_fresh_plan_confirmation_task(tmp_path: Path) -> None:
    """A revised plan must not reuse an earlier completed output-review task."""
    issue_dir = tmp_path / ".cafe" / "issues" / "revised-plan-confirmation"
    issue_dir.mkdir(parents=True)
    playbook = PlaybookLoader().load("standard-qa")
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("plan", playbook_id="standard-qa")
    records = HumanTaskRecordStore(issue_dir)
    policy, binding = resolve_step_human_task(
        playbook_data=playbook,
        step_name="plan",
        trigger="confirm_output",
        iteration=4,
    )
    previous = records.materialize(
        workflow_id=state.workflow_id,
        step="plan",
        iteration=4,
        trigger="confirm_output",
        policy_id=policy.id,
        prompt=policy.prompt,
        expected_result=policy.model_dump(mode="json"),
        continuations=binding.outcomes,
        assignee_type="user",
    )
    records.complete(
        workflow_id=state.workflow_id,
        task_id=previous.id,
        payload={"task": policy.id, "decision": "revise", "feedback": "Narrow the plan."},
        source="test",
    )
    (issue_dir / "plan" / "iteration_005").mkdir(parents=True)

    def executor(step_name: str, step_def: dict, blackboard: object) -> StepExecutionResult:
        BlackboardStore(issue_dir).update_handoff_contract(
            blackboard,
            from_step="plan",
            to_owner=HandoffOwner.USER,
            to_step="user",
            intent=HandoffIntent.CONFIRM_OUTPUT,
            source="test.revised_plan",
        )
        return StepExecutionResult(response="confirmed plan revision", artifacts={})

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    ).run(start_step="plan")

    current = BlackboardStore(issue_dir).load_or_create("plan")
    tasks = records.tasks()
    pending = [task for task in tasks if task.status.value == "pending"]

    assert result.completed is False
    assert result.final_status_code == "BATON_CONFIRM_OUTPUT"
    assert current.current_step == "user"
    assert records.get_task(previous.id).status.value == "completed"
    assert len(pending) == 1
    assert pending[0].id != previous.id
    assert pending[0].iteration == 5
    assert pending[0].trigger == "confirm_output"


def test_runtime_enforces_confirmation_gate_over_agent_baton(tmp_path: Path) -> None:
    """A confirmation-gated phase cannot advance itself with an agent baton."""
    issue_dir = tmp_path / ".cafe" / "issues" / "enforced-plan-confirmation"
    issue_dir.mkdir(parents=True)
    playbook = PlaybookLoader().load("standard-qa")

    def executor(step_name: str, step_def: dict, blackboard: object) -> StepExecutionResult:
        assert step_name == "plan"
        BlackboardStore(issue_dir).update_handoff_contract(
            blackboard,
            from_step="plan",
            to_owner=HandoffOwner.AGENT,
            to_step="develop",
            intent=HandoffIntent.AWAIT_AGENT,
            source="test.plan_agent_bypass",
        )
        return StepExecutionResult(response="plan complete", artifacts={})

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    ).run(start_step="plan")

    current = BlackboardStore(issue_dir).load_or_create("plan")
    pending = [
        task
        for task in HumanTaskRecordStore(issue_dir).tasks()
        if task.status is HumanTaskStatus.PENDING
    ]
    enforced = [
        event for event in current.events if event.event_type == "confirmation_gate_enforced"
    ]

    assert result.completed is False
    assert result.final_status_code == "BATON_CONFIRM_OUTPUT"
    assert current.current_step == "user"
    assert current.handoff_contract is not None
    assert current.handoff_contract.to_owner is HandoffOwner.USER
    assert current.handoff_contract.to_step == "user"
    assert current.handoff_contract.intent is HandoffIntent.CONFIRM_OUTPUT
    assert len(pending) == 1
    assert pending[0].step == "plan"
    assert pending[0].trigger == "confirm_output"
    assert enforced[-1].data == {
        "step": "plan",
        "original_owner": "agent",
        "original_step": "develop",
        "original_intent": "await_agent",
    }


@pytest.mark.parametrize(
    ("decision", "correction", "completes"),
    [("confirm", False, True), ("revise", True, False)],
)
def test_runtime_allows_only_a_non_correction_self_loop_confirmation_to_advance(
    tmp_path: Path, decision: str, correction: bool, completes: bool
) -> None:
    """Only a non-correction self-loop decision may advance the approved output."""
    issue_dir = tmp_path / ".cafe" / "issues" / "confirmed-review"
    issue_dir.mkdir(parents=True)
    playbook = {
        "playbook": {"id": "confirmed-review"},
        "steps": {
            "review": {
                "skill": "cafe-spec",
                "role": "reviewer",
                "human_tasks": [
                    {
                        "trigger": "confirm_output",
                        "task_id": "output-review",
                        "outcomes": {decision: "review"},
                    }
                ],
                "on": {"await_agent": "closeout", "confirm_output": "review"},
            },
            "closeout": {
                "skill": "cafe-spec",
                "role": "developer",
                "on": {"await_agent": "_done"},
            },
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("review", playbook_id="confirmed-review")
    records = HumanTaskRecordStore(issue_dir)
    task = records.materialize(
        workflow_id=state.workflow_id,
        step="review",
        iteration=1,
        trigger="confirm_output",
        policy_id="output-review",
        prompt="Confirm review output",
        expected_result={"decisions": [{"id": decision, "correction": correction}]},
        continuations={decision: "review"},
        assignee_type="user",
    )
    records.complete(
        workflow_id=state.workflow_id,
        task_id=task.id,
        payload={"task": "output-review", "decision": decision, "continuation": "review"},
        source="test",
    )
    continuation_dir = issue_dir / "review" / "iteration_002"
    continuation_dir.mkdir(parents=True)
    (continuation_dir / "user_input.md").write_text(
        "CAFE validated this HumanTask response for the continuation phase:\n"
        + json.dumps(
            {
                "schema_version": 1,
                "type": "human_task_completion",
                "human_task_id": task.id,
                "task": "output-review",
                "decision": decision,
                "continuation": "review",
            }
        ),
        encoding="utf-8",
    )
    store.update_handoff_contract(
        state,
        from_step="review",
        to_owner=HandoffOwner.AGENT,
        to_step="review",
        intent=HandoffIntent.AWAIT_AGENT,
        source="human_task.test",
    )

    def executor(step_name: str, _step_def: dict, blackboard: object) -> StepExecutionResult:
        if step_name == "review":
            BlackboardStore(issue_dir).update_handoff_contract(
                blackboard,
                from_step="review",
                to_owner=HandoffOwner.AGENT,
                to_step="closeout",
                intent=HandoffIntent.AWAIT_AGENT,
                source="test.review_confirmed",
            )
        else:
            BlackboardStore(issue_dir).update_handoff_contract(
                blackboard,
                from_step="closeout",
                to_owner=HandoffOwner.DONE,
                to_step="done",
                intent=HandoffIntent.WORKFLOW_COMPLETE,
                source="test.closeout",
            )
        return StepExecutionResult(response="complete", artifacts={})

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    ).run()

    current = BlackboardStore(issue_dir).load_or_create("review")
    assert result.completed is completes
    if completes:
        assert current.current_step == "done"
        assert not [
            event for event in current.events if event.event_type == "confirmation_gate_enforced"
        ]
    else:
        assert result.final_status_code == "BATON_CONFIRM_OUTPUT"
        assert current.current_step == "user"
        assert [
            event for event in current.events if event.event_type == "confirmation_gate_enforced"
        ]


def test_runtime_preserves_declared_manual_handoff_from_confirmation_gate(
    tmp_path: Path,
) -> None:
    """A blocking review returns to its declared correction step without approval."""
    issue_dir = tmp_path / ".cafe" / "issues" / "review-correction"
    issue_dir.mkdir(parents=True)
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "review": {
                "skill": "review",
                "role": "reviewer",
                "on": {
                    "await_agent": "closeout",
                    "confirm_output": "review",
                    "manual_handoff": "knowledge",
                },
            },
            "knowledge": {
                "skill": "knowledge",
                "role": "developer",
                "on": {"await_agent": "_done"},
            },
            "closeout": {
                "skill": "closeout",
                "role": "developer",
                "on": {"await_agent": "_done"},
            },
        },
    }
    calls: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        calls.append(step_name)
        store = BlackboardStore(issue_dir)
        if step_name == "review":
            store.update_handoff_contract(
                state,
                from_step="review",
                to_owner=HandoffOwner.AGENT,
                to_step="knowledge",
                intent=HandoffIntent.MANUAL_HANDOFF,
                source="test.review_blocking",
            )
        else:
            store.update_handoff_contract(
                state,
                from_step="knowledge",
                to_owner=HandoffOwner.DONE,
                to_step="done",
                intent=HandoffIntent.WORKFLOW_COMPLETE,
                source="test.knowledge_complete",
            )
        return StepExecutionResult(response="complete", artifacts={})

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    ).run(start_step="review")

    current = BlackboardStore(issue_dir).load_or_create("review")
    enforced = [
        event for event in current.events if event.event_type == "confirmation_gate_enforced"
    ]

    assert result.completed is True
    assert calls == ["review", "knowledge"]
    assert enforced == []


def test_runtime_status_code_missing_no_handoff_contract(tmp_path: Path) -> None:
    """When status and handoff are absent, the runtime still pauses."""
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


def test_runtime_materializes_one_declared_task_and_recovers_it_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IT-001: pause/restart preserves the exact durable task and wait state."""
    import cafe.core.workflow_runtime as runtime_mod

    issue_dir = tmp_path / ".cafe" / "issues" / "durable-restart"
    playbook = PlaybookLoader().load("standard")
    capability_calls: list[dict[str, object]] = []
    monkeypatch.setattr(runtime_mod, "load_capability_registry", lambda _dirs: {"registered": True})
    monkeypatch.setattr(runtime_mod, "default_capability_definition_dirs", lambda _root: [])

    def _run_capability_request(**kwargs: object) -> SimpleNamespace:
        capability_calls.append(kwargs)
        request = kwargs["capability_request"]
        return SimpleNamespace(
            receipt={
                "capability": "cafe.slack.human_task",
                "success": True,
                "inputs": dict(request["args"]),
            }
        )

    monkeypatch.setattr(runtime_mod, "run_capability_request", _run_capability_request)

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        return StepExecutionResult(
            response="ready_for_review",
            artifacts={},
            status_code="ready_for_review",
            auto_continue=False,
        )

    runtime = BlackboardWorkflowRuntime(issue_dir=issue_dir, playbook=playbook, executor=executor)
    paused = runtime.run(start_step="spec")
    state = BlackboardStore(issue_dir).load_or_create("spec")
    records = HumanTaskRecordStore(issue_dir)
    task = records.tasks()[0]
    wait = records.get_wait_state(task.id)

    recovered = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=playbook, executor=executor
    ).run(max_transitions=2)
    restored = HumanTaskRecordStore(issue_dir)

    assert paused.completed is False
    assert recovered.completed is False
    assert restored.tasks()[0].id == task.id
    assert restored.get_wait_state(task.id) == wait
    assert state.workflow_id == task.workflow_id
    assert len(capability_calls) == 1
    request = capability_calls[0]["capability_request"]
    assert request["args"]["task_id"] == task.id
    assert request["args"]["workflow_id"] == task.workflow_id
    assert request["args"]["repository"] == tmp_path.name


def test_human_task_notification_routes_custom_git_worktrees_to_the_parent_repository(
    tmp_path: Path,
) -> None:
    """A route for the primary checkout covers a linked custom worktree."""
    from cafe.core.workflow_runtime import HumanTaskNotificationDispatcher

    repository = tmp_path / "main-repository"
    worktree = tmp_path / "custom-checkout"
    repository.mkdir()
    subprocess.run(("git", "init"), cwd=repository, check=True, capture_output=True, text=True)
    subprocess.run(
        ("git", "config", "user.email", "cafe-test@example.test"),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ("git", "config", "user.name", "CAFE Test"),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    (repository / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(
        ("git", "add", "README.md"),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ("git", "commit", "-m", "Initial"),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ("git", "worktree", "add", "--detach", str(worktree)),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    issue_dir = worktree / ".cafe" / "issues" / "issue-38"
    issue_dir.mkdir(parents=True)
    dispatcher = HumanTaskNotificationDispatcher(
        issue_dir=issue_dir,
        blackboard_store=SimpleNamespace(),
        blackboard=SimpleNamespace(),
    )

    assert dispatcher._repository_root() == repository.resolve()


def test_human_task_notification_ignores_git_environment_route_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Git environment variables cannot replace the linked-worktree route key."""
    from cafe.core.workflow_runtime import HumanTaskNotificationDispatcher

    repository = tmp_path / "main-repository"
    other_repository = tmp_path / "other-repository"
    worktree = tmp_path / "custom-checkout"
    for root in (repository, other_repository):
        root.mkdir()
        subprocess.run(("git", "init"), cwd=root, check=True, capture_output=True, text=True)
        subprocess.run(
            ("git", "config", "user.email", "cafe-test@example.test"),
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ("git", "config", "user.name", "CAFE Test"),
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        (root / "README.md").write_text("test\n", encoding="utf-8")
        subprocess.run(
            ("git", "add", "README.md"),
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ("git", "commit", "-m", "Initial"),
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    subprocess.run(
        ("git", "worktree", "add", "--detach", str(worktree)),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    issue_dir = worktree / ".cafe" / "issues" / "issue-38"
    issue_dir.mkdir(parents=True)
    monkeypatch.setenv("GIT_DIR", str(other_repository / ".git"))
    dispatcher = HumanTaskNotificationDispatcher(
        issue_dir=issue_dir,
        blackboard_store=SimpleNamespace(),
        blackboard=SimpleNamespace(),
    )

    assert dispatcher._repository_root() == repository.resolve()


def test_runtime_notifies_human_owned_creation_for_builtin_and_project_playbooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both builtin and project playbooks use the same registered capability."""
    import cafe.core.workflow_runtime as runtime_mod

    policy = HumanTaskPolicy(
        id="approval",
        pattern="no_changes_needed",
        prompt="Approve this work",
        input_schema="decision",
        decisions=(HumanTaskDecision(id="accept", label="Accept"),),
    )
    binding = HumanTaskBinding(trigger="initial", task_id="approval", outcomes={"accept": "done"})
    monkeypatch.setattr(runtime_mod, "resolve_step_human_task", lambda **_kwargs: (policy, binding))
    monkeypatch.setattr(runtime_mod, "load_capability_registry", lambda _dirs: {"registered": True})
    monkeypatch.setattr(runtime_mod, "default_capability_definition_dirs", lambda _root: [])
    calls: list[dict[str, object]] = []

    def _run_capability_request(**kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(receipt={"capability": "cafe.slack.human_task", "success": True})

    monkeypatch.setattr(runtime_mod, "run_capability_request", _run_capability_request)

    def _human_playbook(playbook_id: str) -> dict[str, object]:
        return {
            "playbook": {"id": playbook_id},
            "entry_point": "approval",
            "steps": {
                "approval": {
                    "skill": "phase",
                    "role": "operator",
                    "assignee_type": "human",
                    "human_tasks": [binding.model_dump()],
                    "on": {},
                }
            },
        }

    trusted_playbook = PlaybookLoader().load("standard")
    trusted_playbook.clear()
    trusted_playbook.update(_human_playbook("standard"))
    standard_dir = tmp_path / ".cafe" / "issues" / "human-standard"
    standard = BlackboardWorkflowRuntime(
        issue_dir=standard_dir,
        playbook=trusted_playbook,
        executor=lambda *_args: (_ for _ in ()).throw(AssertionError("human step ran agent")),
    )
    standard.run(start_step="approval")
    standard.run(max_transitions=2)

    project_dir = tmp_path / ".cafe" / "issues" / "spoofed-standard"
    BlackboardWorkflowRuntime(
        issue_dir=project_dir,
        playbook=_human_playbook("standard"),
        executor=lambda *_args: (_ for _ in ()).throw(AssertionError("human step ran agent")),
    ).run(start_step="approval")

    assert len(calls) == 2
    standard_task = HumanTaskRecordStore(standard_dir).tasks()[0]
    project_task = HumanTaskRecordStore(project_dir).tasks()[0]
    assert calls[0]["capability_request"]["args"]["task_id"] == standard_task.id
    assert calls[1]["capability_request"]["args"]["task_id"] == project_task.id
    assert calls[0]["timeout_sec"] == 5.0
    assert project_task.id != standard_task.id
    project_receipts = BlackboardStore(project_dir).load_or_create("approval").capability_receipts
    assert project_receipts[0]["task_id"] == project_task.id


def test_notification_failure_preserves_pending_task_and_user_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed delivery is audited but cannot consume or reroute human work."""
    import cafe.core.workflow_runtime as runtime_mod

    issue_dir = tmp_path / ".cafe" / "issues" / "notification-failure"
    playbook = PlaybookLoader().load("standard")
    monkeypatch.setattr(runtime_mod, "load_capability_registry", lambda _dirs: {"registered": True})
    monkeypatch.setattr(runtime_mod, "default_capability_definition_dirs", lambda _root: [])
    monkeypatch.setattr(
        runtime_mod,
        "run_capability_request",
        lambda **_kwargs: SimpleNamespace(
            receipt={
                "capability": "cafe.slack.human_task",
                "success": False,
                "code": "slack_transport_error",
            }
        ),
    )

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=lambda *_args: StepExecutionResult(
            response="ready_for_review",
            artifacts={},
            status_code="ready_for_review",
            auto_continue=False,
        ),
    ).run(start_step="spec")

    task = HumanTaskRecordStore(issue_dir).tasks()[0]
    state = BlackboardStore(issue_dir).load_or_create("spec")
    receipt = state.capability_receipts[0]

    assert result.completed is False
    assert task.status.value == "pending"
    assert state.current_step == "user"
    assert state.handoff_contract.to_owner is HandoffOwner.USER
    assert state.handoff_contract.from_step == "spec"
    assert receipt["success"] is False
    assert receipt["workflow_id"] == task.workflow_id
    assert receipt["task_id"] == task.id


def test_runtime_recovers_notification_when_task_commit_precedes_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unit Tests 7-8: recovery repairs a durable task with no begun attempt."""
    import cafe.core.workflow_runtime as runtime_mod

    issue_dir = tmp_path / ".cafe" / "issues" / "notification-before-attempt-stop"
    playbook = PlaybookLoader().load("standard")

    def _executor(*_args: object) -> StepExecutionResult:
        return StepExecutionResult(
            response="ready_for_review",
            artifacts={},
            status_code="ready_for_review",
            auto_continue=False,
        )

    interrupted = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=_executor,
    )
    monkeypatch.setattr(
        interrupted,
        "_notify_new_human_task",
        lambda _task: (_ for _ in ()).throw(SystemExit("simulated process stop")),
    )
    with pytest.raises(SystemExit, match="simulated process stop"):
        interrupted.run(start_step="spec")

    task = HumanTaskRecordStore(issue_dir).tasks()[0]
    assert BlackboardStore(issue_dir).load_or_create("spec").capability_receipts == []
    calls = []
    monkeypatch.setattr(runtime_mod, "load_capability_registry", lambda _dirs: {})
    monkeypatch.setattr(runtime_mod, "default_capability_definition_dirs", lambda _root: [])
    monkeypatch.setattr(
        runtime_mod,
        "run_capability_request",
        lambda **kwargs: (
            calls.append(kwargs)
            or SimpleNamespace(receipt={"capability": "cafe.slack.human_task", "success": True})
        ),
    )

    BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=PlaybookLoader().load("standard"),
        executor=_executor,
    ).run(start_step="spec")

    state = BlackboardStore(issue_dir).load_or_create("spec")
    assert len(calls) == 1
    assert len(state.capability_receipts) == 1
    assert state.capability_receipts[0]["task_id"] == task.id


def test_runtime_audits_interrupted_attempt_without_duplicate_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unit Tests 7-8: a begun attempt is durable before I/O and never duplicated."""
    import cafe.core.workflow_runtime as runtime_mod

    issue_dir = tmp_path / ".cafe" / "issues" / "notification-after-dispatch-stop"

    def _executor(*_args: object) -> StepExecutionResult:
        return StepExecutionResult(
            response="ready_for_review",
            artifacts={},
            status_code="ready_for_review",
            auto_continue=False,
        )

    monkeypatch.setattr(runtime_mod, "load_capability_registry", lambda _dirs: {})
    monkeypatch.setattr(runtime_mod, "default_capability_definition_dirs", lambda _root: [])

    def _stop_after_attempt_begins(**_kwargs: object):
        receipt = BlackboardStore(issue_dir).load_or_create("spec").capability_receipts[0]
        assert receipt["outcome"] == "attempting"
        raise SystemExit("simulated process stop")

    monkeypatch.setattr(runtime_mod, "run_capability_request", _stop_after_attempt_begins)
    with pytest.raises(SystemExit, match="simulated process stop"):
        BlackboardWorkflowRuntime(
            issue_dir=issue_dir,
            playbook=PlaybookLoader().load("standard"),
            executor=_executor,
        ).run(start_step="spec")

    dispatches = []
    monkeypatch.setattr(
        runtime_mod,
        "run_capability_request",
        lambda **kwargs: (
            dispatches.append(kwargs)
            or SimpleNamespace(receipt={"capability": "cafe.slack.human_task", "success": True})
        ),
    )
    BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=PlaybookLoader().load("standard"),
        executor=_executor,
    ).run(start_step="spec")

    task = HumanTaskRecordStore(issue_dir).tasks()[0]
    state = BlackboardStore(issue_dir).load_or_create("spec")
    assert dispatches == []
    assert len(state.capability_receipts) == 1
    assert state.capability_receipts[0]["code"] == "slack_notification_interrupted"
    assert state.capability_receipts[0]["task_id"] == task.id


def test_concurrent_stale_runtimes_claim_one_notification_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unit Tests 7-8: concurrent recovery has one dispatch and one audited attempt."""
    import cafe.core.workflow_runtime as runtime_mod

    issue_dir = tmp_path / ".cafe" / "issues" / "concurrent-notification-recovery"

    monkeypatch.setattr(runtime_mod, "load_capability_registry", lambda _dirs: {})
    monkeypatch.setattr(runtime_mod, "default_capability_definition_dirs", lambda _root: [])
    dispatches: list[dict[str, object]] = []
    monkeypatch.setattr(
        runtime_mod,
        "run_capability_request",
        lambda **kwargs: (
            dispatches.append(kwargs)
            or SimpleNamespace(receipt={"capability": "cafe.slack.human_task", "success": True})
        ),
    )

    runtimes = [
        BlackboardWorkflowRuntime(
            issue_dir=issue_dir,
            playbook=PlaybookLoader().load("standard"),
            executor=lambda *_args: None,
        )
        for _ in range(2)
    ]
    task = HumanTaskRecordStore(issue_dir).materialize(
        workflow_id=runtimes[0].blackboard.workflow_id,
        step="spec",
        iteration=1,
        trigger="output_ready",
        policy_id="output-review",
        prompt="Review the requirements specification and choose how to continue.",
        expected_result={"input_schema": "decision"},
        continuations={"agree": "plan"},
        assignee_type="human",
    )
    rendezvous = threading.Barrier(2)

    def _notify(runtime: BlackboardWorkflowRuntime) -> None:
        rendezvous.wait(timeout=5)
        runtime._notify_new_human_task(task)

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(_notify, runtime) for runtime in runtimes]
        for future in futures:
            future.result(timeout=10)

    state = BlackboardStore(issue_dir).load_or_create("spec")
    matching_receipts = [
        receipt
        for receipt in state.capability_receipts
        if receipt.get("capability") == "cafe.slack.human_task"
        and receipt.get("task_id") == task.id
    ]
    assert len(dispatches) == 1
    assert len(matching_receipts) == 2
    assert any(receipt.get("success") is True for receipt in matching_receipts)
    assert any(
        receipt.get("code") == "human_task_notification_deduplicated"
        for receipt in matching_receipts
    )


def test_independent_runtimes_claim_one_notification_attempt_across_processes(
    tmp_path: Path,
) -> None:
    """Unit Tests 7-8: process-level claim serialization permits one dispatch."""
    issue_dir = tmp_path / ".cafe" / "issues" / "process-notification-recovery"
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=PlaybookLoader().load("standard"),
        executor=lambda *_args: None,
    )
    task = HumanTaskRecordStore(issue_dir).materialize(
        workflow_id=runtime.blackboard.workflow_id,
        step="spec",
        iteration=1,
        trigger="output_ready",
        policy_id="output-review",
        prompt="Review the requirements specification and choose how to continue.",
        expected_result={"input_schema": "decision"},
        continuations={"agree": "plan"},
        assignee_type="human",
    )
    context = multiprocessing.get_context("spawn")
    rendezvous = context.Barrier(2)
    result_queue = context.Queue()
    workers = [
        context.Process(
            target=_notify_human_task_in_process,
            args=(str(issue_dir), rendezvous, result_queue),
        )
        for _ in range(2)
    ]

    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=15)

    assert [worker.exitcode for worker in workers] == [0, 0]
    results = [result_queue.get(timeout=5) for _ in workers]
    assert [result[0] for result in results] == ["ok", "ok"]
    assert sum(bool(result[1]) for result in results) == 1
    state = BlackboardStore(issue_dir).load_or_create("spec")
    matching_receipts = [
        receipt
        for receipt in state.capability_receipts
        if receipt.get("capability") == "cafe.slack.human_task"
        and receipt.get("task_id") == task.id
    ]
    assert len(matching_receipts) == 2
    assert any(receipt.get("success") is True for receipt in matching_receipts)
    assert any(
        receipt.get("code") == "human_task_notification_deduplicated"
        for receipt in matching_receipts
    )


def test_receipt_transaction_uses_windows_process_lock_without_fcntl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unit Test 7: the Windows fallback remains a cross-process file lock."""
    import cafe.core.blackboard as blackboard_mod

    lock_calls: list[tuple[int, int, int]] = []
    windows_lock = SimpleNamespace(
        LK_LOCK=1,
        LK_UNLCK=2,
        locking=lambda descriptor, mode, count: lock_calls.append((descriptor, mode, count)),
    )
    monkeypatch.setattr(blackboard_mod, "fcntl", None)
    monkeypatch.setattr(blackboard_mod, "msvcrt", windows_lock)
    store = BlackboardStore(tmp_path / "issue")
    state = store.load_or_create("spec")
    lock_calls.clear()

    with store.capability_receipt_transaction(state):
        assert [(mode, count) for _, mode, count in lock_calls] == [(1, 1)]

    assert [(mode, count) for _, mode, count in lock_calls] == [(1, 1), (2, 1)]
    assert lock_calls[0][0] == lock_calls[1][0]

    monkeypatch.setattr(blackboard_mod, "msvcrt", None)
    unavailable_store = BlackboardStore(tmp_path / "unavailable")
    unavailable_state = unavailable_store.load_or_create("spec")
    with pytest.raises(RuntimeError, match="cross-process file locking is unavailable"):
        with unavailable_store.capability_receipt_transaction(unavailable_state):
            pytest.fail("a process-local fallback must not enter the transaction")


def test_unavailable_process_lock_preserves_user_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unit Tests 7-8: no lock backend cannot block a durable user handoff."""
    import cafe.core.blackboard as blackboard_mod
    import cafe.core.workflow_runtime as runtime_mod

    monkeypatch.setattr(blackboard_mod, "fcntl", None)
    monkeypatch.setattr(blackboard_mod, "msvcrt", None)
    dispatches: list[dict[str, object]] = []
    monkeypatch.setattr(
        runtime_mod,
        "run_capability_request",
        lambda **kwargs: (
            dispatches.append(kwargs)
            or SimpleNamespace(receipt={"capability": "cafe.slack.human_task", "success": True})
        ),
    )
    issue_dir = tmp_path / ".cafe" / "issues" / "lock-unavailable-user-handoff"

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=PlaybookLoader().load("standard"),
        executor=lambda *_args: StepExecutionResult(
            response="ready_for_review",
            artifacts={},
            status_code="ready_for_review",
            auto_continue=False,
        ),
    ).run(start_step="spec")

    task = HumanTaskRecordStore(issue_dir).tasks()[0]
    state = BlackboardStore(issue_dir).load_or_create("spec")
    assert result.completed is False
    assert task.status.value == "pending"
    assert state.current_step == "user"
    assert state.handoff_contract.to_owner is HandoffOwner.USER
    assert dispatches == []


def test_windows_process_lock_failure_preserves_human_owned_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unit Tests 7-8: Windows lock failure cannot block human-owned work."""
    import cafe.core.blackboard as blackboard_mod
    import cafe.core.workflow_runtime as runtime_mod

    lock_calls: list[tuple[int, int, int]] = []

    def _fail_lock(descriptor: int, mode: int, count: int) -> None:
        lock_calls.append((descriptor, mode, count))
        raise OSError("simulated Windows process lock failure")

    monkeypatch.setattr(blackboard_mod, "fcntl", None)
    monkeypatch.setattr(
        blackboard_mod,
        "msvcrt",
        SimpleNamespace(LK_LOCK=1, LK_UNLCK=2, locking=_fail_lock),
    )
    dispatches: list[dict[str, object]] = []
    monkeypatch.setattr(
        runtime_mod,
        "run_capability_request",
        lambda **kwargs: (
            dispatches.append(kwargs)
            or SimpleNamespace(receipt={"capability": "cafe.slack.human_task", "success": True})
        ),
    )
    policy = HumanTaskPolicy(
        id="approval",
        pattern="no_changes_needed",
        prompt="Approve this work",
        input_schema="decision",
        decisions=(HumanTaskDecision(id="accept", label="Accept"),),
    )
    binding = HumanTaskBinding(trigger="initial", task_id="approval", outcomes={"accept": "done"})
    monkeypatch.setattr(runtime_mod, "resolve_step_human_task", lambda **_kwargs: (policy, binding))
    playbook = PlaybookLoader().load("standard")
    playbook.clear()
    playbook.update(
        {
            "playbook": {"id": "standard"},
            "entry_point": "approval",
            "steps": {
                "approval": {
                    "skill": "phase",
                    "role": "operator",
                    "assignee_type": "human",
                    "human_tasks": [binding.model_dump()],
                    "on": {},
                }
            },
        }
    )
    issue_dir = tmp_path / ".cafe" / "issues" / "lock-failure-human-owned"

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=lambda *_args: (_ for _ in ()).throw(AssertionError("human step ran agent")),
    ).run(start_step="approval")

    task = HumanTaskRecordStore(issue_dir).tasks()[0]
    state = BlackboardStore(issue_dir).load_or_create("approval")
    assert result.completed is False
    assert task.status.value == "pending"
    assert state.current_step == "user"
    assert state.handoff_contract.to_owner is HandoffOwner.USER
    assert lock_calls
    assert {(mode, count) for _, mode, count in lock_calls} == {(1, 1)}
    assert dispatches == []


def test_runtime_records_non_actionable_configuration_error_for_bad_task_binding(
    tmp_path: Path,
) -> None:
    """IT-005: an unresolved declared task never creates an actionable wait."""
    issue_dir = tmp_path / ".cafe" / "issues" / "durable-config-error"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {
                "skill": "cafe-spec",
                "human_tasks": [],
                "on": {"confirm_output": "spec"},
            }
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        return StepExecutionResult(
            response="ready_for_review",
            artifacts={},
            status_code="ready_for_review",
            auto_continue=False,
        )

    BlackboardWorkflowRuntime(issue_dir=issue_dir, playbook=playbook, executor=executor).run(
        start_step="spec"
    )

    records = HumanTaskRecordStore(issue_dir)
    assert records.tasks() == ()
    assert "configuration_error" in [event.event_type for event in records.lifecycle_events()]


def test_runtime_pauses_brief_ready_for_review_with_confirm_output_intent(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "brief-confirm-pause"
    playbook = {
        "playbook": {"id": "editorial"},
        "steps": {
            "brief": {
                "skill": "brief_first",
                "role": "editor",
                "valid_intents": ["ready_for_review", "confirmed"],
                "on": {"confirm_output": "brief", "await_agent": "draft"},
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
    result = runtime.run(start_step="brief")

    assert result.completed is False
    assert result.final_status_code == "ready_for_review"
    blackboard = BlackboardStore(issue_dir).load_or_create("brief")
    assert blackboard.current_step == "user"
    assert blackboard.handoff_contract is not None
    assert blackboard.handoff_contract.intent == HandoffIntent.CONFIRM_OUTPUT


def test_runtime_ready_for_review_without_confirm_output_uses_manual_handoff(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "develop-review-no-confirm"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "develop": {
                "skill": "develop",
                "role": "developer",
                "valid_intents": ["ready_for_review", "confirmed"],
                "on": {"await_agent": "review", "manual_handoff": "develop"},
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
    result = runtime.run(start_step="develop")

    assert result.completed is False
    blackboard = BlackboardStore(issue_dir).load_or_create("develop")
    assert blackboard.handoff_contract is not None
    assert blackboard.handoff_contract.intent == HandoffIntent.MANUAL_HANDOFF


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
            "pr": {
                "skill": "spec_first",
                "role": "developer",
                "behavior": {"completion": "baton", "publish_confirmation": True},
                "capability_requests": ["cafe.pr.publish"],
                "on": {"await_agent": "_done"},
            },
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
        e
        for e in legacy_state.events
        if e.event_type == "step_started" and e.data.get("step") == "review"
    ]
    review_completed = [
        e
        for e in legacy_state.events
        if e.event_type == "step_completed" and e.data.get("step") == "review"
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
            "pr": {
                "skill": "spec_first",
                "role": "developer",
                "behavior": {"completion": "baton", "publish_confirmation": True},
                "capability_requests": ["cafe.pr.publish"],
                "on": {"await_agent": "_done"},
            },
        },
    }

    def pr_executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        _write_baton(
            issue_dir_pr,
            from_step="pr",
            to_owner="done",
            to_step="done",
            intent="workflow_complete",
        )
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
                "playbook_id": "standard",
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
            "pr": {
                "skill": "spec_first",
                "role": "developer",
                "assignee_type": "agent",
                "behavior": {"completion": "baton", "feedback_target": "develop"},
                "on": {},
            },
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
                "on": {"await_agent": "_done", "manual_handoff": "user"},
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


def test_runtime_rejects_plain_text_baton_written_by_pr_agent(tmp_path: Path) -> None:
    """Issue #386: a plain step-name baton is never normalized at the PR boundary."""
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
        raise AssertionError(f"unexpected step {step_name}")

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )

    with pytest.raises(ValueError):
        runtime.run(start_step="pr", max_transitions=5)

    assert calls == ["pr"]


def test_runtime_handles_keyboard_interrupt(tmp_path: Path) -> None:
    """KeyboardInterrupt records step_interrupted and returns INTERRUPTED."""
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
    bb = BlackboardStore(issue_dir).load_or_create("spec", playbook_id="standard")
    interrupted_events = [e for e in bb.events if e.event_type == "step_interrupted"]
    assert len(interrupted_events) == 1
    msg = (
        json.loads(interrupted_events[0].message)
        if isinstance(interrupted_events[0].message, str)
        else interrupted_events[0].message
    )
    assert msg["step"] == "spec"
    assert bb.step_attempt_counts == {}


def test_runtime_handles_agent_execution_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AgentExecutionError pauses for a notified retry task instead of inferring completion."""
    from cafe.agents.executor import AgentExecutionError
    from cafe.ui.human_tasks import apply_human_task_payload

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

    observer_events: list[dict[str, object]] = []
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
        workflow_event_observer=observer_events.append,
    )
    notifications = []
    monkeypatch.setattr(runtime, "_notify_new_human_task", notifications.append)

    result = runtime.run(start_step="spec", max_transitions=5)

    assert result.completed is False
    assert "agent_rate_limit" in result.final_status_code
    assert result.final_step == "spec"

    bb = BlackboardStore(issue_dir).load_or_create("spec", playbook_id="standard")
    interrupted_events = [e for e in bb.events if e.event_type == "step_interrupted"]
    assert len(interrupted_events) == 1
    msg = (
        json.loads(interrupted_events[0].message)
        if isinstance(interrupted_events[0].message, str)
        else interrupted_events[0].message
    )
    assert msg["step"] == "spec"
    assert msg["reason"] == "agent_rate_limit"
    assert bb.step_attempt_counts == {}
    assert bb.current_step == "user"
    assert bb.handoff_contract is not None
    assert bb.handoff_contract.to_owner is HandoffOwner.USER
    assert bb.handoff_contract.source == "workflow.agent_execution_interrupted"

    task = HumanTaskRecordStore(issue_dir).tasks()[0]
    assert task.step == "spec"
    assert task.trigger == "agent_execution_interrupted"
    assert task.policy_id == "agent-execution-interrupted"
    assert task.continuations == {"retry": "spec"}
    assert notifications == [task]
    assert observer_events == [
        {
            "workflow_id": bb.workflow_id,
            "issue": "demo-agent-error",
            "event_type": "human_task",
            "step": "spec",
            "status_code": "INTERRUPTED:agent_rate_limit",
            "reason": "agent_rate_limit",
            "task_id": task.id,
        }
    ]
    assert not any(event.event_type == "step_reconciled" for event in bb.events)

    applied = apply_human_task_payload(
        issue_dir=issue_dir,
        playbook_data=playbook,
        blackboard=bb,
        from_step="spec",
        trigger=task.trigger,
        raw_payload={
            "task": task.policy_id,
            "decision": "retry",
            "human_task_id": task.id,
        },
        source="test",
    )

    assert applied.target == "spec"
    resumed = BlackboardStore(issue_dir).load_or_create("spec", playbook_id="standard")
    assert resumed.current_step == "spec"
    assert resumed.handoff_contract is not None
    assert resumed.handoff_contract.to_owner is HandoffOwner.AGENT
    assert resumed.handoff_contract.to_step == "spec"
    assert runtime._try_reconcile_current_step(current_step="spec") is None


def test_runtime_does_not_reconcile_agent_error_after_valid_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An agent process error always pauses for review, even after partial handoff evidence."""
    from cafe.agents.executor import AgentExecutionError

    issue_dir = tmp_path / ".cafe" / "issues" / "demo-reconcile-agent-error"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {"skill": "spec_first", "role": "developer", "on": {"await_agent": "plan"}},
            "plan": {"skill": "plan", "role": "developer", "on": {"await_agent": "_done"}},
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        if step_name == "spec":
            _write_baton(
                issue_dir,
                from_step="spec",
                to_owner="agent",
                to_step="plan",
                intent="await_agent",
                status_code="confirmed",
            )
            _write_iteration_evidence(issue_dir, "spec")
            raise AgentExecutionError("Connection stalled", error_type="connection_stalled")
        return StepExecutionResult(response="done", artifacts={}, status_code="confirmed")

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    notifications = []
    monkeypatch.setattr(runtime, "_notify_new_human_task", notifications.append)

    result = runtime.run(start_step="spec", max_transitions=5)

    assert result.completed is False
    assert result.final_step == "spec"
    assert result.final_status_code == "INTERRUPTED:agent_connection_stalled"

    bb = BlackboardStore(issue_dir).load_or_create("spec", playbook_id="standard")
    assert bb.current_step == "user"
    assert not any(e.event_type == "step_reconciled" for e in bb.events)
    task = HumanTaskRecordStore(issue_dir).tasks()[0]
    assert task.continuations == {"retry": "spec"}
    assert notifications == [task]


def test_runtime_preserves_interrupted_when_reconciliation_evidence_incomplete(
    tmp_path: Path,
) -> None:
    """An incomplete agent error pauses with a retry task rather than a stale baton."""
    from cafe.agents.executor import AgentExecutionError

    issue_dir = tmp_path / ".cafe" / "issues" / "demo-reconcile-incomplete"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {"skill": "spec_first", "role": "developer", "on": {"await_agent": "plan"}},
            "plan": {"skill": "plan", "role": "developer", "on": {"await_agent": "_done"}},
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        _write_baton(
            issue_dir,
            from_step="spec",
            to_owner="agent",
            to_step="plan",
            intent="await_agent",
            status_code="confirmed",
        )
        _write_iteration_evidence(issue_dir, "spec", checklist="- [ ] unfinished\n")
        raise AgentExecutionError("Connection stalled", error_type="connection_stalled")

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )

    result = runtime.run(start_step="spec", max_transitions=5)

    assert result.completed is False
    assert result.final_status_code == "INTERRUPTED:agent_connection_stalled"

    bb = BlackboardStore(issue_dir).load_or_create("spec", playbook_id="standard")
    assert bb.current_step == "user"
    assert HumanTaskRecordStore(issue_dir).tasks()[0].continuations == {"retry": "spec"}
    assert not any(e.event_type == "step_reconciliation_failed" for e in bb.events)
    assert any(
        e.event_type == "workflow_paused" and e.data.get("status_code") == "INTERRUPTED"
        for e in bb.events
    )


def test_runtime_resume_reconciliation_is_idempotent(tmp_path: Path) -> None:
    """Resume-time reconciliation repairs an interrupted handoff once."""
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-reconcile-resume"
    issue_dir.mkdir(parents=True)
    _write_baton(
        issue_dir,
        from_step="spec",
        to_owner="agent",
        to_step="plan",
        intent="await_agent",
        status_code="confirmed",
    )
    _write_iteration_evidence(issue_dir, "spec")
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "current_step": "spec",
                "playbook_id": "standard",
                "artifacts": {},
                "events": [
                    {
                        "timestamp": "2026-04-26T23:00:00+08:00",
                        "step": "spec",
                        "event_type": "step_interrupted",
                        "message": "{}",
                        "data": {"step": "spec", "reason": "agent_connection_stalled"},
                    }
                ],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {"skill": "spec_first", "role": "developer", "on": {"await_agent": "plan"}},
            "plan": {"skill": "plan", "role": "developer", "on": {"await_agent": "_done"}},
        },
    }

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=lambda *_args, **_kwargs: StepExecutionResult(
            response="confirmed", artifacts={}, status_code="confirmed"
        ),
    )

    first = runtime._try_resume_reconcile_interrupted_handoff(runtime_label="legacy_until_boundary")
    second = runtime._try_resume_reconcile_interrupted_handoff(
        runtime_label="legacy_until_boundary"
    )

    assert first is not None
    assert second is None
    bb = BlackboardStore(issue_dir).load_or_create("spec", playbook_id="standard")
    assert bb.current_step == "plan"
    assert [e.event_type for e in bb.events].count("step_reconciled") == 1


def test_runtime_reconciles_after_consumed_handoff_start_step(tmp_path: Path) -> None:
    """Normal workflow resume repairs a consumed downstream baton before running target."""
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-reconcile-consumed"
    issue_dir.mkdir(parents=True)
    _write_baton(
        issue_dir,
        from_step="spec",
        to_owner="agent",
        to_step="plan",
        intent="await_agent",
        status_code="confirmed",
        source="workflow.consume_handoff",
    )
    _write_iteration_evidence(issue_dir, "spec")
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "current_step": "plan",
                "playbook_id": "standard",
                "artifacts": {},
                "events": [
                    {
                        "timestamp": "2026-04-26T23:00:00+08:00",
                        "step": "spec",
                        "event_type": "step_interrupted",
                        "message": "{}",
                        "data": {"step": "spec", "reason": "agent_connection_stalled"},
                    }
                ],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {"skill": "spec_first", "role": "developer", "on": {"await_agent": "plan"}},
            "plan": {"skill": "plan", "role": "developer", "on": {"confirmed": "_done"}},
        },
    }
    executed_steps: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        executed_steps.append(step_name)
        return StepExecutionResult(response="confirmed", artifacts={}, status_code="confirmed")

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )

    result = runtime.run(start_step="plan", max_transitions=1)

    assert executed_steps == ["plan"]
    assert result.final_step == "plan"
    bb = BlackboardStore(issue_dir).load_or_create("spec", playbook_id="standard")
    assert [e.event_type for e in bb.events].count("step_reconciled") == 1
    reconciled_event = next(e for e in bb.events if e.event_type == "step_reconciled")
    assert reconciled_event.data["step"] == "spec"
    assert reconciled_event.data["to_step"] == "plan"
    iteration_data = json.loads(
        (issue_dir / "spec" / "iteration_001" / "iteration.json").read_text()
    )
    assert iteration_data["status_code"] == "confirmed"
    assert iteration_data["end_time"]


def test_downstream_handoff_must_declare_await_agent_intent(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-strict-downstream-intent"
    issue_dir.mkdir(parents=True, exist_ok=True)
    _write_baton(
        issue_dir,
        from_step="develop",
        to_owner="agent",
        to_step="review",
        intent="await_agent",
        source="test",
    )
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "develop": {"skill": "develop", "role": "developer", "on": {"confirmed": "review"}},
            "review": {"skill": "review", "role": "developer", "on": {"confirmed": "_done"}},
        },
    }
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=lambda *_args, **_kwargs: StepExecutionResult(response="", artifacts={}),
    )

    with pytest.raises(BatonRejected) as excinfo:
        runtime._load_step_handoff_contract(current_step="develop")

    assert excinfo.value.field == "intent"
    assert excinfo.value.invalid_value == "await_agent"


# ---------------------------------------------------------------------------
# extra_prompt 傳遞測試
# ---------------------------------------------------------------------------


def _simple_playbook(step_name: str = "spec") -> dict:
    return {
        "playbook": {"id": "default"},
        "steps": {
            step_name: {
                "skill": "spec_first",
                "role": "pm",
                "on": {"await_agent": "_done"},
            },
        },
    }


def test_bundled_review_iteration_limits_are_defined_by_playbooks() -> None:
    loader = PlaybookLoader()

    assert loader.load("standard")["steps"]["review"]["max_attempts_per_cycle"] == 5
    assert loader.load("tdd")["steps"]["review"]["max_attempts_per_cycle"] == 5


def test_pre_execution_failure_does_not_consume_agent_visit(tmp_path: Path) -> None:
    from cafe.agents.executor import AgentExecutionError

    issue_dir = tmp_path / ".cafe" / "issues" / "pre-execution-failure"
    playbook = _simple_playbook()
    playbook["steps"]["spec"]["max_attempts_per_cycle"] = 1
    calls = 0

    def executor(step_name: str, step_def: dict, state: object, **kwargs) -> StepExecutionResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise AgentExecutionError("contract preflight failed", error_type="contract")
        _make_valid_baton_text(issue_dir)
        return StepExecutionResult(response="done", artifacts={})

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )

    interrupted = runtime.run(start_step="spec", max_transitions=5)
    assert interrupted.final_status_code == "INTERRUPTED:agent_contract"
    assert BlackboardStore(issue_dir).load_or_create("spec").step_attempt_counts == {}

    completed = runtime.run(start_step="spec", max_transitions=5)
    assert completed.completed is True
    assert BlackboardStore(issue_dir).load_or_create("spec").step_attempt_counts == {"spec": 1}


def test_execute_one_iteration_forwards_extra_prompt_to_executor(tmp_path: Path) -> None:
    """executor 被呼叫時應收到 extra_prompt kwarg（傳入值）。"""
    issue_dir = tmp_path / ".cafe" / "issues" / "extra-prompt-1"
    received_kwargs: list[dict] = []

    def executor(step_name: str, step_def: dict, state: object, **kwargs) -> StepExecutionResult:
        received_kwargs.append(kwargs)
        return StepExecutionResult(response="done", artifacts={}, status_code="confirmed")

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_simple_playbook(),
        executor=executor,
    )
    runtime.run(start_step="spec", max_transitions=5)

    assert len(received_kwargs) >= 1
    assert received_kwargs[0].get("extra_prompt") is None


def test_execute_one_iteration_no_extra_prompt_defaults_to_none(tmp_path: Path) -> None:
    """extra_prompt 未傳入時 executor 收到 extra_prompt=None。"""
    issue_dir = tmp_path / ".cafe" / "issues" / "extra-prompt-2"
    received_extra_prompts: list = []

    def executor(step_name: str, step_def: dict, state: object, **kwargs) -> StepExecutionResult:
        received_extra_prompts.append(kwargs.get("extra_prompt"))
        return StepExecutionResult(response="done", artifacts={}, status_code="confirmed")

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_simple_playbook(),
        executor=executor,
    )
    runtime.run(start_step="spec", max_transitions=5)

    assert received_extra_prompts[0] is None


# ---------------------------------------------------------------------------
# reject-and-retry 機制測試
# ---------------------------------------------------------------------------


def _make_valid_baton_text(
    issue_dir: Path,
    *,
    from_step: str = "spec",
    to_step: str = "done",
    intent: str = "workflow_complete",
) -> None:
    """寫入合法 baton 到 next_step.txt。"""
    to_owner = "done" if to_step == "done" else ("user" if to_step == "user" else "agent")
    (issue_dir / "next_step.txt").write_text(
        json.dumps(
            {
                "version": 1,
                "from_step": from_step,
                "to_owner": to_owner,
                "to_step": to_step,
                "intent": intent,
                "status_code": "",
                "created_at": "2026-05-14T10:00:00+08:00",
                "source": "test",
            }
        ),
        encoding="utf-8",
    )


def _make_invalid_baton_text(issue_dir: Path) -> None:
    """寫入 to_owner='human'（無效）的 baton。"""
    (issue_dir / "next_step.txt").write_text(
        json.dumps(
            {
                "version": 1,
                "from_step": "spec",
                "to_owner": "human",
                "to_step": "user",
                "intent": "need_clarification",
                "status_code": "",
                "created_at": "2026-05-14T10:00:00+08:00",
                "source": "test",
            }
        ),
        encoding="utf-8",
    )


def _make_invalid_target_baton_text(
    issue_dir: Path, *, from_step: str = "spec", to_step: str = "release"
) -> None:
    """Write a baton whose target step does not exist in the playbook."""
    _write_baton(
        issue_dir,
        from_step=from_step,
        to_owner="agent",
        to_step=to_step,
        intent="await_agent",
    )


def _make_missing_intent_baton_text(
    issue_dir: Path, *, from_step: str = "spec", to_step: str = "done"
) -> None:
    """Write JSON baton payload missing `intent`."""
    (issue_dir / "next_step.txt").write_text(
        json.dumps(
            {
                "version": 1,
                "from_step": from_step,
                "to_owner": "done" if to_step == "done" else "agent",
                "to_step": to_step,
                "status_code": "",
                "created_at": "2026-05-14T10:00:00+08:00",
                "source": "test",
            }
        ),
        encoding="utf-8",
    )


def test_runtime_retries_once_on_baton_rejected_then_succeeds(tmp_path: Path) -> None:
    """第一次 baton 無效、第二次合法時 workflow 繼續並記錄 rejection。"""
    issue_dir = tmp_path / ".cafe" / "issues" / "retry-1"
    issue_dir.mkdir(parents=True)
    call_count = [0]

    def executor(step_name: str, step_def: dict, state: object, **kwargs) -> StepExecutionResult:
        call_count[0] += 1
        if call_count[0] == 1:
            _make_invalid_baton_text(issue_dir)
        else:
            _make_valid_baton_text(
                issue_dir, from_step="spec", to_step="done", intent="workflow_complete"
            )
        return StepExecutionResult(response="done", artifacts={}, status_code="")

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_simple_playbook(),
        executor=executor,
    )
    result = runtime.run(start_step="spec", max_transitions=5)

    assert result.completed is True
    bb = BlackboardStore(issue_dir).load_or_create("spec")
    rejected_events = [e for e in bb.events if e.event_type == "baton_rejected"]
    assert len(rejected_events) == 1


def test_runtime_retries_user_owner_with_step_target_then_succeeds(tmp_path: Path) -> None:
    """Semantic owner/target mismatches use the declared baton retry loop."""
    issue_dir = tmp_path / ".cafe" / "issues" / "retry-owner-target"
    issue_dir.mkdir(parents=True)
    prompts: list[str | None] = []

    def executor(step_name: str, step_def: dict, state: object, **kwargs) -> StepExecutionResult:
        prompts.append(kwargs.get("extra_prompt"))
        if len(prompts) == 1:
            _write_baton(
                issue_dir,
                from_step="spec",
                to_owner="user",
                to_step="spec",
                intent="need_clarification",
            )
        else:
            _make_valid_baton_text(
                issue_dir,
                from_step="spec",
                to_step="done",
                intent="workflow_complete",
            )
        return StepExecutionResult(response="done", artifacts={}, status_code="")

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_simple_playbook(),
        executor=executor,
    ).run(start_step="spec", max_transitions=5)

    assert result.completed is True
    assert len(prompts) == 2
    assert "field 'to_step'" in str(prompts[1])
    blackboard = BlackboardStore(issue_dir).load_or_create("spec")
    assert blackboard.step_attempt_counts == {"spec": 1}


def test_runtime_retries_owner_intent_mismatch_then_succeeds(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "retry-owner-intent"
    issue_dir.mkdir(parents=True)
    prompts: list[str | None] = []

    def executor(step_name: str, step_def: dict, state: object, **kwargs) -> StepExecutionResult:
        prompts.append(kwargs.get("extra_prompt"))
        if len(prompts) == 1:
            _write_baton(
                issue_dir,
                from_step="spec",
                to_owner="done",
                to_step="done",
                intent="need_clarification",
            )
        else:
            _make_valid_baton_text(issue_dir)
        return StepExecutionResult(response="done", artifacts={}, status_code="")

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_simple_playbook(),
        executor=executor,
    ).run(start_step="spec", max_transitions=5)

    assert result.completed is True
    assert "field 'intent'" in str(prompts[1])
    assert "workflow_complete" in str(prompts[1])
    assert BlackboardStore(issue_dir).load_or_create("spec").step_attempt_counts == {"spec": 1}


def test_runtime_retries_twice_on_baton_rejected_then_succeeds(tmp_path: Path) -> None:
    """前兩次 baton 無效、第三次合法時 workflow 繼續並記錄兩次 rejection。"""
    issue_dir = tmp_path / ".cafe" / "issues" / "retry-2"
    issue_dir.mkdir(parents=True)
    call_count = [0]

    def executor(step_name: str, step_def: dict, state: object, **kwargs) -> StepExecutionResult:
        call_count[0] += 1
        if call_count[0] <= 2:
            _make_invalid_baton_text(issue_dir)
        else:
            _make_valid_baton_text(
                issue_dir, from_step="spec", to_step="done", intent="workflow_complete"
            )
        return StepExecutionResult(response="done", artifacts={}, status_code="")

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_simple_playbook(),
        executor=executor,
    )
    result = runtime.run(start_step="spec", max_transitions=5)

    assert result.completed is True
    bb = BlackboardStore(issue_dir).load_or_create("spec")
    rejected_events = [e for e in bb.events if e.event_type == "baton_rejected"]
    assert len(rejected_events) == 2


def test_runtime_retries_invalid_target_step_then_succeeds(tmp_path: Path) -> None:
    """If an agent writes to_step outside the playbook, retry with baton feedback."""
    issue_dir = tmp_path / ".cafe" / "issues" / "retry-invalid-target"
    issue_dir.mkdir(parents=True)
    captured_prompts: list[str | None] = []
    retry_flags: list[bool] = []
    call_count = [0]

    def executor(step_name: str, step_def: dict, state: object, **kwargs) -> StepExecutionResult:
        call_count[0] += 1
        captured_prompts.append(kwargs.get("extra_prompt"))
        retry_flags.append(bool(kwargs.get("same_invocation_retry")))
        if call_count[0] == 1:
            _make_invalid_target_baton_text(issue_dir, from_step="spec", to_step="release")
        else:
            _make_valid_baton_text(
                issue_dir, from_step="spec", to_step="done", intent="workflow_complete"
            )
        return StepExecutionResult(response="done", artifacts={}, status_code="")

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_simple_playbook(),
        executor=executor,
    )
    result = runtime.run(start_step="spec", max_transitions=5)

    assert result.completed is True
    assert len(captured_prompts) == 2
    assert captured_prompts[0] is None
    assert "to_step" in (captured_prompts[1] or "")
    assert "release" in (captured_prompts[1] or "")
    assert retry_flags == [False, True]
    bb = BlackboardStore(issue_dir).load_or_create("spec")
    rejected_events = [e for e in bb.events if e.event_type == "baton_rejected"]
    assert len(rejected_events) == 1
    assert rejected_events[0].data["field"] == "to_step"
    assert rejected_events[0].data["invalid_value"] == "release"


def test_runtime_retries_missing_required_field_then_succeeds(tmp_path: Path) -> None:
    """若 structured JSON 缺欄位，runtime 應要求修正而非 fallback 到 legacy。"""
    issue_dir = tmp_path / ".cafe" / "issues" / "retry-missing-field"
    issue_dir.mkdir(parents=True)
    captured_prompts: list[str | None] = []
    call_count = [0]

    def executor(step_name: str, step_def: dict, state: object, **kwargs) -> StepExecutionResult:
        call_count[0] += 1
        captured_prompts.append(kwargs.get("extra_prompt"))
        if call_count[0] == 1:
            _make_missing_intent_baton_text(issue_dir, from_step="spec", to_step="done")
        else:
            _make_valid_baton_text(
                issue_dir, from_step="spec", to_step="done", intent="workflow_complete"
            )
        return StepExecutionResult(response="done", artifacts={}, status_code="")

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_simple_playbook(),
        executor=executor,
    )
    result = runtime.run(start_step="spec", max_transitions=5)

    assert result.completed is True
    assert len(captured_prompts) == 2
    assert captured_prompts[0] is None
    assert "field 'intent'" in (captured_prompts[1] or "")
    assert "missing" in (captured_prompts[1] or "").lower()
    bb = BlackboardStore(issue_dir).load_or_create("spec")
    rejected_events = [e for e in bb.events if e.event_type == "baton_rejected"]
    assert len(rejected_events) == 1
    assert rejected_events[0].data["field"] == "intent"


def test_runtime_rejects_no_status_legacy_text_as_status_transition(tmp_path: Path) -> None:
    """Issue #386: plain-text next_step.txt is rejected in the core runtime path, never accepted."""
    issue_dir = tmp_path / ".cafe" / "issues" / "legacy-status-text"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {
                "skill": "spec_first",
                "role": "pm",
                "valid_intents": ["confirmed"],
                "on": {"confirmed": "plan"},
            },
            "plan": {
                "skill": "spec_first",
                "role": "developer",
                "valid_intents": ["confirmed"],
                "on": {"confirmed": "_done"},
            },
        },
    }

    visited_steps: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        visited_steps.append(step_name)
        if step_name == "spec":
            (issue_dir / "next_step.txt").write_text("plan", encoding="utf-8")
            return StepExecutionResult(response="", artifacts={}, status_code="")
        _make_valid_baton_text(
            issue_dir, from_step="plan", to_step="done", intent="workflow_complete"
        )
        return StepExecutionResult(response="", artifacts={}, status_code="")

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )

    with pytest.raises(ValueError):
        runtime.run(start_step="spec", max_transitions=5)

    assert visited_steps == ["spec"]


def test_runtime_retries_same_phase_baton_then_succeeds(tmp_path: Path) -> None:
    """If an agent points the baton back to the same phase, retry with baton feedback."""
    issue_dir = tmp_path / ".cafe" / "issues" / "retry-same-phase"
    issue_dir.mkdir(parents=True)
    captured_prompts: list[str | None] = []
    call_count = [0]

    def executor(step_name: str, step_def: dict, state: object, **kwargs) -> StepExecutionResult:
        call_count[0] += 1
        captured_prompts.append(kwargs.get("extra_prompt"))
        if call_count[0] == 1:
            _write_baton(
                issue_dir, from_step="spec", to_owner="agent", to_step="spec", intent="await_agent"
            )
        else:
            _make_valid_baton_text(
                issue_dir, from_step="spec", to_step="done", intent="workflow_complete"
            )
        return StepExecutionResult(response="done", artifacts={}, status_code="")

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_simple_playbook(),
        executor=executor,
    )
    result = runtime.run(start_step="spec", max_transitions=5)

    assert result.completed is True
    assert len(captured_prompts) == 2
    assert captured_prompts[0] is None
    assert "cannot point back to the same phase" in (captured_prompts[1] or "")
    bb = BlackboardStore(issue_dir).load_or_create("spec")
    rejected_events = [e for e in bb.events if e.event_type == "baton_rejected"]
    assert len(rejected_events) == 1
    assert rejected_events[0].data["field"] == "to_step"
    assert rejected_events[0].data["invalid_value"] == "spec"


def test_runtime_retries_same_phase_baton_for_pr_then_succeeds(tmp_path: Path) -> None:
    """Baton-driven PR steps should also retry same-phase handoffs instead of pausing."""
    issue_dir = tmp_path / ".cafe" / "issues" / "retry-pr-same-phase"
    issue_dir.mkdir(parents=True)
    captured_prompts: list[str | None] = []
    call_count = [0]

    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "pr": {"skill": "spec_first", "role": "developer", "on": {"await_agent": "_done"}},
        },
    }

    def executor(step_name: str, step_def: dict, state: object, **kwargs) -> StepExecutionResult:
        call_count[0] += 1
        captured_prompts.append(kwargs.get("extra_prompt"))
        if call_count[0] == 1:
            _write_baton(
                issue_dir, from_step="pr", to_owner="agent", to_step="pr", intent="await_agent"
            )
            return StepExecutionResult(response="stale", artifacts={"pr_result": "p1"})
        _write_baton(
            issue_dir, from_step="pr", to_owner="done", to_step="done", intent="workflow_complete"
        )
        return StepExecutionResult(
            response="done",
            artifacts={"pr_result": "p1"},
            events=[{"type": "pr_synced", "url": "https://github.com/test/repo/pull/277"}],
        )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="pr", max_transitions=5)

    assert result.completed is True
    assert len(captured_prompts) == 2
    assert captured_prompts[0] is None
    assert "cannot point back to the same phase" in (captured_prompts[1] or "")
    bb = BlackboardStore(issue_dir).load_or_create("pr")
    rejected_events = [e for e in bb.events if e.event_type == "baton_rejected"]
    assert len(rejected_events) == 1
    assert rejected_events[0].data["field"] == "to_step"
    assert rejected_events[0].data["invalid_value"] == "pr"


def test_runtime_crashes_after_three_baton_rejected(tmp_path: Path) -> None:
    """原始 + 2 次 retry 共 3 次都無效 → RuntimeError。"""
    issue_dir = tmp_path / ".cafe" / "issues" / "retry-3"
    issue_dir.mkdir(parents=True)

    def executor(step_name: str, step_def: dict, state: object, **kwargs) -> StepExecutionResult:
        _make_invalid_baton_text(issue_dir)
        return StepExecutionResult(response="done", artifacts={}, status_code="")

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_simple_playbook(),
        executor=executor,
    )
    with pytest.raises(RuntimeError):
        runtime.run(start_step="spec", max_transitions=5)


def test_runtime_baton_rejected_event_has_correct_fields(tmp_path: Path) -> None:
    """baton_rejected 事件 data 必須含 field、invalid_value、valid_values、retry。"""
    issue_dir = tmp_path / ".cafe" / "issues" / "retry-event"
    issue_dir.mkdir(parents=True)
    call_count = [0]

    def executor(step_name: str, step_def: dict, state: object, **kwargs) -> StepExecutionResult:
        call_count[0] += 1
        if call_count[0] == 1:
            _make_invalid_baton_text(issue_dir)
        else:
            _make_valid_baton_text(
                issue_dir, from_step="spec", to_step="done", intent="workflow_complete"
            )
        return StepExecutionResult(response="done", artifacts={}, status_code="")

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_simple_playbook(),
        executor=executor,
    )
    runtime.run(start_step="spec", max_transitions=5)

    bb = BlackboardStore(issue_dir).load_or_create("spec")
    rejected_events = [e for e in bb.events if e.event_type == "baton_rejected"]
    assert len(rejected_events) == 1
    data = rejected_events[0].data
    assert "field" in data
    assert "invalid_value" in data
    assert "valid_values" in data
    assert "retry" in data


def test_runtime_retry_extra_prompt_contains_feedback(tmp_path: Path) -> None:
    """重試時傳給 executor 的 extra_prompt 應包含欄位名、無效值、合法值清單。"""
    issue_dir = tmp_path / ".cafe" / "issues" / "retry-prompt"
    issue_dir.mkdir(parents=True)
    call_count = [0]
    captured_prompts: list = []

    def executor(step_name: str, step_def: dict, state: object, **kwargs) -> StepExecutionResult:
        call_count[0] += 1
        captured_prompts.append(kwargs.get("extra_prompt"))
        if call_count[0] == 1:
            _make_invalid_baton_text(issue_dir)
        else:
            _make_valid_baton_text(
                issue_dir, from_step="spec", to_step="done", intent="workflow_complete"
            )
        return StepExecutionResult(response="done", artifacts={}, status_code="")

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_simple_playbook(),
        executor=executor,
    )
    runtime.run(start_step="spec", max_transitions=5)

    assert len(captured_prompts) == 2
    assert captured_prompts[0] is None
    retry_prompt = captured_prompts[1]
    assert retry_prompt is not None
    assert "to_owner" in retry_prompt
    assert "human" in retry_prompt
    assert "agent" in retry_prompt
    assert "Retry in baton-only mode" in retry_prompt


def test_runtime_plan_need_permission_pauses_at_user(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "plan-need-permission"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "plan": {
                "skill": "plan",
                "role": "developer",
                "valid_intents": ["need_permission", "confirmed"],
                "on": {"need_permission": "plan", "await_agent": "develop"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        return StepExecutionResult(
            response="need_permission",
            artifacts={},
            status_code="need_permission",
            auto_continue=False,
        )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="plan")

    assert result.completed is False
    assert result.final_status_code == "need_permission"
    blackboard = BlackboardStore(issue_dir).load_or_create("plan")
    assert blackboard.current_step == "user"
    assert blackboard.handoff_contract is not None
    assert blackboard.handoff_contract.to_owner == HandoffOwner.USER
    assert blackboard.handoff_contract.intent == HandoffIntent.NEED_PERMISSION


# ---------------------------------------------------------------------------
