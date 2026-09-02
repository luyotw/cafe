"""Durable parent/child handoff for fixed workflow workers.

This is deliberately a tiny sidecar store.  It is available before a worker
constructs a blackboard-backed host, so an invalid child can fail closed without
initialising or mutating workflow-core state.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterator

from cafe.core.packet_io import atomic_write_bytes
from cafe.orchestration.driver_policy import DriverPolicyContract

try:
    import fcntl
except ImportError:  # pragma: no cover - unavailable on Windows.
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # pragma: no cover - unavailable on non-Windows.
    msvcrt = None  # type: ignore[assignment]


LAUNCH_RECORD_FILENAME = ".workflow-worker-launches.json"
_thread_locks: dict[Path, threading.RLock] = {}
_thread_locks_guard = threading.Lock()


def canonical_policy_digest(policy: DriverPolicyContract) -> str:
    """Return a digest of exactly the validated driver-policy contract."""
    encoded = json.dumps(
        policy.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkerLaunchStore:
    """Atomic per-worker launch records; not a daemon or liveness registry."""

    def __init__(self, issue_dir: Path) -> None:
        self.issue_dir = Path(issue_dir)
        self.path = self.issue_dir / LAUNCH_RECORD_FILENAME
        self.lock_path = self.issue_dir / f"{LAUNCH_RECORD_FILENAME}.lock"

    def start(self, *, mode: str, policy: DriverPolicyContract) -> dict[str, Any]:
        worker_id = str(uuid.uuid4())
        now = _now()
        record = {
            "worker_id": worker_id,
            "mode": mode,
            "policy_digest": canonical_policy_digest(policy),
            "status": "starting",
            "created_at": now,
            "updated_at": now,
        }
        with self._locked_records() as records:
            records[worker_id] = record
        return dict(record)

    def get(self, worker_id: str) -> dict[str, Any] | None:
        if not worker_id:
            return None
        with self._locked_records() as records:
            record = records.get(worker_id)
            return dict(record) if isinstance(record, dict) else None

    def mark(
        self,
        worker_id: str,
        status: str,
        *,
        pid: int | None = None,
        error_code: str | None = None,
    ) -> dict[str, Any] | None:
        with self._locked_records() as records:
            record = records.get(worker_id)
            if not isinstance(record, dict):
                return None
            record["status"] = status
            record["updated_at"] = _now()
            if pid is not None:
                record["pid"] = int(pid)
            if error_code:
                record["error_code"] = error_code[:160]
            elif status not in {"startup_failed", "failed"}:
                record.pop("error_code", None)
            return dict(record)

    def set_pid(self, worker_id: str, pid: int) -> dict[str, Any] | None:
        """Attach the parent-observed PID without changing a child-owned status."""
        with self._locked_records() as records:
            record = records.get(worker_id)
            if not isinstance(record, dict):
                return None
            record["pid"] = int(pid)
            record["updated_at"] = _now()
            return dict(record)

    def validate_child(
        self,
        *,
        worker_id: str | None,
        mode: str,
        policy: DriverPolicyContract,
        policy_digest: str | None,
    ) -> bool:
        """Validate a child against a parent-created record without core I/O."""
        if not worker_id or not policy_digest:
            return False
        expected_digest = canonical_policy_digest(policy)
        with self._locked_records() as records:
            record = records.get(worker_id)
            if not isinstance(record, dict):
                return False
            valid = (
                record.get("mode") == mode
                and record.get("policy_digest") == expected_digest
                and policy_digest == expected_digest
                and record.get("status") == "started"
            )
            if valid:
                record["status"] = "running"
                record["updated_at"] = _now()
                return True
            record["status"] = "startup_failed"
            record["updated_at"] = _now()
            record["error_code"] = "worker_context_mismatch"
            return False

    @contextmanager
    def _locked_records(self) -> Iterator[dict[str, Any]]:
        self.issue_dir.mkdir(parents=True, exist_ok=True)
        self.lock_path.touch(exist_ok=True)
        lock = _thread_lock(self.path)
        with lock:
            with self.lock_path.open("a+", encoding="utf-8") as handle:
                with _file_lock(handle):
                    records = self._read_unlocked()
                    yield records
                    atomic_write_bytes(
                        self.path,
                        json.dumps(
                            {"attempts": records},
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8"),
                    )

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        attempts = loaded.get("attempts") if isinstance(loaded, dict) else None
        return dict(attempts) if isinstance(attempts, dict) else {}


def _thread_lock(path: Path) -> threading.RLock:
    resolved = path.resolve()
    with _thread_locks_guard:
        return _thread_locks.setdefault(resolved, threading.RLock())


@contextmanager
def _file_lock(handle: Any) -> Iterator[None]:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return
    if msvcrt is None:  # pragma: no cover - platform guard.
        raise RuntimeError("cross-process launch locking is unavailable")
    handle.seek(0, 2)
    if handle.tell() == 0:
        handle.write("0")
        handle.flush()
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    try:
        yield
    finally:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


class FixedWorkerLauncher:
    """Spawn the one fixed CLI worker after a durable sidecar handoff."""

    def __init__(
        self,
        issue_dir: Path,
        *,
        popen_factory: Any = subprocess.Popen,
        python_executable: str = sys.executable,
    ) -> None:
        self.issue_dir = Path(issue_dir)
        self.store = WorkerLaunchStore(self.issue_dir)
        self.popen_factory = popen_factory
        self.python_executable = python_executable

    def launch(self, record: dict[str, Any], *, extra_args: list[str] | None = None) -> int:
        worker_id = str(record["worker_id"])
        digest = str(record["policy_digest"])
        command = [
            self.python_executable,
            "-m",
            "cafe.ui.cli",
            "workflow",
            "--execute",
            "--issue",
            self.issue_dir.name,
            "--internal-worker-id",
            worker_id,
            "--internal-policy-digest",
            digest,
        ]
        if extra_args:
            command.extend(extra_args)
        try:
            # Publish `started` before Popen can schedule the child.  The child
            # may immediately advance to `running` or a terminal state, so the
            # parent must never write `started` after spawning it.
            self.store.mark(worker_id, "started")
            process = self.popen_factory(
                command,
                cwd=str(self._repository_root()),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        except OSError:
            self.store.mark(worker_id, "startup_failed", error_code="spawn_failed")
            raise
        self.store.set_pid(worker_id, int(process.pid))
        return int(process.pid)

    def _repository_root(self) -> Path:
        resolved = self.issue_dir.resolve()
        for parent in resolved.parents:
            if parent.name == ".cafe":
                return parent.parent
        return Path.cwd().resolve()
