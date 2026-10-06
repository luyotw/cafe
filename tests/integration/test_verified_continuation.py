"""Declared continuation runs once, including restarted owners (U6–U9/I7–I11)."""

import pytest
import yaml

from cafe.core.blackboard import BlackboardStore
from cafe.playbooks.loader import PlaybookLoader
from tests.integration.integration_fixture import create_journey


def continuation(journey, owner="auto", target="_done"):
    path = journey.root / ".cafe/playbooks/custom-delivery.yaml"
    data = yaml.safe_load(path.read_text())
    data["steps"]["inspect"]["on"]["workflow_complete"] = "after_delivery"
    definition = dict(skill="custom-delivery", role="operator", assignee_type=owner)
    if owner == "auto":
        definition.update(
            automatic=dict(executor="declared_transition", inputs=dict(intent="workflow_complete")),
            on=dict(workflow_complete=target),
        )
    elif owner == "human":
        definition.update(
            on=dict(await_agent=target),
            human_tasks=[
                dict(trigger="initial", task_id="judge", outcomes=dict(ship=target, fix=target))
            ],
        )
    else:
        definition.update(on=dict(await_agent=target))
    data["steps"]["after_delivery"] = definition
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    journey.playbook.clear()
    journey.playbook.update(
        PlaybookLoader(project_root=journey.root).load("custom-delivery", strict=True)
    )


def prepared(tmp_path, monkeypatch, target, owner="auto"):
    journey = create_journey(tmp_path / "repo", monkeypatch)
    continuation(journey, owner)
    if target == "github_pr":
        monkeypatch.setattr(
            "cafe.utils.github.GitHubOps.observe_integration", lambda *a: dict(journey.observation)
        )
    assert journey.complete("ship").rejection is None
    journey.select(target)
    journey.runtime().run()
    assert journey.complete("confirm").rejection is None
    journey.runtime().run()
    journey.human_integrate(target)
    return journey


def downstream_events(journey):
    return [
        e
        for e in journey.state().events
        if e.data.get("step") == "after_delivery"
        and e.event_type in {"automatic_step_completed", "step_completed", "single_step_completed"}
    ]


@pytest.mark.parametrize("target", ["github_pr", "local_branch"])
def test_auto_continuation_finishes_with_one_inspection(tmp_path, monkeypatch, target):
    journey = prepared(tmp_path, monkeypatch, target)
    assert journey.runtime().run().completed
    assert len(downstream_events(journey)) == 1
    assert len(journey.service().records.read()["attempts"]) == 1
    assert journey.runtime().run().completed
    assert len(downstream_events(journey)) == 1


@pytest.mark.parametrize("target", ["github_pr", "local_branch"])
def test_restart_reinspects_without_repeating_completed_auto_work(tmp_path, monkeypatch, target):
    journey = prepared(tmp_path, monkeypatch, target)
    assert not journey.runtime().run(single_step=True).completed
    assert journey.state().current_step == "after_delivery"
    for _ in range(3):
        if journey.runtime().run().completed:
            break
    assert journey.state().current_step == "done"
    assert len(downstream_events(journey)) == 1
    assert len(journey.service().records.read()["attempts"]) == 2


@pytest.mark.parametrize("target", ["github_pr", "local_branch"])
def test_human_continuation_preserves_real_answer_across_terminal_refresh(
    tmp_path, monkeypatch, target
):
    from cafe.core.human_task_records import HumanTaskRecordStore

    journey = prepared(tmp_path, monkeypatch, target, "human")
    assert not journey.runtime().run().completed
    task = journey.pending()
    assert task.step == "after_delivery"
    applied = journey.complete("ship", task)
    assert applied.rejection is None
    for _ in range(3):
        if journey.runtime().run().completed:
            break
    assert journey.state().current_step == "done"
    tasks = [
        t for t in HumanTaskRecordStore(journey.issue_dir).tasks() if t.step == "after_delivery"
    ]
    assert len(tasks) == 1
    assert tasks[0].status.value == "completed"


@pytest.mark.parametrize("target", ["github_pr", "local_branch"])
def test_agent_continuation_executes_once_across_terminal_refresh(tmp_path, monkeypatch, target):
    from cafe.core.workflow_models import StepExecutionResult
    from cafe.core.workflow_runtime import BlackboardWorkflowRuntime

    journey = prepared(tmp_path, monkeypatch, target, "agent")
    calls = []

    def executor(step_name, step_def, board, **kwargs):
        calls.append(step_name)
        return StepExecutionResult(response="", artifacts={}, status_code="await_agent")

    for _ in range(4):
        runtime = BlackboardWorkflowRuntime(
            issue_dir=journey.issue_dir, playbook=journey.playbook, executor=executor
        )
        if runtime.run().completed:
            break
    assert journey.state().current_step == "done"
    assert calls == ["after_delivery"]


