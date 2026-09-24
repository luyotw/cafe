"""Tests for the blackboard-first workflow runtime."""

import json
import multiprocessing
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.core.human_tasks import HumanTaskBinding, HumanTaskDecision, HumanTaskPolicy
from cafe.core.workflow_models import BatonRejected, PlaybookRunResult, StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.playbooks.loader import PlaybookLoader
from cafe.ui.human_tasks import resolve_step_human_task

pytestmark = pytest.mark.usefixtures("cached_builtin_playbook_models")


def test_event_callback_wakes_once_after_a_phase_transition(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "callback-transition"
    events: list[dict[str, object]] = []
    playbook = {
        "playbook": {"id": "callback"},
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
        workflow_event_callback=events.append,
    ).run(start_step="spec")

    assert result.completed is True
    assert [event["event_type"] for event in events] == [
        "phase_terminal",
        "workflow_completed",
    ]
    assert events[0]["step"] == "spec"
    assert events[1]["step"] == "develop"
    assert [event["sequence"] for event in events] == [1, 2]
    assert all(event["event_id"] for event in events)
    assert all(event["occurred_at"] for event in events)


def test_callback_sequence_is_allocated_from_latest_durable_state(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "concurrent-callbacks"
    first_store = BlackboardStore(issue_dir)
    first_state = first_store.load_or_create("spec")
    stale_store = BlackboardStore(issue_dir)
    stale_state = stale_store.load_or_create("spec")
    payload = {
        "workflow_id": first_state.workflow_id,
        "issue": issue_dir.name,
        "event_type": "phase_terminal",
        "step": "spec",
    }

    first = first_store.prepare_workflow_callback_event(first_state, payload)
    second = stale_store.prepare_workflow_callback_event(stale_state, payload)

    durable = BlackboardStore(issue_dir).load_or_create("spec")
    callback_events = [
        event.data
        for event in durable.events
        if event.event_type == "workflow_event_callback_enqueued"
    ]
    assert first["sequence"] == 1
    assert second["sequence"] == 2
    assert [event["sequence"] for event in callback_events] == [1, 2]


def test_legacy_blackboard_events_load_without_callback_identity(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "legacy-events"
    issue_dir.mkdir(parents=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "current_step": "spec",
                "events": [
                    {
                        "timestamp": "2026-01-01T00:00:00+00:00",
                        "step": "spec",
                        "type": "legacy",
                        "payload": {"step": "spec"},
                    }
                ],
            }
        )
    )

    state = BlackboardStore(issue_dir).load_or_create("spec")

    assert state.events[0].data == {"step": "spec"}


def test_store_artifacts_preserves_plan_todo_identity_baseline(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "plan-baseline"
    output = issue_dir / "plan" / "iteration_002" / "output.md"
    output.parent.mkdir(parents=True)
    output.write_text("<!-- plan-stage: solution-alignment -->\n", encoding="utf-8")
    baseline = {"schema_version": 1, "artifact": None}
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook={"playbook": {"id": "test"}, "steps": {"plan": {}}},
        executor=object(),
    )

    runtime._store_artifacts(
        {"plan": str(output)},
        {
            "plan": {
                "name": "plan",
                "kind": "document",
                "version": 2,
                "updated_by": "plan",
                "path": str(output),
                "content_sha256": sha256(output.read_bytes()).hexdigest(),
                "todo_identity_baseline": baseline,
            }
        },
    )

    assert runtime.blackboard.artifacts["plan"].todo_identity_baseline == baseline
    reloaded = BlackboardStore(issue_dir).load_or_create("plan")
    assert reloaded.artifacts["plan"].todo_identity_baseline == baseline


def test_store_artifacts_rejects_malformed_plan_todo_identity_baseline(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "invalid-plan-baseline"
    output = issue_dir / "plan" / "iteration_002" / "output.md"
    output.parent.mkdir(parents=True)
    output.write_text("<!-- plan-stage: solution-alignment -->\n", encoding="utf-8")
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook={"playbook": {"id": "test"}, "steps": {"plan": {}}},
        executor=object(),
    )

    with pytest.raises(ValueError, match="baseline metadata is invalid"):
        runtime._store_artifacts(
            {"plan": str(output)},
            {
                "plan": {
                    "name": "plan",
                    "kind": "document",
                    "version": 2,
                    "updated_by": "plan",
                    "path": str(output),
                    "todo_identity_baseline": None,
                }
            },
        )


def test_event_callback_failure_never_blocks_workflow_advancement(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "callback-failure"
    playbook = {
        "playbook": {"id": "callback"},
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
        workflow_event_callback=lambda _event: (_ for _ in ()).throw(OSError("offline")),
    ).run(start_step="spec")

    assert result.completed is True
    events = BlackboardStore(issue_dir).load_or_create("spec").events
    assert any(event.event_type == "workflow_event_callback_dispatch_failed" for event in events)


def test_event_callback_diagnostic_failure_never_blocks_workflow_advancement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "callback-diagnostic-failure"
    playbook = {
        "playbook": {"id": "callback"},
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
        workflow_event_callback=lambda _event: (_ for _ in ()).throw(OSError("offline")),
    )
    original_record = runtime.blackboard_store.record_event

    def record_event(state, event_type, payload):
        if event_type == "workflow_event_callback_dispatch_failed":
            raise OSError("disk unavailable")
        return original_record(state, event_type, payload)

    monkeypatch.setattr(runtime.blackboard_store, "record_event", record_event)
    assert runtime.run(start_step="spec").completed is True


def test_pause_without_completed_phase_still_wakes_callback(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "callback-pause"
    events: list[dict[str, object]] = []
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook={
            "playbook": {"id": "callback"},
            "steps": {"spec": {"skill": "spec", "role": "pm", "on": {}}},
        },
        executor=lambda *_args: None,
        workflow_event_callback=events.append,
    )

    result = runtime._emit_pause(
        current_step="spec", status_code="ITERATION_LIMIT_REACHED", runtime="test", reason="limit"
    )

    assert result.final_status_code == "ITERATION_LIMIT_REACHED"
    assert len(events) == 1
    assert events[0].items() >= {
        "workflow_id": runtime.blackboard.workflow_id,
        "issue": "callback-pause",
        "event_type": "workflow_interruption",
        "step": "spec",
        "status_code": "ITERATION_LIMIT_REACHED",
        "reason": "limit",
    }.items()
    assert events[0]["event_id"]
    assert events[0]["sequence"] == 1
    assert events[0]["occurred_at"]


def test_terminal_status_rewrite_dispatches_one_callback(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "callback-no-baton"
    callback_events: list[dict[str, object]] = []
    playbook = {
        "playbook": {"id": "callback"},
        "steps": {"spec": {"skill": "spec", "role": "pm", "on": {}}},
    }
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=lambda *_args: None,
        workflow_event_callback=callback_events.append,
    )

    runtime._record_step_completion(
        event_type="step_completed",
        current_step="spec",
        status_code="confirmed",
        runtime="test",
    )
    result = runtime._finalize_observed_result(
        PlaybookRunResult(
            final_step="spec",
            final_status_code="NO_BATON_TRANSITION",
            completed=False,
        )
    )

    assert result.final_status_code == "NO_BATON_TRANSITION"
    assert len(callback_events) == 1
    assert callback_events[0]["event_type"] == "workflow_interruption"
    assert callback_events[0]["status_code"] == "NO_BATON_TRANSITION"


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


def _write_outcome_only_success(issue_dir: Path) -> None:
    (issue_dir / "next_step.txt").write_text(
        json.dumps({"version": 1, "intent": "await_agent"}),
        encoding="utf-8",
    )


def _write_minimal_baton(
    issue_dir: Path,
    *,
    to_owner: str,
    to_step: str,
    intent: str,
) -> None:
    (issue_dir / "next_step.txt").write_text(
        json.dumps(
            {
                "version": 1,
                "to_owner": to_owner,
                "to_step": to_step,
                "intent": intent,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def test_runtime_routes_outcome_only_success_using_renamed_playbook_target(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "outcome-only-renamed-target"
    calls: list[str] = []
    playbook = {
        "playbook": {"id": "outcome-only"},
        "steps": {
            "author": {
                "skill": "authoring-skill",
                "role": "writer",
                "behavior": {"completion": "baton"},
                "on": {"await_agent": "quality_gate"},
            },
            "quality_gate": {
                "skill": "review-skill",
                "role": "reviewer",
                "behavior": {"completion": "baton"},
                "on": {"await_agent": "_done"},
            },
        },
    }

    def executor(step_name: str, _step_def: dict, _state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == "author":
            _write_outcome_only_success(issue_dir)
        else:
            _write_baton(
                issue_dir,
                from_step="quality_gate",
                to_owner="done",
                to_step="done",
                intent="workflow_complete",
            )
        return StepExecutionResult(response="", artifacts={})

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    ).run(start_step="author")

    assert result.completed is True
    assert calls == ["author", "quality_gate"]
    state = BlackboardStore(issue_dir).load_or_create("author")
    transitions = [event.data for event in state.events if event.event_type == "transition"]
    assert transitions[0]["to"] == "quality_gate"
    assert transitions[0]["source"] == "baton"


def test_runtime_outcome_only_success_preserves_default_and_terminal_routes(tmp_path: Path) -> None:
    default_issue_dir = tmp_path / ".cafe" / "issues" / "outcome-only-default"
    default_calls: list[str] = []
    default_playbook = {
        "playbook": {"id": "outcome-only-default"},
        "steps": {
            "author": {
                "skill": "authoring-skill",
                "role": "writer",
                "behavior": {"completion": "baton"},
                "on": {"default": "renamed_review"},
            },
            "renamed_review": {
                "skill": "review-skill",
                "role": "reviewer",
                "behavior": {"completion": "baton"},
                "on": {"await_agent": "_done"},
            },
        },
    }

    def default_executor(step_name: str, _step_def: dict, _state: object) -> StepExecutionResult:
        default_calls.append(step_name)
        if step_name == "author":
            _write_outcome_only_success(default_issue_dir)
        else:
            _write_baton(
                default_issue_dir,
                from_step="renamed_review",
                to_owner="done",
                to_step="done",
                intent="workflow_complete",
            )
        return StepExecutionResult(response="", artifacts={})

    assert BlackboardWorkflowRuntime(
        issue_dir=default_issue_dir,
        playbook=default_playbook,
        executor=default_executor,
    ).run(start_step="author").completed is True
    assert default_calls == ["author", "renamed_review"]

    terminal_issue_dir = tmp_path / ".cafe" / "issues" / "outcome-only-terminal"
    terminal_playbook = {
        "playbook": {"id": "outcome-only-terminal"},
        "steps": {
            "author": {
                "skill": "authoring-skill",
                "role": "writer",
                "behavior": {"completion": "baton"},
                "on": {"await_agent": "_done"},
            },
        },
    }

    def terminal_executor(_step_name: str, _step_def: dict, _state: object) -> StepExecutionResult:
        _write_outcome_only_success(terminal_issue_dir)
        return StepExecutionResult(response="", artifacts={})

    terminal_result = BlackboardWorkflowRuntime(
        issue_dir=terminal_issue_dir,
        playbook=terminal_playbook,
        executor=terminal_executor,
    ).run(start_step="author")
    assert terminal_result.completed is True


def test_runtime_rejects_unmapped_outcome_only_success_without_inventing_target(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "outcome-only-unmapped"
    issue_dir.mkdir(parents=True)
    playbook = {
        "playbook": {"id": "outcome-only-unmapped"},
        "steps": {
            "author": {
                "skill": "authoring-skill",
                "role": "writer",
                "behavior": {"completion": "baton"},
                "on": {"manual_handoff": "author"},
            },
        },
    }

    def executor(_step_name: str, _step_def: dict, _state: object, **_kwargs) -> StepExecutionResult:
        _write_outcome_only_success(issue_dir)
        return StepExecutionResult(response="", artifacts={})

    with pytest.raises(RuntimeError, match="invalid baton 3 times"):
        BlackboardWorkflowRuntime(
            issue_dir=issue_dir,
            playbook=playbook,
            executor=executor,
        ).run(start_step="author")

    payload = json.loads((issue_dir / "next_step.txt").read_text(encoding="utf-8"))
    assert payload == {"version": 1, "intent": "await_agent"}
    state = BlackboardStore(issue_dir).load_or_create("author")
    rejections = [event.data for event in state.events if event.event_type == "baton_rejected"]
    assert [entry["field"] for entry in rejections] == ["intent", "intent", "intent"]


def test_runtime_rejects_legacy_baton_target_that_conflicts_with_mapped_outcome(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "mapped-target-conflict"
    issue_dir.mkdir(parents=True)
    prompts: list[str | None] = []
    playbook = {
        "playbook": {"id": "mapped-target-conflict"},
        "steps": {
            "author": {
                "skill": "authoring-skill",
                "role": "writer",
                "behavior": {"completion": "baton"},
                "allowed_goto": ["revise"],
                "on": {"await_agent": "review"},
            },
            "review": {
                "skill": "review-skill",
                "role": "reviewer",
                "behavior": {"completion": "baton"},
                "on": {"await_agent": "_done"},
            },
            "other": {
                "skill": "other-skill",
                "role": "writer",
                "behavior": {"completion": "baton"},
                "on": {"await_agent": "_done"},
            },
            "revise": {
                "skill": "revision-skill",
                "role": "writer",
                "behavior": {"completion": "baton"},
                "on": {"await_agent": "_done"},
            },
        },
    }
    calls = 0

    def executor(step_name: str, _step_def: dict, _state: object, **kwargs) -> StepExecutionResult:
        nonlocal calls
        if step_name == "author":
            calls += 1
            prompts.append(kwargs.get("extra_prompt"))
            if calls == 1:
                _write_baton(
                    issue_dir,
                    from_step="author",
                    to_owner="agent",
                    to_step="other",
                    intent="await_agent",
                )
            else:
                _write_outcome_only_success(issue_dir)
        else:
            _write_baton(
                issue_dir,
                from_step="review",
                to_owner="done",
                to_step="done",
                intent="workflow_complete",
            )
        return StepExecutionResult(response="", artifacts={})

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    ).run(start_step="author")

    assert result.completed is True
    assert calls == 2
    assert "field 'to_step'" in (prompts[1] or "")
    assert "other" in (prompts[1] or "")
    assert "review" in (prompts[1] or "")
    assert "default route: review" in (prompts[1] or "")
    assert "discretionary routes: revise" in (prompts[1] or "")


def test_runtime_accepts_declared_discretionary_baton_target(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "declared-discretionary-target"
    issue_dir.mkdir(parents=True)
    calls: list[str] = []
    playbook = {
        "playbook": {"id": "declared-discretionary-target"},
        "steps": {
            "author": {
                "skill": "authoring-skill",
                "role": "writer",
                "behavior": {"completion": "baton"},
                "allowed_goto": ["revise"],
                "on": {"await_agent": "review"},
            },
            "review": {
                "skill": "review-skill",
                "role": "reviewer",
                "behavior": {"completion": "baton"},
                "on": {"await_agent": "_done"},
            },
            "revise": {
                "skill": "revision-skill",
                "role": "writer",
                "behavior": {"completion": "baton"},
                "on": {"await_agent": "_done"},
            },
        },
    }

    def executor(step_name: str, _step_def: dict, _state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == "author":
            _write_baton(
                issue_dir,
                from_step="author",
                to_owner="agent",
                to_step="revise",
                intent="await_agent",
            )
        else:
            _write_baton(
                issue_dir,
                from_step="revise",
                to_owner="done",
                to_step="done",
                intent="workflow_complete",
            )
        return StepExecutionResult(response="", artifacts={})

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    ).run(start_step="author")

    assert result.completed is True
    assert calls == ["author", "revise"]


def test_runtime_accepts_declared_minimal_self_loop_after_identical_inbound_handoff(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "declared-minimal-self-loop"
    issue_dir.mkdir(parents=True)
    calls: list[str] = []
    playbook = {
        "playbook": {"id": "declared-minimal-self-loop"},
        "steps": {
            "author": {
                "skill": "authoring-skill",
                "role": "writer",
                "behavior": {"completion": "baton"},
                "on": {"await_agent": "inspect"},
            },
            "inspect": {
                "skill": "review-skill",
                "role": "reviewer",
                "behavior": {"completion": "baton"},
                "allowed_goto": ["inspect"],
                "on": {"await_agent": "_done"},
            },
        },
    }

    def executor(step_name: str, _step_def: dict, _state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == "author" or calls.count("inspect") == 2:
            _write_outcome_only_success(issue_dir)
        else:
            _write_minimal_baton(
                issue_dir,
                to_owner="agent",
                to_step="inspect",
                intent="await_agent",
            )
        return StepExecutionResult(response="", artifacts={})

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="author")

    assert result.completed is True
    assert calls == ["author", "inspect", "inspect"]
    transitions = [
        (event.data.get("from"), event.data.get("to"))
        for event in runtime.blackboard.events
        if event.event_type == "transition"
    ]
    assert ("inspect", "inspect") in transitions


def test_runtime_rejects_undeclared_minimal_self_loop_after_identical_inbound_handoff(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "undeclared-minimal-self-loop"
    issue_dir.mkdir(parents=True)
    calls: list[str] = []
    playbook = {
        "playbook": {"id": "undeclared-minimal-self-loop"},
        "steps": {
            "author": {
                "skill": "authoring-skill",
                "role": "writer",
                "behavior": {"completion": "baton"},
                "on": {"await_agent": "inspect"},
            },
            "inspect": {
                "skill": "review-skill",
                "role": "reviewer",
                "behavior": {"completion": "baton"},
                "on": {"await_agent": "_done"},
            },
        },
    }

    def executor(step_name: str, _step_def: dict, _state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == "author" or calls.count("inspect") == 2:
            _write_outcome_only_success(issue_dir)
        else:
            _write_minimal_baton(
                issue_dir,
                to_owner="agent",
                to_step="inspect",
                intent="await_agent",
            )
        return StepExecutionResult(response="", artifacts={})

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="author")

    assert result.completed is True
    assert calls == ["author", "inspect", "inspect"]
    rejections = [
        event.data
        for event in runtime.blackboard.events
        if event.event_type == "baton_rejected" and event.data.get("step") == "inspect"
    ]
    assert any(
        rejection.get("field") == "to_step"
        and rejection.get("invalid_value") == "inspect"
        and rejection.get("valid_values") == ["done"]
        for rejection in rejections
    )
    assert not any(
        event.event_type == "transition"
        and event.data.get("from") == "inspect"
        and event.data.get("to") == "inspect"
        for event in runtime.blackboard.events
    )


def _write_publication_contract(
    issue_dir: Path,
    *,
    playbook_id: str = "publication-contract",
    persisted: object = False,
) -> None:
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "issue.yaml").write_text(
        yaml.safe_dump(
            {
                "playbook_id": playbook_id,
                "pr": {"auto_create": persisted},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _publication_contract_playbook(*, capable: bool = True) -> dict[str, object]:
    step = {
        "skill": "develop",
        "role": "developer",
        "behavior": {"completion": "baton", "publish_confirmation": True},
        "on": {"workflow_complete": "_done"},
    }
    if capable:
        step["capability_requests"] = ["cafe.pr.publish"]
    return {
        "playbook": {"id": "publication-contract"},
        "steps": {"build": step},
    }


@pytest.mark.parametrize(
    ("config", "capable"),
    [
        (
            {
                "playbook_id": "publication-contract",
                "confirmation_contract": {"pr_auto_create": False},
            },
            True,
        ),
        (
            {
                "playbook_id": "publication-contract",
                "pr": {"auto_create": "true"},
            },
            True,
        ),
        (
            {
                "playbook_id": "publication-contract",
                "pr": {"auto_create": False},
            },
            False,
        ),
        (
            {
                "playbook_id": "publication-contract",
                "pr": {"post_todo_list": False},
            },
            False,
        ),
    ],
)
def test_runtime_does_not_gate_pr_configuration(
    tmp_path: Path,
    config: dict[str, object],
    capable: bool,
) -> None:
    """PR capability settings belong to the capability hook, not workflow core."""
    issue_dir = tmp_path / ".cafe" / "issues" / "publication-config"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False),
        encoding="utf-8",
    )
    calls: list[str] = []

    def executor(step: str, *_args: object, **_kwargs: object) -> StepExecutionResult:
        calls.append(step)
        _write_baton(
            issue_dir,
            from_step=step,
            to_owner="done",
            to_step="done",
            intent="workflow_complete",
        )
        return StepExecutionResult(response="", artifacts={})

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_publication_contract_playbook(capable=capable),
        executor=executor,
    ).run(start_step="build")

    assert result.completed is True
    assert calls == ["build"]
    assert not any(
        event.event_type == "workflow_configuration_invalid"
        for event in BlackboardStore(issue_dir).load_or_create("build").events
    )


def test_runtime_does_not_forward_pr_configuration_to_executor(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "runtime-owned-config"
    _write_publication_contract(issue_dir, persisted=False)
    received_kwargs: list[dict[str, object]] = []

    def executor(step: str, *_args: object, **kwargs: object) -> StepExecutionResult:
        received_kwargs.append(dict(kwargs))
        _write_baton(
            issue_dir,
            from_step=step,
            to_owner="done",
            to_step="done",
            intent="workflow_complete",
        )
        return StepExecutionResult(response="", artifacts={})

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_publication_contract_playbook(),
        executor=executor,
    ).run(start_step="build")

    assert result.completed is True
    assert "validated_pr_auto_create" not in received_kwargs[0]


def test_runtime_continues_after_a_pr_config_change_between_steps(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "changed-between-hops"
    _write_publication_contract(issue_dir, persisted=False)
    playbook = {
        "playbook": {"id": "between-hop-contract"},
        "steps": {
            "review": {
                "skill": "review",
                "role": "reviewer",
                "behavior": {"completion": "baton"},
                "on": {"await_agent": "publish"},
            },
            "publish": {
                "skill": "pr",
                "role": "developer",
                "behavior": {"completion": "baton", "publish_confirmation": True},
                "capability_requests": ["cafe.pr.publish"],
                "on": {"workflow_complete": "_done"},
            },
        },
    }
    calls: list[str] = []

    def executor(step: str, *_args: object, **_kwargs: object) -> StepExecutionResult:
        calls.append(step)
        if step == "review":
            _write_publication_contract(issue_dir, persisted="true")
            _write_baton(
                issue_dir,
                from_step=step,
                to_owner="agent",
                to_step="publish",
                intent="await_agent",
            )
            return StepExecutionResult(response="", artifacts={})
        _write_baton(
            issue_dir,
            from_step=step,
            to_owner="done",
            to_step="done",
            intent="workflow_complete",
        )
        return StepExecutionResult(response="", artifacts={})

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    ).run(start_step="review")

    assert result.completed is True
    assert calls == ["review", "publish"]


@pytest.mark.parametrize("choice", [True, False])
def test_local_review_task_reports_an_emitted_pr_url_only(
    tmp_path: Path,
    choice: bool,
) -> None:
    """A PR URL is display data, not a core publication-mode decision."""
    issue_dir = tmp_path / ".cafe" / "issues" / f"review-outcome-{choice}"
    _write_publication_contract(issue_dir, persisted=choice)
    playbook = PlaybookLoader().load("standard")
    url = "https://github.com/acme/widgets/pull/467"

    def executor(step: str, *_args: object, **_kwargs: object) -> StepExecutionResult:
        _write_baton(
            issue_dir,
            from_step=step,
            to_owner="user",
            to_step="user",
            intent="confirm_output",
        )
        events = [{"type": "pr_synced", "url": url, "source": "capability"}] if choice else []
        return StepExecutionResult(response="", artifacts={}, events=events)

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    runtime.blackboard_store.append_capability_receipt(
        runtime.blackboard,
        {
            "capability": "cafe.pr.publish",
            "success": True,
            "outputs": {"pr_url": "https://github.com/stale/project/pull/1"},
        },
    )

    result = runtime.run(start_step="pr")

    assert result.final_status_code == "BATON_CONFIRM_OUTPUT"
    task = HumanTaskRecordStore(issue_dir).tasks()[0]
    if choice:
        assert f"Verified PR URL: {url}" in task.prompt
    else:
        assert "Publication mode:" not in task.prompt
        assert "https://github.com/stale/project/pull/1" not in task.prompt


@pytest.mark.parametrize(
    "events",
    [
        [
            {
                "type": "capability_receipt",
                "capability": "cafe.pr.publish",
                "success": True,
            }
        ],
        [
            {
                "type": "capability_receipt",
                "capability": "cafe.pr.publish",
                "success": False,
                "code": "policy_denied",
            }
        ],
        [{"type": "pr_synced", "url": "", "source": "capability"}],
    ],
)
def test_published_review_routes_without_current_url_evidence(
    tmp_path: Path,
    events: list[dict[str, object]],
) -> None:
    """Publication evidence does not make the core reject a PR phase handoff."""
    issue_dir = tmp_path / ".cafe" / "issues" / "unverified-review"
    _write_publication_contract(issue_dir, persisted=True)

    def executor(step: str, *_args: object, **_kwargs: object) -> StepExecutionResult:
        _write_baton(
            issue_dir,
            from_step=step,
            to_owner="user",
            to_step="user",
            intent="confirm_output",
        )
        return StepExecutionResult(response="", artifacts={}, events=events)

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=PlaybookLoader().load("standard"),
        executor=executor,
    )
    runtime.blackboard_store.append_capability_receipt(
        runtime.blackboard,
        {
            "capability": "cafe.pr.publish",
            "success": True,
            "outputs": {"pr_url": "https://github.com/stale/project/pull/1"},
        },
    )

    result = runtime.run(start_step="pr")

    assert result.completed is False
    assert result.final_status_code == "BATON_CONFIRM_OUTPUT"
    task = HumanTaskRecordStore(issue_dir).tasks()[0]
    assert "https://github.com/stale/project/pull/1" not in task.prompt


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


def test_runtime_allows_pr_completion_without_publish_receipt(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-pr"
    _write_publication_contract(issue_dir, persisted=True)
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

    assert result.completed is True
    assert result.final_step == "pr"
    assert result.final_status_code == "BATON_WORKFLOW_COMPLETE"


def test_runtime_explicit_local_mode_does_not_require_publish_receipt(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-local-pr"
    _write_publication_contract(issue_dir, persisted=False)
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
    _write_publication_contract(issue_dir, persisted=True)
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

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="pr")

    assert result.completed is True
    assert result.final_step == "pr"
    assert result.final_status_code == "BATON_WORKFLOW_COMPLETE"


def test_runtime_allows_pr_completion_with_unverified_publish_receipt(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-pr-cap"
    _write_publication_contract(issue_dir, persisted=True)
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


def test_runtime_allows_publish_phase_manual_handoff_without_receipt(tmp_path: Path) -> None:
    """A PR correction may return to development before any remote sync."""
    issue_dir = tmp_path / ".cafe" / "issues" / "publish-correction"
    _write_publication_contract(issue_dir, persisted=True)
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "pr": {
                "skill": "spec_first",
                "role": "developer",
                "behavior": {"completion": "baton", "publish_confirmation": True},
                "capability_requests": ["cafe.pr.publish"],
                "on": {"manual_handoff": "develop"},
            },
            "develop": {
                "skill": "develop",
                "role": "developer",
                "on": {"await_agent": "_done"},
            },
        },
    }
    calls: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        del step_def, state
        calls.append(step_name)
        if step_name == "pr":
            _write_baton(
                issue_dir,
                from_step="pr",
                to_owner="agent",
                to_step="develop",
                intent="manual_handoff",
            )
        else:
            _write_baton(
                issue_dir,
                from_step="develop",
                to_owner="done",
                to_step="done",
                intent="workflow_complete",
            )
        return StepExecutionResult(response="done", artifacts={})

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    ).run(start_step="pr")

    assert result.completed is True
    assert calls == ["pr", "develop"]
    state = BlackboardStore(issue_dir).load_or_create("pr")
    assert not [event for event in state.events if event.event_type == "workflow_blocked"]


def test_runtime_preserves_publish_capability_approval_before_review(tmp_path: Path) -> None:
    """Removing the receipt gate does not bypass a pending capability approval."""
    issue_dir = tmp_path / ".cafe" / "issues" / "publish-approval"
    _write_publication_contract(issue_dir, persisted=True)
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "pr": {
                "skill": "spec_first",
                "role": "developer",
                "behavior": {"completion": "baton", "publish_confirmation": True},
                "capability_requests": ["cafe.pr.publish"],
                "on": {"confirm_output": "pr"},
            },
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        del step_def, state
        _write_baton(
            issue_dir,
            from_step=step_name,
            to_owner="user",
            to_step="user",
            intent="confirm_output",
        )
        return StepExecutionResult(
            response="done",
            artifacts={},
            events=[
                {
                    "type": "capability_approval_pending",
                    "capability": "cafe.pr.publish",
                    "task_id": "publish-approval-task",
                    "request_fingerprint": "fingerprint",
                }
            ],
        )

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    ).run(start_step="pr")

    assert result.final_status_code == "CAPABILITY_APPROVAL_PENDING"
    assert result.detail == "publish-approval-task"
    state = BlackboardStore(issue_dir).load_or_create("pr")
    assert state.current_step == "user"
    assert state.handoff_contract is not None
    assert state.handoff_contract.intent is HandoffIntent.MANUAL_HANDOFF


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

    callback_events: list[dict[str, object]] = []
    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
        workflow_event_callback=callback_events.append,
    ).run(start_step="publish")

    assert result.final_status_code == "CAPABILITY_APPROVAL_PENDING"
    assert result.detail == "approval-task"
    blackboard = BlackboardStore(issue_dir).load_or_create("publish")
    assert blackboard.current_step == "user"
    assert blackboard.handoff_contract is not None
    assert blackboard.handoff_contract.intent.value == "manual_handoff"
    assert len(callback_events) == 1
    assert callback_events[0]["event_type"] == "human_task"
    assert callback_events[0]["status_code"] == "CAPABILITY_APPROVAL_PENDING"
    assert callback_events[0]["task_id"] == "approval-task"


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
    _write_publication_contract(issue_dir, persisted=False)
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
    assert result.final_status_code == "INTERRUPTED:agent_status_code_missing"
    assert calls == ["spec", "spec", "spec"]
    assert HumanTaskRecordStore(issue_dir).tasks()[0].continuations == {
        "retry": "spec",
        "retry_fresh_session": "spec",
    }


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
    _write_publication_contract(issue_dir, persisted=False)
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
    _write_publication_contract(issue_dir, persisted=False)
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
    _write_iteration_evidence(issue_dir, "spec")
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
    _write_iteration_evidence(issue_dir, "spec")
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
    _write_iteration_evidence(issue_dir, "pr")
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
    _write_publication_contract(issue_dir, persisted=False)
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
    _write_publication_contract(issue_dir, persisted=False)
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


def test_runtime_reprompts_clean_agent_exit_until_it_writes_a_handoff(tmp_path: Path) -> None:
    """A progress-only exit is sent back to the same invocation for a real handoff."""
    issue_dir = tmp_path / ".cafe" / "issues" / "missing-then-handoff"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "develop": {
                "skill": "develop",
                "role": "developer",
                "on": {"await_agent": "review"},
            },
            "review": {
                "skill": "review",
                "role": "reviewer",
                "on": {"await_agent": "_done"},
            },
        },
    }
    calls: list[tuple[str, str | None, bool]] = []

    def executor(
        step_name: str,
        step_def: dict,
        state: object,
        extra_prompt: str | None = None,
        same_invocation_retry: bool = False,
    ):
        calls.append((step_name, extra_prompt, same_invocation_retry))
        store = BlackboardStore(issue_dir)
        if step_name == "develop" and len(calls) == 1:
            iteration_dir = issue_dir / "develop" / "iteration_001"
            iteration_dir.mkdir(parents=True, exist_ok=True)
            (iteration_dir / "iteration.json").write_text(
                json.dumps(
                    {
                        "iteration": 1,
                        "cli": "codex",
                        "session_id": "develop-session",
                        "end_time": "2026-09-12T09:26:54+08:00",
                    }
                ),
                encoding="utf-8",
            )
            return ("Implemented one unit; more work remains.", {})
        if step_name == "develop":
            store.update_handoff_contract(
                state,
                from_step="develop",
                to_owner=HandoffOwner.AGENT,
                to_step="review",
                intent=HandoffIntent.AWAIT_AGENT,
                source="test.develop_complete",
            )
            return ("Implementation complete.", {})
        store.update_handoff_contract(
            state,
            from_step="review",
            to_owner=HandoffOwner.DONE,
            to_step="done",
            intent=HandoffIntent.WORKFLOW_COMPLETE,
            source="test.review_complete",
        )
        return ("Review complete.", {})

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    ).run(start_step="develop")

    assert result.completed is True
    assert [call[0] for call in calls] == ["develop", "develop", "review"]
    assert calls[0][1:] == (None, False)
    assert calls[1][2] is True
    assert calls[1][1] is not None
    assert "A progress update is not a workflow handoff" in calls[1][1]
    assert "either finish the step and write its valid next-step baton" in calls[1][1]
    blackboard = BlackboardStore(issue_dir).load_or_create("develop")
    retries = [
        event for event in blackboard.events if event.event_type == "completion_handoff_missing"
    ]
    assert len(retries) == 1
    assert retries[0].data["will_retry"] is True
    assert HumanTaskRecordStore(issue_dir).tasks() == ()


def test_runtime_status_code_missing_no_handoff_contract(tmp_path: Path) -> None:
    """Repeated missing status and baton falls back to explicit session recovery."""
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

    calls: list[tuple[str | None, bool]] = []

    def executor(
        step_name: str,
        step_def: dict,
        state: object,
        extra_prompt: str | None = None,
        same_invocation_retry: bool = False,
    ):
        calls.append((extra_prompt, same_invocation_retry))
        # No handoff contract written — agent produced nothing useful.
        iteration_dir = issue_dir / "spec" / "iteration_001"
        iteration_dir.mkdir(parents=True, exist_ok=True)
        (iteration_dir / "iteration.json").write_text(
            json.dumps(
                {
                    "iteration": 1,
                    "cli": "codex",
                    "session_id": "session-without-handoff",
                    "end_time": "2026-09-12T09:26:54+08:00",
                }
            ),
            encoding="utf-8",
        )
        return ("plain response without status token", {})

    callback_events: list[dict[str, object]] = []
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
        workflow_event_callback=callback_events.append,
    )
    result = runtime.run(start_step="spec")

    assert result.completed is False
    assert result.final_status_code == "INTERRUPTED:agent_status_code_missing"
    assert len(calls) == 3
    assert calls[0] == (None, False)
    assert all(prompt and retry for prompt, retry in calls[1:])
    blackboard = BlackboardStore(issue_dir).load_or_create("spec")
    retry_events = [
        event for event in blackboard.events if event.event_type == "completion_handoff_missing"
    ]
    assert [event.data["will_retry"] for event in retry_events] == [True, True, False]
    missing_events = [e for e in blackboard.events if e.event_type == "status_code_missing"]
    assert missing_events
    assert "baton_fallback" not in missing_events[-1].data
    iteration_data = json.loads(
        (issue_dir / "spec" / "iteration_001" / "iteration.json").read_text(encoding="utf-8")
    )
    assert iteration_data["end_time"]
    assert iteration_data["workflow_completion_trusted"] is False
    assert blackboard.current_step == "user"
    assert blackboard.handoff_contract is not None
    assert blackboard.handoff_contract.source == "workflow.agent_execution_interrupted"

    task = HumanTaskRecordStore(issue_dir).tasks()[0]
    assert task.trigger == "agent_execution_interrupted"
    assert task.continuations == {
        "retry": "spec",
        "retry_fresh_session": "spec",
    }
    assert (
        callback_events[0].items()
        >= {
            "event_type": "human_task",
            "step": "spec",
            "status_code": "INTERRUPTED:agent_status_code_missing",
            "reason": "agent_status_code_missing",
            "task_id": task.id,
        }.items()
    )


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
    _write_publication_contract(issue_dir, persisted=False)
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
    _write_publication_contract(issue_dir, persisted=False)
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
    _write_publication_contract(issue_dir, persisted=False)
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
    _write_publication_contract(issue_dir, persisted=False)

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
    _write_publication_contract(issue_dir, persisted=False)

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
    _write_publication_contract(issue_dir_legacy, persisted=False)
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
    _write_publication_contract(issue_dir_pr, persisted=True)
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
            events=[
                {
                    "type": "pr_synced",
                    "url": "https://github.com/test/repo/pull/467",
                }
            ],
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
                "behavior": {
                    "completion": "baton",
                    "feedback_target": "develop",
                    "feedback_artifact": "workflow_feedback",
                    "feedback_source_kind": "github_pr",
                    "feedback_todo_source": "pr_comment",
                    "feedback_todo_id_prefix": "PRC",
                },
                "allowed_goto": ["develop"],
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


def test_runtime_keeps_feedback_pending_until_curator_writes_an_outbound_handoff(
    tmp_path: Path,
) -> None:
    """A retried curator cannot consume input before its handoff is durable."""
    from cafe.core.workflow_feedback import WorkflowFeedbackLedger

    issue_dir = tmp_path / ".cafe" / "issues" / "pending-curation"
    ledger = WorkflowFeedbackLedger(issue_dir)
    _created, feedback = ledger.record(
        source_identity="github-pr:42:comment-7",
        source_kind="github_pr",
        target_step="curator",
        content="Keep this source pending until curation commits.",
    )
    playbook = {
        "playbook": {"id": "pending-curation"},
        "steps": {
            "curator": {
                "skill": "phase",
                "role": "developer",
                "assignee_type": "agent",
                "output_artifact": "curated_result",
                "behavior": {"completion": "baton"},
                "on": {"manual_handoff": "consumer"},
            },
            "consumer": {
                "skill": "phase",
                "role": "developer",
                "assignee_type": "agent",
                "on": {"await_agent": "_done"},
            },
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("curator", playbook_id="pending-curation")
    store.update_handoff_contract(
        state,
        from_step="curator",
        to_owner=HandoffOwner.AGENT,
        to_step="curator",
        intent=HandoffIntent.AWAIT_AGENT,
        source="test.setup",
    )

    def executor(_step_name: str, _step: dict, _state: object) -> StepExecutionResult:
        return StepExecutionResult(
            response="retry",
            artifacts={"curated_result": "curator/output.md"},
            status_code="confirmed",
            agent_invoked=True,
        )

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    ).run(start_step="curator", max_transitions=1)

    assert result.completed is False
    assert ledger.pending(target_step="curator") == [feedback]


def test_runtime_requires_the_declared_curated_artifact_before_consuming_feedback(
    tmp_path: Path,
) -> None:
    """A handoff cannot make an unpersisted curated result look delivered."""
    from cafe.core.workflow_feedback import WorkflowFeedbackLedger
    from cafe.core.workflow_runtime import StepIterationFrame

    issue_dir = tmp_path / ".cafe" / "issues" / "missing-curated-artifact"
    ledger = WorkflowFeedbackLedger(issue_dir)
    _created, feedback = ledger.record(
        source_identity="github-pr:42:comment-8",
        source_kind="github_pr",
        target_step="curator",
        content="Do not consume an artifactless curation attempt.",
    )
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook={
            "playbook": {"id": "missing-curated-artifact"},
            "steps": {
                "curator": {
                    "skill": "phase",
                    "role": "developer",
                    "output_artifact": "curated_result",
                    "on": {"manual_handoff": "consumer"},
                },
                "consumer": {"skill": "phase", "role": "developer"},
            },
        },
        executor=lambda *_args, **_kwargs: None,
    )

    runtime._commit_delivered_feedback(
        current_step="curator",
        frame=StepIterationFrame(
            execution_result=StepExecutionResult(
                response="curated",
                artifacts={},
                agent_invoked=True,
            ),
            response="curated",
            artifacts={},
            explicit_status_code=None,
            auto_continue=False,
            pending_feedback=(feedback.source_identity,),
        ),
    )

    assert ledger.pending(target_step="curator") == [feedback]


def _feedback_curation_playbook() -> dict[str, object]:
    """Create a topology-neutral curator/consumer route for delivery tests."""
    return {
        "playbook": {"id": "feedback-curation"},
        "steps": {
            "curator": {
                "skill": "phase",
                "role": "developer",
                "assignee_type": "agent",
                "output_artifact": "curated_result",
                "behavior": {
                    "completion": "baton",
                    "feedback_target": "curator",
                    "feedback_artifact": "workflow_feedback",
                    "feedback_source_kind": "external_note",
                    "feedback_todo_source": "review_note",
                    "feedback_todo_id_prefix": "REV",
                },
                "on": {"manual_handoff": "consumer"},
            },
            "consumer": {
                "skill": "phase",
                "role": "developer",
                "assignee_type": "agent",
                "on": {"await_agent": "_done"},
            },
        },
    }


def _curated_feedback_output(path: Path, identities: list[str]) -> None:
    """Write one canonical deterministic row for each listed source identity."""
    rows = ["## Todo List"]
    for identity in identities:
        item_id = f"REV-{sha256(identity.encode('utf-8')).hexdigest()[:12].upper()}"
        rows.append(
            f"- [ ] `{item_id}` — Source: `review_note` — Work: Address {identity} — "
            "Closure: verified — Evidence: targeted test"
        )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_runtime_commits_feedback_discovered_during_current_curator_invocation(
    tmp_path: Path,
) -> None:
    """Feedback prepared by the current invocation is delivered with its artifact."""
    from cafe.core.workflow_feedback import WorkflowFeedbackLedger

    issue_dir = tmp_path / ".cafe" / "issues" / "late-feedback"
    ledger = WorkflowFeedbackLedger(issue_dir)
    identity = "external:review:late-1"
    output = issue_dir / "curator" / "iteration_001" / "output.md"
    output.parent.mkdir(parents=True)
    calls: list[str] = []

    def executor(step_name: str, _step: dict, state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == "consumer":
            _write_baton(
                issue_dir,
                from_step="consumer",
                to_owner="done",
                to_step="done",
                intent="workflow_complete",
            )
            return StepExecutionResult(response="consumed", artifacts={})
        assert step_name == "curator"
        created, _entry = ledger.record(
            source_identity=identity,
            source_kind="external_note",
            target_step="curator",
            content="Record this during prepare-input, after runtime startup.",
        )
        assert created is True
        _curated_feedback_output(output, [identity])
        _write_baton(
            issue_dir,
            from_step="curator",
            to_owner="agent",
            to_step="consumer",
            intent="manual_handoff",
        )
        return StepExecutionResult(
            response="curated",
            artifacts={"curated_result": str(output)},
            agent_invoked=True,
            feedback_source_identities=(identity,),
        )

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_feedback_curation_playbook(),
        executor=executor,
    ).run(start_step="curator", max_transitions=2)

    assert calls == ["curator", "consumer"]
    assert result.final_step == "consumer"
    assert ledger.pending(target_step="curator") == []
    state = BlackboardStore(issue_dir).load_or_create("curator")
    delivered = [
        event for event in state.events if event.event_type == "workflow_feedback_delivered"
    ]
    assert delivered[-1].data["source_identities"] == [identity]
    assert state.artifacts["curated_result"].path == str(output)


def test_runtime_records_excluded_feedback_without_widening_the_curated_handoff(
    tmp_path: Path,
) -> None:
    """Curation records an exclusion but hands the consumer only represented work."""
    from cafe.core.workflow_feedback import WorkflowFeedbackLedger

    issue_dir = tmp_path / ".cafe" / "issues" / "incomplete-curation"
    ledger = WorkflowFeedbackLedger(issue_dir)
    identities = ["external:review:one", "external:review:two"]
    for identity in identities:
        ledger.record(
            source_identity=identity,
            source_kind="external_note",
            target_step="curator",
            content=f"Address {identity}.",
        )
    output = issue_dir / "curator" / "iteration_001" / "output.md"
    output.parent.mkdir(parents=True)

    def executor(step_name: str, _step: dict, _state: object) -> StepExecutionResult:
        if step_name == "consumer":
            _write_baton(
                issue_dir,
                from_step="consumer",
                to_owner="done",
                to_step="done",
                intent="workflow_complete",
            )
            return StepExecutionResult(response="consumed", artifacts={})
        assert step_name == "curator"
        _curated_feedback_output(output, identities[:1])
        _write_baton(
            issue_dir,
            from_step="curator",
            to_owner="agent",
            to_step="consumer",
            intent="manual_handoff",
        )
        return StepExecutionResult(
            response="incomplete",
            artifacts={"curated_result": str(output)},
            agent_invoked=True,
            feedback_source_identities=tuple(identities),
        )

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_feedback_curation_playbook(),
        executor=executor,
    ).run(start_step="curator", max_transitions=2)

    assert result.final_step == "consumer"
    assert ledger.pending(target_step="curator") == []
    entries = {entry.source_identity: entry for entry in ledger.load()}
    assert entries[identities[0]].disposition == "delivered"
    assert entries[identities[1]].disposition == "excluded"
    state = BlackboardStore(issue_dir).load_or_create("curator")
    delivered = [
        event for event in state.events if event.event_type == "workflow_feedback_delivered"
    ]
    assert {key: value for key, value in delivered[-1].data.items() if key != "delivery_id"} == {
        "step": "curator",
        "source_identities": [identities[0]],
        "excluded_source_identities": [identities[1]],
        "deferred_source_identities": [],
    }
    assert delivered[-1].data["delivery_id"]


