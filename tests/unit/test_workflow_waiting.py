"""Machine waiting preserves phase ownership and uses no human/agent retry budget."""
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
import pytest

from cafe.core.blackboard import BlackboardStore, HandoffOwner, HandoffIntent
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.workflow_models import StepWaiting, StepExecutionResult, PlaybookRunResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.core.workspace_lock import workspace_execution_lock
from cafe.workflow_execution.workflow_hosting import WorkflowHost, WorkerAlreadyRunningError
from tests.unit.test_workflow_runtime import _write_baton


@pytest.mark.parametrize("known_wait", [False, True])
def test_hard_exit_during_query_does_not_consume_agent_attempt(tmp_path, known_wait):
    issue = tmp_path / ".cafe/issues/wait"
    graph = {"playbook": {"id": "wait"}, "steps": {"ship": {
        "skill": "custom", "role": "developer", "max_attempts_per_cycle": 1,
        "on": {"workflow_complete": "_done"},
    }}}
    def waiting(name, declaration, state, before_agent=None):
        raise StepWaiting(step=name, identity="approved", delay=30)
    if known_wait:
        assert BlackboardWorkflowRuntime(issue_dir=issue, playbook=graph,
                                         executor=waiting).run().wait_seconds == 30
    process = subprocess.run([sys.executable, "-c", f'''
import os
from pathlib import Path
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
def query(name, declaration, state, before_agent=None):
    os._exit(77)
BlackboardWorkflowRuntime(issue_dir=Path({str(issue)!r}), playbook={graph!r}, executor=query).run()
'''], capture_output=True, text=True, timeout=10)
    assert process.returncode == 77, process.stderr
    saved = BlackboardStore(issue).load_or_create("ship", playbook_id="wait")
    assert saved.step_attempt_counts.get("ship", 0) == 0
    resumed = BlackboardWorkflowRuntime(issue_dir=issue, playbook=graph, executor=waiting)
    assert resumed.run().wait_seconds == 30


def test_actual_agent_reserves_attempt_before_hard_exit(tmp_path):
    issue = tmp_path / ".cafe/issues/wait"
    graph = {"playbook": {"id": "wait"}, "steps": {"ship": {
        "skill": "custom", "role": "developer", "max_attempts_per_cycle": 1,
        "on": {"workflow_complete": "_done"},
    }}}
    process = subprocess.run([sys.executable, "-c", f'''
import os
from pathlib import Path
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
def agent(name, declaration, state, before_agent=None):
    before_agent()
    os._exit(77)
BlackboardWorkflowRuntime(issue_dir=Path({str(issue)!r}), playbook={graph!r}, executor=agent).run()
'''], capture_output=True, text=True, timeout=10)
    assert process.returncode == 77, process.stderr
    saved = BlackboardStore(issue).load_or_create("ship", playbook_id="wait")
    assert saved.step_attempt_counts["ship"] == 1
    resumed = BlackboardWorkflowRuntime(issue_dir=issue, playbook=graph, executor=lambda *a: None)
    with pytest.raises(RuntimeError, match="max_attempts_per_cycle=1"):
        resumed._record_step_attempt(current_step="ship", step_def=graph["steps"]["ship"], runtime="test")


@pytest.mark.parametrize("baton", [False, True])
def test_wait_longer_than_thirty_one_minutes_without_agent_or_human_retry(
    tmp_path, monkeypatch, baton,
):
    issue = tmp_path / ".cafe/issues/wait"
    step = {"skill": "custom-deliver", "role": "developer",
            "on": {"workflow_complete": "_done"}, "max_attempts_per_cycle": 1}
    if baton:
        step["behavior"] = {"completion": "baton"}
    graph = {"playbook": {"id": "wait"}, "steps": {"ship": step}}
    calls, sleeps, callbacks = [], [], []
    def executor(name, declaration, state, **kwargs):
        with workspace_execution_lock(tmp_path):
            calls.append(len(calls))
            if len(calls) <= 18:
                raise StepWaiting(step=name, identity="approved-tool-and-version", delay=120)
            _write_baton(issue, from_step=name, to_owner="done", to_step="done",
                         intent="workflow_complete")
            return StepExecutionResult(response="CAFE_WORKFLOW_COMPLETE", artifacts={})

    def make_runtime():
        return BlackboardWorkflowRuntime(issue_dir=issue, playbook=graph,
                                         executor=executor, workflow_event_callback=callbacks.append)
    runtime = make_runtime()
    def consume_workspace():
        with workspace_execution_lock(tmp_path):
            return True
    def sleep(delay):
        nonlocal runtime
        sleeps.append(delay)
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(consume_workspace).result(timeout=2)
        with pytest.raises(WorkerAlreadyRunningError):
            WorkflowHost(issue).run(lambda: pytest.fail("second worker"), hosting="foreground")
        saved = BlackboardStore(issue).load_or_create("ship", playbook_id="wait")
        assert saved.current_step == "ship"
        assert saved.host_wait == {"step": "ship", "identity": "approved-tool-and-version"}
        assert saved.step_attempt_counts.get("ship", 0) == 0
        assert not HumanTaskRecordStore(issue).tasks()
        assert callbacks == []
        # Reconstructing the runtime proves restart does not depend on an in-memory cursor.
        if len(sleeps) == 3:
            runtime = make_runtime()
    monkeypatch.setattr("cafe.workflow_execution.workflow_hosting.time.sleep", sleep)
    result = WorkflowHost(issue).run_worker(lambda: runtime.run()).result
    assert result.completed
    assert sum(sleeps) == 2160
    assert len(calls) == 19
    saved = BlackboardStore(issue).load_or_create("ship", playbook_id="wait")
    assert saved.host_wait is None
    assert sum(event.event_type == "step_waiting" for event in saved.events) == 1
    assert not any(event["event_type"] == "workflow_interruption" for event in callbacks)


def test_foreground_returns_waiting_and_releases_advancement_lock(tmp_path):
    host = WorkflowHost(tmp_path)
    result = PlaybookRunResult("ship", "HOST_WAITING", False, wait_seconds=30)
    assert host.run(lambda: result, hosting="foreground").result is result
    assert host.run(lambda: "reacquired", hosting="foreground").result == "reacquired"


def test_changed_workflow_position_stops_old_waiter(tmp_path):
    issue = tmp_path / ".cafe/issues/wait"
    graph = {"playbook": {"id": "wait"}, "steps": {
        "ship": {"skill": "custom", "role": "developer", "on": {"workflow_complete": "_done"}}
    }}
    def executor(name, declaration, state):
        raise StepWaiting(step=name, identity="old", delay=30)
    runtime = BlackboardWorkflowRuntime(issue_dir=issue, playbook=graph, executor=executor)
    assert runtime.run().wait_seconds == 30
    store = BlackboardStore(issue)
    state = store.load_or_create("ship", playbook_id="wait")
    store.set_current_step(state, "done")
    store.update_handoff_contract(state, from_step="ship", to_owner=HandoffOwner.DONE,
                                 to_step="done", intent=HandoffIntent.WORKFLOW_COMPLETE, source="test")
    assert runtime.run().completed
