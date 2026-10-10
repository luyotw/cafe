"""Project one explicit action decision into its reviewed host approvals."""

from cafe.core.capabilities import PolicyDecision, evaluate_capability_request
from cafe.delivery.contracts import ActionSnapshot, digest
from cafe.delivery.selection import validate_snapshot_authority


def reviewed_request(request):
    """The displayed boundary; the immutable action snapshot supplies operation arguments."""
    if hasattr(request, "model_dump"):
        request = request.model_dump(mode="json")
    return {key: request[key] for key in ("capability", "effects", "credentials", "permissions")}


def collect_review(proposal, issue_dir, registry):
    from cafe.delivery.service import action_request

    preview = ActionSnapshot(
        proposal=proposal,
        proposal_digest=proposal.digest,
        task_id="preview",
        result_id="preview",
        selected=proposal.proposals if proposal.issue_repository else (),
    )
    reviews = {}
    actions = ["integration", *[item.id for item in preview.selected]]
    if proposal.verification is not None and proposal.verification.tool is not None:
        actions.append("verification")
    for action in actions:
        request = action_request(registry, preview, issue_dir, action)
        evaluation = evaluate_capability_request(registry, request)
        if evaluation.decision == PolicyDecision.DENY:
            raise ValueError("delivery capability policy denies the proposed action")
        if action == "verification":
            from cafe.delivery.verification import tool_bytes, validate_tool_capability
            validate_tool_capability(evaluation.manifest)
            tool_bytes(issue_dir.resolve().parents[2], proposal.verification.tool)
        reviews[action] = {
            "manifest": evaluation.manifest.model_dump(mode="json"),
            "request": reviewed_request(evaluation.request),
        }
    return reviews


def review_text(reviews):
    import json

    return (
        "Host capability review SHA256: "
        + digest(reviews)
        + "\n\n"
        + json.dumps(reviews, ensure_ascii=False, sort_keys=True, indent=2)
        + ("\n\nVerification authorizes repeated host execution of the exact approved "
           "Python tool and options. Host code has access to the declared credentials; "
           "read-only is a reviewed tool obligation, not an enforced OS sandbox."
           if "verification" in reviews else "")
    )


def validate_reviewed_request(*, issue_dir, snapshot, action, evaluation):
    reviews = snapshot.proposal.capability_review
    if reviews is None:
        return
    validate_snapshot_authority(issue_dir, snapshot)
    reviewed = reviews.get(action)
    current = {
        "manifest": evaluation.manifest.model_dump(mode="json"),
        "request": reviewed_request(evaluation.request),
    }
    if reviewed != current:
        raise ValueError("host capability boundary changed; fresh action review is required")


def approve_reviewed_request(service, task, *, issue_dir, snapshot, action, evaluation):
    """Reuse only the user's exact displayed bundle, never a Manager-authored answer."""
    reviews = snapshot.proposal.capability_review
    if reviews is None:
        return service.inspect(task.id)  # Legacy action selection grants no host approval.
    validate_reviewed_request(
        issue_dir=issue_dir, snapshot=snapshot, action=action, evaluation=evaluation
    )
    approval = service.inspect(task.id)
    if approval["state"] != "pending":
        return approval
    return service.record_decision(
        task.id,
        {
            "decision": "approve",
            "workflow_id": snapshot.proposal.workflow_id,
            "task_id": task.id,
            "request_fingerprint": approval["fingerprint"],
            "correlation_id": approval["correlation_id"],
            "authority": {
                "source": "confirmed_delivery_review",
                "task_id": snapshot.task_id,
                "result_id": snapshot.result_id,
                "snapshot": snapshot.digest,
                "capability_review": digest(reviews),
            },
        },
    )