@pytest.mark.parametrize(
    "invalid_output",
    ["extra", "nondeterministic", "malformed", "duplicate", "wrong_source"],
)
def test_runtime_rejects_noncanonical_curated_feedback_without_consuming_sources(
    tmp_path: Path, invalid_output: str
) -> None:
    """Every malformed or non-exact row set leaves the source recoverable."""
    from cafe.core.workflow_feedback import WorkflowFeedbackLedger

    issue_dir = tmp_path / ".cafe" / "issues" / f"invalid-curation-{invalid_output}"
    ledger = WorkflowFeedbackLedger(issue_dir)
    identity = "external:review:canonical"
    ledger.record(
        source_identity=identity,
        source_kind="external_note",
        target_step="curator",
        content="Address the canonical source.",
    )
    output = issue_dir / "curator" / "iteration_001" / "output.md"
    output.parent.mkdir(parents=True)

    def executor(step_name: str, _step: dict, _state: object) -> StepExecutionResult:
        if step_name == "consumer":
            _write_baton(
                issue_dir,
                from_step="consumer",
                to_owner="done",
                to_step="done",
                intent="workflow_complete",
            )
            return StepExecutionResult(response="consumed", artifacts={})
        assert step_name == "curator"
        if invalid_output == "extra":
            _curated_feedback_output(output, [identity, "external:review:stale"])
        elif invalid_output == "nondeterministic":
            output.write_text(
                "## Todo List\n"
                "- [ ] `REV-NONDETERMINISTIC` — Source: `review_note` — "
                "Work: Address the source — Closure: verified — Evidence: targeted test\n",
                encoding="utf-8",
            )
        elif invalid_output == "malformed":
            output.write_text("## Todo List\n\n- not a Todo row\n", encoding="utf-8")
        elif invalid_output == "wrong_source":
            item_id = f"PRC-{sha256(identity.encode('utf-8')).hexdigest()[:12].upper()}"
            output.write_text(
                "## Todo List\n"
                f"- [ ] `{item_id}` — Source: `pr_comment` — Work: Address the source — "
                "Closure: verified — Evidence: targeted test\n",
                encoding="utf-8",
            )
        else:
            item_id = f"REV-{sha256(identity.encode('utf-8')).hexdigest()[:12].upper()}"
            row = (
                f"- [ ] `{item_id}` — Source: `review_note` — Work: Address the source — "
                "Closure: verified — Evidence: targeted test"
            )
            output.write_text(f"## Todo List\n{row}\n{row}\n", encoding="utf-8")
        _write_baton(
            issue_dir,
            from_step="curator",
            to_owner="agent",
            to_step="consumer",
            intent="manual_handoff",
        )
        return StepExecutionResult(
            response="invalid curation",
            artifacts={"curated_result": str(output)},
            agent_invoked=True,
            feedback_source_identities=(identity,),
        )

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_feedback_curation_playbook(),
        executor=executor,
    ).run(start_step="curator", max_transitions=2)

    assert result.final_status_code == "INVALID_FEEDBACK_DELIVERY"
    assert [entry.source_identity for entry in ledger.pending(target_step="curator")] == [identity]


