"""Driver-owned, authority-bound completion of a neutral HumanTask."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from cafe.core.task_inbox import TaskInboxService
from cafe.ui.commands.tasks import apply_structured_task

from ._store import contract_lock
from .task_inspection import inspect_task_authority


def complete_driver_task(
    issue_dir: Path,
    task_id: str,
    *,
    response: Mapping[str, Any],
    evidence: Mapping[str, Any],
    contract_sha256: str,
    sources_sha256: str,
) -> dict[str, Any]:
    """Bind Driver authority to the neutral durable completion transaction."""
    issue_dir = Path(issue_dir).resolve()
    project_root = issue_dir.parent.parent.parent
    current_context_sha256: str | None = None

    def require_current_authority() -> None:
        nonlocal current_context_sha256
        facts = inspect_task_authority(issue_dir, task_id, response=response, evidence=evidence)
        if (
            not facts["allowed"]
            or facts["contract_sha256"] != contract_sha256
            or facts["sources_sha256"] != sources_sha256
        ):
            raise ValueError("Driver task authority changed or is insufficient")
        current_context_sha256 = facts["context_sha256"]

    def require_unchanged_context() -> None:
        facts = inspect_task_authority(issue_dir, task_id, response=response, evidence=evidence)
        if (
            facts["contract_sha256"] != contract_sha256
            or facts["context_sha256"] != current_context_sha256
        ):
            raise ValueError("Driver task authority changed during durable completion")

    with contract_lock(issue_dir):
        require_current_authority()
        service = TaskInboxService(project_root / ".cafe")
        preflight = service.preflight_completion(task_id)
        if (
            preflight.issue_dir.resolve() != issue_dir
            or preflight.task.capability_approval is not None
        ):
            raise ValueError("task is outside the Driver completion boundary")
        _, applied = apply_structured_task(
            service,
            task_id,
            response,
            project_root=project_root,
            source="command",
            completion_precondition=require_current_authority,
            completion_postcondition=require_unchanged_context,
        )
        detail = service.inspect_read_only(task_id)
        return {
            "ok": True,
            "task_id": detail.id,
            "status": detail.status,
            "workflow_id": detail.workflow_id,
            "continuation": applied.target,
        }
