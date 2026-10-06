"""Correlate shown immutable proposals with durable workflow task results."""

from __future__ import annotations

import json
from pathlib import Path

from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.core.packet_io import atomic_write_bytes, canonical_json
from cafe.delivery.contracts import ActionProposal, ActionSnapshot, approve_selection, digest


def proposal_path(issue_dir, task_id):
    return issue_dir / "delivery" / "proposals" / f"{digest({'task_id': task_id})}.json"


def save_shown_proposal(issue_dir: Path, task, proposal: ActionProposal):
    path = proposal_path(issue_dir, task.id)
    content = canonical_json(proposal.model_dump(mode="json"))
    if path.exists() and path.read_bytes() != content:
        raise ValueError("shown action proposal is immutable")
    atomic_write_bytes(path, content)


def approved_snapshot(issue_dir: Path, *, workflow_id: str, binding) -> ActionSnapshot:
    records = HumanTaskRecordStore(issue_dir)
    tasks = [
        task
        for task in records.tasks()
        if task.workflow_id == workflow_id
        and task.step == binding.approval_step
        and task.policy_id == binding.approval_task
    ]
    if not tasks:
        raise ValueError("explicit action review is missing")
    task = tasks[-1]
    if task.status != HumanTaskStatus.COMPLETED or task.superseded_by_task_id:
        raise ValueError("current action review is not completed")
    result = records.get_result(task.id)
    if result is None or result.workflow_id != workflow_id:
        raise ValueError("action review has no correlated result")
    proposal = ActionProposal.model_validate_json(proposal_path(issue_dir, task.id).read_bytes())
    if f"Action proposal SHA256: {proposal.digest}" not in task.prompt:
        raise ValueError("task did not display this action proposal")
    return approve_selection(
        proposal,
        {
            "workflow_id": task.workflow_id,
            "step": task.step,
            "iteration": task.iteration,
            "task_id": task.id,
            "result_id": result.id,
            "proposal_digest": proposal.digest,
            "decision": result.payload.get("decision"),
            "feedback": result.payload.get("feedback", ""),
        },
    )


def validate_snapshot_authority(issue_dir: Path, snapshot: ActionSnapshot):
    """A caller-supplied manifest cannot manufacture the correlated human decision."""
    from cafe.delivery.contracts import DeliveryBinding

    records = HumanTaskRecordStore(issue_dir)
    task = records.get_task(snapshot.task_id)
    binding = DeliveryBinding(
        actions_artifact="unused",
        result_artifact="unused",
        approval_step=snapshot.proposal.approval_step,
        approval_task=task.policy_id,
        correction_step="unused",
    )
    current = approved_snapshot(
        issue_dir, workflow_id=snapshot.proposal.workflow_id, binding=binding
    )
    if current != snapshot:
        raise ValueError("action snapshot no longer matches current authority")


def snapshot_from_args(args) -> ActionSnapshot:
    raw = args.get("snapshot")
    if not isinstance(raw, str) or len(raw.encode()) > 1024 * 1024:
        raise ValueError("invalid action snapshot argument")
    return ActionSnapshot.model_validate(json.loads(raw))
