"""Foreground/background hosting parity and bounded ownership tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from cafe.core.blackboard import (
    ArtifactEntry,
    ArtifactKind,
    BlackboardStore,
    EventEntry,
)
from cafe.core.driver_policy import DriverPolicyContract
from cafe.core.workflow_hosting import WorkerAlreadyRunningError, WorkflowHost


def _policy(hosting: str) -> DriverPolicyContract:
    return DriverPolicyContract.model_validate(
        {
            "contract_version": 2,
            "driver": {"mode": "unattended"},
            "execution": {"advancement": "continuous", "hosting": hosting},
        }
    )


def test_foreground_and_internal_worker_use_same_runtime_callable(tmp_path: Path) -> None:
    calls: list[str] = []
    foreground = WorkflowHost(tmp_path / "foreground")
    worker = WorkflowHost(tmp_path / "worker")

    foreground_result = foreground.run(
        _policy("foreground"), lambda: calls.append("foreground") or "finished"
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

    result = host.run(_policy("background"), lambda: pytest.fail("ran in parent"))

    command, kwargs = launches[0]
    assert command[:4] == ["/fixed/python", "-m", "cafe.ui.cli", "workflow"]
    assert "--internal-v2-worker" in command
    assert command[command.index("--issue") + 1] == "issue432"
    assert kwargs["start_new_session"] is True
    assert result.pid == 4321


def test_background_startup_failure_is_durable_without_claiming_phase_work(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue432"

    def fail(*_args, **_kwargs):
        raise OSError("cannot start")

    host = WorkflowHost(issue_dir, popen_factory=fail)

    with pytest.raises(OSError):
        host.run(_policy("background"), lambda: None)

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


def test_stale_reconciliation_changes_only_worker_and_lease_state(tmp_path: Path) -> None:
    issue_dir = tmp_path / "issue"
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    state.artifacts["plan"] = ArtifactEntry(
        name="plan",
        kind=ArtifactKind.DOCUMENT,
        version=1,
        updated_by="plan",
        path="plan/output.md",
    )
    state.events.append(
        EventEntry(
            timestamp="2026-01-01T00:00:00+00:00",
            step="develop",
            event_type="step_interrupted",
            message="executor disappeared",
        )
    )
    state.step_attempt_counts = {"develop": 2}
    store.save(state)
    host = WorkflowHost(issue_dir)
    host.run_worker(lambda: None, worker_id="stale", hold_lease=True)
    with store.driver_transaction(host.state) as persisted:
        persisted.driver_state["advancement_lease"]["expires_at"] = "2000-01-01T00:00:00+00:00"
        persisted.driver_state["worker"]["lease_expires_at"] = "2000-01-01T00:00:00+00:00"
    before = store.load_or_create("develop").to_dict()

    assert host.reconcile_stale_ownership() is True

    after = store.load_or_create("develop").to_dict()
    for key in ("current_step", "artifacts", "events", "handoff_contract", "step_attempt_counts"):
        assert after[key] == before[key]
    assert after["driver_state"]["advancement_lease"] is None
    assert after["driver_state"]["worker"]["status"] == "stale"
    assert "terminal_result" not in after["driver_state"]
    assert host.run_worker(lambda: "resumed", worker_id="replacement").result == "resumed"
