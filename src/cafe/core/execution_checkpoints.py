"""Generic scope checkpoints and content-bound execution evidence."""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import re
import uuid

from cafe.core.file_scope import (
    collect_changes,
    compare_scope,
    content_snapshot,
    validate_scope_paths,
)

BOUNDARIES = frozenset({"before_review", "resume", "before_delivery"})


def review_read_only_enforcement(configuration):
    """Keep role intent separate from the confirmed permission mechanism.

    Missing fields retain the native restrictions of existing contracts.
    Instruction-only review never establishes OS-enforced read-only access.
    """
    enforcement = configuration.get("read_only_enforcement", "sandbox")
    if enforcement not in ("sandbox", "instruction_only"):
        raise ValueError("unsupported reviewer read-only enforcement")
    return enforcement


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


@dataclass(frozen=True)
class CheckpointObservation:
    """Process-local content observation; reuse only within one validation boundary."""

    context_digest: str
    source_revision: str | None
    snapshot: str | None
    passed: bool
    findings: tuple


def observe_checkpoint(context, *, source_revision=None):
    if source_revision is not None and (
        not isinstance(source_revision, str) or
        not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", source_revision)
    ):
        raise ValueError("selected checkpoint source requires an immutable commit identity")
    digest = context_digest(context)
    root = Path(context["root"])
    changes = collect_changes(root, context["baseline_commit"], source_revision=source_revision)
    result = compare_scope(changes, context["paths"], preexisting=context.get("preexisting", []))
    snapshot = None
    if result.passed:
        try:
            snapshot = content_snapshot(root, changes, context["paths"], source_revision=source_revision)
        except (OSError, ValueError) as exc:
            from cafe.core.file_scope import ScopeResult

            result = ScopeResult(False, ({"reason": "evidence_unavailable", "detail": str(exc)},))
    return CheckpointObservation(digest, source_revision, snapshot, result.passed, result.findings)


def checkpoint(context, boundary, *, round_id, parent_id, source_revision=None, observation=None):
    if boundary not in BOUNDARIES or not round_id or not parent_id:
        raise ValueError("checkpoint requires a declared boundary and invocation identity")
    current = observation if observation is not None else observe_checkpoint(context, source_revision=source_revision)
    if (not isinstance(current, CheckpointObservation) or
            current.context_digest != context_digest(context) or current.source_revision != source_revision):
        raise ValueError("checkpoint observation belongs to another authority or source")
    return {
        "version": 1,
        "receipt_id": str(uuid.uuid4()),
        "context_digest": current.context_digest,
        "authority_digest": context["authority_digest"],
        "revision": context["revision"],
        "identity": context["identity"],
        "boundary": boundary,
        "round_id": round_id,
        "parent_id": parent_id,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "snapshot": current.snapshot,
        "passed": current.passed,
        "findings": list(current.findings),
    }


def require_checkpoint(context, receipt, boundary, *, source_revision=None, observation=None):
    if (
        not isinstance(receipt, dict)
        or receipt.get("version") != 1
        or receipt.get("passed") is not True
        or receipt.get("boundary") != boundary
        or receipt.get("context_digest") != context_digest(context)
        or any(receipt.get(key) != context[key] for key in ("authority_digest", "revision", "identity"))
    ):
        raise ValueError("missing, failed or stale execution checkpoint")
    if not all(
        receipt.get(k) for k in ("receipt_id", "round_id", "parent_id", "observed_at", "snapshot")
    ):
        raise ValueError("checkpoint lacks invocation-bound evidence")
    current = checkpoint(
        context, boundary, round_id=receipt["round_id"], parent_id=receipt["parent_id"],
        source_revision=source_revision, observation=observation,
    )
    if not current["passed"] or current["snapshot"] != receipt["snapshot"]:
        raise ValueError("execution checkpoint does not cover current content")
    return receipt


def load_execution_context(path: Path):
    from cafe.core.execution_artifacts import load_execution_artifact
    context = load_execution_artifact(path)
    context_digest(context)
    return context


