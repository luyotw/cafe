"""Delivery acceptance and its single, explicitly selected terminal action."""

import json
import re

from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.delivery.contracts import ActionSnapshot, digest
from cafe.delivery.selection import validate_complete_report, validate_effect_receipts

TERMINAL_DECISIONS = {
    "confirm": "leave",
    "confirm_cleanup": "cleanup",
    "confirm_archive": "archive",
}


def read_plan(issue_dir, workflow_id):
    path = issue_dir / "delivery" / "closeout.json"
    if not path.exists():
        return None
    if (
        path.parent.is_symlink()
        or path.is_symlink()
        or not path.resolve().is_relative_to(issue_dir.resolve())
        or path.stat().st_size > 256 * 1024
    ):
        raise ValueError("unsafe delivery closeout plan")
    plan = json.loads(path.read_text())
    if (
        not isinstance(plan, dict)
        or set(plan) != {"version", "workflow_id", "contract_sha256", "cleanup"}
        or plan.get("version") != 1
        or plan.get("workflow_id") != workflow_id
        or not re.fullmatch(r"[0-9a-f]{64}", plan.get("contract_sha256", ""))
        or not isinstance(plan.get("cleanup"), list)
        or any(
            not isinstance(argv, list) or not argv or any(not isinstance(arg, str) for arg in argv)
            for argv in plan["cleanup"]
        )
    ):
        raise ValueError("invalid delivery closeout plan")
    return plan


def plan_text(plan):
    return (
        "Closeout plan SHA256: "
        + digest(plan)
        + "\n\n"
        + json.dumps(plan, ensure_ascii=False, indent=2)
    )


def validate_choice(issue_dir, snapshot, task, decision):
    if decision not in TERMINAL_DECISIONS:
        return
    plan = read_plan(issue_dir, snapshot.proposal.workflow_id)
    if plan is None:
        if decision != "confirm":
            raise ValueError(
                "no displayed terminal plan; separate explicit cleanup authority is required"
            )
        return
    if plan_text(plan) not in task.prompt:
        raise ValueError("terminal plan changed since the displayed outcome")
    if decision == "confirm_cleanup" and not plan["cleanup"]:
        raise ValueError("the displayed cleanup plan has no commands")


def accepted_choice(issue_dir, *, workflow_id, contract_sha256, cleanup):
    """Return a verified existing user reply; never infer acceptance from phase success."""
    plan = read_plan(issue_dir, workflow_id)
    if plan is None:
        return None
    if plan["contract_sha256"] != contract_sha256 or plan["cleanup"] != cleanup:
        raise ValueError("terminal choice belongs to a stale closeout contract")
    records = HumanTaskRecordStore(issue_dir)
    tasks = [
        task
        for task in records.tasks()
        if task.workflow_id == workflow_id
        and task.trigger == "confirm_output"
        and "Closeout plan SHA256:" in task.prompt
    ]
    if not tasks:
        return None
    task = tasks[-1]
    if task.status != HumanTaskStatus.COMPLETED or task.superseded_by_task_id:
        return None
    result = records.get_result(task.id)
    if result is None or result.workflow_id != workflow_id:
        raise ValueError("terminal acceptance has no correlated result")
    decision = result.payload.get("decision")
    if decision not in TERMINAL_DECISIONS:
        return None
    match = re.search(r"Action snapshot SHA256: ([0-9a-f]{64})", task.prompt)
    if not match:
        raise ValueError("terminal acceptance has no exact delivery snapshot")
    directory = issue_dir / "delivery" / match[1]
    snapshot = ActionSnapshot.model_validate_json((directory / "actions.json").read_bytes())
    report = json.loads((directory / "result.json").read_text())
    if (
        snapshot.digest != match[1]
        or f"Delivery result SHA256: {digest(report)}" not in task.prompt
    ):
        raise ValueError("accepted delivery result changed")
    if snapshot.proposal.verification is None:
        # This task is already durably completed above. Preserve pre-upgrade acceptance,
        # without letting a pending legacy task acquire new acceptance or action rights.
        validate_effect_receipts(issue_dir, snapshot, report)
    else:
        validate_complete_report(issue_dir, snapshot, report)
    validate_choice(issue_dir, snapshot, task, decision)
    return {
        "choice": TERMINAL_DECISIONS[decision],
        "task_id": task.id,
        "result_id": result.id,
        "contract_sha256": contract_sha256,
        "cleanup": cleanup,
        "archive": ["cafe", "close", "--archive-only"],
    }
