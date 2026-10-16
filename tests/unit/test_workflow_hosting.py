"""Generic foreground/background workflow-host ownership tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from cafe.workflow_execution.worker_launch import FixedWorkerLauncher, WorkerLaunchStore
from cafe.workflow_execution.workflow_hosting import WorkerAlreadyRunningError, WorkflowHost


def test_foreground_and_background_use_the_same_runtime_callable(tmp_path: Path) -> None:
    calls: list[str] = []
    foreground = WorkflowHost(tmp_path / "foreground")
    worker = WorkflowHost(tmp_path / "worker")

    foreground_result = foreground.run(
        lambda: calls.append("foreground") or "finished", hosting="foreground"
    )
    worker_result = worker.run_worker(lambda: calls.append("worker") or "finished")

    assert foreground_result.result == worker_result.result == "finished"
    assert calls == ["foreground", "worker"]


def test_fixed_worker_launch_passes_an_opaque_child_token(tmp_path: Path) -> None:
    launches: list[tuple[list[str], dict]] = []

    def popen(command, **kwargs):
        launches.append((command, kwargs))
        return type("Process", (), {"pid": 4321})()

    issue_dir = tmp_path / ".cafe" / "issues" / "issue432"
    record = WorkerLaunchStore(issue_dir).start()
    pid = FixedWorkerLauncher(
        issue_dir, popen_factory=popen, python_executable="/fixed/python"
    ).launch(record)

    command, kwargs = launches[0]
    assert command[:4] == ["/fixed/python", "-m", "cafe.ui.cli", "workflow"]
    assert command[command.index("--internal-worker-id") + 1] == record["worker_id"]
    assert command[command.index("--internal-worker-token") + 1] == record["worker_token"]
    assert kwargs["start_new_session"] is True
    assert pid == 4321
    assert WorkerLaunchStore(issue_dir).validate_child(
        worker_id=record["worker_id"], worker_token=record["worker_token"]
    )


def test_fixed_worker_launch_drops_parent_host_session_controls(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CODEX_REMOTE_PAYLOAD", "host-bootstrap")
    monkeypatch.setenv("CODEX_SESSION_ID", "host-session")
    monkeypatch.setenv("CODEX_THREAD_ID", "host-thread")
    monkeypatch.setenv("CODEX_HOME", "/provider-config")
    launches: list[dict] = []

    def popen(_command, **kwargs):
        launches.append(kwargs)
        return type("Process", (), {"pid": 4321})()

    issue_dir = tmp_path / ".cafe" / "issues" / "issue432"
    record = WorkerLaunchStore(issue_dir).start()
    FixedWorkerLauncher(issue_dir, popen_factory=popen).launch(record)

    environment = launches[0]["env"]
    assert all(
        key not in environment
        for key in ("CODEX_REMOTE_PAYLOAD", "CODEX_SESSION_ID", "CODEX_THREAD_ID")
    )
    assert environment["CODEX_HOME"] == "/provider-config"


def test_invalid_worker_handshake_fails_closed(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue458"
    store = WorkerLaunchStore(issue_dir)
    record = store.start()

    assert not store.validate_child(worker_id=record["worker_id"], worker_token="replaced")
    assert store.get(record["worker_id"])["status"] == "startup_failed"


def test_failed_spawn_retains_only_bounded_sanitized_diagnostic(tmp_path):
    issue = tmp_path / ".cafe/issues/failed-spawn"
    store = WorkerLaunchStore(issue)
    record = store.start()
    def fail(*_args, **_kwargs):
        raise OSError("startup unavailable token=private-credential " + "x" * 10000)
    with pytest.raises(OSError):
        FixedWorkerLauncher(issue, popen_factory=fail).launch(record)
    observed = store.get(record["worker_id"])
    assert observed["status"] == "startup_failed"
    assert "pid" not in observed
    assert observed["diagnostic"]["error_type"] == "OSError"
    assert "private-credential" not in observed["diagnostic"]["detail"]
    assert len(observed["diagnostic"]["detail"]) <= 2048
    assert not list(issue.glob("*.log"))


def test_only_one_host_worker_advances_at_a_time(tmp_path: Path) -> None:
    host = WorkflowHost(tmp_path)
    release = Event()
    entered = Event()

    def running() -> str:
        entered.set()
        assert release.wait(timeout=2)
        return "done"

    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(host.run_worker, running, worker_id="first")
        assert entered.wait(timeout=2)
        with pytest.raises(WorkerAlreadyRunningError):
            WorkflowHost(tmp_path).run_worker(lambda: "overlap", worker_id="second")
        release.set()
        assert future.result(timeout=2).result == "done"

    assert host.run_worker(lambda: "next", worker_id="next").result == "next"


@pytest.mark.parametrize("fails", [False, True])
def test_finished_host_releases_its_own_pid_for_same_process_reentry(tmp_path, fails):
    import os
    from cafe.core.workflow_repair import _quiescent

    def invocation():
        if fails:
            raise ValueError("stopped runtime")
        return "done"

    host = WorkflowHost(tmp_path)
    if fails:
        with pytest.raises(ValueError, match="stopped runtime"):
            host.run_worker(invocation, worker_id="owned")
    else:
        host.run_worker(invocation, worker_id="owned")
    record = WorkerLaunchStore(tmp_path).get("owned")
    assert record["pid"] is None and record["last_pid"] == os.getpid()
    _quiescent(tmp_path)
    assert host.run_worker(lambda: "resumed").result == "resumed"


@pytest.mark.parametrize("provider_state", ["starting", "running", "unverified", None])
def test_host_never_releases_uncertain_provider_ownership(tmp_path, provider_state):
    import json
    import os
    from cafe.core.workflow_repair import _quiescent

    store = WorkerLaunchStore(tmp_path)

    def invocation():
        with store._locked_records() as records:
            records["owned"]["provider"] = {"state": provider_state}

    if provider_state is None:
        with pytest.raises(ValueError, match="provider ownership state is malformed"):
            WorkflowHost(tmp_path).run_worker(invocation, worker_id="owned")
        expected = "live or uncertain worker"
    else:
        WorkflowHost(tmp_path).run_worker(invocation, worker_id="owned")
        expected = "PID is still live"
    record = json.loads(store.path.read_text())["attempts"]["owned"]
    assert record["pid"] == os.getpid()
    with pytest.raises(ValueError, match=expected):
        _quiescent(tmp_path)
