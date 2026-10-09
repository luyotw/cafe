"""Execute only approved action snapshots through existing host capability policy."""

from __future__ import annotations

import json
import time

from cafe.core.capabilities import (
    PolicyDecision,
    evaluate_capability_request,
    run_capability_request,
)
from cafe.core.capability_approvals import CapabilityApprovalService
from cafe.core.packet_io import atomic_write_bytes, canonical_json
from cafe.delivery.capabilities import boundaries
from cafe.delivery.records import ActionStore
from cafe.delivery.selection import validate_snapshot_authority


def action_request(registry, snapshot, issue_dir, action):
    cap = (
        ("cafe.branch.integrate" if snapshot.proposal.mode == "local" else "cafe.github.pr.merge")
        if action == "integration"
        else "cafe.github.issue.create"
    )
    manifest = registry[cap]
    args = {
        "snapshot": json.dumps(snapshot.model_dump(mode="json"), sort_keys=True),
        "issue_dir": str(issue_dir.resolve()),
        "action": action,
    }
    from types import SimpleNamespace

    replacements = boundaries(SimpleNamespace(capability=cap, args=args))

    def expand(values):
        return [replacements.get(value, value) for value in values]

    return {
        "capability": cap,
        "args": args,
        "effects": {
            **manifest.effects.model_dump(mode="json"),
            "writes": expand(manifest.effects.writes),
        },
        "credentials": list(manifest.credentials),
        "permissions": {key: expand(value) for key, value in manifest.permissions.items()},
    }


def execute_snapshot(
    *, root, issue_dir, snapshot, registry, step, iteration, output_file, timeout=120
):
    """One bounded batch; approval pauses before dispatch and successes survive iterations."""
    from cafe.delivery.operations import execute_action

    if snapshot.proposal.verification is None:
        raise ValueError("verification scope is missing; fresh PR action review is required")
    validate_snapshot_authority(issue_dir, snapshot)
    deadline = time.monotonic() + min(timeout, 180)
    actions = ["integration", *[p.id for p in snapshot.selected]]
    results = {}
    pending_task = None
    store = ActionStore(issue_dir, snapshot)
    previous_results = {}
    history = list((issue_dir / "delivery").glob("*/actions.json"))
    if len(history) > 100:
        raise ValueError("delivery history requires bounded manual reconciliation")
    from cafe.delivery.contracts import ActionSnapshot

    for manifest in history:
        if manifest.parent == store.directory:
            continue
        if manifest.stat().st_size > 1024 * 1024:
            raise ValueError("oversized historical action manifest")
        previous = ActionSnapshot.model_validate_json(manifest.read_bytes())
        if previous.proposal.workflow_id != snapshot.proposal.workflow_id:
            continue
        previous_store = ActionStore(issue_dir, previous)
        for action in ["integration", *[item.id for item in previous.selected]]:
            receipt = previous_store.read(action)
            if not receipt or receipt["state"] not in {"unknown", "succeeded", "settled"}:
                continue
            if receipt["state"] == "unknown" and deadline - time.monotonic() > 5:
                # Existing unknown attempts permit observation only, even if a revision drops them.
                receipt = execute_action(
                    root, issue_dir, previous, action, timeout=deadline - time.monotonic() - 5
                )
            previous_results[f"{previous.digest}:{action}"] = receipt
    unresolved_history = [
        "previous:" + key
        for key, value in previous_results.items()
        if value["state"] not in {"succeeded", "settled"}
    ]
    # Resolve retained uncertainty before any newly approved effect or host approval request.
    for action in (actions if not unresolved_history else []):
        remaining = deadline - time.monotonic()
        if remaining <= 5:
            results[action] = {"state": "not_dispatched", "error": "batch_deadline"}
            break
        prior = store.read(action) or store.correlated_attempt(action)
        if prior and prior["state"] in {"unknown", "succeeded", "settled"}:
            # Observation is read-only. It never replays an unknown/successful effect.
            result = execute_action(root, issue_dir, snapshot, action, timeout=remaining)
            results[action] = result
            if result["state"] != "succeeded":
                break
            continue
        if action != "integration":
            # Finish bounded read-only observation before opening a mutating host attempt.
            # A partial scan cannot consume the one-shot capability execution or bypass it.
            result = execute_action(
                root, issue_dir, snapshot, action, timeout=remaining, observe_only=True
            )
            if result["state"] == "succeeded":
                results[action] = result
                continue
            if not result.get("issue_observation_ready"):
                results[action] = result
                break
            remaining = deadline - time.monotonic()
        request = action_request(registry, snapshot, issue_dir, action)
        evaluation = evaluate_capability_request(registry, request)
        from cafe.delivery.approvals import validate_reviewed_request

        validate_reviewed_request(
            issue_dir=issue_dir, snapshot=snapshot, action=action, evaluation=evaluation,
        )
        if evaluation.decision == PolicyDecision.REQUIRE_APPROVAL:
            service = CapabilityApprovalService(
                issue_dir=issue_dir,
                workflow_id=snapshot.proposal.workflow_id,
                step=step,
                iteration=iteration,
            )
            task = service.request_approval(
                request=evaluation.request, manifest=evaluation.manifest
            )
            approval = service.inspect(task.id)
            if snapshot.proposal.capability_review is not None:
                from cafe.delivery.approvals import approve_reviewed_request

                approval = approve_reviewed_request(
                    service, task, issue_dir=issue_dir, snapshot=snapshot,
                    action=action, evaluation=evaluation,
                )
            if approval["state"] == "pending":
                pending_task = task.id
                results[action] = {
                    "state": "not_dispatched",
                    "error": "capability_approval_pending",
                    "task_id": task.id,
                }
                break
            receipt = service.resume(
                task.id,
                correlation_id=approval["correlation_id"],
                request=evaluation.request,
                registry=registry,
                repo_root=root,
                output_file=output_file,
                timeout_sec=max(1, remaining - 5),
                before_dispatch=lambda: validate_snapshot_authority(issue_dir, snapshot),
            )
            execution = receipt.get("execution", receipt)
        else:
            execution = run_capability_request(
                repo_root=root,
                registry=registry,
                capability_request=request,
                output_file=output_file,
                timeout_sec=max(1, remaining - 5),
            ).receipt
        result = store.read(action) or {
            "state": "not_dispatched",
            "error": execution.get("outcome", "policy_denied"),
        }
        results[action] = result
        if not execution.get("success") or result["state"] != "succeeded":
            break
    complete = len(results) == len(actions) and all(
        r["state"] == "succeeded" for r in results.values()
    )
    complete = complete and not unresolved_history
    verification = {"state": "pending", "error": "integration_not_complete"}
    if complete:
        from cafe.delivery.verification import wait_for_delivery

        verification = wait_for_delivery(snapshot, results["integration"].get("commit"))
        complete = verification["state"] in {"succeeded", "not_required"}
    report = {
        "version": 1,
        "snapshot": snapshot.digest,
        "task_id": snapshot.task_id,
        "result_id": snapshot.result_id,
        "complete": complete,
        "actions": results,
        "previous_results": previous_results,
        "remaining": [a for a in actions if results.get(a, {}).get("state") != "succeeded"]
        + unresolved_history,
        "pending_task": pending_task,
        "verification": verification,
    }
    path = store.directory / "result.json"
    atomic_write_bytes(path, canonical_json(report))
    return report, path
