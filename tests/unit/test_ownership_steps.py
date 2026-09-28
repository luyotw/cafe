"""Ownership-step contracts (UT-001–UT-007 and UT-011)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cafe.core.automatic_steps import AutomaticExecutionResult, AutomaticExecutorRegistry
from cafe.core.blackboard import (
    BlackboardState,
    BlackboardStore,
    HandoffIntent,
    HandoffOwner,
)
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.human_tasks import HumanTaskBinding, HumanTaskDecision, HumanTaskPolicy
from cafe.core.playbook import PlaybookDefinition, StepConfig, validate_playbook
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime, StepIterationFrame
from cafe.playbooks.simulate import analyze_playbook, format_dot, format_text_report
from cafe.ui.human_tasks import apply_human_task_payload


def _approval_policy() -> HumanTaskPolicy:
    return HumanTaskPolicy(
        id="approval",
        pattern="no_changes_needed",
        prompt="Approve this work",
        input_schema="decision",
        decisions=(HumanTaskDecision(id="accept", label="Accept"),),
    )


def _mixed_owner_model() -> PlaybookDefinition:
    return PlaybookDefinition.model_validate(
        {
            "playbook": {"id": "mixed-owner"},
            "steps": {
                "agent": {"skill": "phase", "role": "operator", "on": {"await_agent": "human"}},
                "human": {
                    "skill": "phase",
                    "role": "operator",
                    "assignee_type": "human",
                    "human_tasks": [
                        {
                            "trigger": "initial",
                            "task_id": "approval",
                            "outcomes": {"accept": "automatic"},
                        }
                    ],
                    "on": {},
                },
                "automatic": {
                    "skill": "phase",
                    "role": "operator",
                    "assignee_type": "auto",
                    "automatic": {
                        "executor": "declared_transition",
                        "inputs": {"intent": "await_agent"},
                    },
                    "on": {"await_agent": "hybrid"},
                },
                "hybrid": {
                    "skill": "phase",
                    "role": "operator",
                    "assignee_type": "hybrid",
                    "human_tasks": [
                        {
                            "trigger": "approve",
                            "task_id": "approval",
                            "outcomes": {"accept": "hybrid"},
                        }
                    ],
                    "hybrid": {
                        "entry_portion": "draft",
                        "portions": [
                            {
                                "id": "draft",
                                "owner": "agent",
                                "on": {"await_agent": {"portion": "approve"}},
                            },
                            {
                                "id": "approve",
                                "owner": "human",
                                "on": {"accept": {"step": "_done"}},
                            },
                        ],
                    },
                    "on": {},
                },
            },
        }
    )


def test_ownership_schema_normalizes_legacy_and_requires_complete_explicit_shapes() -> None:
    """UT-001–UT-004: owner contracts are explicit and hybrid edges are typed."""
    legacy = StepConfig.model_validate({"skill": "phase", "role": "operator", "on": {}})
    assert legacy.assignee_type == "agent"
    assert "assignee_type" not in legacy.model_fields_set

    hybrid = StepConfig.model_validate(
        {
            "skill": "phase",
            "role": "operator",
            "assignee_type": "hybrid",
            "human_tasks": [
                {
                    "trigger": "approve",
                    "task_id": "approval",
                    "outcomes": {"accept": "mixed"},
                }
            ],
            "hybrid": {
                "entry_portion": "draft",
                "portions": [
                    {
                        "id": "draft",
                        "owner": "agent",
                        "on": {"await_agent": {"portion": "approve"}},
                    },
                    {
                        "id": "approve",
                        "owner": "human",
                        "on": {"accept": {"step": "_done"}},
                    },
                ],
            },
            "on": {},
        }
    )
    assert hybrid.hybrid is not None
    assert hybrid.hybrid.entry_portion == "draft"

    with pytest.raises(ValueError, match="automatic"):
        StepConfig.model_validate(
            {"skill": "phase", "role": "operator", "assignee_type": "auto", "on": {}}
        )
    with pytest.raises(ValueError, match="matching assignee_type"):
        StepConfig.model_validate(
            {
                "skill": "phase",
                "role": "operator",
                "automatic": {"executor": "advance"},
                "on": {},
            }
        )
    with pytest.raises(ValueError, match="JSON"):
        StepConfig.model_validate(
            {
                "skill": "phase",
                "role": "operator",
                "assignee_type": "auto",
                "automatic": {"executor": "declared_transition", "inputs": {"bad": object()}},
                "on": {"await_agent": "_done"},
            }
        )


def test_strict_validation_accepts_declared_non_agent_owners(tmp_path: Path) -> None:
    """UT-001–UT-004: strict loading no longer treats supported owners as reserved."""
    data = _mixed_owner_model().model_dump(mode="json", exclude_none=True, exclude_unset=True)
    data["playbook"]["applicability"] = {
        "summary": "A test workflow with declared non-agent owners.",
        "use_when": ["The workflow requires declared ownership."],
        "avoid_when": ["The workflow requires agent-only ownership."],
    }
    data["roles"] = {"operator": {}}
    data["skills"] = {"workflow": {"shared": []}, "chat": {"shared": []}}
    model = PlaybookDefinition.model_validate(data)
    contract = SimpleNamespace(
        prompt_inputs=(),
        prompt_references=(),
        required_tools=(),
        human_tasks=(_approval_policy(),),
        output_templates=None,
        checklist=None,
        checklist_overlay=None,
        execution_profile=None,
    )

    class SkillLoaderStub:
        global_root = tmp_path / "global"

        def get_skill_dir(self, _skill_name: str) -> Path:
            return tmp_path

        def get_workflow_declaration(self, _skill_name: str) -> SimpleNamespace:
            return contract

        def get_workflow_declaration_data(
            self, skill_name: str
        ) -> tuple[SimpleNamespace, dict]:
            return (
                SimpleNamespace(name=skill_name, source="test", directory=tmp_path),
                {},
            )

        def parse_workflow_declaration(
            self, _entry: SimpleNamespace, _raw: dict
        ) -> SimpleNamespace:
            return contract

        def validate_workflow_declaration_resources(
            self, _skill_dir: Path, _declaration: SimpleNamespace
        ) -> None:
            return None

    warnings = validate_playbook(
        model,
        skill_loader=SkillLoaderStub(),
        source="test",
        path=tmp_path / "mixed-owner.yml",
        strict=True,
    )

    assert len(warnings) == 1
    assert "assignee_type='hybrid'" in warnings[0]
    assert "next breaking release" in warnings[0]


def test_agent_completion_with_unchecked_checklist_is_not_published(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "checklist-gate"
    playbook = {
        "playbook": {"id": "checklist-gate"},
        "steps": {
            "build": {
                "skill": "phase",
                "role": "operator",
                "on": {"await_agent": "review"},
            },
            "review": {"skill": "phase", "role": "operator", "on": {}},
        },
    }

    def executor(step_name: str, _step_def: dict, state: BlackboardState, **_kwargs: object):
        iteration_dir = issue_dir / step_name / "iteration_001"
        iteration_dir.mkdir(parents=True)
        output = iteration_dir / "output.md"
        output.write_text("partial work\n", encoding="utf-8")
        (iteration_dir / "checklist.md").write_text("[ ] finish work\n", encoding="utf-8")
        BlackboardStore(issue_dir).update_handoff_contract(
            state,
            from_step=step_name,
            to_owner=HandoffOwner.AGENT,
            to_step="review",
            intent=HandoffIntent.AWAIT_AGENT,
            source="baton",
        )
        return StepExecutionResult(
            response="",
            artifacts={"code": str(output)},
            status_code=None,
            artifact_ready=True,
            agent_invoked=True,
        )

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    ).run(start_step="build")
    state = BlackboardStore(issue_dir).load_or_create("build")

    assert result.final_status_code == "CHECKLIST_VALIDATION_FAILED"
    assert state.current_step == "user"
    assert state.handoff_contract is not None
    assert state.handoff_contract.to_step == "user"
    assert "code" not in state.artifacts


@pytest.mark.parametrize(
    ("source", "checklist_state"),
    [
        ("baton", "unchecked"),
        ("unknown", "unchecked"),
        ("workflow.confirmation_gate", "unchecked"),
        ("baton", "unreadable"),
    ],
)
def test_resume_rejects_persisted_outbound_baton_with_unchecked_checklist(
    tmp_path: Path,
    source: str,
    checklist_state: str,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "resume-checklist-gate"
    playbook = {
        "playbook": {"id": "resume-checklist-gate"},
        "steps": {
            "build": {
                "skill": "phase",
                "role": "operator",
                "on": {"await_agent": "review"},
            },
            "review": {"skill": "phase", "role": "operator", "on": {}},
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("build", playbook_id="resume-checklist-gate")
    iteration_dir = issue_dir / "build" / "iteration_001"
    iteration_dir.mkdir(parents=True)
    (iteration_dir / "output.md").write_text("partial work\n", encoding="utf-8")
    checklist_path = iteration_dir / "checklist.md"
    if checklist_state == "unreadable":
        checklist_path.mkdir()
    else:
        checklist_path.write_text("[ ] finish work\n", encoding="utf-8")
    store.update_handoff_contract(
        state,
        from_step="build",
        to_owner=HandoffOwner.AGENT,
        to_step="review",
        intent=HandoffIntent.AWAIT_AGENT,
        source=source,
    )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=lambda *_args, **_kwargs: pytest.fail("resume must not execute downstream"),
    )
    result = runtime.run()
    reloaded = store.load_or_create("build")

    assert result.final_status_code == "CHECKLIST_VALIDATION_FAILED"
    assert reloaded.current_step == "user"
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.to_step == "user"


def test_clarification_handoff_allows_unchecked_checklist(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "clarification-checklist"
    playbook = {
        "playbook": {"id": "clarification-checklist"},
        "steps": {
            "build": {
                "skill": "phase",
                "role": "operator",
                "on": {"need_clarification": "build"},
            }
        },
    }

    def executor(step_name: str, _step_def: dict, state: BlackboardState, **_kwargs: object):
        iteration_dir = issue_dir / step_name / "iteration_001"
        iteration_dir.mkdir(parents=True)
        (iteration_dir / "output.md").write_text("Need an answer\n", encoding="utf-8")
        (iteration_dir / "checklist.md").write_text("[ ] blocked item\n", encoding="utf-8")
        BlackboardStore(issue_dir).update_handoff_contract(
            state,
            from_step=step_name,
            to_owner=HandoffOwner.USER,
            to_step="user",
            intent=HandoffIntent.NEED_CLARIFICATION,
            source="baton",
        )
        return StepExecutionResult(response="", artifacts={}, agent_invoked=True)

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    ).run(start_step="build")

    assert result.final_status_code == "BATON_NEED_CLARIFICATION"
    assert BlackboardStore(issue_dir).load_or_create("build").current_step == "user"


def test_hook_only_completion_is_not_forced_to_own_agent_checklist(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "hook-only-checklist"
    playbook = {
        "playbook": {"id": "hook-only-checklist"},
        "steps": {
            "build": {
                "skill": "phase",
                "role": "operator",
                "on": {"await_agent": "review"},
            },
            "review": {"skill": "phase", "role": "operator", "on": {}},
        },
    }

    def executor(step_name: str, _step_def: dict, state: BlackboardState, **_kwargs: object):
        BlackboardStore(issue_dir).update_handoff_contract(
            state,
            from_step=step_name,
            to_owner=HandoffOwner.AGENT,
            to_step="review",
            intent=HandoffIntent.AWAIT_AGENT,
            source="hook",
        )
        return StepExecutionResult(response="", artifacts={}, agent_invoked=False)

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    ).run(start_step="build", single_step=True)

    assert result.final_status_code == "BATON_AWAIT_AGENT"
    assert BlackboardStore(issue_dir).load_or_create("build").current_step == "review"


def test_resume_preserves_durable_hook_only_checklist_exception(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "resume-hook-only-checklist"
    playbook = {
        "playbook": {"id": "resume-hook-only-checklist"},
        "steps": {
            "build": {
                "skill": "phase",
                "role": "operator",
                "on": {"await_agent": "review"},
            },
            "review": {"skill": "phase", "role": "operator", "on": {}},
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("build", playbook_id="resume-hook-only-checklist")
    iteration_dir = issue_dir / "build" / "iteration_001"
    iteration_dir.mkdir(parents=True)
    (iteration_dir / "iteration.json").write_text(
        json.dumps({"agent_invoked": False}),
        encoding="utf-8",
    )
    store.update_handoff_contract(
        state,
        from_step="build",
        to_owner=HandoffOwner.AGENT,
        to_step="review",
        intent=HandoffIntent.AWAIT_AGENT,
        source="workflow.status_transition_adapter",
    )
    executed_steps: list[str] = []

    def executor(step_name: str, _step_def: dict, state: BlackboardState, **_kwargs: object):
        executed_steps.append(step_name)
        store.update_handoff_contract(
            state,
            from_step=step_name,
            to_owner=HandoffOwner.DONE,
            to_step="done",
            intent=HandoffIntent.WORKFLOW_COMPLETE,
            source="hook",
        )
        return StepExecutionResult(response="", artifacts={}, agent_invoked=False)

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    ).run()

    assert result.completed is True
    assert executed_steps == ["review"]


def test_automatic_registry_is_closed_and_returns_declared_intent() -> None:
    """UT-003/UT-006: only a host-supplied registered executor can run."""
    calls: list[dict[str, object]] = []
    registry = AutomaticExecutorRegistry(
        {
            "advance": lambda inputs: calls.append(dict(inputs))
            or AutomaticExecutionResult("await_agent")
        }
    )

    assert registry.execute("advance", {"value": 1}).intent == "await_agent"
    assert calls == [{"value": 1}]
    with pytest.raises(ValueError, match="not registered"):
        registry.execute("./untrusted-script", {})


def test_human_owner_pauses_idempotently_without_invoking_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UT-005: a human owner materializes one durable wait and never calls the agent."""
    issue_dir = tmp_path / ".cafe" / "issues" / "human-owner"
    binding = HumanTaskBinding(trigger="initial", task_id="approval", outcomes={"accept": "after"})
    monkeypatch.setattr(
        "cafe.core.workflow_runtime.resolve_step_human_task",
        lambda **_: (_approval_policy(), binding),
    )
    calls = 0

    def executor(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("human-owned work must not invoke the agent executor")

    playbook = {
        "playbook": {"id": "owner-test"},
        "steps": {
            "approval": {
                "skill": "phase",
                "role": "operator",
                "assignee_type": "human",
                "human_tasks": [binding.model_dump()],
                "on": {},
            },
            "after": {"skill": "phase", "role": "operator", "on": {}},
        },
    }
    paused = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=playbook, executor=executor
    ).run(start_step="approval")
    recovered = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=playbook, executor=executor
    ).run()

    records = HumanTaskRecordStore(issue_dir)
    assert paused.completed is False
    assert recovered.completed is False
    assert calls == 0
    assert len(records.tasks()) == 1
    assert records.active_wait_state(records.tasks()[0].workflow_id) is not None