def test_runtime_accepts_canonical_empty_as_a_durable_exclusion_and_replay_is_idempotent(
    tmp_path: Path,
) -> None:
    """An informational source reaches the consumer as canonical empty work once."""
    from cafe.core.workflow_feedback import WorkflowFeedbackLedger

    issue_dir = tmp_path / ".cafe" / "issues" / "empty-curation"
    ledger = WorkflowFeedbackLedger(issue_dir)
    identity = "external:review:informational"
    ledger.record(
        source_identity=identity,
        source_kind="external_note",
        target_step="curator",
        content="Looks good to me.",
    )
    output = issue_dir / "curator" / "iteration_001" / "output.md"
    output.parent.mkdir(parents=True)
    calls: list[str] = []

    def executor(step_name: str, _step: dict, _state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == "consumer":
            _write_baton(
                issue_dir,
                from_step="consumer",
                to_owner="done",
                to_step="done",
                intent="workflow_complete",
            )
            return StepExecutionResult(response="consumed", artifacts={})
        assert step_name == "curator"
        batch = (identity,) if ledger.pending(target_step="curator") else ()
        output.write_text("## Todo List\n\nNo actionable work.\n", encoding="utf-8")
        _write_baton(
            issue_dir,
            from_step="curator",
            to_owner="agent",
            to_step="consumer",
            intent="manual_handoff",
        )
        return StepExecutionResult(
            response="empty curation",
            artifacts={"curated_result": str(output)},
            agent_invoked=True,
            feedback_source_identities=batch,
        )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_feedback_curation_playbook(),
        executor=executor,
    )
    first = runtime.run(start_step="curator", max_transitions=2)
    replay = runtime.run(start_step="curator", max_transitions=2)

    assert first.final_step == "consumer"
    assert replay.final_step == "consumer"
    assert calls == ["curator", "consumer", "curator", "consumer"]
    entry = ledger.load()[0]
    assert entry.disposition == "excluded"
    assert ledger.pending(target_step="curator") == []
    delivered = [
        event
        for event in BlackboardStore(issue_dir).load_or_create("curator").events
        if event.event_type == "workflow_feedback_delivered"
    ]
    assert len(delivered) == 1
    assert delivered[0].data["source_identities"] == []
    assert delivered[0].data["excluded_source_identities"] == [identity]
    assert delivered[0].data["deferred_source_identities"] == []