def require_current_review(context, evidence, *, native_observations=None, source_revision=None, observation=None):
    """Accept exactly one independent terminal invocation of current content."""
    if not isinstance(evidence, dict) or evidence.get("version") != 1:
        raise ValueError("native review evidence is missing")
    receipt = require_checkpoint(context, evidence.get("checkpoint"), "before_review",
                                 source_revision=source_revision, observation=observation)
    if evidence.get("round_id") != receipt["round_id"]:
        raise ValueError("review round does not match its checkpoint")
    invocations = evidence.get("invocations")
    if not isinstance(invocations, list) or len(invocations) != 1:
        raise ValueError("review requires exactly one native invocation per round")
    reviewer = invocations[0]
    if not isinstance(reviewer, dict):
        raise ValueError("native reviewer evidence is invalid")
    enforcement = review_read_only_enforcement(context.get("review_configuration") or {})
    if enforcement == "instruction_only" and native_observations is None:
        raise ValueError("instruction-only review requires host-observed permission evidence")
    if native_observations is not None:
        observed = native_observations.get("observations", [])
        if len(observed) != 1 or native_observations.get("parent_id") != receipt["parent_id"]:
            raise ValueError("native invocation evidence is missing or differs from the parent")
        actual = observed[0]
        if (enforcement == "instruction_only" or "read_only_enforcement" in actual) and (
            actual.get("read_only_enforcement") != enforcement
            or actual.get("sandbox_enabled") is not (enforcement == "sandbox")
        ):
            raise ValueError("review permission evidence differs from confirmed enforcement")
        if (actual.get("receipt_id") != receipt["receipt_id"] or
                actual.get("reviewer_id") != reviewer.get("reviewer_id") or
                actual.get("configuration") != context["review_configuration"] or
                actual.get("terminal") != "result" or actual.get("exit_status") != 0):
            raise ValueError("review is not associated with a successful checkpointed native invocation")
        if (actual.get("findings") != reviewer.get("findings") or
                actual.get("targeted_tests") != reviewer.get("targeted_tests")):
            raise ValueError("review conclusion differs from the independent native result")
        if not isinstance(actual.get("observed_at"), str):
            raise ValueError("native invocation has no trusted observation time")
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
    tests = reviewer.get("targeted_tests")
    if (not isinstance(reviewer.get("result_reference"), str) or not reviewer["result_reference"].strip()
            or not isinstance(tests, list) or not tests
            or any(not isinstance(test, str) or not test.strip() for test in tests)):
        raise ValueError("native review lacks terminal result or targeted test evidence")
    findings = reviewer.get("findings")
    if not isinstance(findings, list):
        raise ValueError("terminal review must contain explicit findings")
    for finding in findings:
        if (
            not isinstance(finding, dict)
            or not isinstance(finding.get("severity"), str)
            or finding["severity"] not in {"blocking", "nonblocking"}
            or not isinstance(finding.get("detail"), str) or not finding["detail"].strip()
        ):
            raise ValueError("review finding is invalid")
        if finding["severity"] == "blocking":
            raise ValueError("current native review contains blocking findings")
    return evidence


def load_review_evidence(path: Path):
    from cafe.core.execution_artifacts import load_execution_artifact
    return load_execution_artifact(path)


def require_verified_review(context, evidence, *, source_revision=None, observation=None):
    """Consumers require host-observed invocation proof, not parent-only claims."""
    if not isinstance(evidence, dict) or not isinstance(evidence.get("native_observations"), dict):
        raise ValueError("host-observed native review evidence is missing")
    return require_current_review(context, evidence, native_observations=evidence["native_observations"],
                                  source_revision=source_revision, observation=observation)


def guard_execution_delivery(context, request, *, issue_dir, output_dir):
    """Check resolved scope/endpoint before an existing workflow publication hook."""
    import subprocess
    from cafe.core.git_delivery import require_publication_target
    require_publication_target(context, request.get("args", {}))
    command = list(context["checkpoint_command"]) + ["--boundary", "before_delivery",
        "--round-id", "publication", "--parent-id", "publication-hook", "--output",
        str(output_dir / "delivery_checkpoint.json")]
    observed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    if observed.returncode != 0:
        raise ValueError("publication scope checkpoint failed")
    selected = request["args"]["head_oid"]
    current = observe_checkpoint(context, source_revision=selected)
    require_checkpoint(context, json.loads(observed.stdout), "before_delivery",
                       source_revision=selected, observation=current)
    if context.get("review_policy") == "single_native":
        require_verified_review(context, load_review_evidence(issue_dir / "execution_review.json"),
                                source_revision=selected, observation=current)


class DeliveryReadinessError(ValueError):
    """Specific machine-schema correction versus changed execution authority."""
    def __init__(self, category, detail):
        self.category = category
        super().__init__(detail)


