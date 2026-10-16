"""Read-only proof for the closed, host-only delivery path in older workflows."""

from __future__ import annotations

import re

from cafe.delivery.contracts import ActionProposal, ActionSnapshot, digest
from cafe.delivery.selection import proposal_path


def no_model_delivery_proof(issue_dir, source_id, workflow_id, events):
    """Do not infer model absence from an automatic owner or a missing file."""
    from cafe.services.cost_summary import read_accounting_file

    evidence = {}

    def read(name):
        value = read_accounting_file(issue_dir / name)
        evidence[name] = digest(value)
        if len(evidence) > 128:
            raise ValueError("delivery accounting exceeds evidence bound")
        return value

    try:
        step, directory = source_id.split("/")
        iteration = int(directory.removeprefix("iteration_"))
        store = read("human_tasks.json")
        if store.get("workflow_id") != workflow_id:
            return None
        tasks = [
            task
            for task in store["tasks"]
            if task.get("step") == step and task.get("iteration") == iteration
        ]
        if not tasks or len(tasks) > 32:
            return None
        if any(
            event.get("step") == step
            and event.get("event_type")
            in {"step_started", "step_completed", "single_step_completed"}
            and event.get("data", {}).get("iteration") in {None, iteration}
            for event in events
        ):
            return None
        results = {result["task_id"]: result for result in store["results"]}
        # Inspect every historical bundle: reconciliation may execute earlier actions.
        manifests = list((issue_dir / "delivery").glob("*/actions.json"))
        if len(manifests) > 32:
            return None
        snapshots, reports = {}, {}
        for manifest in manifests:
            name = manifest.relative_to(issue_dir).as_posix()
            snapshot = ActionSnapshot.model_validate(read(name))
            proposal = snapshot.proposal
            if proposal.workflow_id != workflow_id:
                return None
            if (
                manifest.parent.name != snapshot.digest
                or snapshot.selected
                or proposal.mode != "github"
                or proposal.verification is None
                or proposal.verification.tool is not None
                or not proposal.verification.not_required_reason
                or set(proposal.capability_review or {}) != {"integration"}
                or proposal.capability_review["integration"]["manifest"]["implementation"]
                != "merge_github_pr"
            ):
                return None
            names = {p.name for p in manifest.parent.iterdir() if p.suffix == ".json"}
            if names - {"actions.json", "integration.json", "result.json"}:
                return None
            snapshots[snapshot.digest] = snapshot
            receipt = None
            if "integration.json" in names:
                receipt = read(f"delivery/{snapshot.digest}/integration.json")
                if (
                    receipt.get("action") != "integration"
                    or receipt.get("snapshot") != snapshot.digest
                ):
                    return None
            if "result.json" in names:
                report = read(f"delivery/{snapshot.digest}/result.json")
                if (
                    report.get("snapshot") != snapshot.digest
                    or report.get("task_id") != snapshot.task_id
                    or report.get("result_id") != snapshot.result_id
                    or report.get("complete") is not True
                    or report.get("remaining") != []
                    or report.get("pending_task") is not None
                    or report.get("verification", {}).get("state") != "not_required"
                    or set(report.get("actions", {})) != {"integration"}
                    or report["actions"]["integration"] != receipt
                ):
                    return None
                reports[snapshot.digest] = report
        for report in reports.values():
            for key, receipt in report.get("previous_results", {}).items():
                prior, action = key.split(":")
                if (
                    prior not in snapshots
                    or action != "integration"
                    or read(f"delivery/{prior}/integration.json") != receipt
                ):
                    return None
        for task in tasks:
            if task.get("workflow_id") != workflow_id or task.get("status") != "completed":
                return None
            result = results[task["id"]]
            payload = result["payload"]
            approval = task.get("capability_approval")
            if approval is not None:
                nested = ActionSnapshot.model_validate_json(
                    approval["argument_summary"]["snapshot"]
                )
                authority = payload.get("authority", {})
                if (
                    task.get("policy_id") != "capability-approval"
                    or nested.digest not in snapshots
                    or nested != snapshots[nested.digest]
                    or approval["argument_summary"].get("action") != "integration"
                    or approval["manifest"].get("implementation") != "merge_github_pr"
                    or approval.get("workflow_id") != workflow_id
                    or approval.get("task_id") != task["id"]
                    or approval.get("state") != "succeeded"
                    or result.get("workflow_id") != workflow_id
                    or payload.get("decision") != "approve"
                    or payload.get("request_fingerprint") != approval.get("fingerprint")
                    or authority.get("snapshot") != nested.digest
                    or authority.get("task_id") != nested.task_id
                    or authority.get("result_id") != nested.result_id
                ):
                    return None
                continue
            if task.get("policy_id") not in {"delivery-review", "delivery-outcome"}:
                return None
            decisions = task["expected_result"]["decisions"]
            decision = next(row for row in decisions if row["id"] == payload["decision"])
            if (
                result.get("workflow_id") != workflow_id
                or payload.get("continuation") != task["continuations"][decision["id"]]
                or payload.get("supervisor_handoff_to") is not None
            ):
                return None
            materialized = [
                e
                for e in events
                if e["event_type"] == "human_task_materialized"
                and e.get("step") == step
                and e["data"].get("task_id") == task["id"]
            ]
            if len(materialized) != 1:
                return None
            start = materialized[0]["sequence"]
            following = [e for e in events if e["sequence"] > start and e.get("step") == step]
            if not following:
                return None
            pause = following[0]
            if (
                pause["event_type"] != "workflow_paused"
                or pause["data"].get("reason") != "automatic_task"
                or pause["data"].get("runtime") != "owner_dispatch"
            ):
                return None
            completed = next(e for e in following if e["event_type"] == "human_task_completed")
            if (
                completed["data"].get("to_step") != payload["continuation"]
                or completed["data"].get("task_id") != task["policy_id"]
                or completed["data"].get("human_task_id", task["id"]) != task["id"]
                or any(
                    e["event_type"] == "step_started"
                    for e in events
                    if start < e["sequence"] <= completed["sequence"] and e.get("step") == step
                )
            ):
                return None
            for event in (materialized[0], pause, completed):
                read(f"audit_events/events/{event['sequence']:010d}.json")
            if task["policy_id"] == "delivery-review":
                shown = ActionProposal.model_validate(
                    read(proposal_path(issue_dir, task["id"]).relative_to(issue_dir).as_posix())
                )
                if (
                    shown.workflow_id != workflow_id
                    or shown.approval_step != step
                    or shown.approval_iteration != iteration
                    or f"Action proposal SHA256: {shown.digest}" not in task["prompt"]
                ):
                    return None
                approved = [s for s in snapshots.values() if s.task_id == task["id"]]
                if decision.get("correction") is True:
                    if approved or payload["continuation"] == step:
                        return None
                elif (
                    len(approved) != 1
                    or approved[0].proposal != shown
                    or approved[0].result_id != result["id"]
                    or approved[0].proposal.approval_step != step
                    or approved[0].proposal.approval_iteration != iteration
                ):
                    return None
            else:
                match = re.search(r"Action snapshot SHA256: ([0-9a-f]{64})", task["prompt"])
                result_match = re.search(r"Delivery result SHA256: ([0-9a-f]{64})", task["prompt"])
                if (
                    not match
                    or not result_match
                    or match[1] not in reports
                    or digest(reports[match[1]]) != result_match[1]
                ):
                    return None
        return dict(
            version=1,
            owner="delivery",
            workflow_id=workflow_id,
            source_id=source_id,
            evidence=evidence,
        )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        StopIteration,
        RecursionError,
    ):
        return None
