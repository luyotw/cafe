"""Foreground/background hosting parity and bounded ownership tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from cafe.core.blackboard import BlackboardStore
from cafe.core.workflow_hosting import WorkerAlreadyRunningError, WorkflowHost


def test_foreground_and_internal_worker_use_same_runtime_callable(tmp_path: Path) -> None:
    calls: list[str] = []
    foreground = WorkflowHost(tmp_path / "foreground")
    worker = WorkflowHost(tmp_path / "worker")

    foreground_result = foreground.run(
        lambda: calls.append("foreground") or "finished", hosting="foreground"
    )
    worker_result = worker.run_worker(lambda: calls.append("worker") or "finished")

    assert foreground_result.result == worker_result.result == "finished"
    assert calls == ["foreground", "worker"]
    assert foreground_result.hosting == "foreground"
    assert worker_result.hosting == "background"


def test_background_launch_is_fixed_typed_worker_command(tmp_path: Path) -> None:
    launches: list[tuple[list[str], dict]] = []

    def popen(command, **kwargs):
        launches.append((command, kwargs))
        return SimpleNamespace(pid=4321)

    host = WorkflowHost(
        tmp_path / ".cafe" / "issues" / "issue432",
        popen_factory=popen,
        python_executable="/fixed/python",
    )

    result = host.run(lambda: pytest.fail("ran in parent"), hosting="background")

    command, kwargs = launches[0]
    assert command[:4] == ["/fixed/python", "-m", "cafe.ui.cli", "workflow"]
    assert "--internal-v2-worker" not in command
    assert command[command.index("--issue") + 1] == "issue432"
    assert kwargs["start_new_session"] is True
    assert result.pid == 4321


def test_background_startup_failure_is_durable_without_claiming_phase_work(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue432"

    def fail(*_args, **_kwargs):
        raise OSError("cannot start")

    host = WorkflowHost(issue_dir, popen_factory=fail)

    with pytest.raises(OSError):
        host.run(lambda: None, hosting="background")

    state = BlackboardStore(issue_dir).load_or_create("spec")
    assert state.driver_state["worker"]["status"] == "startup_failed"
    assert state.driver_state["advancement_lease"] is None
    assert state.current_step == "spec"


def test_only_one_host_worker_advances_and_clean_release_allows_next(tmp_path: Path) -> None:
    host = WorkflowHost(tmp_path)

    def claim(worker_id: str) -> bool:
        try:
            host.run_worker(lambda: "ok", worker_id=worker_id, hold_lease=True)
        except WorkerAlreadyRunningError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=6) as pool:
        claimed = list(pool.map(claim, [f"worker-{index}" for index in range(6)]))

    assert claimed.count(True) == 1
    host.release_held_lease()
    assert host.run_worker(lambda: "next", worker_id="next").result == "next"


def test_live_worker_renews_lease_until_runtime_returns(tmp_path: Path) -> None:
    runtime_release = Event()
    lease_renewed = Event()
    host = WorkflowHost(
        tmp_path,
        lease_ttl_seconds=1,
        lease_renew_interval_seconds=0.01,
    )
    original_renew = host.coordinator.renew_advancement_lease

    def renew(holder: str, *, ttl_seconds: int) -> bool:
        renewed = original_renew(holder, ttl_seconds=ttl_seconds)
        lease_renewed.set()
        return renewed

    host.coordinator.renew_advancement_lease = renew

    def run_until_released() -> str:
        assert runtime_release.wait(timeout=2)
        return "finished"

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(host.run_worker, run_until_released, worker_id="live")
        assert lease_renewed.wait(timeout=2)
        competitor = WorkflowHost(tmp_path)
        with pytest.raises(WorkerAlreadyRunningError):
            competitor.run_worker(lambda: "overlap", worker_id="competitor")
        runtime_release.set()
        assert future.result(timeout=2).result == "finished"


def test_lease_renewal_error_fails_closed_after_runtime_returns(tmp_path: Path) -> None:
    renewal_attempted = Event()
    host = WorkflowHost(
        tmp_path,
        lease_ttl_seconds=1,
        lease_renew_interval_seconds=0.01,
    )

    def fail_renewal(_holder: str, *, ttl_seconds: int) -> bool:
        assert ttl_seconds == 1
        renewal_attempted.set()
        raise OSError("lease store unavailable")

    host.coordinator.renew_advancement_lease = fail_renewal

    def run_until_attempted() -> str:
        assert renewal_attempted.wait(timeout=2)
        return "unsafe-success"

    with pytest.raises(WorkerAlreadyRunningError):
        host.run_worker(run_until_attempted, worker_id="live")


def test_lease_renewal_loss_keeps_runtime_exclusive_until_callable_returns(
    tmp_path: Path,
) -> None:
    renewal_failed = Event()
    runtime_release = Event()
    overlap_calls: list[str] = []
    host = WorkflowHost(
        tmp_path,
        lease_ttl_seconds=1,
        lease_renew_interval_seconds=0.01,
    )

    def fail_renewal(_holder: str, *, ttl_seconds: int) -> bool:
        assert ttl_seconds == 1
        renewal_failed.set()
        raise OSError("lease store unavailable")

    host.coordinator.renew_advancement_lease = fail_renewal

    def run_until_released() -> str:
        assert runtime_release.wait(timeout=2)
        return "unsafe-success"

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(host.run_worker, run_until_released, worker_id="live")
        assert renewal_failed.wait(timeout=2)
        store = BlackboardStore(tmp_path)
        state = store.load_or_create("spec")
        with store.driver_transaction(state) as persisted:
            persisted.driver_state["advancement_lease"]["expires_at"] = (
                "2000-01-01T00:00:00+00:00"
            )

        competitor = WorkflowHost(tmp_path)
        blocked = False
        try:
            competitor.run_worker(
                lambda: overlap_calls.append("overlap"), worker_id="competitor"
            )
        except WorkerAlreadyRunningError:
            blocked = True
        finally:
            runtime_release.set()

        assert blocked is True
        assert overlap_calls == []
        with pytest.raises(WorkerAlreadyRunningError):
            future.result(timeout=2)