def test_automatic_owner_dispatches_registry_without_agent(tmp_path: Path) -> None:
    """UT-006: automatic work transitions through its registered host executor only."""
    issue_dir = tmp_path / ".cafe" / "issues" / "automatic-owner"
    agent_calls = 0
    automatic_calls: list[dict[str, object]] = []

    def executor(*_args: object, **_kwargs: object) -> object:
        nonlocal agent_calls
        agent_calls += 1
        raise AssertionError("automatic work must not fall back to the agent")

    registry = AutomaticExecutorRegistry(
        {
            "advance": lambda inputs: automatic_calls.append(dict(inputs))
            or AutomaticExecutionResult("await_agent")
        }
    )
    playbook = {
        "playbook": {"id": "owner-test"},
        "steps": {
            "automatic": {
                "skill": "phase",
                "role": "operator",
                "assignee_type": "auto",
                "automatic": {"executor": "advance", "inputs": {"safe": True}},
                "on": {"await_agent": "_done"},
            }
        },
    }
    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
        automatic_registry=registry,
    ).run(start_step="automatic")

    assert result.completed is True
    assert agent_calls == 0
    assert automatic_calls == [{"safe": True}]


def test_automatic_inputs_are_validated_before_a_visit_is_persisted(tmp_path: Path) -> None:
    """UT-003: executor-specific automatic input is rejected before runtime state."""
    issue_dir = tmp_path / ".cafe" / "issues" / "invalid-automatic-input"
    playbook = {
        "playbook": {"id": "owner-test"},
        "steps": {
            "automatic": {
                "skill": "phase",
                "role": "operator",
                "assignee_type": "auto",
                "automatic": {"executor": "declared_transition", "inputs": {}},
                "on": {"await_agent": "_done"},
            }
        },
    }

    with pytest.raises(ValueError, match="requires a non-empty inputs.intent"):
        BlackboardWorkflowRuntime(
            issue_dir=issue_dir,
            playbook=playbook,
            executor=lambda *_args, **_kwargs: pytest.fail("automatic work must not run"),
        )

    assert BlackboardStore(issue_dir).load_or_create("automatic").step_attempt_counts == {}


