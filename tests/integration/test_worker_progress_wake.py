"""A hosted provider can outlive its meaningful progress between handoffs."""

import json
import sys
from pathlib import Path
import time

import pytest

from cafe.agents.executor import AgentExecutor
from cafe.core.checklist import ChecklistGate, ChecklistMaterialization, checklist_digest
from cafe.core.types import AgentCLI, AgentConfig
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.utils.checklist_validator import validate_checklist
from cafe.workflow_execution.workflow_hosting import WorkflowHost


@pytest.mark.parametrize("activity", ["repeat", "quiet", "advance", "checklist", "history", "worker_history", "pending", "identical_gates", "large_checklist"])
def test_host_observes_provider_deadline_before_next_handoff(tmp_path, monkeypatch, activity):
    _exercise_hosted_wait(tmp_path, monkeypatch, activity)


@pytest.mark.parametrize("row_count", [10_000, 10_001])
@pytest.mark.parametrize("agent_owned", [False, True], ids=["declared", "agent_owned"])
def test_host_credits_outstanding_items_among_completed_notes(tmp_path, monkeypatch, row_count, agent_owned):
    blocks = [f"[ ] Verify result {n}\n" for n in range(3)]
    gates = tuple(ChecklistGate(checklist_digest(block), "policy", block) for block in blocks)
    pinned = (ChecklistMaterialization("", (), (), False, agent_owned=True) if agent_owned
              else ChecklistMaterialization("".join(blocks), gates, (), False))
    notes = "".join(f"[x] Note {n}\n" for n in range(row_count - len(blocks)))
    _exercise_hosted_wait(tmp_path, monkeypatch, "checklist", live_checklist="".join(blocks) + notes,
                          pinned=pinned)


def _exercise_hosted_wait(tmp_path, monkeypatch, activity, *, live_checklist=None, pinned=None):
    from cafe.agents import executor

    original = executor.numeric_limit

    def wait_limit(key, *args, **kwargs):
        return 1 if key == "agent.stdout-idle" else original(key, *args, **kwargs)

    monkeypatch.setattr(executor, "numeric_limit", wait_limit)
    issue = tmp_path / ".cafe/issues/fixture"
    step = "assemble_custom"
    output = issue / step / "iteration_001/output.md"
    events = []

    def execute(*args, **kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        script = """
import sys, time
from pathlib import Path
activity, path = sys.argv[1:]
for i in range(16):
    if activity == 'advance' and i in (4, 8, 12):
        with Path(path).open('a') as out:
            out.write(f'### PLAN-00{i}\\n- Status: completed\\n')
    if activity in ('checklist', 'identical_gates', 'large_checklist') and i in (4, 8, 12):
        checklist = Path(path).with_name('checklist.md')
        checklist.write_text(checklist.read_text().replace('[ ]', '[x]', 1))
    if activity != 'quiet':
        print('same observation', flush=True)
    time.sleep(.1)
print('complete', flush=True)
"""
        if activity in {"history", "pending"}:
            from cafe.core.human_task_records import HumanTaskRecordStore
            records = HumanTaskRecordStore(issue)
            task = records.materialize(workflow_id=runtime.blackboard.workflow_id, step=step,
                iteration=1, trigger="confirm_output", policy_id="prior", prompt="Earlier decision",
                expected_result={}, continuations={}, assignee_type="user")
            if activity == "history":
                records.complete(workflow_id=task.workflow_id, task_id=task.id,
                                 payload={"feedback": "x" * (256 * 1024)}, source="interactive")
        output.write_text("## Todo Progress\n")
        checklist = "[ ] Verify result\n  first source\n[ ] Verify result\n  second source\n[ ] Verify result\n  third source\n"
        if activity == "identical_gates":
            checklist = "".join(f"## {section}\n[ ] Verify result\n" for section in ("Build", "Review", "Delivery"))
        if activity == "large_checklist":
            checklist += "a" * (262145 - len(checklist))
        if pinned is not None:
            metadata = output.with_name("iteration.json")
            record = json.loads(metadata.read_text()) if metadata.exists() else {}
            record["effective_checklist"] = pinned.to_dict()
            metadata.write_text(json.dumps(record))
        checklist_path = output.with_name("checklist.md")
        checklist_path.write_text(live_checklist if live_checklist is not None else checklist)
        AgentExecutor(AgentConfig(name="fixture", cli=AgentCLI.CLAUDE))._execute_streaming_process(
            [sys.executable, "-c", script, activity, str(output)], "fixture"
        )
        if pinned is not None:
            assert validate_checklist(checklist_path, expected=pinned).is_complete
        return StepExecutionResult(response="", status_code="await_agent", artifacts={})

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue,
        playbook={"playbook": {"id": "fixture"}, "steps": {
            step: {"skill": "fixture", "on": {"await_agent": "_done"}}}},
        executor=execute, workflow_event_callback=events.append,
    )
    if activity == "worker_history":
        from cafe.workflow_execution.worker_launch import WorkerLaunchStore
        store = WorkerLaunchStore(issue)
        with store._locked_records() as records:
            for i in range(1400):
                key = f"prior-{i}"
                records[key] = {"worker_id": key, "status": "exited", "error_code": "x" * 170}
        assert store.path.stat().st_size > 256 * 1024
    start = time.monotonic()
    WorkflowHost(issue).run(runtime.run, hosting="foreground")
    diagnostics = [e for e in events if e["event_type"] == "worker_progress_overdue"]
    assert len(diagnostics) == (0 if activity in ("advance", "checklist", "pending", "identical_gates", "large_checklist") else 1)
    if diagnostics:
        diagnostic, = diagnostics
        assert diagnostic["step"] == step and diagnostic["worker_id"]
        assert diagnostic["elapsed_seconds"] >= diagnostic["wait_seconds"] == 1
        assert time.monotonic() - start < 8
        if activity == "worker_history":
            assert not diagnostic.get("reason")
        if activity == "history":
            assert diagnostic.get("reason") == "progress_ownership_unverified"


