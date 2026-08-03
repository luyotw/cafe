"""Tests for the blackboard-first workflow runtime."""

import json
from pathlib import Path

import pytest

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime


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
    rejected = [
        event for event in blackboard.events if event.event_type == "baton_rejected"
    ]
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
                    "intent": "await_agent",
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
    assert result.final_status_code == "BATON_AWAIT_AGENT"


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


def test_runtime_pauses_after_realigning_stale_current_step_from_handoff_contract(
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
                "playbook_id": "default",
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
        to_owner="agent",
        to_step="plan",
        intent="await_agent",
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

    assert result.completed is False
    assert result.final_step == "spec"
    assert result.final_status_code == "BATON_POSITION_REALIGNED"
    assert executed_steps == []
    blackboard = BlackboardStore(issue_dir).load_or_create("spec")
    assert blackboard.current_step == "plan"
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
                "playbook_id": "default",
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
                "playbook_id": "default",
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
            "pr": {"skill": "spec_first", "role": "developer", "on": {"await_agent": "_done"}},
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


def test_runtime_rejects_plain_text_baton_written_by_pr_agent(tmp_path: Path) -> None:
    """Issue #386: a plain step-name baton is never normalized, even at the pr/baton-driven boundary."""
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
    msg = (
        json.loads(interrupted_events[0].message)
        if isinstance(interrupted_events[0].message, str)
        else interrupted_events[0].message
    )
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
    msg = (
        json.loads(interrupted_events[0].message)
        if isinstance(interrupted_events[0].message, str)
        else interrupted_events[0].message
    )
    assert msg["step"] == "spec"
    assert msg["reason"] == "agent_rate_limit"


def test_runtime_reconciles_agent_error_after_valid_handoff(tmp_path: Path) -> None:
    """Agent failure after a complete on-disk handoff records a reconciled transition."""
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

    result = runtime.run(start_step="spec", max_transitions=5)

    assert result.completed is False
    assert result.final_step == "spec"
    assert result.final_status_code == "confirmed"

    bb = BlackboardStore(issue_dir).load_or_create("spec", playbook_id="default")
    assert bb.current_step == "plan"
    assert [e.event_type for e in bb.events].count("step_reconciled") == 1
    reconciled_event = next(e for e in bb.events if e.event_type == "step_reconciled")
    assert reconciled_event.data["to_step"] == "plan"
    assert reconciled_event.data["validated_evidence"] == ["baton", "output", "checklist"]
    assert not any(
        e.event_type == "workflow_paused" and e.data.get("status_code") == "INTERRUPTED"
        for e in bb.events
    )

    iteration_data = json.loads(
        (issue_dir / "spec" / "iteration_001" / "iteration.json").read_text()
    )
    assert iteration_data["status_code"] == "confirmed"
    assert iteration_data["end_time"]


def test_runtime_preserves_interrupted_when_reconciliation_evidence_incomplete(
    tmp_path: Path,
) -> None:
    """Incomplete persisted evidence should not be inferred as a completed handoff."""
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

    bb = BlackboardStore(issue_dir).load_or_create("spec", playbook_id="default")
    assert bb.current_step == "spec"
    failed_event = next(e for e in bb.events if e.event_type == "step_reconciliation_failed")
    assert "checklist_complete" in failed_event.data["missing_evidence"]
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
                "playbook_id": "default",
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
    bb = BlackboardStore(issue_dir).load_or_create("spec", playbook_id="default")
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
                "playbook_id": "default",
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
    bb = BlackboardStore(issue_dir).load_or_create("spec", playbook_id="default")
    assert [e.event_type for e in bb.events].count("step_reconciled") == 1
    reconciled_event = next(e for e in bb.events if e.event_type == "step_reconciled")
    assert reconciled_event.data["step"] == "spec"
    assert reconciled_event.data["to_step"] == "plan"
    iteration_data = json.loads(
        (issue_dir / "spec" / "iteration_001" / "iteration.json").read_text()
    )
    assert iteration_data["status_code"] == "confirmed"
    assert iteration_data["end_time"]


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
    """Write JSON baton payload missing `intent`. """
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
    """第 1 次寫出無效 baton，第 2 次（retry 1）寫出合法 baton → workflow 正常繼續，blackboard 有 1 筆 baton_rejected 事件。"""
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


def test_runtime_retries_twice_on_baton_rejected_then_succeeds(tmp_path: Path) -> None:
    """第 1、2 次無效，第 3 次（retry 2）合法 → workflow 繼續，blackboard 有 2 筆 baton_rejected 事件。"""
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
