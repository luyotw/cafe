"""Runtime-owned correction transaction for pending HumanTasks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from cafe.core.artifact_revisions import ArtifactRevision, ArtifactRevisionStore
from cafe.core.blackboard import ArtifactEntry, BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.event_dispatches import EventDispatchFenceStore
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.playbook import PlaybookDefinition
from cafe.playbooks.loader import PlaybookLoader, apply_issue_playbook_overrides
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
        self.revisions.validate_operation_id(request.operation_id)
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
            or entry.get("kind") not in {"artifact", "human_task", "approval", "worker", "dispatch", "continuation"}
            or not all(isinstance(entry[key], str) and entry[key] for key in ("kind", "id"))
            for entry in request.manifest
        ):
            raise ValueError("correction invalidation manifest is invalid")
        if len(str(request.manifest).encode("utf-8")) > 256_000:
            raise ValueError("correction invalidation manifest exceeds the task limit")
        # Direct callers already derive their graph closure at the trusted CLI
        # boundary. Driver requests are public API input, so their manifest is
        # never authority; derive it again from durable workflow state.
        manifest = (
            self._canonical_manifest(task, request.artifact)
            if request.actor == "driver_on_behalf_of_user"
            or (self.revisions.issue_dir / "issue.yaml").is_file()
            else request.manifest
        )
        self._validate_manifest_revocability(manifest)
        # All untrusted request structure is validated before bootstrap creates
        # an immutable pointer.  Rejected requests therefore leave no state.
        published = BlackboardStore(self.revisions.issue_dir).load_or_create(task.step)
        entry = published.artifacts.get(request.artifact)
        if entry is None:
            raise ValueError("correction artifact is not currently published")
        source = (self.revisions.issue_dir / entry.path).resolve()
        if self.revisions.issue_dir.resolve() not in source.parents:
            raise ValueError("correction artifact path escapes the issue")
        # A stat/read pair is raceable.  Read at most the allowed bytes, then
        # probe one additional byte without allocating an unbounded replacement.
        with source.open("rb") as handle:
            content_bytes = handle.read(1_000_000)
            exceeds_limit = bool(handle.read(1))
        if exceeds_limit:
            raise ValueError("correction base content exceeds the task limit")
        base_revision = self.revisions.bootstrap(
            request.artifact, content=content_bytes.decode("utf-8")
        )
        if request.base_hash != base_revision.sha256:
            raise ValueError("correction base hash is not current")
        correction_generation = (
            WorkerLaunchStore(self.revisions.issue_dir).generation + 1
            if any(entry["kind"] == "continuation" for entry in manifest)
            else None
        )
        context = {
            "workflow_id": request.workflow_id,
            "task_id": request.task_id,
            "artifact": request.artifact,
            "base_hash": request.base_hash,
            "content": request.content,
            "actor": request.actor,
            "proxy_authorization_id": request.proxy_authorization_id,
            "suitability": dict(request.suitability or {}),
            "completion_payload": dict(request.completion_payload or {}),
        }
        if correction_generation is not None:
            context["correction_generation"] = correction_generation
        journal = self.revisions.prepare(
            request.operation_id,
            list(manifest),
            context=context,
        )
        journal_generation = journal["context"].get("correction_generation")
        if journal_generation is not None and (
            not isinstance(journal_generation, int) or journal_generation < 1
        ):
            raise ValueError("correction journal has an invalid generation")
        correction_generation = journal_generation
        for entry in journal["manifest"]:
            self._invalidate(
                entry,
                request.operation_id,
                corrected_artifact=request.artifact,
                step=task.step,
                workflow_id=task.workflow_id,
                correction_generation=correction_generation,
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
        self._schedule_graph_continuation(task)
        # A committed journal means all durable stores, including the task
        # result that drives continuation, have reached the same correction.
        # Until then runtime recovery keeps the operation fenced.
        self.revisions.commit(request.operation_id)
        return CorrectionResult(revision=revision, operation_id=request.operation_id)

    def _schedule_graph_continuation(self, task) -> None:
        """Resume at the task step's declared agent edge after correction.

        Correction replaces an already-reviewed output; it is not a confirmation
        of the old output.  The persisted graph remains the authority for the
        next agent step, so a custom playbook receives the same durable baton
        without naming a built-in phase or gate here.
        """
        issue_config = self.revisions.issue_dir / "issue.yaml"
        if not issue_config.is_file():
            return
        board_store = BlackboardStore(self.revisions.issue_dir)
        board = board_store.load_or_create(task.step)
        playbook = self._load_playbook(board, issue_config)
        step = playbook.steps.get(task.step)
        target = step.on.get("await_agent") if step is not None else None
        if not isinstance(target, str) or target not in playbook.steps:
            raise ValueError("correction task has no declared agent continuation")
        board_store.set_current_step(board, target)
        board_store.update_handoff_contract(
            board,
            from_step=task.step,
            to_owner=HandoffOwner.AGENT,
            to_step=target,
            intent=HandoffIntent.AWAIT_AGENT,
            source="human_task_correction",
        )

    def _load_playbook(self, board, issue_config: Path) -> PlaybookDefinition:
        project_root = self.revisions.issue_dir.parents[2]
        data = PlaybookLoader(project_root=project_root).load(board.playbook_id)
        return PlaybookDefinition.model_validate(
            apply_issue_playbook_overrides(data, issue_config)
        )

    def _validate_manifest_revocability(self, manifest: tuple[dict[str, str], ...]) -> None:
        """Reject externally active work before a journal can fence execution."""
        for entry in manifest:
            if entry["kind"] == "approval":
                approval = self.tasks.get_task(entry["id"])
                metadata = approval.capability_approval or {}
                if metadata.get("state") in {"attempt_started", "uncertain", "succeeded", "failed"}:
                    raise ValueError("capability approval requires external reconciliation")
            elif entry["kind"] == "worker":
                worker = WorkerLaunchStore(self.revisions.issue_dir).get(entry["id"])
                if worker is None:
                    raise ValueError("correction worker invalidation target is absent")
                if worker.get("status") in {"running", "completed"}:
                    raise ValueError("worker has already crossed the correction fence")

    def _canonical_manifest(
        self, task, artifact: str
    ) -> tuple[dict[str, str], ...]:
        """Derive mutation targets from the active graph, never a Driver request.

        Older unit-level callers do not have an issue playbook on disk.  Their
        only safe compatibility behavior is to replace the declared artifact;
        production issues always have the persisted playbook declaration.
        """
        board = BlackboardStore(self.revisions.issue_dir).load_or_create(task.step)
        issue_config = self.revisions.issue_dir / "issue.yaml"
        if not issue_config.is_file():
            return ({"kind": "artifact", "id": artifact},)
        playbook = self._load_playbook(board, issue_config)
        names = (artifact, *playbook.downstream_artifacts(artifact))
        manifest = [
            {"kind": "artifact", "id": name}
            for name in names
            if name == artifact or name in board.artifacts
        ]
        downstream_steps = set(playbook.downstream_steps(artifact))
        manifest.extend(
            {
                "kind": "approval" if candidate.capability_approval is not None else "human_task",
                "id": candidate.id,
            }
            for candidate in self.tasks.tasks()
            if candidate.workflow_id == task.workflow_id
            and candidate.id != task.id
            and candidate.status.value == "pending"
            and candidate.step in downstream_steps
        )
        manifest.extend(
            {"kind": "worker", "id": worker_id}
            for worker_id in WorkerLaunchStore(self.revisions.issue_dir).correction_candidates()
        )
        manifest.extend(
            {"kind": "dispatch", "id": event_id}
            for event_id in EventDispatchFenceStore(self.revisions.issue_dir).correction_candidates()
        )
        manifest.append({"kind": "continuation", "id": task.workflow_id})
        return tuple(manifest)

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
            # Task completion and handoff publication are separate durable
            # writes.  A crash between them must resume the frozen graph edge
            # before this journal can become authoritative.
            self._schedule_graph_continuation(task)
            self.revisions.commit(operation_id)
            return CorrectionResult(revision=revision, operation_id=operation_id)
        revision = self.revisions.revision_for_operation(operation_id)
        if revision is not None:
            # Replacement is already immutable and idempotent.  Do not run the
            # original base-CAS again: publication may have advanced between
            # replacement and task completion when the process stopped.
            return self._finish_recovered_replacement(
                task=task, journal=journal, context=context, revision=revision
            )
        return self.apply(CorrectionRequest(
            workflow_id=context["workflow_id"], task_id=context["task_id"], artifact=context["artifact"],
            base_hash=context["base_hash"], content=context["content"], operation_id=operation_id,
            actor=context["actor"], manifest=tuple(journal["manifest"]),
            completion_payload=context["completion_payload"],
            proxy_authorization_id=context.get("proxy_authorization_id"),
            suitability=context.get("suitability") or None,
        ))

    def _finish_recovered_replacement(
        self, *, task, journal: Mapping[str, Any], context: Mapping[str, Any], revision: ArtifactRevision
    ) -> CorrectionResult:
        with self.tasks.transaction():
            board_store = BlackboardStore(self.revisions.issue_dir)
            board = board_store.load_or_create(task.step)
            current = board.artifacts.get(revision.artifact)
            if current is None:
                raise ValueError("recovered correction artifact is no longer published")
            if current.path != revision.path:
                board_store.put_artifact(board, ArtifactEntry(
                    name=current.name, kind=current.kind, version=current.version + 1,
                    updated_by="human_task_correction", path=revision.path,
                    summary=current.summary, base_sha=current.base_sha, head_sha=current.head_sha,
                ))
            payload = dict(context["completion_payload"])
            payload["correction"] = {
                "actor": context["actor"], "artifact": revision.artifact,
                "base_hash": context["base_hash"], "new_hash": revision.sha256,
                "operation_id": journal["operation_id"],
                "authorization_id": context.get("proxy_authorization_id"),
                "validation": {"suitability": dict(context.get("suitability") or {})}
                if context.get("suitability") else {"source": "user_command"},
                "manifest": list(journal["manifest"]),
            }
            self.tasks.complete(
                workflow_id=context["workflow_id"], task_id=context["task_id"],
                payload=payload, source="human_task_correction",
            )
            self._schedule_graph_continuation(task)
            self.revisions.commit(journal["operation_id"])
            return CorrectionResult(revision=revision, operation_id=journal["operation_id"])

    def _invalidate(
        self,
        entry: Mapping[str, str],
        operation_id: str,
        *,
        corrected_artifact: str,
        step: str,
        workflow_id: str,
        correction_generation: int | None,
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
        elif entry["kind"] == "continuation":
            if entry["id"] != workflow_id:
                raise ValueError("correction continuation invalidation target is invalid")
            store = BlackboardStore(self.revisions.issue_dir)
            if not store.invalidate_continuation_for_correction(
                store.load_or_create(step), operation_id=operation_id
            ):
                raise ValueError("correction continuation invalidation target is absent")
            if correction_generation is None:
                raise ValueError("correction continuation generation is missing")
            WorkerLaunchStore(
                self.revisions.issue_dir, allow_initial_prepared_generation=True
            ).ensure_correction_generation(correction_generation)
        else:  # pragma: no cover - request validation keeps this fail-closed.
            raise ValueError("correction invalidation kind is unsupported")