def test_identical_item_cannot_renew_or_duplicate_elapsed_diagnostic(tmp_path):
    from cafe.workflow_execution.progress_watch import ProgressWatch

    now = [0]
    output = tmp_path / "output.md"
    output.write_text("## Todo Progress\n### A\n- Status: completed\n")
    watch = ProgressWatch(output, clock=lambda: now[0])
    for t in (3, 6, 9):
        now[0] = t
        output.write_text(output.read_text() + "heartbeat\n")
        assert watch.due(10) is None
    now[0] = 10
    assert watch.due(10)["elapsed_seconds"] == 10
    now[0] = 15
    assert watch.due(10) is None
    output.write_text(output.read_text() + "### B\n- Status: completed\n")
    now[0] = 17
    assert watch.due(10) is None
    now[0] = 26
    assert watch.due(10) is None
    now[0] = 27
    assert watch.due(10)["progress_source"] == "completed_todo"

def test_unsuitable_progress_file_leaves_deadline_serviceable(tmp_path):
    import os
    import subprocess
    output = tmp_path / "output.md"
    os.mkfifo(output)
    command = "from cafe.workflow_execution.progress_watch import ProgressWatch; from pathlib import Path; import sys; assert ProgressWatch(Path(sys.argv[1])).due(0)"
    subprocess.run([sys.executable, "-c", command, str(output)], check=True, timeout=3)


def test_new_checkboxes_cannot_extend_the_declared_review_work(tmp_path):
    from cafe.workflow_execution.progress_watch import ProgressWatch

    now = [0]
    checklist = tmp_path / "checklist.md"
    checklist.write_text("[ ] Verify review evidence\n")
    watch = ProgressWatch(tmp_path / "output.md", clock=lambda: now[0])
    checklist.write_text(checklist.read_text() + "[ ] Observe heartbeat\n")
    now[0] = 5
    assert watch.due(10) is None
    checklist.write_text(checklist.read_text().replace("[ ] Observe", "[x] Observe"))
    now[0] = 10
    assert watch.due(10)["progress_source"] == "worker_start"