@pytest.mark.parametrize("invalid_target", ["inspect", "after_delivery", "land"])
def test_cyclic_verified_continuation_is_rejected_on_loading(tmp_path, monkeypatch, invalid_target):
    journey = create_journey(tmp_path / "repo", monkeypatch)
    with pytest.raises(ValueError):
        continuation(journey, target=invalid_target)


@pytest.mark.parametrize("target", ["github_pr", "local_branch"])
def test_restart_observes_destination_drift_and_recovers_without_rework(
    tmp_path, monkeypatch, target
):
    journey = prepared(tmp_path, monkeypatch, target)
    journey.runtime().run(single_step=True)
    if target == "github_pr":
        journey.observation.update(merged=False, state="open")
    else:
        journey.git("update-ref", "refs/heads/main", journey.base)
    assert not journey.runtime().run().completed
    assert not journey.runtime().run(single_step=True).completed
    assert len(downstream_events(journey)) == 1
    assert journey.state().current_step != "done"
    journey.human_integrate(target)
    for _ in range(3):
        if journey.runtime().run().completed:
            break
    assert journey.state().current_step == "done"
    assert len(downstream_events(journey)) == 1


@pytest.mark.parametrize("target", ["github_pr", "local_branch"])
def test_terminal_publication_retry_does_not_repeat_continuation(tmp_path, monkeypatch, target):
    journey = prepared(tmp_path, monkeypatch, target)
    journey.runtime().run(single_step=True)
    original = BlackboardStore.record_event
    failures = []

    def publish(store, state, kind, data, **kwargs):
        if kind == "workflow_completed" and not failures:
            failures.append(kind)
            raise OSError("publication boundary interrupted")
        return original(store, state, kind, data, **kwargs)

    monkeypatch.setattr(BlackboardStore, "record_event", publish)
    for _ in range(4):
        if journey.runtime().run().completed:
            break
    assert failures
    assert journey.state().current_step == "done"
    assert len(downstream_events(journey)) == 1


@pytest.mark.parametrize("target", ["github_pr", "local_branch"])
def test_source_drift_requires_review_instead_of_consuming_continuation_proof(
    tmp_path, monkeypatch, target
):
    import json

    journey = prepared(tmp_path, monkeypatch, target)
    journey.runtime().run(single_step=True)
    path = journey.issue_dir / "reviewed.json"
    source = json.loads(path.read_text())
    source["head_sha"] = journey.base
    path.write_text(json.dumps(source))
    assert not journey.runtime().run(single_step=True).completed
    assert not journey.runtime().run(single_step=True).completed
    assert journey.state().current_step == "forge"
    assert len(downstream_events(journey)) == 1


