"""Capability-owned publication of an already resolved delivery endpoint."""

from pathlib import Path
from cafe.core.git_delivery import (
    git_text as _git, bind_publication_request, require_publication_target,
)
from cafe.core.packet_io import canonical_json


def publish_resolved_pr(issue_dir: Path, root: Path, output: Path, *, context, before_dispatch, registry=None, approval_task_id=None, correlation_id=None) -> dict:
    """Publish through existing capability policy; persist attempts before external work."""
    from cafe.core.capabilities import load_capability_registry, default_capability_definition_dirs, run_capability_request
    from cafe.core.packet_io import atomic_write_bytes
    import json
    import subprocess

    def action():
        before_dispatch()
        endpoint = context["delivery_endpoint"]
        if endpoint["route"] != "pr":
            raise ValueError("confirmed delivery is not PR publication")
        target = output.resolve().relative_to(root.resolve()).as_posix()
        if not output.resolve().is_relative_to(issue_dir.resolve()) or output.is_symlink():
            raise ValueError("PR material must belong to this issue")
        request = {"capability": "cafe.pr.publish", "args": {"output": target,
            "base": endpoint["target_branch"], "remote": endpoint["remote"]},
            "effects": {"writes": [target, ".git", issue_dir.relative_to(root).as_posix()],
                "network_destinations": ["github.com", "api.github.com"], "browser_open": []},
            "credentials": ["gh"], "permissions": {
                "writes": [target, ".git", issue_dir.relative_to(root).as_posix()],
                "network": ["github.com", "api.github.com"]}}
        request = bind_publication_request(context, request)
        path = issue_dir / "delivery_result.json"
        if path.exists():
            raise ValueError("PR delivery already attempted; reconcile read-only instead of replaying")
        from cafe.core.capabilities import evaluate_capability_request, PolicyDecision
        definitions = registry if registry is not None else load_capability_registry(default_capability_definition_dirs(root))
        evaluation = evaluate_capability_request(definitions, request)
        if evaluation.decision == PolicyDecision.REQUIRE_APPROVAL and approval_task_id is None:
            from cafe.core.capability_approvals import CapabilityApprovalService
            service = CapabilityApprovalService(issue_dir=issue_dir,
                workflow_id=context["identity"]["workflow_id"], step="delivery", iteration=1)
            task = service.request_approval(request=evaluation.request, manifest=evaluation.manifest)
            return {"delivered": False, "needs_human_task": True, "task_id": task.id,
                    "correlation_id": task.capability_approval["correlation_id"]}
        if evaluation.decision == PolicyDecision.DENY:
            run = run_capability_request(repo_root=root, registry=definitions,
                capability_request=request, output_file=output, timeout_sec=240)
            return {"delivered": False, "receipt": run.receipt,
                    "needs_human_task": evaluation.decision == PolicyDecision.REQUIRE_APPROVAL}
        repository = None
        def dispatch_check():
            nonlocal repository
            before_dispatch()
            # Credential-bearing repository lookup follows the capability gate.
            require_publication_target(context, request["args"])
            from cafe.core.execution_checkpoints import (
                load_review_evidence, require_checkpoint, require_verified_review, observe_checkpoint,
            )
            selected = request["args"]["head_oid"]
            readiness = load_review_evidence(issue_dir / "execution_delivery.json")
            current = observe_checkpoint(context, source_revision=selected)
            require_checkpoint(context, readiness.get("checkpoint"), "before_delivery",
                               source_revision=selected, observation=current)
            if context.get("review_policy") == "single_native":
                require_verified_review(context, load_review_evidence(issue_dir / "execution_review.json"),
                                        source_revision=selected, observation=current)
            push_url = request["args"]["push_url"]
            repository = subprocess.run(["gh", "repo", "view", push_url, "--json",
                "nameWithOwner", "--jq", ".nameWithOwner"], cwd=root, capture_output=True,
                text=True, check=True, timeout=20).stdout.strip()
            if len(repository.split("/")) != 2 or any(not part for part in repository.split("/")):
                raise ValueError("authorized repository identity could not be resolved")
            before_dispatch()
            require_publication_target(context, request["args"])
        record = {"delivered": False, "status": "unknown", "authority_digest": context["authority_digest"],
                  "commit": _git(root, "rev-parse", "HEAD")}
        atomic_write_bytes(path, canonical_json(record))
        # No approval bypass: the capability re-evaluates the same host policy.
        before_dispatch()
        if approval_task_id is not None:
            from cafe.core.capability_approvals import CapabilityApprovalService
            service = CapabilityApprovalService(issue_dir=issue_dir,
                workflow_id=context["identity"]["workflow_id"], step="delivery", iteration=1)
            receipt = service.resume(approval_task_id, correlation_id=correlation_id,
                request=evaluation.request, registry=definitions, repo_root=root,
                output_file=output, timeout_sec=240,
                before_dispatch=dispatch_check)
        else:
            run = run_capability_request(repo_root=root, registry=definitions,
                capability_request=request, output_file=output, timeout_sec=240,
                before_dispatch=dispatch_check)
            receipt = run.receipt
        record["receipt"] = receipt
        if receipt.get("success"):
            execution_receipt = receipt.get("execution", receipt)
            url = execution_receipt["outputs"]["pr_url"]
            observed = subprocess.run(["gh", "pr", "view", url, "--json",
                "url,headRefName,baseRefName,headRefOid,state,headRepository"], cwd=root,
                capture_output=True, text=True, check=True, timeout=20)
            actual = json.loads(observed.stdout)
            before_dispatch()
            from urllib.parse import urlparse
            parsed = urlparse(url)
            if (parsed.scheme != "https" or parsed.netloc != "github.com"
                    or parsed.path.split("/")[1:3] != repository.split("/")
                    or actual.get("headRepository", {}).get("nameWithOwner") != repository
                    or actual.get("url") != url or actual.get("headRefName") != endpoint["source_branch"]
                    or actual.get("baseRefName") != endpoint["target_branch"]
                    or actual.get("headRefOid") != _git(root, "rev-parse", "HEAD")
                    or actual.get("state") != "OPEN"):
                raise ValueError("published PR endpoint is unverified; reconcile read-only")
            record.update(delivered=True, status="succeeded", pr=actual)
        else:
            # A failed publisher may already have pushed/created a PR.
            record["status"] = "unknown"
        atomic_write_bytes(path, canonical_json(record))
        return record
    return action()
