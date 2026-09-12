"""Runtime-owned correction transaction for pending HumanTasks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from cafe.core.artifact_revisions import ArtifactRevision, ArtifactRevisionStore
from cafe.core.human_task_records import HumanTaskRecordStore


@dataclass(frozen=True)
class CorrectionRequest:
    workflow_id: str
    task_id: str
    artifact: str
    base_hash: str | None
    content: str
    operation_id: str
    actor: str
    manifest: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class CorrectionResult:
    revision: ArtifactRevision
    operation_id: str


class HumanTaskCorrectionService:
    """Commits a correction only after its frozen invalidation receipts exist."""

    def __init__(self, issue_dir: Path) -> None:
        self.revisions = ArtifactRevisionStore(issue_dir)
        self.tasks = HumanTaskRecordStore(issue_dir)

    def apply(self, request: CorrectionRequest) -> CorrectionResult:
        task = self.tasks.get_task(request.task_id)
        if task.workflow_id != request.workflow_id or task.status.value != "pending":
            raise ValueError("correction task is not pending for this workflow")
        journal = self.revisions.prepare(request.operation_id, list(request.manifest))
        for entry in journal["manifest"]:
            self.revisions.receipt(request.operation_id, entry)
        revision = self.revisions.replace(
            request.artifact,
            base_hash=request.base_hash,
            content=request.content,
            operation_id=request.operation_id,
        )
        self.revisions.commit(request.operation_id)
        self.tasks.complete(
            workflow_id=request.workflow_id,
            task_id=request.task_id,
            payload={
                "correction": {
                    "actor": request.actor,
                    "artifact": request.artifact,
                    "base_hash": request.base_hash,
                    "new_hash": revision.sha256,
                    "operation_id": request.operation_id,
                }
            },
            source="human_task_correction",
        )
        return CorrectionResult(revision=revision, operation_id=request.operation_id)
