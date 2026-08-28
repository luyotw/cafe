"""Ownership-step contracts (UT-001–UT-007 and UT-011)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cafe.core.automatic_steps import AutomaticExecutionResult, AutomaticExecutorRegistry
from cafe.core.blackboard import BLACKBOARD_SCHEMA_VERSION, BlackboardState, BlackboardStore
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.human_tasks import HumanTaskBinding, HumanTaskDecision, HumanTaskPolicy
from cafe.core.playbook import PlaybookDefinition, StepConfig, validate_playbook
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
    data["roles"] = {"operator": {}}
    data["skills"] = {"workflow": {"shared": []}, "chat": {"shared": []}}
    model = PlaybookDefinition.model_validate(data)
    contract = SimpleNamespace(
        prompt_inputs=(),
        required_tools=(),
        human_tasks=(_approval_policy(),),
        output_templates=None,
    )

    class SkillLoaderStub:
        def get_skill_dir(self, _skill_name: str) -> Path:
            return tmp_path

        def get_workflow_contract(self, _skill_name: str) -> SimpleNamespace:
            return contract

    assert validate_playbook(
        model,
        skill_loader=SkillLoaderStub(),
        source="test",
        path=tmp_path / "mixed-owner.yml",
        strict=True,
    ) == []


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


def test_blackboard_v2_state_migrates_to_v4_without_losing_handoff(tmp_path: Path) -> None:
    """UT-011: v2 data gains ownership defaults and persists as schema v4."""
    issue_dir = tmp_path / ".cafe" / "issues" / "migration"
    store = BlackboardStore(issue_dir)
    issue_dir.mkdir(parents=True)
    store.file_path.write_text(
        '{"schema_version": 2, "current_step": "approval", "playbook_id": "owner-test", '
        '"workflow_id": "stable-id", "handoff_summary": "waiting"}',
        encoding="utf-8",
    )

    state = store.load_or_create("approval", playbook_id="owner-test")
    assert state.schema_version == BLACKBOARD_SCHEMA_VERSION == 4
    assert state.ownership_cursor is None
    assert state.step_attempt_counts == {}
    store.save(state)
    assert '"schema_version": 4' in store.file_path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="future"):
        BlackboardState.from_dict({"schema_version": 99}, initial_step="approval")


def test_blackboard_v3_attempt_state_migrates_without_losing_cycle_progress(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "attempt-migration"
    store = BlackboardStore(issue_dir)
    issue_dir.mkdir(parents=True)
    store.file_path.write_text(
        '{"schema_version": 3, "current_step": "review", '
        '"step_visit_counts": {"review": 2}, '
        '"ownership_cursor": {"step": "review", "visit_count": 2}}',
        encoding="utf-8",
    )

    state = store.load_or_create("review")
    assert state.schema_version == BLACKBOARD_SCHEMA_VERSION == 4
    assert state.step_attempt_counts == {"review": 2}
    assert state.ownership_cursor == {"step": "review", "attempt_count": 2}

    store.save(state)
    persisted = store.file_path.read_text(encoding="utf-8")
    assert '"step_attempt_counts"' in persisted
    assert '"step_visit_counts"' not in persisted
    assert '"attempt_count"' in persisted
    assert '"visit_count"' not in persisted


def test_blackboard_v3_attempt_events_migrate_to_one_v4_shape(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "attempt-event-migration"
    store = BlackboardStore(issue_dir)
    issue_dir.mkdir(parents=True)
    store.file_path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "current_step": "review",
                "events": [
                    {
                        "timestamp": "2026-08-28T00:00:00+00:00",
                        "step": "review",
                        "event_type": "step_completed",
                        "message": '{"step": "review", "visit": 2}',
                        "data": {"step": "review", "visit": 2},
                    },
                    {
                        "timestamp": "2026-08-28T00:00:01+00:00",
                        "step": "review",
                        "event_type": "loop_detected",
                        "message": "legacy loop",
                        "data": {
                            "step": "review",
                            "visits": 6,
                            "max_iterations": 5,
                        },
                    },
                    {
                        "timestamp": "2026-08-28T00:00:02+00:00",
                        "step": "review",
                        "event_type": "step_visit_count_reset",
                        "message": "legacy reset",
                        "data": {"step": "review", "completed_visits": 2},
                    },
                    {
                        "timestamp": "2026-08-28T00:00:03+00:00",
                        "step": "audit",
                        "event_type": "custom_audit",
                        "message": "unrelated legacy vocabulary",
                        "data": {"visit": "homepage", "max_iterations": "external"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    state = store.load_or_create("review")

    assert [event.event_type for event in state.events] == [
        "step_completed",
        "loop_detected",
        "step_attempt_count_reset",
        "custom_audit",
    ]
    assert state.events[0].data["attempt"] == 2
    assert state.events[1].data["attempts"] == 6
    assert state.events[1].data["max_attempts_per_cycle"] == 5
    assert state.events[2].data["completed_attempts"] == 2
    assert all("visit" not in event.message for event in state.events[:3])
    assert state.events[3].data == {"visit": "homepage", "max_iterations": "external"}
    assert state.events[3].message == "unrelated legacy vocabulary"

    store.save(state)
    persisted = json.loads(store.file_path.read_text(encoding="utf-8"))
    migrated_events = persisted["events"][:3]
    assert '"step_visit_count_reset"' not in json.dumps(migrated_events)
    assert '"max_iterations"' not in json.dumps(migrated_events)
    assert '"completed_visits"' not in json.dumps(migrated_events)
    assert persisted["events"][3]["data"] == {
        "visit": "homepage",
        "max_iterations": "external",
    }


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