def test_invalid_automatic_result_is_rejected_before_visits_or_artifacts(tmp_path: Path) -> None:
    """UT-003/UT-006: an undeclared result cannot create workflow progress."""
    issue_dir = tmp_path / ".cafe" / "issues" / "invalid-automatic-result"
    playbook = {
        "playbook": {"id": "owner-test"},
        "steps": {
            "automatic": {
                "skill": "phase",
                "role": "operator",
                "assignee_type": "auto",
                "automatic": {"executor": "invalid-result", "inputs": {}},
                "on": {"await_agent": "_done"},
            }
        },
    }
    registry = AutomaticExecutorRegistry(
        {
            "invalid-result": lambda _inputs: AutomaticExecutionResult(
                "undeclared", artifacts={"escaped": "outside-the-contract"}
            )
        }
    )

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=lambda *_args, **_kwargs: pytest.fail("automatic work must not call an agent"),
        automatic_registry=registry,
    ).run(start_step="automatic")

    state = BlackboardStore(issue_dir).load_or_create("automatic")
    assert result.final_status_code == "AUTOMATIC_EXECUTOR_REJECTED"
    assert state.step_attempt_counts == {}
    assert "escaped" not in state.artifacts


def test_unknown_automatic_executor_is_rejected_before_a_visit_is_persisted(tmp_path: Path) -> None:
    """UT-003/IT-002: an undeclared executor cannot mutate workflow progress."""
    issue_dir = tmp_path / ".cafe" / "issues" / "automatic-owner"
    playbook = {
        "playbook": {"id": "owner-test"},
        "steps": {
            "automatic": {
                "skill": "phase",
                "role": "operator",
                "assignee_type": "auto",
                "automatic": {"executor": "not-registered", "inputs": {}},
                "on": {"await_agent": "_done"},
            }
        },
    }

    with pytest.raises(ValueError, match="not registered"):
        BlackboardWorkflowRuntime(
            issue_dir=issue_dir,
            playbook=playbook,
            executor=lambda *_args, **_kwargs: pytest.fail("agent must not run"),
        )

    state = BlackboardStore(issue_dir).load_or_create("automatic", playbook_id="owner-test")
    assert state.step_attempt_counts == {}