def test_runtime_delivers_over_budget_feedback_in_two_bounded_curation_cycles(
    tmp_path: Path,
) -> None:
    """A 101-source cycle makes bounded, lossless progress to its consumer."""
    from cafe.core.workflow_feedback import WorkflowFeedbackLedger

    issue_dir = tmp_path / ".cafe" / "issues" / "over-budget-curation"
    ledger = WorkflowFeedbackLedger(issue_dir)
    identities = [f"external:review:{index}" for index in range(101)]
    for identity in identities:
        ledger.record(
            source_identity=identity,
            source_kind="external_note",
            target_step="curator",
            content=f"Address {identity}.",
        )
    output = issue_dir / "curator" / "iteration_001" / "output.md"
    output.parent.mkdir(parents=True)
    calls: list[str] = []

    def executor(step_name: str, _step: dict, _state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == "consumer":
            _write_baton(
                issue_dir,
                from_step="consumer",
                to_owner="done",
                to_step="done",
                intent="workflow_complete",
            )
            return StepExecutionResult(response="consumed", artifacts={})
        assert step_name == "curator"
        pending = [entry.source_identity for entry in ledger.pending(target_step="curator")]
        _curated_feedback_output(output, pending[:100])
        _write_baton(
            issue_dir,
            from_step="curator",
            to_owner="agent",
            to_step="consumer",
            intent="manual_handoff",
        )
        return StepExecutionResult(
            response="over budget",
            artifacts={"curated_result": str(output)},
            agent_invoked=True,
            feedback_source_identities=tuple(pending[:100]),
        )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_feedback_curation_playbook(),
        executor=executor,
    )
    first = runtime.run(start_step="curator", max_transitions=2)
    second = runtime.run(start_step="curator", max_transitions=2)

    assert first.final_step == "consumer"
    assert second.final_step == "consumer"
    assert calls == ["curator", "consumer", "curator", "consumer"]
    assert ledger.pending(target_step="curator") == []
    delivered = [
        event
        for event in BlackboardStore(issue_dir).load_or_create("curator").events
        if event.event_type == "workflow_feedback_delivered"
    ]
    assert [event.data["source_identities"] for event in delivered] == [
        identities[:100],
        identities[100:],
    ]
    assert [event.data["deferred_source_identities"] for event in delivered] == [
        identities[100:],
        [],
    ]


