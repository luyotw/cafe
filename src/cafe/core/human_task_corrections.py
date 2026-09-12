"""Runtime-owned correction transaction for pending HumanTasks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from cafe.core.artifact_revisions import ArtifactRevision, ArtifactRevisionStore
from cafe.core.blackboard import ArtifactEntry, BlackboardStore
from cafe.core.event_dispatches import EventDispatchFenceStore
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.workflow_execution.worker_launch import WorkerLaunchStore


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
    proxy_authorization_id: str | None = None
    suitability: Mapping[str, bool] | None = None


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
        if task.workflow_id == request.workflow_id and task.status.value == "completed":
            result = self.tasks.get_result(task.id)
            correction = result.payload.get("correction") if result is not None else None
            if isinstance(correction, Mapping) and correction.get("operation_id") == request.operation_id:
                revision = self.revisions.revision_for_operation(request.operation_id)
                if revision is None:
                    raise ValueError("completed correction is missing its immutable revision")
                return CorrectionResult(revision=revision, operation_id=request.operation_id)
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
        if request.actor == "driver_on_behalf_of_user":
            authorization = declaration.get("driver_authorization")
            if not isinstance(authorization, dict):
                raise ValueError("the pending task has no explicit Driver authorization")
            authorization_id = authorization.get("id")
            if (
                not isinstance(authorization_id, str)
                or request.proxy_authorization_id != authorization_id
            ):
                raise ValueError("Driver authorization does not match the pending task")
            required_suitability = {
                "bounded", "clear", "reversible", "within_scope", "no_new_authority"
            }
            if not isinstance(request.suitability, Mapping) or set(request.suitability) != required_suitability:
                raise ValueError("Driver suitability assessment is incomplete")
            if any(request.suitability[name] is not True for name in required_suitability):
                raise ValueError("Driver correction is unsuitable for proxy completion")
        if not isinstance(request.content, str) or len(request.content.encode("utf-8")) > 1_000_000:
            raise ValueError("correction content is invalid or exceeds the task limit")
        if not request.manifest or len(request.manifest) > 1_000:
            raise ValueError("correction invalidation manifest is required")
        if any(
            not isinstance(entry, dict)
            or set(entry) != {"kind", "id"}
            or entry.get("kind") not in {"artifact", "human_task", "approval", "worker", "dispatch"}
            or not all(isinstance(entry[key], str) and entry[key] for key in ("kind", "id"))
            for entry in request.manifest
        ):
            raise ValueError("correction invalidation manifest is invalid")
        if len(str(request.manifest).encode("utf-8")) > 256_000:
            raise ValueError("correction invalidation manifest exceeds the task limit")
        # All untrusted request structure is validated before bootstrap creates
        # an immutable pointer.  Rejected requests therefore leave no state.
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
        journal = self.revisions.prepare(
            request.operation_id,
            list(request.manifest),
            context={
                "workflow_id": request.workflow_id,
                "task_id": request.task_id,
                "artifact": request.artifact,
                "base_hash": request.base_hash,
                "content": request.content,
                "actor": request.actor,
                "proxy_authorization_id": request.proxy_authorization_id,
                "suitability": dict(request.suitability or {}),
                "completion_payload": dict(request.completion_payload or {}),
            },
        )
        for entry in journal["manifest"]:
            self._invalidate(
                entry,
                request.operation_id,
                corrected_artifact=request.artifact,
                step=task.step,
            )
            self.revisions.receipt(request.operation_id, entry)
        revision = self.revisions.replace(
            request.artifact,
            base_hash=request.base_hash,
            content=request.content,
            operation_id=request.operation_id,
        )
        # Publishing the replacement pointer is part of the correction
        # transaction, rather than a best-effort responsibility of one caller.
        # This keeps direct-user and Driver submissions on the same authority
        # path and makes recovery independent of a particular UI command.
        blackboard_store = BlackboardStore(self.revisions.issue_dir)
        blackboard = blackboard_store.load_or_create(task.step)
        current = blackboard.artifacts.get(request.artifact)
        if current is None:
            raise ValueError("correction artifact is no longer published")
        blackboard_store.put_artifact(
            blackboard,
            ArtifactEntry(
                name=current.name,
                kind=current.kind,
                version=current.version + 1,
                updated_by="human_task_correction",
                path=revision.path,
                summary=current.summary,
                base_sha=current.base_sha,
                head_sha=current.head_sha,
            ),
        )
        payload = dict(request.completion_payload or {})
        payload["correction"] = {
            "actor": request.actor,
            "artifact": request.artifact,
            "base_hash": request.base_hash,
            "new_hash": revision.sha256,
            "operation_id": request.operation_id,
            "authorization_id": request.proxy_authorization_id,
            "validation": (
                {"suitability": dict(request.suitability)}
                if request.suitability is not None
                else {"source": "user_command"}
            ),
            "manifest": list(journal["manifest"]),
        }
        self.tasks.complete(
            workflow_id=request.workflow_id,
            task_id=request.task_id,
            payload=payload,
            source="human_task_correction",
        )
        # A committed journal means all durable stores, including the task
        # result that drives continuation, have reached the same correction.
        # Until then runtime recovery keeps the operation fenced.
        self.revisions.commit(request.operation_id)
        return CorrectionResult(revision=revision, operation_id=request.operation_id)

    def recover(self, operation_id: str) -> CorrectionResult:
        """Replay one durable, incomplete correction without another submission."""
        journal = self.revisions.journal(operation_id)
        if journal["state"] == "committed":
            revision = self.revisions.revision_for_operation(operation_id)
            if revision is None:
                raise ValueError("committed correction is missing its immutable revision")
            return CorrectionResult(revision=revision, operation_id=operation_id)
        context = journal["context"]
        required = {"workflow_id", "task_id", "artifact", "base_hash", "content", "actor", "completion_payload"}
        if not isinstance(context, dict) or not required <= set(context):
            raise ValueError("correction recovery context is incomplete")
        task = self.tasks.get_task(context["task_id"])
        if task.status.value == "completed":
            revision = self.revisions.revision_for_operation(operation_id)
            if revision is None:
                raise ValueError("completed correction is missing its immutable revision")
            self.revisions.commit(operation_id)
            return CorrectionResult(revision=revision, operation_id=operation_id)
        return self.apply(CorrectionRequest(
            workflow_id=context["workflow_id"], task_id=context["task_id"], artifact=context["artifact"],
            base_hash=context["base_hash"], content=context["content"], operation_id=operation_id,
            actor=context["actor"], manifest=tuple(journal["manifest"]),
            completion_payload=context["completion_payload"],
            proxy_authorization_id=context.get("proxy_authorization_id"),
            suitability=context.get("suitability") or None,
        ))

    def _invalidate(
        self,
        entry: Mapping[str, str],
        operation_id: str,
        *,
        corrected_artifact: str,
        step: str,
    ) -> None:
        """Apply the typed revocation before acknowledging its receipt."""
        if entry["kind"] == "artifact":
            # The replacement below is the corrected artifact's new current
            # pointer. Every other listed pointer is stale immediately.
            if entry["id"] == corrected_artifact:
                return
            store = BlackboardStore(self.revisions.issue_dir)
            blackboard = store.load_or_create(step)
            if not store.invalidate_artifact_for_correction(
                blackboard, name=entry["id"], operation_id=operation_id
            ):
                raise ValueError("correction artifact invalidation target is absent")
        elif entry["kind"] == "worker":
            invalidated = WorkerLaunchStore(self.revisions.issue_dir).invalidate(
                entry["id"], operation_id=operation_id
            )
            if invalidated is None:
                raise ValueError("correction worker invalidation target is absent")
        elif entry["kind"] == "human_task":
            invalidated = self.tasks.invalidate_for_correction(
                workflow_id=self.tasks.get_task(entry["id"]).workflow_id,
                task_id=entry["id"],
                operation_id=operation_id,
            )
            if invalidated is None:
                raise ValueError("correction human-task invalidation target is absent")
        elif entry["kind"] == "approval":
            approval = self.tasks.get_task(entry["id"])
            invalidated = self.tasks.invalidate_capability_for_correction(
                workflow_id=approval.workflow_id, task_id=entry["id"], operation_id=operation_id
            )
            if invalidated is None:
                raise ValueError("correction approval invalidation target is absent")
        elif entry["kind"] == "dispatch":
            if not EventDispatchFenceStore(self.revisions.issue_dir).invalidate(
                entry["id"], operation_id=operation_id
            ):
                raise ValueError("correction dispatch invalidation target is absent")
        else:  # pragma: no cover - request validation keeps this fail-closed.
            raise ValueError("correction invalidation kind is unsupported")
