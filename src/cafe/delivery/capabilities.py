"""Registered host adapters for three exact development delivery effects."""

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
        return result, None
    except (OSError, ValueError, KeyError) as exc:
        raise CapabilityExecutionError(
            "delivery_binding", "invalid_action_binding", outputs={"reason": str(exc)[:1024]}
        ) from exc
