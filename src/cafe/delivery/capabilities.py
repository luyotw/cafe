"""Registered host adapters for exact delivery effects and approved observers."""

import json
from pathlib import Path

from cafe.delivery.operations import execute_action
from cafe.delivery.selection import snapshot_from_args, validate_snapshot_authority


def boundaries(request):
    snapshot = snapshot_from_args(request.args)
    p = snapshot.proposal
    if request.capability == "cafe.branch.integrate":
        return {
            "delivery_effect": p.destination,
            "delivery_git": p.repository,
            "delivery_records": str(Path(request.args["issue_dir"]) / "delivery"),
        }
    if request.capability == "cafe.github.pr.merge":
        return {
            "delivery_effect": f"github_pr:{p.repository}#{p.pr_number}",
            "delivery_records": str(Path(request.args["issue_dir"]) / "delivery"),
        }
    return {
        "delivery_effect": f"github_issues:{p.issue_repository}",
        "delivery_records": str(Path(request.args["issue_dir"]) / "delivery"),
    }


def adapter(*, repo_root, request, manifest, output_file, timeout_sec):
    from cafe.core.capabilities import CapabilityExecutionError

    try:
        snapshot = snapshot_from_args(request.args)
        issue_dir = Path(request.args["issue_dir"]).resolve()
        issue_dir.relative_to(Path(repo_root).resolve() / ".cafe" / "issues")
        validate_snapshot_authority(issue_dir, snapshot)
        action = request.args["action"]
        expected = (
            (
                "cafe.branch.integrate"
                if snapshot.proposal.mode == "local"
                else "cafe.github.pr.merge"
            )
            if action == "integration"
            else "cafe.github.issue.create"
        )
        if request.capability != expected or (
            action != "integration" and action not in {p.id for p in snapshot.selected}
        ):
            raise ValueError("capability does not match selected effect")
        result = execute_action(Path(repo_root), issue_dir, snapshot, action, timeout=timeout_sec)
        if result["state"] != "succeeded":
            raise CapabilityExecutionError(
                "delivery_operation", str(result.get("error", result["state"])), outputs=result
            )
        # Fixed capability outputs use the existing scalar schema; domain receipts stay structured.
        outputs = {
            key: result[key]
            for key in manifest.outputs.properties
            if key in result and key != "process"
        }
        return {**outputs, "process": json.dumps(result["process"], sort_keys=True)}, None
    except (OSError, ValueError, KeyError) as exc:
        raise CapabilityExecutionError(
            "delivery_binding", "invalid_action_binding", outputs={"reason": str(exc)[:1024]}
        ) from exc


def verification_adapter(*, repo_root, request, manifest, output_file, timeout_sec):
    """Fixed observer execution; platform semantics belong to the approved tool."""
    from cafe.core.capabilities import CapabilityExecutionError
    from cafe.delivery.records import ActionStore
    from cafe.delivery.verification import run_tool, validate_tool_capability
    from cafe.delivery.approvals import validate_reviewed_request
    from cafe.core.capabilities import evaluate_capability_request

    try:
        snapshot = snapshot_from_args(request.args)
        issue_dir = Path(request.args["issue_dir"]).resolve()
        issue_dir.relative_to(Path(repo_root).resolve() / ".cafe" / "issues")
        validate_snapshot_authority(issue_dir, snapshot)
        validate_tool_capability(manifest)
        if (request.args["action"] != "verification"
                or request.capability != snapshot.proposal.verification.tool.capability):
            raise ValueError("verification capability differs from the approved tool")
        if snapshot.proposal.capability_review is None or "verification" not in snapshot.proposal.capability_review:
            raise ValueError("verification needs the displayed repeated host-execution authority")
        validate_reviewed_request(
            issue_dir=issue_dir, snapshot=snapshot, action="verification",
            evaluation=evaluate_capability_request({manifest.id: manifest}, request.model_dump(mode="json")),
        )
        receipt = ActionStore(issue_dir, snapshot).read("integration")
        if not receipt or receipt["state"] != "succeeded":
            raise ValueError("successful integration evidence is required")
        result = run_tool(repo_root, snapshot, receipt["commit"], timeout=min(timeout_sec, 30))
        return {"state": result["state"], "payload": json.dumps(result, sort_keys=True)}, None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CapabilityExecutionError(
            "verification_binding", "invalid_verification_binding",
            outputs={"reason": str(exc)[:1024]},
        ) from exc