def test_runtime_defers_feedback_that_arrives_after_the_agent_batch_is_pinned(
    tmp_path: Path,
) -> None:
    """A post-output source remains pending for its own later curator cycle."""
    from cafe.core.workflow_feedback import WorkflowFeedbackLedger

    issue_dir = tmp_path / ".cafe" / "issues" / "post-output-feedback"
    ledger = WorkflowFeedbackLedger(issue_dir)
    initial = "external:review:initial"
    later = "external:review:later"
    ledger.record(
        source_identity=initial,
        source_kind="external_note",
        target_step="curator",
        content="Address the reviewed source.",
    )
    output = issue_dir / "curator" / "iteration_001" / "output.md"
    output.parent.mkdir(parents=True)
    calls: list[str] = []
    batches: list[tuple[str, ...]] = []

    def executor(step_name: str, _step: dict, _state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == "consumer":
            _write_baton(
                issue_dir,
                from_step="consumer",
                to_owner="done",
                to_step="done",
                intent="workflow_complete",
            )
            return StepExecutionResult(response="consumed", artifacts={})
        batch = (initial,) if not batches else (later,)
        batches.append(batch)
        _curated_feedback_output(output, list(batch))
        if len(batches) == 1:
            ledger.record(
                source_identity=later,
                source_kind="external_note",
                target_step="curator",
                content="Arrived after the agent wrote its artifact.",
            )
        _write_baton(
            issue_dir,
            from_step="curator",
            to_owner="agent",
            to_step="consumer",
            intent="manual_handoff",
        )
        return StepExecutionResult(
            response="curated",
            artifacts={"curated_result": str(output)},
            agent_invoked=True,
            feedback_source_identities=batch,
        )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_feedback_curation_playbook(),
        executor=executor,
    )
    first = runtime.run(start_step="curator", max_transitions=2)
    assert first.final_step == "consumer"
    entries = {entry.source_identity: entry for entry in ledger.load()}
    assert entries[initial].disposition == "delivered"
    assert entries[later].disposition == "pending"
    assert [entry.source_identity for entry in ledger.pending(target_step="curator")] == [later]

    second = runtime.run(start_step="curator", max_transitions=2)
    assert second.final_step == "consumer"
    assert calls == ["curator", "consumer", "curator", "consumer"]
    assert ledger.pending(target_step="curator") == []
    delivered = [
        event
        for event in BlackboardStore(issue_dir).load_or_create("curator").events
        if event.event_type == "workflow_feedback_delivered"
    ]
    assert [event.data["source_identities"] for event in delivered] == [[initial], [later]]


def test_runtime_settles_the_exact_agent_selected_bounded_batch(
    tmp_path: Path,
) -> None:
    """A valid non-prefix 100-source batch reaches the declared consumer first."""
    from cafe.core.workflow_feedback import WorkflowFeedbackLedger

    issue_dir = tmp_path / ".cafe" / "issues" / "agent-selected-batch"
    ledger = WorkflowFeedbackLedger(issue_dir)
    identities = [f"external:review:{index}" for index in range(101)]
    for identity in identities:
        ledger.record(
            source_identity=identity,
            source_kind="external_note",
            target_step="curator",
            content=f"Address {identity}.",
        )
    output = issue_dir / "curator" / "iteration_001" / "output.md"
    output.parent.mkdir(parents=True)
    selected_batches = [tuple(identities[1:]), (identities[0],)]
    calls: list[str] = []

    def executor(step_name: str, _step: dict, _state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == "consumer":
            _write_baton(
                issue_dir,
                from_step="consumer",
                to_owner="done",
                to_step="done",
                intent="workflow_complete",
            )
            return StepExecutionResult(response="consumed", artifacts={})
        batch = selected_batches.pop(0)
        _curated_feedback_output(output, list(batch))
        _write_baton(
            issue_dir,
            from_step="curator",
            to_owner="agent",
            to_step="consumer",
            intent="manual_handoff",
        )
        return StepExecutionResult(
            response="curated",
            artifacts={"curated_result": str(output)},
            agent_invoked=True,
            feedback_source_identities=batch,
        )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_feedback_curation_playbook(),
        executor=executor,
    )
    first = runtime.run(start_step="curator", max_transitions=2)
    assert first.final_step == "consumer"
    assert [entry.source_identity for entry in ledger.pending(target_step="curator")] == [
        identities[0]
    ]

    second = runtime.run(start_step="curator", max_transitions=2)
    assert second.final_step == "consumer"
    assert calls == ["curator", "consumer", "curator", "consumer"]
    assert ledger.pending(target_step="curator") == []