@pytest.mark.parametrize("size,accepted", [(2 * 1024 * 1024, True), (2 * 1024 * 1024 + 1, False)])
def test_progress_at_materialized_checklist_size_limit(tmp_path, size, accepted):
    from cafe.workflow_execution.progress_watch import ProgressWatch

    now = [0]
    path = tmp_path / "checklist.md"
    body = "[ ] Verify result\n"
    path.write_text(body + "a" * (size - len(body)))
    watch = ProgressWatch(tmp_path / "output.md", clock=lambda: now[0])
    path.write_text(path.read_text().replace("[ ]", "[x]", 1))
    now[0] = 8
    assert watch.due(10) is None
    now[0] = 10
    diagnostic = watch.due(10)
    if accepted:
        assert diagnostic is None
        now[0] = 18
        assert watch.due(10)["progress_source"] == "completed_checklist_item"
    else:
        assert diagnostic["progress_source"] == "worker_start"


def test_rechecking_same_gate_cannot_impersonate_next_occurrence(tmp_path):
    from cafe.workflow_execution.progress_watch import ProgressWatch

    now = [0]
    path = tmp_path / "checklist.md"

    def write(states):
        path.write_text("".join(f"[{state}] Verify result\n" for state in states))

    write("  ")
    watch = ProgressWatch(tmp_path / "output.md", clock=lambda: now[0])
    for mark, states in [(4, "x "), (8, "  "), (12, "x ")]:
        now[0] = mark
        write(states)
        assert watch.due(10) is None
    now[0] = 14
    assert watch.due(10)["elapsed_seconds"] == 10
    write(" x")
    now[0] = 18
    assert watch.due(10) is None
    now[0] = 28
    assert watch.due(10)["elapsed_seconds"] == 10


def test_added_notes_cross_count_boundary_without_renewing_a_completed_item(tmp_path):
    from cafe.workflow_execution.progress_watch import ProgressWatch

    now = [0]
    path = tmp_path / "checklist.md"
    notes = "[x] Observation\n" * 9998
    gates = "[ ] First\n[ ] Second\n"
    path.write_text(gates + notes)
    watch = ProgressWatch(tmp_path / "output.md", clock=lambda: now[0])
    notes += "[x] Extra observation\n"
    for tick, first in [(4, "x"), (8, " "), (12, "x")]:
        now[0] = tick
        path.write_text(gates.replace("[ ] First", f"[{first}] First") + notes)
        assert watch.due(10) is None
    now[0] = 14
    assert watch.due(10)["elapsed_seconds"] == 10
    path.write_text(gates.replace("[ ]", "[x]") + notes)
    now[0] = 18
    assert watch.due(10) is None
    now[0] = 28
    assert watch.due(10)["elapsed_seconds"] == 10


def test_changed_nested_rule_cannot_credit_declared_parent(tmp_path):
    from cafe.workflow_execution.progress_watch import ProgressWatch

    now = [0]
    path = tmp_path / "checklist.md"
    path.write_text("[ ] Verify\n  [ ] Read the receipt\n")
    watch = ProgressWatch(tmp_path / "output.md", clock=lambda: now[0])
    path.write_text("[x] Verify\n  [ ] Observe a heartbeat\n")
    now[0] = 10
    assert watch.due(10)["progress_source"] == "worker_start"
    path.write_text("[x] Verify\n  [ ] Read the receipt\n")
    now[0] = 15
    assert watch.due(10) is None
    now[0] = 25
    assert watch.due(10)["progress_source"] == "completed_checklist_item"


def test_polling_active_worker_does_not_read_archived_records(tmp_path, monkeypatch):
    from cafe.workflow_execution.intervention import InterventionControl

    control = InterventionControl(tmp_path, "active")
    store = control.store
    with store._locked_records() as records:
        for i in range(1400):
            records[str(i)] = {"worker_id": str(i), "status": "exited", "error_code": "x" * 170}
        records["active"] = {"worker_id": "active", "status": "running"}

    original = Path.read_text
    def bounded_read(path, *args, **kwargs):
        assert path != store.path, "unrestricted worker history read during provider poll"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", bounded_read)
    control.check()
