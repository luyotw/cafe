"""Runtime-owned correction transaction for pending HumanTasks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from cafe.core.artifact_revisions import ArtifactRevision, ArtifactRevisionStore
from cafe.core.blackboard import BlackboardStore
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
    completion_payload: Mapping[str, Any] | None = None


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
        """Serialize every correction for a task through its durable lock."""
        with self.tasks.transaction():
            return self._apply_locked(request)

    def _apply_locked(self, request: CorrectionRequest) -> CorrectionResult:
        task = self.tasks.get_task(request.task_id)
        if task.workflow_id != request.workflow_id or task.status.value != "pending":
            raise ValueError("correction task is not pending for this workflow")
        declaration = task.expected_result.get("correction")
        if not isinstance(declaration, dict):
            raise ValueError("the pending task does not permit corrections")
        allowed = declaration.get("artifacts")
        if not isinstance(allowed, list) or request.artifact not in allowed:
            raise ValueError("correction artifact is not declared by the pending task")
        if request.actor not in {"user", "driver_on_behalf_of_user"}:
            raise ValueError("correction actor is not trusted")
        if request.actor == "driver_on_behalf_of_user" and not declaration.get("allow_driver_proxy"):
            raise ValueError("the pending task does not authorize a Driver proxy")
        if not isinstance(request.content, str) or len(request.content.encode("utf-8")) > 1_000_000:
            raise ValueError("correction content is invalid or exceeds the task limit")
        if not request.manifest or len(request.manifest) > 1_000:
            raise ValueError("correction invalidation manifest is required")
        if any(
            not isinstance(entry, dict)
            or set(entry) != {"kind", "id"}
            or not all(isinstance(entry[key], str) and entry[key] for key in ("kind", "id"))
            for entry in request.manifest
        ):
            raise ValueError("correction invalidation manifest is invalid")
        if len(str(request.manifest).encode("utf-8")) > 256_000:
            raise ValueError("correction invalidation manifest exceeds the task limit")
        published = BlackboardStore(self.revisions.issue_dir).load_or_create(task.step)
        entry = published.artifacts.get(request.artifact)
        if entry is None:
            raise ValueError("correction artifact is not currently published")
        source = (self.revisions.issue_dir / entry.path).resolve()
        if self.revisions.issue_dir.resolve() not in source.parents:
            raise ValueError("correction artifact path escapes the issue")
        content_bytes = source.read_bytes()
        if len(content_bytes) > 1_000_000:
            raise ValueError("correction base content exceeds the task limit")
        base_revision = self.revisions.bootstrap(
            request.artifact, content=content_bytes.decode("utf-8")
        )
        if request.base_hash != base_revision.sha256:
            raise ValueError("correction base hash is not current")
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
        payload = dict(request.completion_payload or {})
        payload["correction"] = {
            "actor": request.actor,
            "artifact": request.artifact,
            "base_hash": request.base_hash,
            "new_hash": revision.sha256,
            "operation_id": request.operation_id,
        }
        self.tasks.complete(
            workflow_id=request.workflow_id,
            task_id=request.task_id,
            payload=payload,
            source="human_task_correction",
        )
        return CorrectionResult(revision=revision, operation_id=request.operation_id)