def _recovery_fault_curation_runtime(
    *,
    issue_dir: Path,
    calls: list[str],
) -> BlackboardWorkflowRuntime:
    """Build one production runtime whose curator writes durable handoff evidence."""
    from cafe.core.workflow_feedback import WorkflowFeedbackLedger

    output = issue_dir / "curator" / "iteration_001" / "output.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.parent.joinpath("checklist.md").write_text("[x] complete\n", encoding="utf-8")
    output.parent.joinpath("iteration.json").write_text(
        json.dumps({"iteration": 1, "step_name": "curator"}), encoding="utf-8"
    )

    def executor(step_name: str, _step: dict, _state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == "consumer":
            _write_baton(
                issue_dir,
                from_step="consumer",
                to_owner="done",
                to_step="done",
                intent="workflow_complete",
            )
            return StepExecutionResult(response="consumed", artifacts={})
        assert step_name == "curator"
        pending = WorkflowFeedbackLedger(issue_dir).pending(target_step="curator")
        _curated_feedback_output(output, [entry.source_identity for entry in pending])
        _write_baton(
            issue_dir,
            from_step="curator",
            to_owner="agent",
            to_step="consumer",
            intent="manual_handoff",
        )
        return StepExecutionResult(
            response="curated",
            artifacts={"curated_result": str(output)},
            agent_invoked=True,
            feedback_source_identities=tuple(entry.source_identity for entry in pending),
        )

    return BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_feedback_curation_playbook(),
        executor=executor,
    )


def test_recovery_settles_the_persisted_batch_before_running_its_consumer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-settlement crash resumes the same delivery before consumer use."""
    from cafe.core.workflow_feedback import WorkflowFeedbackLedger

    issue_dir = tmp_path / ".cafe" / "issues" / "recover-before-feedback-settlement"
    ledger = WorkflowFeedbackLedger(issue_dir)
    _created, feedback = ledger.record(
        source_identity="external:recovery:before",
        source_kind="external_note",
        target_step="curator",
        content="Preserve this reviewed source across the fault.",
    )
    calls: list[str] = []
    interrupted = _recovery_fault_curation_runtime(
        issue_dir=issue_dir, calls=calls
    )

    def crash_before_settlement(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated pre-settlement crash")

    monkeypatch.setattr(interrupted, "_commit_delivered_feedback", crash_before_settlement)
    with pytest.raises(RuntimeError, match="pre-settlement"):
        interrupted.run(start_step="curator", max_transitions=2)

    assert calls == ["curator"]
    assert ledger.pending(target_step="curator") == [feedback]

    resumed = _recovery_fault_curation_runtime(issue_dir=issue_dir, calls=calls)
    result = resumed.run(max_transitions=2)

    assert result.completed is True
    assert calls == ["curator", "consumer"]
    assert ledger.pending(target_step="curator") == []
    state = BlackboardStore(issue_dir).load_or_create("curator")
    prepared = [
        event for event in state.events if event.event_type == "workflow_feedback_delivery_prepared"
    ]
    delivered = [
        event for event in state.events if event.event_type == "workflow_feedback_delivered"
    ]
    assert len(prepared) == 1
    assert prepared[0].data["source_identities"] == [feedback.source_identity]
    assert prepared[0].data["artifact"]["version"] == state.artifacts["curated_result"].version
    assert prepared[0].data["target"] == {
        "to_owner": "agent",
        "to_step": "consumer",
        "intent": "manual_handoff",
    }
    assert len(delivered) == 1
    assert delivered[0].data["delivery_id"] == prepared[0].data["delivery_id"]
    assert any(event.event_type == "workflow_feedback_delivery_reconciled" for event in state.events)


def test_recovery_reconciles_an_already_settled_batch_without_duplicate_consumer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A post-settlement crash records the same delivery once on restart."""
    from cafe.core.workflow_feedback import WorkflowFeedbackLedger

    issue_dir = tmp_path / ".cafe" / "issues" / "recover-after-feedback-settlement"
    ledger = WorkflowFeedbackLedger(issue_dir)
    _created, feedback = ledger.record(
        source_identity="external:recovery:after",
        source_kind="external_note",
        target_step="curator",
        content="Do not duplicate this settled source after restart.",
    )
    calls: list[str] = []
    interrupted = _recovery_fault_curation_runtime(
        issue_dir=issue_dir, calls=calls
    )
    settle_reviewed = WorkflowFeedbackLedger.settle_reviewed

    def crash_after_settlement(
        self: WorkflowFeedbackLedger, *args: object, **kwargs: object
    ) -> object:
        settled = settle_reviewed(self, *args, **kwargs)
        raise RuntimeError("simulated post-settlement crash")

    monkeypatch.setattr(WorkflowFeedbackLedger, "settle_reviewed", crash_after_settlement)
    with pytest.raises(RuntimeError, match="post-settlement"):
        interrupted.run(start_step="curator", max_transitions=2)
    monkeypatch.setattr(WorkflowFeedbackLedger, "settle_reviewed", settle_reviewed)

    assert calls == ["curator"]
    assert ledger.pending(target_step="curator") == []

    resumed = _recovery_fault_curation_runtime(issue_dir=issue_dir, calls=calls)
    result = resumed.run(max_transitions=2)

    assert result.completed is True
    assert calls == ["curator", "consumer"]
    state = BlackboardStore(issue_dir).load_or_create("curator")
    prepared = [
        event for event in state.events if event.event_type == "workflow_feedback_delivery_prepared"
    ]
    delivered = [
        event for event in state.events if event.event_type == "workflow_feedback_delivered"
    ]
    assert len(prepared) == 1
    assert len(delivered) == 1
    assert delivered[0].data["delivery_id"] == prepared[0].data["delivery_id"]
    assert delivered[0].data["source_identities"] == [feedback.source_identity]


def test_recovery_rejects_stale_artifact_evidence_before_consumer_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A changed artifact version cannot authorize a recorded feedback delivery."""
    from cafe.core.workflow_feedback import WorkflowFeedbackLedger

    issue_dir = tmp_path / ".cafe" / "issues" / "recover-stale-feedback-evidence"
    ledger = WorkflowFeedbackLedger(issue_dir)
    _created, feedback = ledger.record(
        source_identity="external:recovery:stale",
        source_kind="external_note",
        target_step="curator",
        content="Reject stale recovery evidence.",
    )
    calls: list[str] = []
    interrupted = _recovery_fault_curation_runtime(issue_dir=issue_dir, calls=calls)

    def crash_before_settlement(*_args: object, **_kwargs: object) -> None:
        interrupted.blackboard_store.record_event(
            interrupted.blackboard,
            "step_interrupted",
            {"step": "curator", "reason": "recovery_fault"},
        )
        raise RuntimeError("simulated stale-evidence crash")

    monkeypatch.setattr(interrupted, "_commit_delivered_feedback", crash_before_settlement)
    with pytest.raises(RuntimeError, match="stale-evidence"):
        interrupted.run(start_step="curator", max_transitions=2)

    store = BlackboardStore(issue_dir)
    state = store.load_or_create("curator")
    store.set_artifact(state, "curated_result", state.artifacts["curated_result"].path)
    resumed = _recovery_fault_curation_runtime(issue_dir=issue_dir, calls=calls)

    assert (
        resumed._try_reconcile_interrupted_step(
            current_step="curator", runtime="test", reason="recovery_fault"
        )
        is None
    )
    assert ledger.pending(target_step="curator") == [feedback]
    recovered = BlackboardStore(issue_dir).load_or_create("curator")
    assert recovered.handoff_contract is not None
    assert recovered.handoff_contract.to_step == "curator"
    failures = [
        event for event in recovered.events if event.event_type == "step_reconciliation_failed"
    ]
    assert "feedback_delivery_artifact" in failures[-1].data["missing_evidence"]
    assert calls == ["curator"]


def test_recovery_rejects_same_version_artifact_replacement_before_consumer_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery rejects bytes that no longer match the prepared delivery."""
    from cafe.core.workflow_feedback import WorkflowFeedbackLedger

    issue_dir = tmp_path / ".cafe" / "issues" / "recover-replaced-feedback-evidence"
    ledger = WorkflowFeedbackLedger(issue_dir)
    identities = ["external:recovery:original", "external:recovery:replacement"]
    for identity in identities:
        ledger.record(
            source_identity=identity,
            source_kind="external_note",
            target_step="curator",
            content=f"Protect {identity} from artifact replacement.",
        )
    calls: list[str] = []
    interrupted = _recovery_fault_curation_runtime(issue_dir=issue_dir, calls=calls)

    def crash_before_settlement(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated artifact-replacement crash")

    monkeypatch.setattr(interrupted, "_commit_delivered_feedback", crash_before_settlement)
    with pytest.raises(RuntimeError, match="artifact-replacement"):
        interrupted.run(start_step="curator", max_transitions=2)

    output = issue_dir / "curator" / "iteration_001" / "output.md"
    _curated_feedback_output(output, [identities[1]])
    resumed = _recovery_fault_curation_runtime(issue_dir=issue_dir, calls=calls)
    result = resumed.run(max_transitions=2)

    assert result.final_status_code == "INVALID_FEEDBACK_DELIVERY"
    assert calls == ["curator"]
    assert [entry.source_identity for entry in ledger.pending(target_step="curator")] == identities
    state = BlackboardStore(issue_dir).load_or_create("curator")
    prepared = [
        event for event in state.events if event.event_type == "workflow_feedback_delivery_prepared"
    ]
    rejected = [
        event for event in state.events if event.event_type == "workflow_feedback_delivery_rejected"
    ]
    assert len(prepared) == 1
    assert prepared[0].data["artifact"]["sha256"]
    assert rejected[-1].data["delivery_id"] == prepared[0].data["delivery_id"]
    failures = [
        event for event in state.events if event.event_type == "step_reconciliation_failed"
    ]
    assert "feedback_delivery_artifact_content" in failures[-1].data["missing_evidence"]
    assert not any(event.event_type == "workflow_feedback_delivered" for event in state.events)


def test_rejected_feedback_delivery_does_not_replay_after_later_success(
    tmp_path: Path,
) -> None:
    """An invalid delivery is terminal before a later successful correction run."""
    from cafe.core.workflow_feedback import WorkflowFeedbackLedger

    issue_dir = tmp_path / ".cafe" / "issues" / "rejected-feedback-delivery-replay"
    ledger = WorkflowFeedbackLedger(issue_dir)
    identity = "external:recovery:terminal-rejection"
    ledger.record(
        source_identity=identity,
        source_kind="external_note",
        target_step="curator",
        content="Do not revive this rejected delivery.",
    )
    output = issue_dir / "curator" / "iteration_001" / "output.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.parent.joinpath("checklist.md").write_text("[x] complete\n", encoding="utf-8")
    output.parent.joinpath("iteration.json").write_text(
        json.dumps({"iteration": 1, "step_name": "curator"}), encoding="utf-8"
    )
    attempts = ["invalid", "valid"]
    calls: list[str] = []

    def executor(step_name: str, _step: dict, _state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == "consumer":
            _write_baton(
                issue_dir,
                from_step="consumer",
                to_owner="done",
                to_step="done",
                intent="workflow_complete",
            )
            return StepExecutionResult(response="consumed", artifacts={})
        assert step_name == "curator"
        attempt = attempts.pop(0)
        _curated_feedback_output(
            output,
            ["external:recovery:wrong"] if attempt == "invalid" else [identity],
        )
        _write_baton(
            issue_dir,
            from_step="curator",
            to_owner="agent",
            to_step="consumer",
            intent="manual_handoff",
        )
        return StepExecutionResult(
            response=attempt,
            artifacts={"curated_result": str(output)},
            agent_invoked=True,
            feedback_source_identities=(identity,),
        )

    first = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_feedback_curation_playbook(),
        executor=executor,
    ).run(start_step="curator", max_transitions=2)

    assert first.final_status_code == "INVALID_FEEDBACK_DELIVERY"
    assert calls == ["curator"]
    state = BlackboardStore(issue_dir).load_or_create("curator")
    prepared = [
        event for event in state.events if event.event_type == "workflow_feedback_delivery_prepared"
    ]
    rejected = [
        event for event in state.events if event.event_type == "workflow_feedback_delivery_rejected"
    ]
    assert len(prepared) == len(rejected) == 1
    assert rejected[0].data["delivery_id"] == prepared[0].data["delivery_id"]

    second = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_feedback_curation_playbook(),
        executor=executor,
    ).run(start_step="curator", max_transitions=2)

    assert second.completed is True
    assert calls == ["curator", "curator", "consumer"]
    assert ledger.pending(target_step="curator") == []
    state = BlackboardStore(issue_dir).load_or_create("curator")
    prepared = [
        event for event in state.events if event.event_type == "workflow_feedback_delivery_prepared"
    ]
    delivered = [
        event for event in state.events if event.event_type == "workflow_feedback_delivered"
    ]
    assert len(prepared) == 2
    assert len(delivered) == 1
    assert delivered[0].data["delivery_id"] == prepared[1].data["delivery_id"]
    assert state.current_step == "done"

    third = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_feedback_curation_playbook(),
        executor=executor,
    ).run(max_transitions=2)

    assert third.completed is True
    assert calls == ["curator", "curator", "consumer"]
    assert BlackboardStore(issue_dir).load_or_create("curator").current_step == "done"