@pytest.mark.parametrize("target", ["github_pr", "local_branch"])
@pytest.mark.parametrize("owner", ["auto", "human"])
def test_public_cli_continuation_survives_separate_processes(tmp_path, monkeypatch, target, owner):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path
    from tests.integration.integration_fixture import install_github_process_fixture

    journey = create_journey(tmp_path / "repo", monkeypatch)
    continuation(journey, owner)
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src"),
        CAFE_SKIP_GLOBAL_SKILL_SYNC="1",
    )
    if target == "github_pr":
        fixture = install_github_process_fixture(journey.root, journey.source)
        env["PATH"] = str(fixture) + os.pathsep + env["PATH"]

    def process(*args):
        result = subprocess.run(
            [sys.executable, "-c", "from cafe.ui.cli import app; app()", *args],
            cwd=journey.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def complete(decision):
        task = journey.pending()
        process(
            "task",
            "complete",
            task.id,
            "--result",
            json.dumps(dict(task=task.policy_id, human_task_id=task.id, decision=decision)),
            "--no-resume",
        )

    complete("ship")
    args = [
        "integration",
        "select",
        "--issue",
        "delivery",
        "--target",
        target,
        "--repository",
        str(journey.root) if target == "local_branch" else "owner/repo",
        "--target-branch",
        "main",
        "--json",
    ]
    args += ["--feature-branch", "feature"] if target == "local_branch" else ["--pr", "17"]
    process(*args)
    complete("confirm")
    process("workflow", "--issue", "delivery", "--execute")
    complete("performed")
    if target == "local_branch":
        journey.human_integrate()
    else:
        data = json.loads((fixture / "pr.json").read_text())
        data.update(state="closed", merged=True, merge_commit_sha="c" * 40)
        (fixture / "pr.json").write_text(json.dumps(data))
    process("workflow", "--issue", "delivery", "--execute", "--single-step")
    process("workflow", "--issue", "delivery", "--execute")
    if owner == "human":
        complete("ship")
    process("workflow", "--issue", "delivery", "--execute")
    assert journey.state().current_step == "done"
    assert json.loads(process("integration", "status", "--issue", "delivery", "--json"))[
        "completed"
    ]
    attempts = journey.service().records.read()["attempts"]
    assert len(attempts) == 2
    if owner == "auto":
        assert len(downstream_events(journey)) == 1
    if target == "github_pr":
        assert len((fixture / "requests.jsonl").read_text().splitlines()) == 2


@pytest.mark.parametrize("target", ["github_pr", "local_branch"])
def test_intermediate_human_edge_keeps_its_actual_result_and_runs_next_owner_once(
    tmp_path, monkeypatch, target
):
    journey = prepared(tmp_path, monkeypatch, target, "human")
    path = journey.root / ".cafe/playbooks/custom-delivery.yaml"
    data = yaml.safe_load(path.read_text())
    data["steps"]["after_delivery"]["on"]["await_agent"] = "record_delivery"
    data["steps"]["after_delivery"]["human_tasks"][0]["outcomes"] = dict(
        ship="record_delivery", fix="record_delivery"
    )
    data["steps"]["record_delivery"] = dict(
        skill="custom-delivery",
        role="operator",
        assignee_type="auto",
        automatic=dict(executor="declared_transition", inputs=dict(intent="workflow_complete")),
        on=dict(workflow_complete="_done"),
    )
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    journey.playbook.clear()
    journey.playbook.update(
        PlaybookLoader(project_root=journey.root).load("custom-delivery", strict=True)
    )
    journey.runtime().run()
    task = journey.pending()
    assert journey.complete("ship", task).rejection is None
    for _ in range(3):
        if journey.runtime().run().completed:
            break
    assert journey.state().current_step == "done"
    events = [
        e
        for e in journey.state().events
        if e.event_type == "automatic_step_completed" and e.data.get("step") == "record_delivery"
    ]
    assert len(events) == 1


@pytest.mark.parametrize("target", ["github_pr", "local_branch"])
def test_agent_confirmation_boundary_retains_one_agent_execution_and_one_answer(
    tmp_path, monkeypatch, target
):
    from cafe.core.workflow_models import StepExecutionResult
    from cafe.core.workflow_runtime import BlackboardWorkflowRuntime

    journey = prepared(tmp_path, monkeypatch, target, "agent")
    path = journey.root / ".cafe/playbooks/custom-delivery.yaml"
    data = yaml.safe_load(path.read_text())
    definition = data["steps"]["after_delivery"]
    definition["on"]["confirm_output"] = "after_delivery"
    definition["human_tasks"] = [
        dict(trigger="confirm_output", task_id="judge", outcomes=dict(ship="_done", fix="_done"))
    ]
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    journey.playbook.clear()
    journey.playbook.update(
        PlaybookLoader(project_root=journey.root).load("custom-delivery", strict=True)
    )
    calls = []

    def executor(step_name, step_def, board, **kwargs):
        calls.append(step_name)
        return StepExecutionResult(response="", artifacts={}, status_code="confirm_output")

    runtime = BlackboardWorkflowRuntime(
        issue_dir=journey.issue_dir, playbook=journey.playbook, executor=executor
    )
    for _ in range(2):
        assert not runtime.run().completed
        if journey.pending().step == "after_delivery":
            break
    task = journey.pending()
    assert task.step == "after_delivery"
    assert journey.complete("ship", task).rejection is None
    assert journey.runtime().run().completed
    assert calls == ["after_delivery"]


@pytest.mark.parametrize("target", ["github_pr", "local_branch"])
@pytest.mark.parametrize("drift", ["source", "transition"])
def test_continuation_final_publication_fence_rejects_intervening_changes(
    tmp_path, monkeypatch, target, drift
):
    import json

    journey = prepared(tmp_path, monkeypatch, target)
    original = BlackboardStore.record_event
    changed = []

    def publish(store, state, kind, data, **kwargs):
        if kind == "workflow_completed" and not changed:
            changed.append(kind)
            if drift == "source":
                path = journey.issue_dir / "reviewed.json"
                source = json.loads(path.read_text())
                source["head_sha"] = journey.base
                path.write_text(json.dumps(source))
            else:
                persisted = store.load_read_only()
                original(
                    store,
                    persisted,
                    "transition",
                    {"from": "after_delivery", "to": "inspect", "status_code": "manual_handoff"},
                )
        return original(store, state, kind, data, **kwargs)

    monkeypatch.setattr(BlackboardStore, "record_event", publish)
    assert not journey.runtime().run(max_transitions=2).completed
    assert changed and journey.state().current_step != "done"