def validate_delivery_readiness(context, readiness, *, review_evidence=None,
                                source_revision=None, observation=None):
    """Producer and consumer share complete, current before_delivery evidence."""
    fields = {'version', 'receipt_id', 'context_digest', 'authority_digest', 'revision',
              'identity', 'boundary', 'round_id', 'parent_id', 'observed_at', 'snapshot',
              'passed', 'findings'}
    if not isinstance(readiness, dict) or not isinstance(readiness.get('checkpoint'), dict):
        raise DeliveryReadinessError('schema', 'delivery readiness requires the full checkpoint object')
    saved = readiness['checkpoint']
    if not fields.issubset(saved) or not isinstance(saved.get('findings'), list):
        raise DeliveryReadinessError('schema', 'delivery readiness checkpoint fields are incomplete')
    try:
        datetime.fromisoformat(saved['observed_at'])
    except (TypeError, ValueError):
        raise DeliveryReadinessError('schema', 'delivery checkpoint observation time is malformed')
    if (readiness.get('authority_digest') != context['authority_digest']
            or readiness.get('endpoint') != context.get('delivery_endpoint')
            or not isinstance(context.get('delivery_endpoint'), dict)
            or ('route' in readiness and readiness['route'] != context['delivery_endpoint'].get('route'))):
        raise DeliveryReadinessError('authority', 'delivery readiness differs from the confirmed authority or endpoint')
    require_checkpoint(context, saved, 'before_delivery', source_revision=source_revision, observation=observation)
    if saved['findings']:
        raise ValueError('passing delivery checkpoint contains unresolved findings')
    if context.get('review_policy') == 'single_native':
        require_verified_review(context, review_evidence, source_revision=source_revision, observation=observation)
    return readiness


def prepare_delivery_readiness(context, saved_checkpoint, *, review_evidence=None, **references):
    """Serialize the actual saved object; receipt/reference alone never suffices."""
    if set(references) - {'review_reference', 'checkpoint_reference'}:
        raise ValueError('unsupported readiness reference')
    readiness = dict(version=1, authority_digest=context['authority_digest'],
                     endpoint=context['delivery_endpoint'], checkpoint=saved_checkpoint, **references)
    return validate_delivery_readiness(context, readiness, review_evidence=review_evidence)


def load_or_restore_delivery_readiness(context, path, *, review_evidence=None):
    """Under the caller's workspace lease, fill only a missing checkpoint object.

    The original invocation's unique saved checkpoint must pass the same current
    validator. This changes no task, authority, completion receipt or owner.
    """
    from itertools import islice
    from cafe.core.execution_artifacts import bounded_execution_json, load_execution_artifact, MAX_EXECUTION_ARTIFACT_BYTES
    from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
    from cafe.core.packet_io import atomic_write_bytes
    from cafe.core.phase_state_mixin import load_iteration_control_metadata

    readiness = load_execution_artifact(path)
    try:
        return validate_delivery_readiness(context, readiness, review_evidence=review_evidence)
    except DeliveryReadinessError as error:
        if error.category != 'schema' or not isinstance(readiness, dict):
            raise
        omission = error
    def read_original():
        if path.is_symlink() or not path.is_file():
            raise ValueError('delivery readiness is not a regular file')
        with path.open('rb') as handle:
            content = handle.read(MAX_EXECUTION_ARTIFACT_BYTES + 1)
        if len(content) > MAX_EXECUTION_ARTIFACT_BYTES:
            raise ValueError('delivery readiness exceeds durable reader capacity')
        return content
    original = read_original()
    if json.loads(original) != readiness:
        raise ValueError('delivery readiness changed during checkpoint restoration')
    directory = path.parent
    issue = directory.parents[1]
    if any(task.status is HumanTaskStatus.PENDING for task in HumanTaskRecordStore(issue).tasks()):
        raise omission
    metadata = load_iteration_control_metadata(directory / 'iteration.json')
    session = metadata.get('session_id')
    if (not isinstance(session, str) or not session
            or type(metadata.get('iteration')) is not int
            or directory.name != f"iteration_{metadata.get('iteration', 0):03d}"
            or metadata.get('workflow_id') not in {None, context['identity']['workflow_id']}
            or issue.name != context['identity']['issue_name']):
        raise omission
    partial = readiness.get('checkpoint')
    if partial is not None and not isinstance(partial, dict):
        raise omission
    paths = list(islice(directory.glob('*checkpoint*.json'), 129))
    if len(paths) > 128:
        raise omission
    candidates = {}
    for candidate_path in paths:
        candidate = load_execution_artifact(candidate_path)
        if not isinstance(candidate, dict) or candidate.get('boundary') != 'before_delivery':
            continue
        candidates[json.dumps(candidate, sort_keys=True)] = candidate
    if len(candidates) != 1:
        raise omission
    saved = next(iter(candidates.values()))
    if saved.get('parent_id') != session or any(saved.get(k) != v for k, v in (partial or {}).items()):
        raise omission
    target = dict(readiness, checkpoint=saved)
    validate_delivery_readiness(context, target, review_evidence=review_evidence)
    content = bounded_execution_json(target)
    if read_original() != original:
        raise ValueError('delivery readiness changed before checkpoint restoration')
    atomic_write_bytes(path, content)
    return target
