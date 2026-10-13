"""Mode-neutral local process ownership for one workflow worker."""

from __future__ import annotations

import uuid
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from cafe.utils.file_lock import (
    _release_advancement_process_lock,
    _try_advancement_process_lock,
)


class WorkerAlreadyRunningError(RuntimeError):
    """Another live worker owns workflow advancement."""


@dataclass(frozen=True)
class HostRunResult:
    hosting: str
    worker_id: str
    result: Any = None
    pid: int | None = None


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
            while True:
                result = runtime()
                delay = getattr(result, "wait_seconds", None)
                if hosting != "background" or delay is None:
                    return HostRunResult(hosting=hosting, worker_id=identity, result=result)
                if not 1 <= delay <= 300:
                    raise ValueError("invalid host wait interval")
                # Phase execution has unwound its workspace lease; only workflow
                # advancement ownership remains. No agent or timer service is used.
                time.sleep(delay)
        finally:
            _release_advancement_process_lock(process_lock)
