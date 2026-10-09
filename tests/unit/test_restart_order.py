"""U3/U4/U5: immutable invocation order is independent of exact session identity."""

import json

import pytest

from cafe.agents.manager import AgentManager
from cafe.core.session_continuation import SessionContinuation
from cafe.core.types import AgentCLI, AgentConfig, CliEntry


def _manager(tmp_path, monkeypatch, chain=None):
    monkeypatch.chdir(tmp_path)
    manager = AgentManager(issue_name="custom")
    entries = chain or [
        CliEntry(cli=AgentCLI.CODEX, model="primary", phase_models={"compose": "current"}),
        CliEntry(cli=AgentCLI.GEMINI, model="fallback"),
    ]
    manager.register_agent(AgentConfig(name="Writer", cli=entries[0].cli, clis=entries))
    directory = tmp_path / ".cafe" / "issues" / "custom"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "active_clis.json").write_text(
        json.dumps(
            {
                "Writer": {
                    "cli": "gemini",
                    "model": "fallback",
                    "configured_primary": "codex",
                    "chain": [
                        {"cli": "codex", "model": "current"},
                        {"cli": "gemini", "model": "fallback"},
                    ],
                }
            }
        )
    )
    return manager


def test_invocation_bypasses_sticky_once_and_exact_session_still_wins(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    sticky = manager.resolve_invocation_order("Writer", phase_name="compose")
    reset = manager.resolve_invocation_order("Writer", phase_name="compose", configured_order=True)
    assert sticky.entries == (("gemini", "fallback"), ("codex", "current"))
    assert reset.entries == (("codex", "current"), ("gemini", "fallback"))
    assert reset.sticky_disposition == "bypassed"
    config = manager.get_execution_config(
        "Writer",
        phase_name="compose",
        continuation=SessionContinuation.new(),
        invocation_order=reset,
    )
    assert config.cli == AgentCLI.CODEX and config.model == "current" and config.session_id is None
    exact = manager.get_execution_config(
        "Writer",
        phase_name="compose",
        continuation=SessionContinuation.resume_exact(AgentCLI.GEMINI, "original"),
        invocation_order=reset,
    )
    assert exact.cli == AgentCLI.GEMINI and exact.session_id == "original"
    assert (
        manager.resolve_invocation_order("Writer", phase_name="compose").entries == sticky.entries
    )


@pytest.mark.parametrize(
    "change", ["primary", "remove", "reorder", "model", "step_model", "legacy"]
)
def test_current_configuration_invalidates_incompatible_preference(tmp_path, monkeypatch, change):
    manager = _manager(tmp_path, monkeypatch)
    base = manager.get_agent("Writer").config
    if change == "primary":
        base.clis[0] = CliEntry(cli=AgentCLI.CLAUDE, model="new")
    elif change == "remove":
        base.clis.pop()
    elif change == "reorder":
        base.clis.reverse()
    elif change == "model":
        base.clis[1].model = "new-fallback"
    elif change == "step_model":
        base.clis[0].phase_models["compose"] = "new-primary"
    else:
        path = tmp_path / ".cafe" / "issues" / "custom" / "active_clis.json"
        path.write_text(json.dumps({"Writer": {"cli": "gemini"}}))
    order = manager.resolve_invocation_order("Writer", phase_name="compose")
    assert order.sticky_disposition == "stale"
    assert order.entries == tuple((e.cli.value, e.resolve_model("compose")) for e in base.clis)


def test_single_entry_snapshot_never_adds_fallback(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch, [CliEntry(cli=AgentCLI.CODEX, model="only")])
    order = manager.resolve_invocation_order("Writer", phase_name="compose", configured_order=True)
    config = manager.get_execution_config(
        "Writer", continuation=SessionContinuation.new(), invocation_order=order
    )
    assert config.backup_clis == []
    assert [e.cli for e in config.clis] == [AgentCLI.CODEX]


def test_step_recovery_consumes_only_at_invocation_and_replay_resumes_exact(tmp_path, monkeypatch):
    """U7: a previous failed invocation marker cannot consume a new decision."""
    from cafe.core.git import GitOperations
    from cafe.core.human_task_records import HumanTaskRecordStore
    from cafe.core.human_tasks import agent_execution_interrupted_human_task
    from cafe.phases.generic_phase import GenericPhase
    from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
    from cafe.skills.loader import SkillLoader

    manager = _manager(tmp_path, monkeypatch)
    issue = tmp_path / ".cafe" / "issues" / "custom"
    (issue / "issue.yaml").write_text("execution:\n  rate_limit_restart_policy: recheck_priority\n")
    step = GenericWorkflowStepExecutor(
        issue_dir=issue,
        issue_name="custom",
        playbook={"playbook": {"id": "custom"}, "steps": {"compose": {}}},
        generic_phase=GenericPhase(SkillLoader(project_root=tmp_path)),
        agent_manager=manager,
        git_ops=GitOperations(tmp_path),
        role_agent_map={},
    )
    step.phase_name = "compose"
    step.phase_dir = issue / "compose"
    step.iteration = 1
    directory = step.phase_dir / "iteration_001"
    directory.mkdir(parents=True)
    interruption = {
        "id": "failure-1",
        "workflow_id": "workflow",
        "step": "compose",
        "iteration": 1,
        "reason": "agent_rate_limit",
    }
    context = {
        "cli": "gemini",
        "session_id": "previous",
        "agent_invoked": True,
        "workflow_completion_trusted": False,
        "agent_interruption": interruption,
    }
    path = directory / "iteration.json"
    path.write_text(json.dumps(context))
    policy, binding = agent_execution_interrupted_human_task(
        step_name="compose",
        restart_policy="recheck_priority",
        interruption_reason="agent_rate_limit",
    )
    records = HumanTaskRecordStore(issue)
    task = records.materialize(
        workflow_id="workflow",
        step="compose",
        iteration=1,
        trigger="agent_execution_interrupted",
        policy_id=policy.id,
        prompt=policy.prompt,
        expected_result=policy.model_dump(mode="json"),
        continuations=binding.outcomes,
        assignee_type="user",
    )
    recovery = {
        "schema_version": 1,
        "decision": "retry_configured_order",
        "workflow_id": "workflow",
        "human_task_id": task.id,
        "step": "compose",
        "iteration": 1,
        "interruption": interruption,
    }
    result = records.complete(
        workflow_id="workflow",
        task_id=task.id,
        payload={"decision": "retry_configured_order", "restart_recovery": recovery},
        source="test",
    )
    first = step._select_session_continuation(
        agent_name="Writer", step_def={}, workflow_id="workflow"
    )
    assert first.policy.value == "new"
    # Merely selecting/preparing is retryable and has not consumed the result.
    second = step._select_session_continuation(
        agent_name="Writer", step_def={}, workflow_id="workflow"
    )
    assert second.policy.value == "new"
    step._consume_restart_recovery(directory / "iteration.json")
    saved = json.loads(path.read_text())
    marker = saved["restart_recovery_consumption"]
    assert marker["result_id"] == result.id and marker["human_task_id"] == task.id
    assert marker["interruption_id"] == interruption["id"] and marker["invocation_id"]
    saved.update(cli="codex", session_id="new-session")
    path.write_text(json.dumps(saved))
    replay = step._select_session_continuation(
        agent_name="Writer", step_def={}, workflow_id="workflow"
    )
    assert replay.is_exact and replay.cli == AgentCLI.CODEX and replay.session_id == "new-session"
