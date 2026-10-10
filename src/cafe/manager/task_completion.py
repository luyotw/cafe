"""Manager-owned, authority-bound completion of a neutral HumanTask."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from cafe.core.task_inbox import TaskInboxService
from cafe.ui.commands.tasks import apply_structured_task

from .task_inspection import inspect_task_authority


def complete_manager_task(
    issue_dir: Path,
    task_id: str,
    *,
    response: Mapping[str, Any],
    evidence: Mapping[str, Any],
    contract_sha256: str,
    sources_sha256: str,
) -> dict[str, Any]:
    """Validate Manager authority before neutral durable task completion."""
    issue_dir = Path(issue_dir).resolve()
    project_root = issue_dir.parent.parent.parent
    facts = inspect_task_authority(issue_dir, task_id, response=response, evidence=evidence)
    if (
        not facts["allowed"]
        or facts["contract_sha256"] != contract_sha256
        or facts["sources_sha256"] != sources_sha256
    ):
        raise ValueError("Manager task authority changed or is insufficient")
    # Concurrent decision-source changes after this check are not guarded through completion.
    service = TaskInboxService(project_root / ".cafe")
    preflight = service.preflight_completion(task_id)
    if preflight.issue_dir.resolve() != issue_dir or preflight.task.capability_approval is not None:
        raise ValueError("task is outside the Manager completion boundary")
    _, applied = apply_structured_task(
        service,
        task_id,
        response,
        project_root=project_root,
        source="command",
        completion_authority={"kind": "manager_proxy", "contract_sha256": contract_sha256,
                              "sources_sha256": sources_sha256},
    )
    detail = service.inspect_read_only(task_id)
    return {
        "ok": True,
        "task_id": detail.id,
        "status": detail.status,
        "workflow_id": detail.workflow_id,
        "continuation": applied.target,
    }
