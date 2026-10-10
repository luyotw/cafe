#!/usr/bin/env python3
"""Read the existing combined delivery acceptance and terminal choice."""

import argparse
import json
import re
from pathlib import Path

from cafe.delivery.closeout import accepted_choice
from cafe.manager._store import load_contract


def _archived_choice(issue_dir, contract, sha):
    """Check completed acceptance without treating an archive as a live checkout.

    Task completion already validated the live source. Archival inspection checks
    the same immutable approval/result and effect receipts; it executes no action.
    """
    from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
    from cafe.delivery.closeout import TERMINAL_DECISIONS, read_plan, plan_text, validate_choice
    from cafe.delivery.contracts import ActionSnapshot, DeliveryBinding, digest
    from cafe.delivery.records import ActionStore
    from cafe.delivery.selection import approved_snapshot
    from cafe.delivery.verification import validate_observation

    workflow_id = contract["identity"]["workflow_id"]
    plan = read_plan(issue_dir, workflow_id)
    cleanup = [c["argv"] for c in contract["delivery_contract"]["closeout_plan"]["cleanup"]]
    if plan is None or plan["contract_sha256"] != sha or plan["cleanup"] != cleanup:
        raise ValueError("archived terminal plan differs from its confirmed contract")
    records = HumanTaskRecordStore(issue_dir)
    tasks = [t for t in records.tasks() if t.workflow_id == workflow_id
             and t.trigger == "confirm_output" and "Closeout plan SHA256:" in t.prompt]
    if not tasks:
        return None
    task = tasks[-1]
    if task.status != HumanTaskStatus.COMPLETED or task.superseded_by_task_id:
        return None
    result = records.get_result(task.id)
    if result is None or result.workflow_id != workflow_id:
        raise ValueError("archived acceptance has no correlated result")
    decision = result.payload.get("decision")
    if decision not in TERMINAL_DECISIONS:
        return None
    match = re.search(r"Action snapshot SHA256: ([0-9a-f]{64})", task.prompt)
    if not match or plan_text(plan) not in task.prompt:
        raise ValueError("archived acceptance has no exact displayed plan and snapshot")
    directory = issue_dir / "delivery" / match[1]
    snapshot = ActionSnapshot.model_validate_json((directory / "actions.json").read_bytes())
    approval_task = records.get_task(snapshot.task_id)
    binding = DeliveryBinding(actions_artifact="unused", result_artifact="unused",
                              approval_step=snapshot.proposal.approval_step,
                              approval_task=approval_task.policy_id, correction_step="unused")
    if approved_snapshot(issue_dir, workflow_id=workflow_id, binding=binding) != snapshot:
        raise ValueError("archived action approval changed")
    report = json.loads((directory / "result.json").read_text())
    if (snapshot.digest != match[1] or not report.get("complete") or report.get("remaining")
            or report.get("snapshot") != snapshot.digest
            or f"Delivery result SHA256: {digest(report)}" not in task.prompt):
        raise ValueError("archived accepted result changed")
    store = ActionStore(issue_dir, snapshot)
    expected = ["integration", *[p.id for p in snapshot.selected]]
    if set(report.get("actions", {})) != set(expected) or any(
            store.read(action) != report["actions"][action]
            or report["actions"][action].get("state") != "succeeded" for action in expected):
        raise ValueError("archived effect receipts differ from the accepted result")
    historical = list((issue_dir / "delivery").glob("*/actions.json"))
    if len(historical) > 100:
        raise ValueError("archived delivery history requires bounded reconciliation")
    retained = {}
    def effect(row):
        return {k: v for k, v in row.items() if k not in {"snapshot", "action"}}
    for manifest in historical:
        if manifest.parent == directory:
            continue
        if manifest.stat().st_size > 1024 * 1024:
            raise ValueError("oversized archived action manifest")
        previous = ActionSnapshot.model_validate_json(manifest.read_bytes())
        if previous.proposal.workflow_id != workflow_id:
            continue
        previous_store = ActionStore(issue_dir, previous)
        for action in ["integration", *[p.id for p in previous.selected]]:
            receipt = previous_store.read(action)
            if receipt and receipt["state"] in {"unknown", "succeeded", "settled"}:
                if receipt["state"] == "unknown":
                    raise ValueError("archived delivery has an unresolved effect")
                retained[f"{previous.digest}:{action}"] = effect(receipt)
    if retained != {k: effect(v) for k, v in report.get("previous_results", {}).items()}:
        raise ValueError("archived historical receipts changed")
    verification = snapshot.proposal.verification
    if verification is not None:
        commit = report["actions"]["integration"].get("commit")
        shown = report.get("verification", {})
        if verification.tool is None:
            if shown != {"state": "not_required", "commit": commit,
                         "reason": verification.not_required_reason}:
                raise ValueError("archived no-verification scope changed")
        elif shown.get("state") != "succeeded":
            raise ValueError("archived verification was not completed")
        else:
            validate_observation(shown, commit)
    validate_choice(issue_dir, snapshot, task, decision)
    return {"choice": TERMINAL_DECISIONS[decision], "task_id": task.id, "result_id": result.id,
            "contract_sha256": sha, "cleanup": cleanup, "archive": ["cafe", "close", "--archive-only"]}


def inspect(issue_dir, workflow_id, project_root=None):
    contract, sha = load_contract(issue_dir, workflow_id=workflow_id)
    state = json.loads((issue_dir / "blackboard.json").read_text())
    if state.get("workflow_id") != workflow_id or state.get("current_step") != "done":
        raise ValueError("workflow completion must be verified before terminal actions")
    if contract["delivery_contract"].get("terminal_selection") != "delivery_outcome":
        return {"status": "legacy", "selection": None}
    plan = contract["delivery_contract"]["closeout_plan"]
    if project_root is not None:
        from cafe.manager.costs import _archive

        if Path(issue_dir).absolute() == _archive(project_root, contract["identity"]["issue_name"]).absolute():
            result = _archived_choice(Path(issue_dir), contract, sha)
            return {"status": "accepted" if result else "not_recorded", "selection": result}
    result = accepted_choice(
        issue_dir,
        workflow_id=workflow_id,
        contract_sha256=sha,
        cleanup=[item["argv"] for item in plan["cleanup"]],
    )
    return {"status": "accepted" if result else "not_recorded", "selection": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue-dir", type=Path, required=True)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--project-root", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(args.issue_dir, args.workflow_id, args.project_root), ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
