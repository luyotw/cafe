"""End-to-end owner-boundary journeys (IT-001–IT-006)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from cafe.core.automatic_steps import AutomaticExecutionResult, AutomaticExecutorRegistry
from cafe.core.blackboard import BLACKBOARD_SCHEMA_VERSION, BlackboardStore
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.human_tasks import HumanTaskBinding, HumanTaskDecision, HumanTaskPolicy
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.ui.cli import app
from cafe.ui.human_tasks import apply_human_task_payload


def _approval_policy() -> HumanTaskPolicy:
    return HumanTaskPolicy(
        id="approval",
        pattern="no_changes_needed",
        prompt="Approve this work",
        input_schema="decision",
        decisions=(HumanTaskDecision(id="accept", label="Accept"),),
    )


def test_agent_human_agent_journey_resumes_across_runtime_instances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IT-001: human work is durable and agent work resumes only after its result."""
    issue_dir = tmp_path / ".cafe" / "issues" / "agent-human-agent"
    binding = HumanTaskBinding(trigger="initial", task_id="approval", outcomes={"accept": "final"})
    monkeypatch.setattr(
        "cafe.core.workflow_runtime.resolve_step_human_task",
        lambda **_kwargs: (_approval_policy(), binding),
    )
    monkeypatch.setattr(
        "cafe.ui.human_tasks.resolve_step_human_task",
        lambda **_kwargs: (_approval_policy(), binding),
    )
    agent_steps: list[str] = []

    def executor(step_name: str, *_args: object, **_kwargs: object) -> tuple[str, dict]:
        agent_steps.append(step_name)
        return "confirmed", {}

    playbook = {
        "playbook": {"id": "owner-integration"},
        "steps": {
            "draft": {
                "skill": "phase",
                "role": "operator",
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "approval"},
            },
            "approval": {
                "skill": "phase",
                "role": "operator",
                "assignee_type": "human",
                "human_tasks": [binding.model_dump()],
                "on": {},
            },
            "final": {
                "skill": "phase",
                "role": "operator",
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            },
        },
    }

    paused = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=playbook, executor=executor
    ).run(start_step="draft")
    task = HumanTaskRecordStore(issue_dir).tasks()[0]
    state = BlackboardStore(issue_dir).load_or_create("draft")
    applied = apply_human_task_payload(
        issue_dir=issue_dir,
        playbook_data=playbook,
        blackboard=state,
        from_step="approval",
        trigger="initial",
        raw_payload={"task": "approval", "decision": "accept", "human_task_id": task.id},
        source="integration",
    )
    completed = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=playbook, executor=executor
    ).run()

    assert paused.final_status_code == "HUMAN_TASK_PENDING"
    assert applied.target == "final"
    assert completed.completed is True
    assert agent_steps == ["draft", "final"]


def test_automatic_owner_runs_only_registered_runtime_authority(tmp_path: Path) -> None:
    """IT-002: native authority advances, while an unknown ID creates no visit."""
    issue_dir = tmp_path / ".cafe" / "issues" / "automatic"
    calls: list[dict[str, object]] = []
    registry = AutomaticExecutorRegistry(
        {
            "advance": lambda inputs: calls.append(dict(inputs))
            or AutomaticExecutionResult("await_agent")
        }
    )
    playbook = {
        "playbook": {"id": "owner-integration"},
        "steps": {
            "automatic": {
                "skill": "phase",
                "role": "operator",
                "assignee_type": "auto",
                "automatic": {"executor": "advance", "inputs": {"mode": "safe"}},
                "on": {"await_agent": "_done"},
            }
        },
    }

    completed = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=lambda *_args, **_kwargs: pytest.fail("auto must not call an agent"),
        automatic_registry=registry,
    ).run(start_step="automatic")

    assert completed.completed is True
    assert calls == [{"mode": "safe"}]

    unknown_dir = tmp_path / ".cafe" / "issues" / "unknown-automatic"
    unknown_playbook = {
        **playbook,
        "steps": {
            "automatic": {
                **playbook["steps"]["automatic"],
                "automatic": {"executor": "unknown", "inputs": {}},
            }
        },
    }
    with pytest.raises(ValueError, match="not registered"):
        BlackboardWorkflowRuntime(
            issue_dir=unknown_dir,
            playbook=unknown_playbook,
            executor=lambda *_args, **_kwargs: pytest.fail("unknown auto must not call an agent"),
            automatic_registry=registry,
        )
    assert BlackboardStore(unknown_dir).load_or_create("automatic").step_visit_counts == {}


