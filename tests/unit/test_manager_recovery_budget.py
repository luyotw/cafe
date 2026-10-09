"""Manager retry accounting uses durable tasks across callbacks and restart."""

import importlib.util
from pathlib import Path

import pytest

from cafe.core.human_task_records import HumanTaskRecordStore

SCRIPT = (
    Path(__file__).parents[2]
    / "src/cafe/data/skills/use-cafe-workflow/scripts/inspect_recovery_budget.py"
)
spec = importlib.util.spec_from_file_location("recovery_budget", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def interruption(store, *, step="develop", iteration=1):
    return store.materialize(
        workflow_id="workflow",
        step=step,
        iteration=iteration,
        trigger="agent_execution_interrupted",
        policy_id="agent-execution-interrupted",
        prompt="Recover stopped agent",
        expected_result={},
        continuations={"retry": step},
        assignee_type="user",
    )


def test_three_retries_survive_restart_and_duplicate_callbacks(tmp_path):
    store = HumanTaskRecordStore(tmp_path)
    for used in range(4):
        task = interruption(store)
        before = store.file_path.read_bytes()
        observed = module.inspect_budget(tmp_path, task.id)
        assert observed["retries_used"] == used
        assert observed["retries_remaining"] == 3 - used
        assert observed["delay_seconds"] == 30
        assert observed["action"] == ("user_handoff" if used == 3 else "inspect_retry_safety")
        assert store.file_path.read_bytes() == before
        if used == 3:
            break
        payload = {"decision": "retry", "work_report": {"summary": f"retry {used + 1}"}}
        first = store.complete(
            workflow_id="workflow", task_id=task.id, payload=payload, source="manager"
        )
        store = HumanTaskRecordStore(tmp_path)  # New Manager process uses no memory counter.
        replay = store.complete(
            workflow_id="workflow", task_id=task.id, payload=payload, source="callback"
        )
        assert first.id == replay.id
        assert module.inspect_budget(tmp_path, task.id)["action"] == "ignore_callback"


def test_user_stop_retains_pause_and_cancelled_task_cannot_retry(tmp_path):
    store = HumanTaskRecordStore(tmp_path)
    task = interruption(store)
    before = store.file_path.read_bytes()
    assert module.inspect_budget(tmp_path, task.id, stop_requested=True)["action"] == "retain_pause"
    assert store.file_path.read_bytes() == before
    store.cancel(workflow_id="workflow", task_id=task.id, reason="user stopped")
    assert module.inspect_budget(tmp_path, task.id)["action"] == "ignore_callback"


def test_user_recovery_counts_and_only_a_new_iteration_gets_a_new_budget(tmp_path):
    store = HumanTaskRecordStore(tmp_path)
    for decision in ("retry", "retry_fresh_session", "retry"):
        task = interruption(store)
        store.complete(
            workflow_id="workflow", task_id=task.id, payload={"decision": decision}, source="user"
        )
    current = interruption(store)
    assert module.inspect_budget(tmp_path, current.id)["action"] == "user_handoff"
    new = interruption(store, iteration=2)
    assert module.inspect_budget(tmp_path, new.id)["retries_used"] == 0
    other = interruption(store, step="review")
    assert module.inspect_budget(tmp_path, other.id)["retries_used"] == 0


def test_ambiguous_recovery_history_fails_closed(tmp_path):
    store = HumanTaskRecordStore(tmp_path)
    task = interruption(store)
    store.complete(
        workflow_id="workflow", task_id=task.id, payload={"decision": "unknown"}, source="user"
    )
    current = interruption(store)
    with pytest.raises(ValueError, match="unknown decision"):
        module.inspect_budget(tmp_path, current.id)
    store.file_path.write_text('{"schema_version":99}')
    with pytest.raises(ValueError):
        module.inspect_budget(tmp_path, current.id)