def test_recovery_rejects_missing_preparation_after_source_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resolved source cannot erase a missing delivery binding on recovery."""
    from cafe.core.workflow_feedback import WorkflowFeedbackLedger

    issue_dir = tmp_path / ".cafe" / "issues" / "recover-missing-preparation"
    ledger = WorkflowFeedbackLedger(issue_dir)
    _created, feedback = ledger.record(
        source_identity="external:recovery:missing-preparation",
        source_kind="external_note",
        target_step="curator",
        content="Require a prepared operation before this handoff is used.",
    )
    calls: list[str] = []
    interrupted = _recovery_fault_curation_runtime(issue_dir=issue_dir, calls=calls)

    def crash_before_preparation(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated pre-preparation crash")

    monkeypatch.setattr(interrupted, "_prepare_feedback_delivery", crash_before_preparation)
    with pytest.raises(RuntimeError, match="pre-preparation"):
        interrupted.run(start_step="curator", max_transitions=2)
    assert ledger.reconcile_resolved({feedback.source_identity}) == 1

    resumed = _recovery_fault_curation_runtime(issue_dir=issue_dir, calls=calls)
    result = resumed.run(max_transitions=2)

    assert result.final_status_code == "INVALID_FEEDBACK_DELIVERY"
    assert calls == ["curator"]
    resolved = {entry.source_identity: entry for entry in ledger.load()}[feedback.source_identity]
    assert resolved.resolved is True
    assert resolved.consumed is False
    assert ledger.pending(target_step="curator") == []
    state = BlackboardStore(issue_dir).load_or_create("curator")
    failures = [
        event for event in state.events if event.event_type == "step_reconciliation_failed"
    ]
    assert "feedback_delivery_prepared" in failures[-1].data["missing_evidence"]
    assert not any(event.event_type == "workflow_feedback_delivered" for event in state.events)

    _created, later = ledger.record(
        source_identity="external:recovery:later-after-missing-preparation",
        source_kind="external_note",
        target_step="curator",
        content="Deliver this later independent source once.",
    )
    delivered = _recovery_fault_curation_runtime(issue_dir=issue_dir, calls=calls).run(
        start_step="curator", max_transitions=2
    )

    assert delivered.completed is True
    assert calls == ["curator", "curator", "consumer"]
    assert ledger.pending(target_step="curator") == []
    state = BlackboardStore(issue_dir).load_or_create("curator")
    delivered_events = [
        event for event in state.events if event.event_type == "workflow_feedback_delivered"
    ]
    assert delivered_events[-1].data["source_identities"] == [later.source_identity]

    clean = _recovery_fault_curation_runtime(issue_dir=issue_dir, calls=calls).run(
        max_transitions=2
    )

    assert clean.completed is True
    assert calls == ["curator", "curator", "consumer"]


def test_recovery_rejects_malformed_preparation_after_source_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resolved source cannot erase malformed delivery evidence on recovery."""
    from cafe.core.workflow_feedback import WorkflowFeedbackLedger

    issue_dir = tmp_path / ".cafe" / "issues" / "recover-malformed-preparation"
    ledger = WorkflowFeedbackLedger(issue_dir)
    _created, feedback = ledger.record(
        source_identity="external:recovery:malformed-preparation",
        source_kind="external_note",
        target_step="curator",
        content="Reject malformed recovery evidence before consumer use.",
    )
    calls: list[str] = []
    interrupted = _recovery_fault_curation_runtime(issue_dir=issue_dir, calls=calls)

    def crash_before_settlement(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated malformed-preparation crash")

    monkeypatch.setattr(interrupted, "_commit_delivered_feedback", crash_before_settlement)
    with pytest.raises(RuntimeError, match="malformed-preparation"):
        interrupted.run(start_step="curator", max_transitions=2)

    store = BlackboardStore(issue_dir)
    state = store.load_or_create("curator")
    prepared = [
        event for event in state.events if event.event_type == "workflow_feedback_delivery_prepared"
    ]
    prepared[-1].data["source_identities"] = "not-a-source-identity-list"
    store.save(state)
    assert ledger.reconcile_resolved({feedback.source_identity}) == 1

    resumed = _recovery_fault_curation_runtime(issue_dir=issue_dir, calls=calls)
    result = resumed.run(max_transitions=2)

    assert result.final_status_code == "INVALID_FEEDBACK_DELIVERY"
    assert calls == ["curator"]
    resolved = {entry.source_identity: entry for entry in ledger.load()}[feedback.source_identity]
    assert resolved.resolved is True
    assert resolved.consumed is False
    assert ledger.pending(target_step="curator") == []
    state = store.load_or_create("curator")
    rejected = [
        event for event in state.events if event.event_type == "workflow_feedback_delivery_rejected"
    ]
    assert rejected[-1].data["delivery_id"] == prepared[-1].data["delivery_id"]
    failures = [
        event for event in state.events if event.event_type == "step_reconciliation_failed"
    ]
    assert "feedback_delivery_batch" in failures[-1].data["missing_evidence"]
    assert not any(event.event_type == "workflow_feedback_delivered" for event in state.events)

    _created, later = ledger.record(
        source_identity="external:recovery:later-after-malformed-preparation",
        source_kind="external_note",
        target_step="curator",
        content="Deliver this later independent source once.",
    )
    delivered = _recovery_fault_curation_runtime(issue_dir=issue_dir, calls=calls).run(
        start_step="curator", max_transitions=2
    )

    assert delivered.completed is True
    assert calls == ["curator", "curator", "consumer"]
    assert ledger.pending(target_step="curator") == []
    state = BlackboardStore(issue_dir).load_or_create("curator")
    delivered_events = [
        event for event in state.events if event.event_type == "workflow_feedback_delivered"
    ]
    assert delivered_events[-1].data["source_identities"] == [later.source_identity]

    clean = _recovery_fault_curation_runtime(issue_dir=issue_dir, calls=calls).run(
        max_transitions=2
    )

    assert clean.completed is True
    assert calls == ["curator", "curator", "consumer"]


def test_recovery_terminalizes_resolved_preparation_before_later_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resolved prepared source rejects once and cannot revive after later work."""
    from cafe.core.workflow_feedback import WorkflowFeedbackLedger

    issue_dir = tmp_path / ".cafe" / "issues" / "recover-resolved-preparation"
    ledger = WorkflowFeedbackLedger(issue_dir)
    _created, feedback = ledger.record(
        source_identity="external:recovery:resolved-preparation",
        source_kind="external_note",
        target_step="curator",
        content="Reject this source if it resolves before recovery.",
    )
    calls: list[str] = []
    interrupted = _recovery_fault_curation_runtime(issue_dir=issue_dir, calls=calls)

    def crash_before_settlement(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated resolved-preparation crash")

    monkeypatch.setattr(interrupted, "_commit_delivered_feedback", crash_before_settlement)
    with pytest.raises(RuntimeError, match="resolved-preparation"):
        interrupted.run(start_step="curator", max_transitions=2)
    assert ledger.reconcile_resolved({feedback.source_identity}) == 1

    resumed = _recovery_fault_curation_runtime(issue_dir=issue_dir, calls=calls)
    result = resumed.run(max_transitions=2)

    assert result.final_status_code == "INVALID_FEEDBACK_DELIVERY"
    assert calls == ["curator"]
    resolved = {entry.source_identity: entry for entry in ledger.load()}[feedback.source_identity]
    assert resolved.resolved is True
    assert resolved.consumed is False
    state = BlackboardStore(issue_dir).load_or_create("curator")
    prepared = [
        event for event in state.events if event.event_type == "workflow_feedback_delivery_prepared"
    ]
    rejected = [
        event for event in state.events if event.event_type == "workflow_feedback_delivery_rejected"
    ]
    assert rejected[-1].data["delivery_id"] == prepared[-1].data["delivery_id"]
    assert not any(event.event_type == "workflow_feedback_delivered" for event in state.events)

    _created, later = ledger.record(
        source_identity="external:recovery:later-after-resolution",
        source_kind="external_note",
        target_step="curator",
        content="Deliver this later independent source once.",
    )
    later_runtime = _recovery_fault_curation_runtime(issue_dir=issue_dir, calls=calls)
    delivered = later_runtime.run(start_step="curator", max_transitions=2)

    assert delivered.completed is True
    assert calls == ["curator", "curator", "consumer"]
    assert ledger.pending(target_step="curator") == []
    state = BlackboardStore(issue_dir).load_or_create("curator")
    delivered_events = [
        event for event in state.events if event.event_type == "workflow_feedback_delivered"
    ]
    assert delivered_events[-1].data["source_identities"] == [later.source_identity]

    clean = _recovery_fault_curation_runtime(issue_dir=issue_dir, calls=calls).run(
        max_transitions=2
    )

    assert clean.completed is True
    assert calls == ["curator", "curator", "consumer"]


def test_recovery_rejects_missing_new_preparation_after_terminal_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A terminal earlier operation cannot authorize a later curator handoff."""
    from cafe.core.workflow_feedback import WorkflowFeedbackLedger

    issue_dir = tmp_path / ".cafe" / "issues" / "recover-after-terminal-delivery"
    ledger = WorkflowFeedbackLedger(issue_dir)
    _created, first = ledger.record(
        source_identity="external:recovery:first-terminal-delivery",
        source_kind="external_note",
        target_step="curator",
        content="Deliver this first source exactly once.",
    )
    calls: list[str] = []

    initial = _recovery_fault_curation_runtime(issue_dir=issue_dir, calls=calls).run(
        start_step="curator", max_transitions=2
    )

    assert initial.completed is True
    assert calls == ["curator", "consumer"]
    state = BlackboardStore(issue_dir).load_or_create("curator")
    delivered_events = [
        event for event in state.events if event.event_type == "workflow_feedback_delivered"
    ]
    assert delivered_events[-1].data["source_identities"] == [first.source_identity]

    _created, interrupted_feedback = ledger.record(
        source_identity="external:recovery:missing-after-terminal",
        source_kind="external_note",
        target_step="curator",
        content="Require a distinct preparation for this later handoff.",
    )
    interrupted = _recovery_fault_curation_runtime(issue_dir=issue_dir, calls=calls)

    def crash_before_preparation(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated later pre-preparation crash")

    monkeypatch.setattr(interrupted, "_prepare_feedback_delivery", crash_before_preparation)
    with pytest.raises(RuntimeError, match="later pre-preparation"):
        interrupted.run(start_step="curator", max_transitions=2)
    assert calls == ["curator", "consumer", "curator"]
    assert ledger.reconcile_resolved({interrupted_feedback.source_identity}) == 1

    resumed = _recovery_fault_curation_runtime(issue_dir=issue_dir, calls=calls)
    rejected = resumed.run(max_transitions=2)

    assert rejected.final_status_code == "INVALID_FEEDBACK_DELIVERY"
    assert calls == ["curator", "consumer", "curator"]
    state = BlackboardStore(issue_dir).load_or_create("curator")
    failures = [
        event for event in state.events if event.event_type == "step_reconciliation_failed"
    ]
    assert "feedback_delivery_artifact" in failures[-1].data["missing_evidence"]

    _created, later = ledger.record(
        source_identity="external:recovery:independent-after-rejection",
        source_kind="external_note",
        target_step="curator",
        content="Deliver this later independent source once.",
    )
    delivered = _recovery_fault_curation_runtime(issue_dir=issue_dir, calls=calls).run(
        start_step="curator", max_transitions=2
    )

    assert delivered.completed is True
    assert calls == ["curator", "consumer", "curator", "curator", "consumer"]
    state = BlackboardStore(issue_dir).load_or_create("curator")
    delivered_events = [
        event for event in state.events if event.event_type == "workflow_feedback_delivered"
    ]
    assert delivered_events[-1].data["source_identities"] == [later.source_identity]

    clean = _recovery_fault_curation_runtime(issue_dir=issue_dir, calls=calls).run(
        max_transitions=2
    )

    assert clean.completed is True
    assert calls == ["curator", "consumer", "curator", "curator", "consumer"]


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

    callback_events: list[dict[str, object]] = []
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
        workflow_event_callback=callback_events.append,
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
    assert task.continuations == {
        "retry": "spec",
        "retry_fresh_session": "spec",
    }
    assert notifications == [task]
    assert len(callback_events) == 1
    assert callback_events[0].items() >= {
        "workflow_id": bb.workflow_id,
        "issue": "demo-agent-error",
        "event_type": "human_task",
        "step": "spec",
        "status_code": "INTERRUPTED:agent_rate_limit",
        "reason": "agent_rate_limit",
        "task_id": task.id,
    }.items()
    assert callback_events[0]["event_id"]
    assert callback_events[0]["sequence"] == 1
    assert callback_events[0]["occurred_at"]
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
    assert task.continuations == {
        "retry": "spec",
        "retry_fresh_session": "spec",
    }
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
    assert HumanTaskRecordStore(issue_dir).tasks()[0].continuations == {
        "retry": "spec",
        "retry_fresh_session": "spec",
    }
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


@pytest.mark.parametrize(
    ("intent", "policy_id", "input_schema", "questions"),
    [
        ("confirm_output", "output-review", "decision", None),
        (
            "need_clarification",
            "clarification-answers",
            "answers",
            """<?xml version="1.0" encoding="UTF-8"?>
<questions>
  <question id="scope">
    <title>Which scope should the specification cover?</title>
    <options><option>Current workflow only</option></options>
  </question>
</questions>
""",
        ),
    ],
)
def test_runtime_recovered_user_handoff_materializes_one_actionable_task(
    tmp_path: Path,
    intent: str,
    policy_id: str,
    input_schema: str,
    questions: str | None,
) -> None:
    """An agent-error recovery exposes each declared user handoff exactly once."""
    issue_dir = tmp_path / ".cafe" / "issues" / f"reconcile-user-{intent}"
    _write_publication_contract(issue_dir, playbook_id="tdd-qa", persisted=False)
    _write_baton(
        issue_dir,
        from_step="spec",
        to_owner="user",
        to_step="spec",
        intent=intent,
    )
    _write_iteration_evidence(issue_dir, "spec", questions=questions)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "current_step": "spec",
                "playbook_id": "tdd-qa",
                "artifacts": {},
                "events": [
                    {
                        "timestamp": "2026-04-26T23:00:00+08:00",
                        "step": "spec",
                        "event_type": "step_interrupted",
                        "message": "{}",
                        "data": {"step": "spec", "reason": "agent_error"},
                    }
                ],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=PlaybookLoader().load("tdd-qa"),
        executor=lambda *_args, **_kwargs: pytest.fail("reconciliation must not rerun the agent"),
    )
    _write_baton(
        issue_dir,
        from_step="spec",
        to_owner="user",
        to_step="user",
        intent=intent,
    )

    first = runtime.run()
    second = runtime.run()

    assert first.completed is False
    assert first.final_status_code == f"BATON_{intent.upper()}"
    assert second.completed is False
    tasks = HumanTaskRecordStore(issue_dir).tasks()
    assert len(tasks) == 1
    task = tasks[0]
    assert task.status is HumanTaskStatus.PENDING
    assert task.trigger == intent
    assert task.policy_id == policy_id
    assert task.expected_result["input_schema"] == input_schema
    bb = BlackboardStore(issue_dir).load_or_create("spec", playbook_id="tdd-qa")
    assert bb.current_step == "user"
    assert [e.event_type for e in bb.events].count("step_reconciled") == 1
    materialized = [e for e in bb.events if e.event_type == "human_task_materialized"]
    assert len(materialized) == 1
    assert materialized[0].data["task_id"] == task.id


def test_runtime_recovered_user_handoff_remains_actionable_after_reconciliation_marker_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash after the completion marker cannot strand a user handoff without its task."""
    issue_dir = tmp_path / ".cafe" / "issues" / "reconcile-user-marker-crash"
    _write_publication_contract(issue_dir, playbook_id="tdd-qa", persisted=False)
    _write_baton(
        issue_dir,
        from_step="spec",
        to_owner="user",
        to_step="spec",
        intent="confirm_output",
    )
    _write_iteration_evidence(issue_dir, "spec")
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "current_step": "spec",
                "playbook_id": "tdd-qa",
                "artifacts": {},
                "events": [
                    {
                        "timestamp": "2026-04-26T23:00:00+08:00",
                        "step": "spec",
                        "event_type": "step_interrupted",
                        "message": "{}",
                        "data": {"step": "spec", "reason": "agent_error"},
                    }
                ],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )
    interrupted_runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=PlaybookLoader().load("tdd-qa"),
        executor=lambda *_args, **_kwargs: pytest.fail("reconciliation must not rerun the agent"),
    )
    _write_baton(
        issue_dir,
        from_step="spec",
        to_owner="user",
        to_step="user",
        intent="confirm_output",
    )
    record_event = interrupted_runtime.blackboard_store.record_event

    def crash_after_reconciliation_marker(*args: object, **kwargs: object) -> object:
        result = record_event(*args, **kwargs)
        event_type = args[1] if len(args) > 1 else kwargs.get("event_type")
        if event_type == "step_reconciled":
            raise RuntimeError("simulated crash after reconciliation marker")
        return result

    monkeypatch.setattr(
        interrupted_runtime.blackboard_store,
        "record_event",
        crash_after_reconciliation_marker,
    )

    with pytest.raises(RuntimeError, match="simulated crash"):
        interrupted_runtime.run()

    interrupted_tasks = HumanTaskRecordStore(issue_dir).tasks()
    assert len(interrupted_tasks) == 1
    assert interrupted_tasks[0].status is HumanTaskStatus.PENDING

    resumed = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=PlaybookLoader().load("tdd-qa"),
        executor=lambda *_args, **_kwargs: pytest.fail("resume must not rerun the agent"),
    ).run()

    assert resumed.completed is False
    assert resumed.final_status_code == "BATON_CONFIRM_OUTPUT"
    tasks = HumanTaskRecordStore(issue_dir).tasks()
    assert len(tasks) == 1
    assert tasks[0].id == interrupted_tasks[0].id
    assert tasks[0].status is HumanTaskStatus.PENDING
    assert tasks[0].policy_id == "output-review"


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


def test_execute_one_iteration_does_not_retry_an_internal_executor_type_error(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "internal-type-error"
    _write_publication_contract(issue_dir, persisted=False)
    playbook = _simple_playbook()
    playbook["steps"]["spec"]["capability_requests"] = ["cafe.pr.publish"]
    received_pr_config: list[bool] = []

    def executor(step_name: str, step_def: dict, state: object, **kwargs) -> StepExecutionResult:
        received_pr_config.append("validated_pr_auto_create" in kwargs)
        raise TypeError("executor implementation failed")

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )

    result = runtime.run(start_step="spec", max_transitions=5)

    assert result.completed is False
    assert received_pr_config == [False]


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
    assert "A progress update is not a workflow handoff" in (captured_prompts[1] or "")
    bb = BlackboardStore(issue_dir).load_or_create("spec")
    rejected_events = [e for e in bb.events if e.event_type == "completion_handoff_missing"]
    assert len(rejected_events) == 1
    assert rejected_events[0].data["retry"] == 1
    assert rejected_events[0].data["will_retry"] is True


def test_runtime_retries_same_phase_baton_for_pr_then_succeeds(tmp_path: Path) -> None:
    """Baton-driven PR steps should also retry same-phase handoffs instead of pausing."""
    issue_dir = tmp_path / ".cafe" / "issues" / "retry-pr-same-phase"
    issue_dir.mkdir(parents=True)
    captured_prompts: list[str | None] = []
    call_count = [0]

    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "pr": {
                "skill": "spec_first",
                "role": "developer",
                "behavior": {"completion": "baton"},
                "on": {"workflow_complete": "_done"},
            },
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
    assert "A progress update is not a workflow handoff" in (captured_prompts[1] or "")
    bb = BlackboardStore(issue_dir).load_or_create("pr")
    rejected_events = [e for e in bb.events if e.event_type == "completion_handoff_missing"]
    assert len(rejected_events) == 1
    assert rejected_events[0].data["retry"] == 1
    assert rejected_events[0].data["will_retry"] is True


def test_baton_completion_missing_handoff_exhaustion_pauses_for_recovery(
    tmp_path: Path,
) -> None:
    """A baton-only step fails safely after bounded completion-error retries."""
    issue_dir = tmp_path / ".cafe" / "issues" / "baton-missing-handoff"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "publish": {
                "skill": "publish",
                "role": "developer",
                "behavior": {"completion": "baton"},
                "on": {"workflow_complete": "_done"},
            },
        },
    }
    calls: list[tuple[str | None, bool]] = []

    def executor(
        step_name: str,
        step_def: dict,
        state: object,
        extra_prompt: str | None = None,
        same_invocation_retry: bool = False,
    ) -> StepExecutionResult:
        calls.append((extra_prompt, same_invocation_retry))
        iteration_dir = issue_dir / "publish" / "iteration_001"
        iteration_dir.mkdir(parents=True, exist_ok=True)
        (iteration_dir / "iteration.json").write_text(
            json.dumps(
                {
                    "iteration": 1,
                    "cli": "codex",
                    "session_id": "publish-session",
                    "end_time": "2026-09-12T09:26:54+08:00",
                }
            ),
            encoding="utf-8",
        )
        return StepExecutionResult(response="Publishing is not finished.", artifacts={})

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    ).run(start_step="publish")

    assert result.completed is False
    assert result.final_status_code == "INTERRUPTED:agent_status_code_missing"
    assert len(calls) == 3
    assert calls[0] == (None, False)
    assert all(prompt and retry for prompt, retry in calls[1:])
    blackboard = BlackboardStore(issue_dir).load_or_create("publish")
    retry_events = [
        event for event in blackboard.events if event.event_type == "completion_handoff_missing"
    ]
    assert [event.data["will_retry"] for event in retry_events] == [True, True, False]
    task = HumanTaskRecordStore(issue_dir).tasks()[0]
    assert task.trigger == "agent_execution_interrupted"
    assert task.continuations == {
        "retry": "publish",
        "retry_fresh_session": "publish",
    }


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
