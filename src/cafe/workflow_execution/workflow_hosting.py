"""Mode-neutral local process ownership for one workflow worker."""

from __future__ import annotations

import os
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
        from cafe.core.workflow_control import bind_control
        from cafe.workflow_execution.intervention import InterventionControl
        from cafe.workflow_execution.worker_launch import WorkerLaunchStore

        store = WorkerLaunchStore(self.issue_dir)
        with advancement_execution_lock(self.issue_dir, reuse_existing=False):
            control = InterventionControl(self.issue_dir, worker_id or "")
            control.ensure_no_pending()
            if worker_id and store.get(worker_id) is None:
                store.start(worker_id=worker_id)
            identity = worker_id or store.start()["worker_id"]
            control.worker_id = identity
            store.mark(identity, "running", pid=os.getpid())
            from cafe.agents.process_control import process_birth

            with store._locked_records() as records:
                records[identity]["process_birth"] = process_birth(os.getpid())
            try:
                with bind_control(control):
                    while True:
                        result = runtime()
                        delay = getattr(result, "wait_seconds", None)
                        if delay is not None:
                            control.waiting()
                        if hosting != "background" or delay is None:
                            break
                        if not 1 <= delay <= 300:
                            raise ValueError("invalid host wait interval")
                        # Background observation keeps existing waits; intervention is for active agents.
                        time.sleep(delay)
            except BaseException:
                try:
                    store.mark(identity, "failed")
                    store.release_owned_process(identity)
                except (OSError, ValueError):
                    pass  # Retain the original error; unreadable control stays frozen.
                raise
            if control.on_quiesced is None:
                store.mark(identity, "stopped")
                store.release_owned_process(identity)
        if control.on_quiesced is not None:
            control.finish()
        return HostRunResult(hosting=hosting, worker_id=identity, result=result)
