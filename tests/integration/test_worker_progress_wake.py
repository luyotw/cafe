"""A hosted provider can outlive its meaningful progress between handoffs."""

import sys
import time

import pytest

from cafe.agents.executor import AgentExecutor
from cafe.core.types import AgentCLI, AgentConfig
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.workflow_execution.workflow_hosting import WorkflowHost


@pytest.mark.parametrize("activity", ["repeat", "quiet", "advance", "checklist"])
def test_host_observes_provider_deadline_before_next_handoff(tmp_path, monkeypatch, activity):
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
    if activity == 'checklist' and i in (4, 8, 12):
        checklist = Path(path).with_name('checklist.md')
        checklist.write_text(checklist.read_text().replace('[ ]', '[x]', 1))
    if activity != 'quiet':
        print('same observation', flush=True)
    time.sleep(.1)
print('complete', flush=True)
"""
        output.write_text("## Todo Progress\n")
        output.with_name("checklist.md").write_text("[ ] First check\n[ ] Second check\n[ ] Third check\n")
        AgentExecutor(AgentConfig(name="fixture", cli=AgentCLI.CLAUDE))._execute_streaming_process(
            [sys.executable, "-c", script, activity, str(output)], "fixture"
        )
        return StepExecutionResult(response="", status_code="await_agent", artifacts={})

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue,
        playbook={"playbook": {"id": "fixture"}, "steps": {
            step: {"skill": "fixture", "on": {"await_agent": "_done"}}}},
        executor=execute, workflow_event_callback=events.append,
    )
    start = time.monotonic()
    WorkflowHost(issue).run(runtime.run, hosting="foreground")
    diagnostics = [e for e in events if e["event_type"] == "worker_progress_overdue"]
    assert len(diagnostics) == (0 if activity in ("advance", "checklist") else 1)
    if diagnostics:
        diagnostic, = diagnostics
        assert diagnostic["step"] == step and diagnostic["worker_id"]
        assert diagnostic["elapsed_seconds"] >= diagnostic["wait_seconds"] == 1
        assert time.monotonic() - start < 8


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