def test_hybrid_journey_retains_one_visit_through_its_human_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IT-003/IT-005: a hybrid wait resumes its cursor without consuming another visit."""
    issue_dir = tmp_path / ".cafe" / "issues" / "hybrid"
    binding = HumanTaskBinding(trigger="approve", task_id="approval", outcomes={"accept": "mixed"})
    monkeypatch.setattr(
        "cafe.core.workflow_runtime.resolve_step_human_task",
        lambda **_kwargs: (_approval_policy(), binding),
    )
    monkeypatch.setattr(
        "cafe.ui.human_tasks.resolve_step_human_task",
        lambda **_kwargs: (_approval_policy(), binding),
    )
    portions: list[str] = []

    def executor(_step_name: str, step_def: dict, *_args: object, **_kwargs: object):
        portions.append(step_def["hybrid_portion"]["id"])
        return "confirmed", {}

    playbook = {
        "playbook": {"id": "owner-integration"},
        "steps": {
            "mixed": {
                "skill": "phase",
                "role": "operator",
                "assignee_type": "hybrid",
                "max_iterations": 1,
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
                            "on": {"accept": {"portion": "final"}},
                        },
                        {
                            "id": "final",
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
    apply_human_task_payload(
        issue_dir=issue_dir,
        playbook_data=playbook,
        blackboard=state,
        from_step="mixed",
        trigger="approve",
        raw_payload={"task": "approval", "decision": "accept", "human_task_id": task.id},
        source="integration",
    )
    completed = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=playbook, executor=executor
    ).run()

    assert paused.final_status_code == "HYBRID_HUMAN_TASK_PENDING"
    assert completed.completed is True
    assert portions == ["draft", "final"]
    assert BlackboardStore(issue_dir).load_or_create("mixed").step_visit_counts == {"mixed": 1}


def test_ownership_cli_dry_run_is_a_side_effect_free_simulation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IT-004: the public dry-run command previews ownership without state."""
    monkeypatch.chdir(tmp_path)
    playbook_dir = tmp_path / ".cafe" / "playbooks"
    playbook_dir.mkdir(parents=True)
    (playbook_dir / "ownership-preview.yaml").write_text(
        """
playbook:
  id: ownership-preview
steps:
  draft:
    skill: cafe-develop
    role: developer
    on: {await_agent: approval}
  approval:
    skill: cafe-develop
    role: developer
    assignee_type: human
    human_tasks:
      - trigger: initial
        task_id: no-change-decision
        outcomes: {agree: automatic}
    on: {}
  automatic:
    skill: cafe-develop
    role: developer
    assignee_type: auto
    automatic:
      executor: declared_transition
      inputs: {intent: await_agent}
    on: {await_agent: mixed}
  mixed:
    skill: cafe-develop
    role: developer
    assignee_type: hybrid
    human_tasks:
      - trigger: approve
        task_id: no-change-decision
        outcomes: {agree: mixed}
    hybrid:
      entry_portion: draft
      portions:
        - id: draft
          owner: agent
          on: {await_agent: {portion: approve}}
        - id: approve
          owner: human
          on: {agree: {step: _done}}
    on: {}
""".strip(),
        encoding="utf-8",
    )

    with patch("cafe.ui.cli.GitOperations") as mock_git_operations:
        git = MagicMock()
        git.get_current_branch.return_value = "ownership-preview"
        mock_git_operations.return_value = git
        result = CliRunner().invoke(
            app,
            ["workflow", "--playbook", "ownership-preview", "--dry-run"],
        )

    assert result.exit_code == 0, result.output
    assert "Ownership plan (read-only)" in result.stdout
    assert "draft: owner=agent" in result.stdout
    assert "approval: owner=human" in result.stdout
    assert "automatic: owner=auto" in result.stdout
    assert "mixed: owner=hybrid" in result.stdout
    assert not (tmp_path / ".cafe" / "issues" / "ownership-preview" / "blackboard.json").exists()


