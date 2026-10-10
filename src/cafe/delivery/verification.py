"""Execute approved phase-owned observers; no CI platform semantics live here."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from cafe.core.packet_io import canonical_json


# Execute verified bytes, never a mutable path reopened by the child.
# Isolated Python excludes the checkout and PYTHONPATH from imports.
_BOOTSTRAP = (
    "import io,json,sys; p=json.load(sys.stdin); "
    "sys.stdin=io.StringIO(json.dumps(p['input'])); "
    "exec(compile(p['code'],'<approved-delivery-tool>','exec'),"
    "{'__name__':'__main__','__file__':'<approved-delivery-tool>'})"
)


class VerificationReviewRequired(ValueError):
    """A phase can help close this gap, but execution needs fresh action review."""


def tool_bytes(root, tool):
    root = Path(root).resolve()
    if tool.owner == "repository":
        owner = root
    else:
        from cafe.skills.loader import SkillLoader
        from cafe.skills.exceptions import SkillDiscoveryError
        try:
            owner = SkillLoader(project_root=root).get_skill_dir(tool.owner).resolve()
        except SkillDiscoveryError as exc:
            raise VerificationReviewRequired(
                "verification tool owner is missing; restore or replace it before fresh delivery action review"
            ) from exc
    path = owner / tool.path
    if path.is_symlink() or not path.resolve().is_relative_to(owner):
        raise VerificationReviewRequired("verification tool must stay within its approved owner")
    if not path.is_file():
        raise VerificationReviewRequired("verification tool is missing; implement it before fresh delivery action review")
    if path.stat().st_size > 128 * 1024:
        raise ValueError("verification tool exceeds the bounded code size")
    code = path.read_bytes()
    if hashlib.sha256(code).hexdigest() != tool.sha256:
        raise VerificationReviewRequired("verification tool changed; fresh delivery action review is required")
    return code


def validate_observation(value, commit):
    if not isinstance(value, dict) or set(value) - {
        "state", "commit", "evidence", "retryable", "error"
    }:
        raise ValueError("invalid verification observation")
    if value.get("state") not in {"pending", "succeeded", "failed", "unknown"}:
        raise ValueError("invalid verification state")
    if value.get("commit") != commit:
        raise ValueError("verification version differs from the integration commit")
    if "retryable" in value and type(value["retryable"]) is not bool:
        raise ValueError("invalid retryable observation")
    if "error" in value and (not isinstance(value["error"], str) or len(value["error"]) > 1024):
        raise ValueError("invalid verification error")
    if value["state"] == "succeeded" and not value.get("evidence"):
        raise ValueError("successful verification needs version-specific evidence")
    if len(canonical_json(value)) > 64 * 1024:
        raise ValueError("verification observation exceeds the bounded result size")
    return value


def run_tool(root, snapshot, commit, *, timeout=30):
    """Called only by the fixed, revalidated host capability adapter."""
    tool = snapshot.proposal.verification.tool
    code = tool_bytes(root, tool)
    p = snapshot.proposal
    inputs = {"repository": p.repository, "target_branch": p.target_branch,
              "commit": commit, "options": tool.options}
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-c", _BOOTSTRAP],
            input=json.dumps({"code": code.decode("utf-8"), "input": inputs}),
            cwd=str(root), capture_output=True, text=True, check=False, timeout=timeout,
        )
        if result.returncode:
            return {"state": "unknown", "commit": commit, "error": "verification_tool_failed",
                    "retryable": False}
        if len(result.stdout.encode()) > 64 * 1024:
            raise ValueError("verification observation exceeds the bounded result size")
        return validate_observation(json.loads(result.stdout), commit)
    except subprocess.TimeoutExpired:
        return {"state": "unknown", "commit": commit, "error": "verification_tool_timeout",
                "retryable": True}
    except (OSError, UnicodeError, ValueError, TypeError):
        return {"state": "unknown", "commit": commit,
                "error": "verification_tool_unavailable_or_invalid", "retryable": False}


def validate_tool_capability(manifest):
    if (manifest.implementation != "verify_delivery_tool" or manifest.idempotency != "safe"
            or manifest.effects.writes or manifest.effects.browser_open
            or manifest.permissions.get("writes") or manifest.permissions.get("browser")):
        raise ValueError("verification requires an explicitly reviewed read-only tool capability")


def observe_delivery(snapshot, commit, *, root, issue_dir, registry=None, timeout=30):
    from cafe.delivery.selection import validate_snapshot_authority
    validate_snapshot_authority(issue_dir, snapshot)
    plan = snapshot.proposal.verification
    if plan is None:
        return {"state": "missing", "error": "verification_scope_requires_fresh_pr_review"}
    if not re.fullmatch(r"[0-9a-f]{40}", commit or ""):
        return {"state": "missing", "error": "integration_commit_unavailable"}
    if plan.tool is None:
        return {"state": "not_required", "reason": plan.not_required_reason, "commit": commit}
    from cafe.core.capabilities import (
        default_capability_definition_dirs, dispatch_revalidated_capability_request,
        evaluate_capability_request, load_capability_registry,
    )
    from cafe.delivery.approvals import validate_reviewed_request
    from cafe.delivery.service import action_request
    if registry is None:
        registry = load_capability_registry(default_capability_definition_dirs(root))
    evaluation = evaluate_capability_request(
        registry, action_request(registry, snapshot, issue_dir, "verification")
    )
    validate_tool_capability(evaluation.manifest)
    if snapshot.proposal.capability_review is None or "verification" not in snapshot.proposal.capability_review:
        raise ValueError("repeated host verification needs the displayed delivery capability review")
    validate_reviewed_request(
        issue_dir=issue_dir, snapshot=snapshot, action="verification", evaluation=evaluation
    )
    tool_bytes(root, plan.tool)
    run = dispatch_revalidated_capability_request(
        repo_root=root, evaluation=evaluation,
        output_file=issue_dir / "delivery" / snapshot.digest / "result.json",
        timeout_sec=timeout,
        before_dispatch=lambda: validate_snapshot_authority(issue_dir, snapshot),
    )
    validate_snapshot_authority(issue_dir, snapshot)
    tool_bytes(root, plan.tool)
    if not run.receipt.get("success"):
        return {"state": "unknown", "commit": commit,
                "error": run.error_message or "verification_denied", "retryable": False}
    return validate_observation(json.loads(run.receipt["outputs"]["payload"]), commit)


def validate_verification(issue_dir, snapshot, report):
    shown = report.get("verification")
    if not shown or shown.get("state") not in {"succeeded", "not_required"}:
        raise ValueError("required post-integration verification is incomplete")
    current = observe_delivery(
        snapshot, report["actions"]["integration"].get("commit"),
        root=issue_dir.resolve().parents[2], issue_dir=issue_dir,
    )
    if current != shown:
        raise ValueError("post-integration verification changed or cannot be verified")
