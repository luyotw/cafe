"""Generic scope checkpoints and content-bound execution evidence."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import uuid

from cafe.core.file_scope import (
    collect_changes,
    compare_scope,
    content_snapshot,
    validate_scope_paths,
)

BOUNDARIES = frozenset({"before_review", "resume", "before_delivery"})


def context_digest(context):
    validate_scope_paths(context["paths"])
    if (
        context.get("version") != 1
        or type(context.get("revision")) is not int
        or context["revision"] < 1
        or not isinstance(context.get("authority_digest"), str)
        or len(context["authority_digest"]) != 64
    ):
        raise ValueError("invalid resolved execution context")
    return hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()


def checkpoint(context, boundary, *, round_id, parent_id):
    if boundary not in BOUNDARIES or not round_id or not parent_id:
        raise ValueError("checkpoint requires a declared boundary and invocation identity")
    digest = context_digest(context)
    root = Path(context["root"])
    changes = collect_changes(root, context["baseline_commit"])
    result = compare_scope(changes, context["paths"], preexisting=context.get("preexisting", []))
    snapshot = None
    if result.passed:
        try:
            snapshot = content_snapshot(root, changes, context["paths"])
        except (OSError, ValueError) as exc:
            from cafe.core.file_scope import ScopeResult

            result = ScopeResult(False, ({"reason": "evidence_unavailable", "detail": str(exc)},))
    return {
        "version": 1,
        "receipt_id": str(uuid.uuid4()),
        "context_digest": digest,
        "authority_digest": context["authority_digest"],
        "revision": context["revision"],
        "identity": context["identity"],
        "boundary": boundary,
        "round_id": round_id,
        "parent_id": parent_id,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "snapshot": snapshot,
        "passed": result.passed,
        "findings": list(result.findings),
    }


def require_checkpoint(context, receipt, boundary):
    if (
        not isinstance(receipt, dict)
        or receipt.get("passed") is not True
        or receipt.get("boundary") != boundary
        or receipt.get("context_digest") != context_digest(context)
    ):
        raise ValueError("missing, failed or stale execution checkpoint")
    if not all(
        receipt.get(k) for k in ("receipt_id", "round_id", "parent_id", "observed_at", "snapshot")
    ):
        raise ValueError("checkpoint lacks invocation-bound evidence")
    current = checkpoint(
        context, boundary, round_id=receipt["round_id"], parent_id=receipt["parent_id"]
    )
    if not current["passed"] or current["snapshot"] != receipt["snapshot"]:
        raise ValueError("execution checkpoint does not cover current content")
    return receipt


def load_execution_context(path: Path):
    from cafe.core.execution_artifacts import load_execution_artifact
    context = load_execution_artifact(path)
    context_digest(context)
    return context


def require_current_review(context, evidence, *, native_observations=None):
    """Accept exactly one independent terminal invocation of current content."""
    if not isinstance(evidence, dict) or evidence.get("version") != 1:
        raise ValueError("native review evidence is missing")
    receipt = require_checkpoint(context, evidence.get("checkpoint"), "before_review")
    if evidence.get("round_id") != receipt["round_id"]:
        raise ValueError("review round does not match its checkpoint")
    invocations = evidence.get("invocations")
    if not isinstance(invocations, list) or len(invocations) != 1:
        raise ValueError("review requires exactly one native invocation per round")
    reviewer = invocations[0]
    if not isinstance(reviewer, dict):
        raise ValueError("native reviewer evidence is invalid")
    if native_observations is not None:
        observed = native_observations.get("observations", [])
        if len(observed) != 1 or native_observations.get("parent_id") != receipt["parent_id"]:
            raise ValueError("native invocation evidence is missing or differs from the parent")
        actual = observed[0]
        if (actual.get("receipt_id") != receipt["receipt_id"] or
                actual.get("reviewer_id") != reviewer.get("reviewer_id") or
                actual.get("configuration") != context["review_configuration"] or
                actual.get("terminal") != "result" or actual.get("exit_status") != 0):
            raise ValueError("review is not associated with a successful checkpointed native invocation")
        if (actual.get("findings") != reviewer.get("findings") or
                actual.get("targeted_tests") != reviewer.get("targeted_tests")):
            raise ValueError("review conclusion differs from the independent native result")
        if datetime.fromisoformat(actual["observed_at"]) < datetime.fromisoformat(receipt["observed_at"]):
            raise ValueError("native invocation preceded its checkpoint")
    if (
        not isinstance(reviewer, dict)
        or not reviewer.get("reviewer_id")
        or reviewer["reviewer_id"] == receipt["parent_id"]
        or reviewer.get("parent_id") != receipt["parent_id"]
    ):
        raise ValueError("reviewer must be independent of the parent invocation")
    if (
        reviewer.get("configuration") != context.get("review_configuration")
        or reviewer.get("configuration", {}).get("read_only") is not True
    ):
        raise ValueError("reviewer effective configuration is unsupported or changed")
    if (
        reviewer.get("terminal") not in {"turn.completed", "result"}
        or reviewer.get("exit_status") != 0
    ):
        raise ValueError("native review requires explicit successful terminal evidence")
    if not reviewer.get("result_reference") or not reviewer.get("targeted_tests"):
        raise ValueError("native review lacks terminal result or targeted test evidence")
    findings = reviewer.get("findings")
    if not isinstance(findings, list):
        raise ValueError("terminal review must contain explicit findings")
    for finding in findings:
        if (
            not isinstance(finding, dict)
            or finding.get("severity") not in {"blocking", "nonblocking"}
            or not finding.get("detail")
        ):
            raise ValueError("review finding is invalid")
        if finding["severity"] == "blocking":
            raise ValueError("current native review contains blocking findings")
    return evidence


def load_review_evidence(path: Path):
    from cafe.core.execution_artifacts import load_execution_artifact
    return load_execution_artifact(path)


def require_verified_review(context, evidence):
    """Consumers require host-observed invocation proof, not parent-only claims."""
    if not isinstance(evidence, dict) or not isinstance(evidence.get("native_observations"), dict):
        raise ValueError("host-observed native review evidence is missing")
    return require_current_review(context, evidence, native_observations=evidence["native_observations"])


def guard_execution_delivery(context, request, *, issue_dir, output_dir):
    """Check resolved scope/endpoint before an existing workflow publication hook."""
    import subprocess
    endpoint = context["delivery_endpoint"]
    args = request.get("args", {})
    if (endpoint["route"] != "pr" or args.get("base") != endpoint["target_branch"]
            or args.get("remote") != endpoint["remote"]):
        raise ValueError("publication request differs from resolved delivery endpoint")
    root = Path(context["root"])
    def git(*argv):
        return subprocess.run(["git", "-C", str(root), *argv], capture_output=True,
            text=True, check=True, timeout=20).stdout.strip()
    if git("symbolic-ref", "--short", "HEAD") != endpoint["source_branch"]:
        raise ValueError("publication source branch changed")
    from cafe.core.git_delivery import remote_identity
    if remote_identity(root, endpoint["remote"], endpoint["route"]) != endpoint["remote_identity"]:
        raise ValueError("publication remote endpoint changed")
    command = list(context["checkpoint_command"]) + ["--boundary", "before_delivery",
        "--round-id", "publication", "--parent-id", "publication-hook", "--output",
        str(output_dir / "delivery_checkpoint.json")]
    observed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    if observed.returncode != 0:
        raise ValueError("publication scope checkpoint failed")
    require_checkpoint(context, json.loads(observed.stdout), "before_delivery")
    if context.get("review_policy") == "single_native":
        require_verified_review(context, load_review_evidence(issue_dir / "execution_review.json"))