def test_v2_blackboard_human_resume_completes_across_runtime_instances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IT-006: migrated state retains a durable human wait and its continuation."""
    issue_dir = tmp_path / ".cafe" / "issues" / "migrated-human"
    issue_dir.mkdir(parents=True)
    binding = HumanTaskBinding(trigger="initial", task_id="approval", outcomes={"accept": "final"})
    monkeypatch.setattr(
        "cafe.core.workflow_runtime.resolve_step_human_task",
        lambda **_kwargs: (_approval_policy(), binding),
    )
    monkeypatch.setattr(
        "cafe.ui.human_tasks.resolve_step_human_task",
        lambda **_kwargs: (_approval_policy(), binding),
    )
    (issue_dir / "blackboard.json").write_text(
        '{"schema_version": 2, "current_step": "approval", '
        '"playbook_id": "owner-integration", "workflow_id": "migrated-workflow"}',
        encoding="utf-8",
    )
    playbook = {
        "playbook": {"id": "owner-integration"},
        "steps": {
            "approval": {
                "skill": "phase",
                "role": "operator",
                "assignee_type": "human",
                "human_tasks": [binding.model_dump()],
                "on": {},
            },
            "final": {
                "skill": "phase",
                "role": "operator",
                "valid_intents": ["confirmed"],
                "on": {"await_agent": "_done"},
            },
        },
    }
    agent_steps: list[str] = []

    def executor(step_name: str, *_args: object, **_kwargs: object) -> tuple[str, dict]:
        agent_steps.append(step_name)
        return "confirmed", {}

    paused = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=playbook, executor=executor
    ).run()
    state = BlackboardStore(issue_dir).load_or_create("approval")
    task = HumanTaskRecordStore(issue_dir).tasks()[0]
    applied = apply_human_task_payload(
        issue_dir=issue_dir,
        playbook_data=playbook,
        blackboard=state,
        from_step="approval",
        trigger="initial",
        raw_payload={"task": "approval", "decision": "accept", "human_task_id": task.id},
        source="integration",
    )
    completed = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=playbook, executor=executor
    ).run()

    assert paused.final_status_code == "HUMAN_TASK_PENDING"
    assert state.schema_version == BLACKBOARD_SCHEMA_VERSION == 3
    assert applied.target == "final"
    assert completed.completed is True
    assert agent_steps == ["final"]


@pytest.mark.parametrize("owner", ("agent", "human", "auto"))
def test_loop_limit_persists_for_each_owner_before_a_second_visit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owner: str
) -> None:
    """IT-005: owner dispatch cannot reset or bypass a persisted visit limit."""
    issue_dir = tmp_path / ".cafe" / "issues" / f"loop-{owner}"
    calls: list[str] = []
    step: dict[str, object] = {
        "skill": "phase",
        "role": "operator",
        "assignee_type": owner,
        "max_iterations": 1,
        "on": {"await_agent": "loop"},
    }
    registry = None
    if owner == "human":
        binding = HumanTaskBinding(
            trigger="initial",
            task_id="approval",
            outcomes={"accept": "loop"},
        )
        monkeypatch.setattr(
            "cafe.core.workflow_runtime.resolve_step_human_task",
            lambda **_kwargs: (_approval_policy(), binding),
        )
        monkeypatch.setattr(
            "cafe.ui.human_tasks.resolve_step_human_task",
            lambda **_kwargs: (_approval_policy(), binding),
        )
        step.update({"human_tasks": [binding.model_dump()], "on": {}})
    elif owner == "auto":
        step.update({"automatic": {"executor": "advance", "inputs": {}}})
        registry = AutomaticExecutorRegistry(
            {
                "advance": lambda _inputs: calls.append("automatic")
                or AutomaticExecutionResult("await_agent")
            }
        )
    else:
        step.update({"valid_intents": ["confirmed"]})

    playbook = {"playbook": {"id": "owner-integration"}, "steps": {"loop": step}}

    def executor(*_args: object, **_kwargs: object) -> tuple[str, dict]:
        calls.append("agent")
        return "confirmed", {}

    first_runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
        automatic_registry=registry,
    )
    first_runtime.run(start_step="loop", single_step=True)
    if owner == "human":
        state = BlackboardStore(issue_dir).load_or_create("loop")
        task = HumanTaskRecordStore(issue_dir).tasks()[0]
        apply_human_task_payload(
            issue_dir=issue_dir,
            playbook_data=playbook,
            blackboard=state,
            from_step="loop",
            trigger="initial",
            raw_payload={"task": "approval", "decision": "accept", "human_task_id": task.id},
            source="integration",
        )

    with pytest.raises(RuntimeError, match="max_iterations=1"):
        BlackboardWorkflowRuntime(
            issue_dir=issue_dir,
            playbook=playbook,
            executor=executor,
            automatic_registry=registry,
        ).run(start_step="loop", single_step=True)

    assert BlackboardStore(issue_dir).load_or_create("loop").step_visit_counts == {"loop": 1}
    assert calls == (["automatic"] if owner == "auto" else ["agent"] if owner == "agent" else [])
