"""Local process ownership around an outer workflow controller."""

from __future__ import annotations

import errno
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread
from typing import IO, Any, Callable

from cafe.core.blackboard import BlackboardStore
from cafe.orchestration.driver_runtime import DriverCoordinator

try:
    import fcntl
except ImportError:  # pragma: no cover - unavailable on Windows.
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # pragma: no cover - available only on Windows.
    msvcrt = None  # type: ignore[assignment]


class WorkerAlreadyRunningError(RuntimeError):
    """Another live worker owns workflow advancement."""


@dataclass(frozen=True)
class HostRunResult:
    hosting: str
    worker_id: str
    result: Any = None
    pid: int | None = None


def _try_advancement_process_lock(path: Path) -> IO[str] | None:
    """Acquire the callable-duration lock without waiting for another worker."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        if msvcrt is None:
            raise RuntimeError("cross-process file locking is unavailable")
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write("\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return handle
    except OSError as exc:
        handle.close()
        if exc.errno in {errno.EACCES, errno.EAGAIN}:
            return None
        raise
    except BaseException:
        handle.close()
        raise


def _release_advancement_process_lock(handle: IO[str]) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        elif msvcrt is not None:  # pragma: no branch - platform-specific.
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        handle.close()


class WorkflowHost:
    """Change only local process ownership around one runtime callable."""

    def __init__(
        self,
        issue_dir: Path,
        *,
        lease_ttl_seconds: int = 300,
        lease_renew_interval_seconds: float = 100.0,
    ) -> None:
        if lease_ttl_seconds <= 0 or lease_renew_interval_seconds <= 0:
            raise ValueError("lease TTL and renewal interval must be positive")
        if lease_renew_interval_seconds >= lease_ttl_seconds:
            raise ValueError("lease renewal interval must be shorter than the lease TTL")
        self.issue_dir = Path(issue_dir)
        self.store = BlackboardStore(self.issue_dir)
        self.state = self.store.load_or_create("spec")
        self.coordinator = DriverCoordinator(self.store, self.state)
        self.lease_ttl_seconds = lease_ttl_seconds
        self.lease_renew_interval_seconds = lease_renew_interval_seconds
        self.advancement_lock_path = self.issue_dir / ".workflow-advancement.lock"
        self._held_worker_id: str | None = None

    def run(
        self,
        runtime: Callable[[], Any],
        *,
        hosting: str,
    ) -> HostRunResult:
        if hosting == "foreground":
            return self.run_worker(runtime, hosting="foreground")
        raise ValueError("hosting must be 'foreground'")

    def run_worker(
        self,
        runtime: Callable[[], Any],
        *,
        worker_id: str | None = None,
        hosting: str = "background",
        hold_lease: bool = False,
    ) -> HostRunResult:
        process_lock = _try_advancement_process_lock(self.advancement_lock_path)
        if process_lock is None:
            raise WorkerAlreadyRunningError("workflow advancement is owned by another worker")
        try:
            return self._run_worker_under_process_lock(
                runtime,
                worker_id=worker_id,
                hosting=hosting,
                hold_lease=hold_lease,
            )
        finally:
            _release_advancement_process_lock(process_lock)

    def _run_worker_under_process_lock(
        self,
        runtime: Callable[[], Any],
        *,
        worker_id: str | None,
        hosting: str,
        hold_lease: bool,
    ) -> HostRunResult:
        identity = worker_id or str(uuid.uuid4())
        if not self.coordinator.claim_advancement_lease(
            identity, ttl_seconds=self.lease_ttl_seconds
        ):
            raise WorkerAlreadyRunningError("workflow advancement is owned by another worker")
        lease = self.state.driver_state["advancement_lease"]
        self._record_worker(
            identity,
            status="running",
            hosting=hosting,
            lease_expires_at=str(lease["expires_at"]),
        )
        heartbeat_stop = Event()
        lease_lost = Event()
        heartbeat = Thread(
            target=self._renew_while_running,
            args=(identity, hosting, heartbeat_stop, lease_lost),
            name=f"cafe-workflow-lease-{identity}",
            daemon=True,
        )
        heartbeat.start()
        try:
            result = runtime()
        except BaseException as exc:
            heartbeat_stop.set()
            heartbeat.join(timeout=min(self.lease_renew_interval_seconds + 1, 5))
            self._record_worker(
                identity,
                status="failed",
                hosting=hosting,
                error_type=type(exc).__name__,
            )
            self.coordinator.release_advancement_lease(identity)
            raise
        heartbeat_stop.set()
        heartbeat.join(timeout=min(self.lease_renew_interval_seconds + 1, 5))
        if heartbeat.is_alive():
            lease_lost.set()
        if lease_lost.is_set():
            self._record_worker(
                identity,
                status="failed",
                hosting=hosting,
                error_type="AdvancementLeaseLost",
            )
            self.coordinator.release_advancement_lease(identity)
            raise WorkerAlreadyRunningError("workflow advancement lease was lost")
        if hold_lease:
            self._held_worker_id = identity
        else:
            self._record_worker(identity, status="stopped", hosting=hosting)
            self.coordinator.release_advancement_lease(identity)
        return HostRunResult(hosting=hosting, worker_id=identity, result=result)

    def _renew_while_running(
        self,
        identity: str,
        hosting: str,
        stop: Event,
        lease_lost: Event,
    ) -> None:
        while not stop.wait(self.lease_renew_interval_seconds):
            try:
                renewed = self.coordinator.renew_advancement_lease(
                    identity,
                    ttl_seconds=self.lease_ttl_seconds,
                )
            except (OSError, RuntimeError, ValueError):
                lease_lost.set()
                return
            if not renewed:
                lease_lost.set()
                return
            try:
                lease = self.state.driver_state.get("advancement_lease")
                if isinstance(lease, dict):
                    self._record_worker(
                        identity,
                        status="running",
                        hosting=hosting,
                        lease_expires_at=str(lease["expires_at"]),
                    )
            except (OSError, RuntimeError, ValueError):
                lease_lost.set()
                return

    def release_held_lease(self) -> bool:
        identity = self._held_worker_id
        if identity is None:
            return False
        self._record_worker(identity, status="stopped", hosting="background")
        released = self.coordinator.release_advancement_lease(identity)
        self._held_worker_id = None
        return released

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
