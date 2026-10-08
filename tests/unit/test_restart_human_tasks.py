"""U5/U6/I4: typed interruptions declare immutable human recovery choices."""
import json

import pytest

from cafe.agents.executor import AgentExecutionError
from cafe.core.blackboard import BlackboardStore
from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.core.human_tasks import agent_execution_interrupted_human_task
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.ui.human_tasks import apply_human_task_payload


@pytest.mark.parametrize("policy,reason,offered", [
    ("continue_last_success", "agent_rate_limit", False),
    ("recheck_priority", "agent_error", False),
    ("recheck_priority", "agent_rate_limit", True),
])
def test_conditional_decision_preserves_old_choices(policy, reason, offered):
    task, binding = agent_execution_interrupted_human_task(step_name="compose", restart_policy=policy, interruption_reason=reason)
    assert ("retry_configured_order" in binding.outcomes) is offered
    assert binding.outcomes["retry"] == binding.outcomes["retry_fresh_session"] == "compose"
    assert {d.id for d in task.decisions} == set(binding.outcomes)


def _interrupt(tmp_path, policy="continue_last_success", error="rate_limit"):
    issue = tmp_path / ".cafe" / "issues" / "example"
    issue.mkdir(parents=True)
    (issue / "issue.yaml").write_text(f"execution:\n  rate_limit_restart_policy: {policy}\n")
    playbook = {"playbook": {"id": "custom"}, "steps": {"compose": {"role": "writer", "skill": "compose", "on": {"await_agent": "_done"}}}}
    def provider(step, definition, state, **kwargs):
        directory = issue / step / "iteration_001"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "iteration.json").write_text(json.dumps({"cli": "gemini", "model": "backup-model", "session_id": "interrupted-session", "agent_invoked": True}))
        raise AgentExecutionError("provider interrupted", error_type=error)
    runtime = BlackboardWorkflowRuntime(issue_dir=issue, playbook=playbook, executor=provider)
    runtime.run(start_step="compose")
    return issue, playbook, runtime


def _answer(issue, playbook, task, decision):
    boards = BlackboardStore(issue)
    state = boards.load_or_create("compose", playbook_id="custom")
    return apply_human_task_payload(issue_dir=issue, playbook_data=playbook, blackboard=state, from_step="compose", trigger="agent_execution_interrupted", raw_payload={"human_task_id": task.id, "decision": decision}, source="test")


def test_configured_recovery_completion_correlates_typed_interruption(tmp_path):
    issue, graph, runtime = _interrupt(tmp_path, "recheck_priority")
    records = HumanTaskRecordStore(issue)
    task = records.tasks()[0]
    result = _answer(issue, graph, task, "retry_configured_order")
    assert result.rejection is None
    assert result.target == "compose"
    payload = records.get_result(task.id).payload["restart_recovery"]
    assert payload["human_task_id"] == task.id
    assert payload["workflow_id"] == runtime.blackboard.workflow_id
    assert payload["step"] == "compose" and payload["iteration"] == 1
    assert payload["interruption"]["reason"] == "agent_rate_limit"


def test_pending_task_upgrade_supersedes_only_correlated_predecessor(tmp_path):
    issue, graph, runtime = _interrupt(tmp_path)
    records = HumanTaskRecordStore(issue)
    old = records.tasks()[0]
    old_contract = old.expected_result
    assert _answer(issue, graph, old, "retry_configured_order").rejection is not None
    unrelated = records.materialize(workflow_id=old.workflow_id, step="other", iteration=1, trigger="agent_execution_interrupted", policy_id=old.policy_id, prompt=old.prompt, expected_result=old_contract, continuations={"retry": "other"}, assignee_type="user")
    (issue / "issue.yaml").write_text("execution:\n  rate_limit_restart_policy: recheck_priority\n")
    # Settings alone cannot authorize execution. The next workflow boundary offers a new task.
    result = runtime.run()
    assert not result.completed
    current = next(t for t in records.tasks() if t.step == "compose" and t.status is HumanTaskStatus.PENDING)
    assert current.id != old.id
    assert records.get_task(old.id).status is HumanTaskStatus.CANCELLED
    assert records.get_task(old.id).expected_result == old_contract
    assert records.get_task(unrelated.id).status is HumanTaskStatus.PENDING
    assert _answer(issue, graph, old, "retry").rejection is not None
    assert _answer(issue, graph, current, "retry_configured_order").rejection is None


def test_unsuperseded_old_task_remains_answerable(tmp_path):
    issue, graph, runtime = _interrupt(tmp_path)
    task = HumanTaskRecordStore(issue).tasks()[0]
    (issue / "issue.yaml").write_text("execution:\n  rate_limit_restart_policy: recheck_priority\n")
    assert _answer(issue, graph, task, "retry").rejection is None
    assert "retry_configured_order" not in task.continuations
