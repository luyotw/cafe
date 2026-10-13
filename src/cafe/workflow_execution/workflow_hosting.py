"""Mode-neutral local process ownership for one workflow worker."""

from __future__ import annotations

import uuid
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from cafe.utils.file_lock import (
    _release_advancement_process_lock as _release_advancement_process_lock,
    _try_advancement_process_lock as _try_advancement_process_lock,
    advancement_execution_lock as advancement_execution_lock,
    WorkerAlreadyRunningError as WorkerAlreadyRunningError,
)


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
        with advancement_execution_lock(self.issue_dir, reuse_existing=False):
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
