"""Exact public requests against a real host and quiet provider process group."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from typer.testing import CliRunner

from cafe.agents.process_control import live_processes
from cafe.core.blackboard import BlackboardStore
from cafe.ui.cli import app
from cafe.workflow_execution.intervention import inspect_intervention
from cafe.workflow_execution.worker_launch import WorkerLaunchStore

WORKER = """
import json, sys, os
from pathlib import Path
from cafe.core.blackboard import BlackboardStore
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_control import current_control
from cafe.core.workspace_lock import workspace_execution_lock
from cafe.agents.executor import AgentExecutor
from cafe.core.types import AgentConfig, AgentCLI
from cafe.workflow_execution.workflow_hosting import WorkflowHost
root=Path(sys.argv[1]); issue=root/'.cafe/issues/job'
crash=os.environ.get('CAFE_TEST_CRASH_POINT')
if crash=='spawn':
    import subprocess, time
    from cafe.workflow_execution.worker_launch import WorkerLaunchStore
    popen=subprocess.Popen
    def crash_spawn(*args,**kwargs):
        child=popen(*args,**kwargs)
        if len(args[0])>1 and args[0][1]==str(root/'provider.py'):
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                if (root/'release').exists(): os._exit(19)
                time.sleep(.03)
        return child
    subprocess.Popen=crash_spawn
if crash=='event':
    import cafe.workflow_execution.intervention as mod
    queue=mod._queue_quiescence
    def crash_queue(issue,request):
        queue(issue,request); os._exit(23)
    mod._queue_quiescence=crash_queue
if crash=='input':
    import cafe.core.packet_io as io
    write=io.atomic_write_bytes
    def crash_write(path,data):
        write(path,data)
        if path.name=='user_input.md' and b'cafe-supervisor-correction:' in data: os._exit(7)
    io.atomic_write_bytes=crash_write
if crash=='handoff':
    import cafe.core.workflow_runtime as mod
    apply=mod.apply_correction
    def crash_apply(issue,request):
        apply(issue,request); os._exit(7)
    mod.apply_correction=crash_apply
steps={'build':{'skill':'cafe-develop','role':'developer','assignee_type':'agent','on':{'await_agent':'publish'}},
       'publish':{'skill':'cafe-develop','role':'developer','on':{'await_agent':'_done'}}}
if crash=='native': steps['build']['execution']={'review_policy':'single_native'}
def execute(step, definition, state, **kw):
    if step=='publish':
        (root/'downstream').write_text('must not start')
    iteration=issue/step/'iteration_001'; iteration.mkdir(parents=True,exist_ok=True)
    (iteration/'iteration.json').write_text(json.dumps({'iteration':1,'agent_invoked':True}))
    if not (iteration/'user_input.md').exists(): (iteration/'user_input.md').write_text('Original obligations')
    current_control().position(state,steps,iteration=1)
    with workspace_execution_lock(root):
        AgentExecutor(AgentConfig(name='provider',cli=AgentCLI.CLAUDE),stream_output=False)._execute_streaming_process(
            [sys.executable,str(root/'provider.py'),str(root),str(iteration/'user_input.md')],'fixture')
    return StepExecutionResult(response='',status_code='await_agent',artifacts={})
runtime=BlackboardWorkflowRuntime(issue_dir=issue, playbook={'playbook':{'id':'controlled'},'steps':steps},executor=execute,
    workflow_event_callback=lambda event:(root/'callback.json').write_text(json.dumps(event)))
if crash=='between':
    original=runtime._run_non_agent_owner
    def between(**kwargs):
        if kwargs['current_step']=='publish':
            (root/'between').write_text('ready')
            import time
            while not (root/'release').exists(): time.sleep(.03)
        return original(**kwargs)
    runtime._run_non_agent_owner=between
if crash=='boundary':
    def callback(event):
        if event.get('step')=='build':
            (root/'boundary').write_text('committed')
            import time
            while not (root/'release').exists(): time.sleep(.03)
    runtime._workflow_event_callback=callback
