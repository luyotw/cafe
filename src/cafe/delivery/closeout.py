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


def retained_combined_task(issue_dir, step, task, saved, current, machine_contract):
    """Read an exact legacy acceptance schema without refreshing its saved bytes."""
    binding = step.get("delivery") or {}
    if (task.trigger != "confirm_output" or task.policy_id != binding.get("result_task")
            or "Closeout plan SHA256:" not in task.prompt):
        return False
    if read_result_contract(issue_dir, task.workflow_id) is not None:
        return False
    plan = read_plan(issue_dir, task.workflow_id)
    if (plan is None or plan_text(plan) not in task.prompt):
        return False
    if {d.id for d in saved.decisions} != {"confirm", "revise", "confirm_cleanup", "confirm_archive"}:
        return False
    reduced = saved.model_copy(update={"decisions": tuple(d for d in saved.decisions
                                                     if d.id not in {"confirm_cleanup", "confirm_archive"})})
    return (machine_contract(current) in (machine_contract(reduced), machine_contract(saved))
            and set(task.continuations) == {d.id for d in saved.decisions}
            and all(task.continuations[d] == "_done" for d in TERMINAL_DECISIONS)
            and task.continuations["revise"] == task.step)


def read_result_contract(issue_dir, workflow_id):
    """Read only the bounded host projection needed to supersede a result task."""
    path = issue_dir / "delivery" / "result-contract.json"
    if not path.exists():
        return None
    if (path.is_symlink() or path.parent.is_symlink()
            or not path.resolve().is_relative_to(issue_dir.resolve())
            or path.stat().st_size > 256 * 1024):
        raise ValueError("unsafe delivery result contract")
    value = json.loads(path.read_text())
    if (not isinstance(value, dict)
            or set(value) != {"version", "workflow_id", "contract_sha256", "delivery_result"}
            or type(value["version"]) is not int or value["version"] != 1
            or value["workflow_id"] != workflow_id
            or not isinstance(value["contract_sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", value["contract_sha256"])
            or not isinstance(value["delivery_result"], dict)
            or set(value["delivery_result"]) != {"step", "task_id", "artifact"}
            or any(not isinstance(v, str) or not v for v in value["delivery_result"].values())):
        raise ValueError("invalid delivery result contract")
    return value["delivery_result"]


def resolve_result_binding(issue_dir, playbook_id):
    """Resolve a result owner through the public project graph and issue overrides."""
    from cafe.playbooks.loader import PlaybookLoader, apply_issue_playbook_overrides
    graph = PlaybookLoader(
        project_root=issue_dir.parent.parent.parent, read_only=True, resolve_presentation=False
    ).load(playbook_id)
    graph = apply_issue_playbook_overrides(graph, issue_dir / "issue.yaml")
    owners = [
        (name, step["delivery"])
        for name, step in graph["steps"].items()
        if step.get("delivery") and step["delivery"].get("result_task")
        and step.get("output_artifact") == step["delivery"]["result_artifact"]
    ]
    if len(owners) != 1:
        raise ValueError("legacy closeout requires one declared delivery result owner")
    name, binding = owners[0]
    return {"step": name, "task_id": binding["result_task"]}


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
    result_contract = read_result_contract(issue_dir, snapshot.proposal.workflow_id)
    if result_contract is not None:
        if (task.step != result_contract["step"] or task.policy_id != result_contract["task_id"]
                or decision != "confirm"):
            raise ValueError("result acceptance cannot select terminal actions")
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