def test_hybrid_owner_resumes_only_its_declared_portion_after_matching_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UT-007/UT-010: hybrid cursor contains agent completion and durable human resume."""
    issue_dir = tmp_path / ".cafe" / "issues" / "hybrid-owner"
    binding = HumanTaskBinding(trigger="approve", task_id="approval", outcomes={"accept": "mixed"})
    monkeypatch.setattr(
        "cafe.core.workflow_runtime.resolve_step_human_task",
        lambda **_: (_approval_policy(), binding),
    )
    monkeypatch.setattr(
        "cafe.ui.human_tasks.resolve_step_human_task",
        lambda **_: (_approval_policy(), binding),
    )
    calls: list[str] = []

    def executor(step_name: str, step_def: dict, _state: object, **_kwargs: object):
        calls.append(step_def["hybrid_portion"]["id"])
        return ("confirmed", {})

    playbook = {
        "playbook": {"id": "owner-test"},
        "steps": {
            "mixed": {
                "skill": "phase",
                "role": "operator",
                "assignee_type": "hybrid",
                "max_attempts_per_cycle": 1,
                "human_tasks": [binding.model_dump()],
                "hybrid": {
                    "entry_portion": "draft",
                    "portions": [
                        {
                            "id": "draft",
                            "owner": "agent",
                            "on": {"await_agent": {"portion": "approve"}},
                        },
                        {
                            "id": "approve",
                            "owner": "human",
                            "on": {"accept": {"portion": "finalize"}},
                        },
                        {
                            "id": "finalize",
                            "owner": "agent",
                            "on": {"await_agent": {"step": "_done"}},
                        },
                    ],
                },
                "on": {},
            }
        },
    }
    paused = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=playbook, executor=executor
    ).run(start_step="mixed")
    task = HumanTaskRecordStore(issue_dir).tasks()[0]
    state = BlackboardStore(issue_dir).load_or_create("mixed")
    applied = apply_human_task_payload(
        issue_dir=issue_dir,
        playbook_data=playbook,
        blackboard=state,
        from_step="mixed",
        trigger="approve",
        raw_payload={"task": "approval", "decision": "accept", "human_task_id": task.id},
        source="test",
    )
    completed = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=playbook, executor=executor
    ).run()

    assert paused.final_status_code == "HYBRID_HUMAN_TASK_PENDING"
    assert applied.target == "mixed"
    assert completed.completed is True
    assert calls == ["draft", "finalize"]
    assert BlackboardStore(issue_dir).load_or_create("mixed").step_attempt_counts == {"mixed": 1}


@pytest.mark.parametrize("failed_signal", ["unchecked", "malformed_baton", "missing_status"])
def test_hybrid_agent_portion_cannot_advance_with_unchecked_checklist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_signal: str
) -> None:
    monkeypatch.setattr(BlackboardWorkflowRuntime, "_notify_new_human_task", lambda *_: None)
    issue_dir = tmp_path / ".cafe" / "issues" / "hybrid-checklist-gate"
    binding = HumanTaskBinding(trigger="approve", task_id="approval", outcomes={"accept": "mixed"})
    playbook = {
        "playbook": {"id": "hybrid-checklist-gate"},
        "steps": {
            "mixed": {
                "skill": "phase",
                "role": "operator",
                "assignee_type": "hybrid",
                "human_tasks": [binding.model_dump()],
                "hybrid": {
                    "entry_portion": "draft",
                    "portions": [
                        {
                            "id": "draft",
                            "owner": "agent",
                            "on": {"await_agent": {"portion": "approve"}},
                        },
                        {
                            "id": "approve",
                            "owner": "human",
                            "on": {"accept": {"step": "_done"}},
                        },
                    ],
                },
                "on": {},
            }
        },
    }

    def executor(step_name: str, _step_def: dict, _state: object, **_kwargs: object):
        iteration_dir = issue_dir / step_name / "iteration_001"
        iteration_dir.mkdir(parents=True)
        output = iteration_dir / "output.md"
        output.write_text("partial draft\n", encoding="utf-8")
        (iteration_dir / "checklist.md").write_text("[ ] finish draft\n", encoding="utf-8")
        return StepExecutionResult(
            response="" if failed_signal == "missing_status" else "confirmed",
            artifacts={"code": str(output)},
            status_code=None if failed_signal == "missing_status" else "confirmed",
            artifact_ready=True,
            agent_invoked=True,
            events=(
                []
                if failed_signal == "unchecked"
                else [{"type": "checklist_validation_failed"}]
                + (
                    [{"type": "hybrid_portion_baton", "payload": "bad baton"}]
                    if failed_signal == "malformed_baton"
                    else []
                )
            ),
        )

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    ).run(start_step="mixed")
    state = BlackboardStore(issue_dir).load_or_create("mixed")

    assert result.final_status_code == "CHECKLIST_VALIDATION_FAILED"
    assert state.current_step == "user"
    assert state.ownership_cursor is not None
    assert state.ownership_cursor["portion"] == "draft"
    assert "code" not in state.artifacts

    (task,) = HumanTaskRecordStore(issue_dir).tasks()
    assert task.status.value == "pending"
    assert task.continuations == {"retry": "mixed", "retry_fresh_session": "mixed"}


@pytest.mark.parametrize(
    ("captured", "explicit_status_code"),
    [
        ("{not-json", None),
        (
            json.dumps(
                {
                    "from_step": "other",
                    "to_owner": "agent",
                    "to_step": "mixed",
                    "intent": "await_agent",
                    "source": "hybrid_portion:mixed:draft",
                }
            ),
            None,
        ),
        (
            json.dumps(
                {
                    "from_step": "mixed",
                    "to_owner": "user",
                    "to_step": "mixed",
                    "intent": "await_agent",
                    "source": "hybrid_portion:mixed:draft",
                }
            ),
            None,
        ),
        (
            json.dumps(
                {
                    "from_step": "mixed",
                    "to_owner": "agent",
                    "to_step": "other",
                    "intent": "await_agent",
                    "source": "hybrid_portion:mixed:draft",
                }
            ),
            None,
        ),
        (
            json.dumps(
                {
                    "from_step": "mixed",
                    "to_owner": "agent",
                    "to_step": "mixed",
                    "intent": "await_agent",
                    "source": "hybrid_portion:mixed:other",
                }
            ),
            None,
        ),
        (
            json.dumps(
                {
                    "from_step": "mixed",
                    "to_owner": "agent",
                    "to_step": "mixed",
                    "intent": "await_agent",
                    "source": "hybrid_portion:mixed:draft",
                }
            ),
            "need_clarification",
        ),
    ],
)
def test_hybrid_rejects_malformed_or_conflicting_captured_batons(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    captured: str,
    explicit_status_code: str | None,
) -> None:
    """UT-010: only an unambiguous declared portion completion can proceed."""
    issue_dir = tmp_path / ".cafe" / "issues" / "hybrid-baton"
    binding = HumanTaskBinding(trigger="approve", task_id="approval", outcomes={"accept": "mixed"})
    playbook = {
        "playbook": {"id": "owner-test"},
        "steps": {
            "mixed": {
                "skill": "phase",
                "role": "operator",
                "assignee_type": "hybrid",
                "human_tasks": [binding.model_dump()],
                "hybrid": {
                    "entry_portion": "draft",
                    "portions": [
                        {
                            "id": "draft",
                            "owner": "agent",
                            "on": {"await_agent": {"portion": "approve"}},
                        },
                        {
                            "id": "approve",
                            "owner": "human",
                            "on": {"accept": {"step": "_done"}},
                        },
                    ],
                },
                "on": {},
            }
        },
    }
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=lambda *_args, **_kwargs: pytest.fail("captured result bypasses the agent"),
    )
    frame = StepIterationFrame(
        execution_result=SimpleNamespace(
            events=[{"type": "hybrid_portion_baton", "payload": captured}]
        ),
        response="",
        artifacts={},
        explicit_status_code=explicit_status_code,
        auto_continue=False,
    )
    monkeypatch.setattr(runtime, "_execute_one_iteration", lambda **_kwargs: frame)

    result = runtime.run(start_step="mixed")

    assert result.final_status_code == "HYBRID_RESULT_REJECTED"
    assert runtime.blackboard.current_step == "mixed"


@pytest.mark.parametrize("schema_version", [2, 3])
def test_executed_workflow_without_external_authorities_is_not_migrated(
    tmp_path: Path, schema_version: int,
) -> None:
    store = BlackboardStore(tmp_path / ".cafe" / "issues" / "old-workflow")
    store.issue_dir.mkdir(parents=True)
    original = json.dumps({
        "schema_version": schema_version, "current_step": "review",
        "workflow_id": "old-workflow", "handoff_summary": "already executed",
    })
    store.file_path.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError, match="workflow authorities are absent"):
        store.load_or_create("review")

    assert store.file_path.read_text(encoding="utf-8") == original
    assert not store.audit.binding.exists()
    assert not store.receipts_path.exists()





def test_simulation_reports_all_owners_without_creating_runtime_state(tmp_path: Path) -> None:
    """UT-008/IT-004: ownership preview is pure and exposes waits/authority."""
    result = analyze_playbook(_mixed_owner_model())
    text = format_text_report(result)

    assert "agent: owner=agent" in text
    assert "human: owner=human" in text
    assert "automatic executor=declared_transition" in text
    assert "portion=approve owner=human wait" in text
    assert not (tmp_path / ".cafe").exists()
    dot = format_dot(result)
    assert "owner=human" in dot
    assert "executor=declared_transition" in dot
    assert "portion=approve" in dot


def test_persisted_visit_limit_survives_a_separate_runtime_instance(tmp_path: Path) -> None:
    """UT-009/IT-005: a restart cannot reset a top-level owner loop limit."""
    issue_dir = tmp_path / ".cafe" / "issues" / "persistent-loop"
    playbook = {
        "playbook": {"id": "owner-test"},
        "steps": {
            "loop": {
                "skill": "phase",
                "role": "operator",
                "max_attempts_per_cycle": 1,
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "loop"},
            }
        },
    }

    def executor(*_args: object, **_kwargs: object):
        return ("confirmed", {})

    BlackboardWorkflowRuntime(issue_dir=issue_dir, playbook=playbook, executor=executor).run(
        start_step="loop", single_step=True
    )
    with pytest.raises(RuntimeError, match="max_attempts_per_cycle=1"):
        BlackboardWorkflowRuntime(issue_dir=issue_dir, playbook=playbook, executor=executor).run(
            start_step="loop", single_step=True
        )


@pytest.mark.parametrize("completion", ["status_code", "baton"])
@pytest.mark.parametrize("decision", ["retry", "retry_fresh_session"])
def test_checklist_failure_has_durable_recovery_and_retries_same_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, completion: str, decision: str
) -> None:
    """A clean provider exit with rejected output remains recoverable after restart."""
    issue_dir = tmp_path / ".cafe/issues/checklist-recovery"
    playbook = {
        "playbook": {"id": "checklist-recovery"},
        "steps": {
            "build": {
                "skill": "phase",
                "role": "operator",
                "behavior": {"completion": completion},
                "on": {"await_agent": "_done"},
            },
        },
    }
    iteration = issue_dir / "build/iteration_001"
    calls = []
    callbacks = []
    notifications = []
    monkeypatch.setattr(
        BlackboardWorkflowRuntime,
        "_notify_new_human_task",
        lambda self, task: notifications.append(task),
    )

    def executor(step_name, _step_def, state, **_kwargs):
        calls.append(step_name)
        iteration.mkdir(parents=True, exist_ok=True)
        output = iteration / "output.md"
        output.write_text("Saved substantive work\n")
        (iteration / "checklist.md").write_text(
            "[ ] verify work\n" if len(calls) == 1 else "[x] verify work\n"
        )
        (iteration / "iteration.json").write_text(
            json.dumps(
                {
                    "iteration": 1,
                    "cli": "claude",
                    "session_id": "original-session",
                    "model": "configured-model",
                    "agent_invoked": True,
                    "end_time": "2026-09-27T09:00:00+08:00",
                }
            )
        )
        # The generic executor pins the baton after exhausting completion repair.
        failed = len(calls) == 1
        BlackboardStore(issue_dir).update_handoff_contract(
            state,
            from_step=step_name,
            to_owner=HandoffOwner.AGENT if failed else HandoffOwner.DONE,
            to_step=step_name if failed else "done",
            intent=HandoffIntent.AWAIT_AGENT if failed else HandoffIntent.WORKFLOW_COMPLETE,
            status_code="CHECKLIST_VALIDATION_FAILED" if failed else "confirmed",
            source="workflow.completion_validation" if failed else "baton",
        )
        return StepExecutionResult(
            response="",
            artifacts={"result": str(output)},
            status_code=None if completion == "baton" else "confirmed",
            artifact_ready=not failed,
            agent_invoked=True,
            events=[{"type": "checklist_validation_failed"}] if failed else [],
        )

    def run():
        return BlackboardWorkflowRuntime(
            issue_dir=issue_dir,
            playbook=playbook,
            executor=executor,
            workflow_event_callback=callbacks.append,
        ).run()

    result = run()
    assert result.final_status_code == "CHECKLIST_VALIDATION_FAILED"
    assert calls == ["build"]
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("build")
    records = HumanTaskRecordStore(issue_dir)
    (task,) = records.tasks()
    assert result.detail == task.id
    assert state.current_step == "user"
    assert state.handoff_contract.to_owner == HandoffOwner.USER
    assert "result" not in state.artifacts
    assert notifications == [task]
    assert [(event["event_type"], event["status_code"]) for event in callbacks] == [
        ("human_task", "CHECKLIST_VALIDATION_FAILED")
    ]
    assert (iteration / "output.md").read_text() == "Saved substantive work\n"
    assert (
        json.loads((iteration / "iteration.json").read_text())["workflow_completion_trusted"]
        is False
    )
    assert any(event.event_type == "checklist_validation_failed" for event in state.events)

    run()  # Restarting while waiting cannot retry or create a second task.
    assert calls == ["build"]
    assert [item.id for item in records.tasks()] == [task.id]
    applied = apply_human_task_payload(
        issue_dir=issue_dir,
        playbook_data=playbook,
        blackboard=store.load_or_create("build"),
        from_step="build",
        trigger=task.trigger,
        raw_payload={"task": task.policy_id, "human_task_id": task.id, "decision": decision},
        source="test",
    )
    assert applied.rejection is None
    assert applied.target == "build"
    recorded = records.get_result(task.id)
    if decision == "retry_fresh_session":
        assert (
            recorded.payload["session_continuation"]["previous"]["session_id"] == "original-session"
        )
    else:
        assert "session_continuation" not in recorded.payload
    assert run().completed
    assert calls == ["build", "build"]


def test_resume_materializes_recovery_for_preexisting_checklist_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    issue_dir = tmp_path / ".cafe/issues/old-checklist-failure"
    playbook = {
        "playbook": {"id": "old-checklist-failure"},
        "steps": {
            "build": {"skill": "phase", "role": "operator", "on": {"await_agent": "_done"}},
        },
    }
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("build", playbook_id="old-checklist-failure")
    iteration = issue_dir / "build/iteration_001"
    iteration.mkdir(parents=True)
    (iteration / "checklist.md").write_text("[ ] unfinished\n")
    store.update_handoff_contract(
        state,
        from_step="build",
        to_owner=HandoffOwner.AGENT,
        to_step="build",
        intent=HandoffIntent.AWAIT_AGENT,
        status_code="CHECKLIST_VALIDATION_FAILED",
        source="workflow.checklist_validation",
    )
    monkeypatch.setattr(BlackboardWorkflowRuntime, "_notify_new_human_task", lambda *_: None)
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=lambda *_args, **_kwargs: pytest.fail("recovery requires a user decision"),
    )
    assert runtime.run().final_status_code == "CHECKLIST_VALIDATION_FAILED"
    assert store.load_or_create("build").current_step == "user"
    (task,) = HumanTaskRecordStore(issue_dir).tasks()
    assert task.status.value == "pending"
    assert task.continuations == {"retry": "build", "retry_fresh_session": "build"}
