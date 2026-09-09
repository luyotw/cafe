"""Mode-neutral local process ownership for one workflow worker."""

from __future__ import annotations

import errno
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Callable

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
    """Own one continuous workflow invocation through a process lock only."""

    def __init__(self, issue_dir: Path) -> None:
        self.issue_dir = Path(issue_dir)
        self.advancement_lock_path = self.issue_dir / ".workflow-advancement.lock"

    def run(self, runtime: Callable[[], Any], *, hosting: str) -> HostRunResult:
        if hosting != "foreground":
            raise ValueError("hosting must be 'foreground'")
        return self.run_worker(runtime, hosting=hosting)

    def run_worker(
        self,
        runtime: Callable[[], Any],
        *,
        worker_id: str | None = None,
        hosting: str = "background",
    ) -> HostRunResult:
        if hosting not in {"foreground", "background"}:
            raise ValueError("hosting must be 'foreground' or 'background'")
        process_lock = _try_advancement_process_lock(self.advancement_lock_path)
        if process_lock is None:
            raise WorkerAlreadyRunningError("workflow advancement is owned by another worker")
        try:
            identity = worker_id or str(uuid.uuid4())
            return HostRunResult(hosting=hosting, worker_id=identity, result=runtime())
        finally:
            _release_advancement_process_lock(process_lock)