result=WorkflowHost(issue).run(lambda:runtime.run(single_step=os.environ.get('CAFE_TEST_RESUME')=='1'),hosting='foreground').result
(root/'result.json').write_text(json.dumps({'status':result.final_status_code,'step':result.final_step}))
"""
PROVIDER = """
import os, subprocess, sys, time
from pathlib import Path
root=Path(sys.argv[1])
child=subprocess.Popen([sys.executable,'-c',"import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(600)"])
(root/'partial.txt').write_text('Preserve partial work')
(root/'pids.json').write_text(__import__('json').dumps([os.getpid(),child.pid]))
time.sleep(600)
"""


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.03)
    pytest.fail("controlled worker did not reach the expected bounded condition")


@pytest.fixture
def worker(tmp_path, monkeypatch, request):
    root = tmp_path / "repo"
    root.mkdir()
    provider = PROVIDER
    if getattr(request, "param", None) == "escape":
        provider = provider.replace(
            'time.sleep(600)"])', 'time.sleep(600)"], start_new_session=True)'
        )
    if getattr(request, "param", None) in {"between", "boundary"}:
        provider = "from pathlib import Path;import sys;Path(sys.argv[1], 'completed-provider').write_text('done')"
    (root / "provider.py").write_text(provider)
    for args in (
        ("init", "-b", "feature"),
        ("config", "user.name", "Test"),
        ("config", "user.email", "test@example.invalid"),
        ("commit", "--allow-empty", "-m", "Baseline"),
    ):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    script = tmp_path / "worker.py"
    script.write_text(WORKER)
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src")}
    if getattr(request, "param", None):
        environment["CAFE_TEST_CRASH_POINT"] = request.param
    process = subprocess.Popen(
        [sys.executable, str(script), str(root)],
        cwd=root,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=(tmp_path / "worker.stderr").open("w"),
    )
    issue = root / ".cafe/issues/job"
    try:
        wait_for(
            lambda: (
                (root / "pids.json").exists()
                or (root / "between").exists()
                or (root / "boundary").exists()
                or process.poll() is not None
            )
        )
        assert process.poll() is None, (tmp_path / "worker.stderr").read_text()
        monkeypatch.chdir(root)
        identity = {
            k: inspect_intervention(issue)[k]
            for k in ("worker_id", "workflow_id", "step", "iteration")
        }
        yield root, issue, process, identity
    finally:
        if (root / "pids.json").exists():
            for pid in json.loads((root / "pids.json").read_text()):
                try:
                    os.kill(pid, 9)
                except ProcessLookupError:
                    pass
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def request(identity, *extra):
    args = ["workflow-interrupt", "--issue", "job"]
    for key, value in identity.items():
        args += ["--" + key.replace("_", "-"), str(value)]
    args += [
        "--reason",
        "Confirmed scope violation",
        "--feedback",
        "Remove the invented release observer",
        *extra,
    ]
    return CliRunner().invoke(app, args)


def test_public_request_stops_quiet_provider_and_term_ignoring_child(worker):
    root, issue, process, identity = worker
    before = BlackboardStore(issue).load_or_create("build").step_attempt_counts.copy()
    result = request(identity)
    assert result.exit_code == 0, result.output + repr(result.exception)
    assert json.loads(result.output)["status"] == "requested"  # A request never claims a stop.
    process.wait(timeout=15)
    assert process.returncode == 0, (root.parent / "worker.stderr").read_text()
    assert (
        json.loads((root / "result.json").read_text())["status"]
        == "INTERRUPTED:supervisor_correction"
    )
    pids = json.loads((root / "pids.json").read_text())
    assert not set(pids) & live_processes().keys()
    assert not (root / "downstream").exists()
    assert (root / "partial.txt").read_text() == "Preserve partial work"
    text = (issue / "build/iteration_001/user_input.md").read_text()
    assert (
        "Original obligations" in text and text.count("Remove the invented release observer") == 1
    )
    state = BlackboardStore(issue).load_or_create("build")
    assert state.current_step == "build" and state.step_attempt_counts == before
    assert state.handoff_contract.source == "workflow.supervisor_correction"
    assert inspect_intervention(issue)["intervention"]["status"] == "quiesced"
    callback = json.loads((root / "callback.json").read_text())
    assert callback["status_code"] == "INTERRUPTED:supervisor_correction"
    # Duplicate delivery changes neither inputs nor tasks, and does not start another worker.
    again = request(identity)
    assert again.exit_code == 0 and json.loads(again.output)["status"] == "quiesced"
    assert (issue / "build/iteration_001/user_input.md").read_text() == text


@pytest.mark.parametrize("field", ["workflow_id", "worker_id", "step", "iteration"])
def test_stale_identity_does_not_touch_live_work(worker, field):
    root, issue, process, identity = worker
    stale = {**identity, field: 2 if field == "iteration" else "old-identity"}
    result = request(stale)
    assert result.exit_code == 2, result.output
    assert process.poll() is None
    assert WorkerLaunchStore(issue).get(identity["worker_id"]).get("intervention") is None


def test_pid_reuse_identity_refused_without_signalling(worker):
    root, issue, process, identity = worker
    store = WorkerLaunchStore(issue)
    with store._locked_records() as records:
        records[identity["worker_id"]]["process_birth"] = "old-pid-incarnation"
    result = request(identity)
    assert result.exit_code == 2 and "process identity" in result.output
    assert process.poll() is None


def test_pending_human_decision_refuses_intervention(worker):
    root, issue, process, identity = worker
    from cafe.core.human_task_records import HumanTaskRecordStore

    task = HumanTaskRecordStore(issue).materialize(
        workflow_id=identity["workflow_id"],
        step="build",
        iteration=1,
        trigger="need_permission",
        policy_id="user-only",
        prompt="Explicit permission required",
        expected_result={},
        continuations={"submit": "build"},
        assignee_type="user",
    )
    result = request(identity)
    assert result.exit_code == 2 and "pending" in result.output
    assert process.poll() is None
    assert HumanTaskRecordStore(issue).get_task(task.id).status.value == "pending"


def test_corrupt_worker_store_freezes_instead_of_forgetting_request(worker):
    root, issue, process, identity = worker
    store = WorkerLaunchStore(issue)
    original = store.path.read_bytes()
    try:
        store.path.write_text('{"attempts":')
        result = request(identity)
        assert result.exit_code == 2 and "frozen" in result.output
    finally:
        store.path.write_bytes(original)
    assert process.poll() is None


@pytest.mark.parametrize("worker", ["input", "handoff"], indirect=True)
def test_restart_recovers_both_correction_commit_crash_points_once(worker):
    root, issue, process, identity = worker
    result = request(identity)
    assert result.exit_code == 0, result.output
    process.wait(timeout=15)
    assert process.returncode == 7, (root.parent / "worker.stderr").read_text()
    record = WorkerLaunchStore(issue).get(identity["worker_id"])
    assert record["intervention"]["status"] == "requested"
    assert record["provider"]["state"] == "stopped"
    from cafe.workflow_execution.workflow_hosting import WorkflowHost

    with pytest.raises(ValueError, match="pending correction"):
        WorkflowHost(issue).run(
            lambda: pytest.fail("frozen request allowed a provider"), hosting="foreground"
        )
    args = ["workflow-interrupt", "--issue", "job", "--recover"]
    for key, value in identity.items():
        args += ["--" + key.replace("_", "-"), str(value)]
    recovered = CliRunner().invoke(app, args)
    assert recovered.exit_code == 0, recovered.output + repr(recovered.exception)
    assert json.loads(recovered.output)["status"] == "quiesced"
    text = (issue / "build/iteration_001/user_input.md").read_text()
    assert text.count("Remove the invented release observer") == 1
    assert "Original obligations" in text
    state = BlackboardStore(issue).load_or_create("build")
    assert sum(e.event_type == "supervisor_correction_prepared" for e in state.events) == 1
    assert state.current_step == "build" and state.step_attempt_counts["build"] == 1
    duplicate = CliRunner().invoke(app, args)
    assert (
        duplicate.exit_code == 0
        and (issue / "build/iteration_001/user_input.md").read_text() == text
    )


def test_unknown_provider_stop_refuses_recovery(worker):
    root, issue, process, identity = worker
    result = request(identity)
    assert result.exit_code == 0
    # Recovery must not rely on an absent/reused external PID or a requested stop.
    args = ["workflow-interrupt", "--issue", "job", "--recover"]
    for key, value in identity.items():
        args += ["--" + key.replace("_", "-"), str(value)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 2  # The live host still owns advancement.
    process.wait(timeout=15)


@pytest.mark.parametrize("worker", ["spawn"], indirect=True)
def test_spawn_crash_never_reuses_missing_or_previous_stop_receipt(worker):
    root, issue, process, identity = worker
    result = request(identity)
    assert result.exit_code == 2 and "transitioning" in result.output
    (root / "release").write_text("crash during spawn handoff")
    process.wait(timeout=15)
    assert process.returncode == 19
    record = WorkerLaunchStore(issue).get(identity["worker_id"])
    assert record["provider"]["state"] == "starting"
    from cafe.workflow_execution.workflow_hosting import WorkflowHost

    with pytest.raises(ValueError, match="unverified provider"):
        WorkflowHost(issue).run(
            lambda: pytest.fail("unknown spawn allowed another provider"), hosting="foreground"
        )
    assert set(json.loads((root / "pids.json").read_text())) & live_processes().keys()
    assert record.get("intervention") is None  # No request was accepted after dispatch began.


@pytest.mark.parametrize("worker", ["escape"], indirect=True)
def test_escaped_descendant_keeps_correction_frozen(worker):
    root, issue, process, identity = worker
    assert request(identity).exit_code == 0
    process.wait(timeout=15)
    assert process.returncode != 0
    record = WorkerLaunchStore(issue).get(identity["worker_id"])
    assert record["provider"]["state"] == "unverified"
    assert record["intervention"]["status"] == "requested"
    assert json.loads((root / "pids.json").read_text())[1] in live_processes()
    assert (
        "cafe-supervisor-correction:"
        not in (issue / "build/iteration_001/user_input.md").read_text()
    )
    assert not (root / "callback.json").exists()


def test_inspection_ignores_old_crashed_worker_incarnation(worker):
    root, issue, process, identity = worker
    store = WorkerLaunchStore(issue)
    with store._locked_records() as records:
        old = {
            **records[identity["worker_id"]],
            "worker_id": "crashed-old",
            "process_birth": "old-birth",
        }
        records["crashed-old"] = old
    assert inspect_intervention(issue)["worker_id"] == identity["worker_id"]
    assert process.poll() is None


@pytest.mark.parametrize("worker", ["event"], indirect=True)
def test_callback_is_durable_before_correction_unfreezes(worker):
    root, issue, process, identity = worker
    assert request(identity).exit_code == 0
    process.wait(timeout=15)
    assert process.returncode == 23
    record = WorkerLaunchStore(issue).get(identity["worker_id"])
    assert record["intervention"]["status"] == "prepared"
    state = BlackboardStore(issue).load_or_create("build")
    events = [
        e.data
        for e in state.events
        if e.event_type == "workflow_event_callback_enqueued"
        and e.data.get("request_id") == record["intervention"]["request_id"]
    ]
    assert len(events) == 1
    from cafe.workflow_execution.intervention import recover_intervention

    recover_intervention(issue, identity)
    fresh = BlackboardStore(issue).load_or_create("build")
    assert (
        sum(
            e.event_type == "workflow_event_callback_enqueued"
            and e.data.get("request_id") == record["intervention"]["request_id"]
            for e in fresh.events
        )
        == 1
    )
    assert (
        WorkerLaunchStore(issue).get(identity["worker_id"])["intervention"]["status"] == "quiesced"
    )


@pytest.mark.parametrize("worker", ["between"], indirect=True)
def test_request_between_steps_prevents_next_provider(worker):
    root, issue, process, identity = worker
    assert identity["step"] == "publish"
    assert request(identity).exit_code == 0
    (root / "release").write_text("continue")
    process.wait(timeout=10)
    assert process.returncode == 0, (root.parent / "worker.stderr").read_text()
    assert not (root / "downstream").exists()
    assert inspect_intervention(issue)["intervention"]["status"] == "quiesced"
    assert (
        "Remove the invented release observer"
        in (issue / "publish/iteration_001/user_input.md").read_text()
    )


@pytest.mark.parametrize("worker", ["boundary"], indirect=True)
def test_committed_transition_rejects_old_identity(worker):
    root, issue, process, identity = worker
    result = request(identity)
    assert result.exit_code == 2 and "transitioning" in result.output
    assert BlackboardStore(issue).load_or_create("build").current_step == "publish"
    assert WorkerLaunchStore(issue).get(identity["worker_id"]).get("intervention") is None
    # Test cleanup stops this worker; a refused request did not cancel or accept anything.


@pytest.mark.parametrize(
    "invalid",
    [
        {"intervention": {}},
        {"intervention": {"status": "requestd"}},
        {"provider": {"state": "complete-maybe"}},
    ],
)
def test_valid_json_invalid_control_state_fails_closed(worker, invalid):
    root, issue, process, identity = worker
    store = WorkerLaunchStore(issue)
    original = store.path.read_bytes()
    try:
        data = json.loads(original)
        data["attempts"][identity["worker_id"]].update(invalid)
        store.path.write_text(json.dumps(data))
        result = request(identity)
        assert result.exit_code == 2 and "frozen" in result.output
    finally:
        store.path.write_bytes(original)
    assert process.poll() is None


@pytest.mark.parametrize("worker", ["native"], indirect=True)
def test_native_review_policy_remains_outside_supported_cancellation(worker):
    root, issue, process, identity = worker
    assert not inspect_intervention(issue)["accepts_interruption"]
    result = request(identity)
    assert result.exit_code == 2
    assert process.poll() is None


def test_explicit_resume_reads_same_step_correction_once(worker):
    root, issue, process, identity = worker
    assert request(identity).exit_code == 0
    process.wait(timeout=15)
    assert process.returncode == 0
    # Resume uses the same workflow/iteration inputs, with a real replacement
    # provider fixture that records the received input and terminates normally.
    (root / "provider.py").write_text(
        "from pathlib import Path;import sys;"
        "Path(sys.argv[1], 'resumed-input').write_text(Path(sys.argv[2]).read_text())"
    )
    environment = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).parents[2] / "src"),
        "CAFE_TEST_RESUME": "1",
    }
    resumed = subprocess.run(
        [sys.executable, str(root.parent / "worker.py"), str(root)],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert resumed.returncode == 0, resumed.stderr + resumed.stdout
    text = (root / "resumed-input").read_text()
    assert text.count("Remove the invented release observer") == 1
    assert "Original obligations" in text
    state = BlackboardStore(issue).load_or_create("build")
    assert state.workflow_id == identity["workflow_id"] and state.current_step == "publish"
    assert not (root / "downstream").exists()
    assert not any(
        e.event_type == "step_attempt_count_reset"
        and e.data.get("step") == "build"
        and e.data.get("transition_source") == "workflow.supervisor_correction"
        for e in state.events
    )


def test_corrupt_worker_control_does_not_suppress_failure_callback(tmp_path):
    from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
    from cafe.ui.commands.workflow import _record_execution_failure

    issue = tmp_path / ".cafe/issues/corrupt-worker"
    seen = []
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue,
        playbook={
            "playbook": {"id": "controlled"},
            "steps": {"build": {"skill": "cafe-develop", "role": "developer", "on": {}}},
        },
        executor=lambda *a, **kw: pytest.fail("failure notification cannot execute a provider"),
        workflow_event_callback=seen.append,
    )
    store = WorkerLaunchStore(issue)
    store.path.write_text('{"attempts":')
    _record_execution_failure(
        runtime,
        ValueError("worker control unreadable"),
        issue_dir=issue,
        launch_store=store,
        worker_id="failed-worker",
        callback=None,
    )
    assert len(seen) == 1 and seen[0]["event_type"] == "workflow_interruption"
    assert any(
        e.event_type == "workflow_interrupted"
        for e in BlackboardStore(issue).load_or_create("build").events
    )
    with pytest.raises(ValueError, match="frozen"):
        store.get("failed-worker")


@pytest.mark.parametrize("failure", ["permission", "format"])
def test_definitely_unspawned_executable_error_does_not_freeze_next_host(tmp_path, failure):
    from cafe.agents.executor import AgentExecutor
    from cafe.core.types import AgentConfig, AgentCLI
    from cafe.core.workflow_control import current_control
    from cafe.workflow_execution.workflow_hosting import WorkflowHost

    root = tmp_path / "repo"
    root.mkdir()
    issue = root / ".cafe/issues/unspawned"
    state = BlackboardStore(issue).load_or_create("build")
    executable = root / "invalid-program"
    executable.write_text("invalid executable format")
    executable.chmod(0o644 if failure == "permission" else 0o755)

    def run():
        current_control().position(state, {"build": {"assignee_type": "agent"}})
        AgentExecutor(
            AgentConfig(name="provider", cli=AgentCLI.CLAUDE), stream_output=False
        )._execute_streaming_process([str(executable)], "fixture")

    with pytest.raises(OSError):
        WorkflowHost(issue).run(run, hosting="foreground")
    records = WorkerLaunchStore(issue)._read_unlocked()
    assert list(records.values())[-1]["provider"]["state"] == "stopped"
    assert WorkflowHost(issue).run(lambda: "ready", hosting="foreground").result == "ready"


def test_host_without_proc_support_retains_normal_provider_execution(tmp_path, monkeypatch):
    from cafe.agents.executor import AgentExecutor
    from cafe.core.types import AgentConfig, AgentCLI
    from cafe.core.workflow_control import current_control
    from cafe.workflow_execution.workflow_hosting import WorkflowHost

    monkeypatch.setattr("cafe.agents.process_control.process_birth", lambda pid: None)
    monkeypatch.setattr("cafe.agents.process_control.group_stopped", lambda pid: False)
    issue = tmp_path / ".cafe/issues/unsupported-host"
    state = BlackboardStore(issue).load_or_create("build")

    def run():
        current_control().position(state, {"build": {"assignee_type": "agent"}})
        return AgentExecutor(
            AgentConfig(name="provider", cli=AgentCLI.CLAUDE), stream_output=False
        )._execute_streaming_process(
            [sys.executable, "-c", "print('ordinary provider result')"], "fixture"
        )

    for _ in range(2):
        result = WorkflowHost(issue).run(run, hosting="foreground")
        assert "ordinary provider result" in result.result.response
    records = WorkerLaunchStore(issue)._read_unlocked()
    assert len(records) == 2 and all(not r.get("provider") for r in records.values())
    assert not inspect_intervention(issue)["accepts_interruption"]
