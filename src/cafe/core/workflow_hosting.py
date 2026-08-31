"""Fixed local foreground/background hosting for the version 2 workflow runtime."""

from __future__ import annotations

import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from cafe.core.blackboard import BlackboardStore
from cafe.core.driver_policy import DriverPolicyContract
from cafe.core.driver_runtime import DriverCoordinator


class WorkerAlreadyRunningError(RuntimeError):
    """Another live worker owns workflow advancement."""


@dataclass(frozen=True)
class HostRunResult:
    hosting: str
    worker_id: str
    result: Any = None
    pid: int | None = None


class WorkflowHost:
    """Change only local process ownership around one runtime callable."""

    def __init__(
        self,
        issue_dir: Path,
        *,
        popen_factory: Callable[..., Any] = subprocess.Popen,
        python_executable: str = sys.executable,
    ) -> None:
        self.issue_dir = Path(issue_dir)
        self.store = BlackboardStore(self.issue_dir)
        self.state = self.store.load_or_create("spec")
        self.coordinator = DriverCoordinator(self.store, self.state)
        self.popen_factory = popen_factory
        self.python_executable = python_executable
        self._held_worker_id: str | None = None

    def run(
        self,
        policy: DriverPolicyContract,
        runtime: Callable[[], Any],
    ) -> HostRunResult:
        if policy.execution.hosting == "foreground":
            return self.run_worker(runtime, hosting="foreground")
        return self._start_background()

    def run_worker(
        self,
        runtime: Callable[[], Any],
        *,
        worker_id: str | None = None,
        hosting: str = "background",
        hold_lease: bool = False,
    ) -> HostRunResult:
        identity = worker_id or str(uuid.uuid4())
        if not self.coordinator.claim_advancement_lease(identity, ttl_seconds=300):
            raise WorkerAlreadyRunningError("workflow advancement is owned by another worker")
        lease = self.state.driver_state["advancement_lease"]
        self._record_worker(
            identity,
            status="running",
            hosting=hosting,
            lease_expires_at=str(lease["expires_at"]),
        )
        try:
            result = runtime()
        except BaseException as exc:
            self._record_worker(
                identity,
                status="failed",
                hosting=hosting,
                error_type=type(exc).__name__,
            )
            self.coordinator.release_advancement_lease(identity)
            raise
        if hold_lease:
            self._held_worker_id = identity
        else:
            self._record_worker(identity, status="stopped", hosting=hosting)
            self.coordinator.release_advancement_lease(identity)
        return HostRunResult(hosting=hosting, worker_id=identity, result=result)

    def release_held_lease(self) -> bool:
        identity = self._held_worker_id
        if identity is None:
            return False
        self._record_worker(identity, status="stopped", hosting="background")
        released = self.coordinator.release_advancement_lease(identity)
        self._held_worker_id = None
        return released

    def reconcile_stale_ownership(self, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        changed = False
        with self.store.driver_transaction(self.state) as state:
            lease = state.driver_state.get("advancement_lease")
            if isinstance(lease, dict):
                expires_at = datetime.fromisoformat(str(lease["expires_at"]))
                if expires_at <= current:
                    state.driver_state["advancement_lease"] = None
                    changed = True
            worker = state.driver_state.get("worker")
            if isinstance(worker, dict) and worker.get("status") in {"starting", "running"}:
                raw_expiry = worker.get("lease_expires_at")
                if isinstance(raw_expiry, str) and datetime.fromisoformat(raw_expiry) <= current:
                    worker["status"] = "stale"
                    worker["reconciled_at"] = current.isoformat()
                    changed = True
        return changed

    def _start_background(self) -> HostRunResult:
        worker_id = str(uuid.uuid4())
        command = [
            self.python_executable,
            "-m",
            "cafe.ui.cli",
            "workflow",
            "--execute",
            "--issue",
            self.issue_dir.name,
            "--internal-v2-worker",
            "--worker-id",
            worker_id,
        ]
        self._record_worker(worker_id, status="starting", hosting="background")
        try:
            process = self.popen_factory(
                command,
                cwd=str(self._repository_root()),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        except OSError as exc:
            self._record_worker(
                worker_id,
                status="startup_failed",
                hosting="background",
                error_type=type(exc).__name__,
            )
            raise
        self._record_worker(
            worker_id,
            status="started",
            hosting="background",
            pid=int(process.pid),
        )
        return HostRunResult(
            hosting="background",
            worker_id=worker_id,
            pid=int(process.pid),
        )

    def _record_worker(self, worker_id: str, *, status: str, hosting: str, **extra: Any) -> None:
        with self.store.driver_transaction(self.state) as state:
            state.driver_state.setdefault("advancement_lease", None)
            prior = state.driver_state.get("worker")
            started_at = (
                prior.get("started_at")
                if isinstance(prior, dict) and prior.get("worker_id") == worker_id
                else datetime.now(timezone.utc).isoformat()
            )
            state.driver_state["worker"] = {
                "worker_id": worker_id,
                "hosting": hosting,
                "status": status,
                "started_at": started_at,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                **extra,
            }

    def _repository_root(self) -> Path:
        resolved = self.issue_dir.resolve()
        for parent in resolved.parents:
            if parent.name == ".cafe":
                return parent.parent
        return Path.cwd().resolve()
