"""Generic foreground/background workflow-host ownership tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from cafe.orchestration.worker_launch import FixedWorkerLauncher, WorkerLaunchStore
from cafe.orchestration.workflow_hosting import WorkerAlreadyRunningError, WorkflowHost


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


def test_invalid_worker_handshake_fails_closed(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue458"
    store = WorkerLaunchStore(issue_dir)
    record = store.start()

    assert not store.validate_child(worker_id=record["worker_id"], worker_token="replaced")
    assert store.get(record["worker_id"])["status"] == "startup_failed"


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
