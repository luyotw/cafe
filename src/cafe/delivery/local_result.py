"""Bind artifact-only delivery to the current clean workspace and shown output.

This route creates no action authority or integration receipt. Existing action
reviews and effect history must be resolved through their original route.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.core.workspace_artifact import verify_workspace_artifact
from cafe.core.workflow_feedback import WorkflowFeedbackLedger
from cafe.delivery.contracts import ActionSnapshot, digest
from cafe.delivery.operations import Commands


def read_bounded(path):
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("local delivery evidence exceeds the artifact limit")
    return path.read_bytes()


def requested(output):
    path = output.parent / "delivery_request.json"
    if not path.exists():
        return False
    request = json.loads(read_bounded(path))
    if not isinstance(request, dict):
        raise ValueError("delivery request must be an object")
    if request.get("mode") != "local-only":
        return False
    if request != {"mode": "local-only"}:
        raise ValueError("local-only delivery cannot contain integration options")
    return True


def result_path(issue_dir, workflow_id, step, iteration):
    identity = {"workflow_id": workflow_id, "step": step, "iteration": iteration}
    return issue_dir / "delivery" / ("local-" + digest(identity)) / "result.json"


def _require_no_actions(issue_dir, workflow_id, binding):
    records = HumanTaskRecordStore(issue_dir)
    for task in records.tasks():
        if task.workflow_id != workflow_id or task.superseded_by_task_id:
            continue
        if task.policy_id == "capability-approval" and task.status == HumanTaskStatus.PENDING:
            raise ValueError("pending capability approval must be resolved before local delivery")
        if task.step != binding.approval_step or task.policy_id != binding.approval_task:
            continue
        result = records.get_result(task.id)
        if task.status == HumanTaskStatus.PENDING or (
            result and result.payload.get("decision") in {"integrate_only", "integrate_selected"}
        ):
            raise ValueError("existing integration authority must use its original delivery route")
    manifests = list((issue_dir / "delivery").glob("*/actions.json"))
    if len(manifests) > 100:
        raise ValueError("delivery history requires bounded manual reconciliation")
    for path in manifests:
        snapshot = ActionSnapshot.model_validate_json(read_bounded(path))
        if snapshot.proposal.workflow_id == workflow_id:
            raise ValueError("existing action history must use its original delivery route")


def build_result(issue_dir, state, step_definition, binding, step, iteration, output):
    """Recompute the whole local result at presentation and final task acceptance."""
    if not requested(output):
        raise ValueError("explicit local-only delivery request is missing")
    _require_no_actions(issue_dir, state.workflow_id, binding)
    root = issue_dir.resolve().parents[2]
    commands = Commands(15)
    workspace_name = step_definition.get("workspace_input_artifact")
    required = {workspace_name, binding.publication_artifact}
    if None in required or "" in required:
        raise ValueError("local delivery requires declared workspace and PR content inputs")
    names = set(step_definition.get("input_artifacts", [])) | required
    artifacts = {}
    for name in sorted(names):
        if name == WorkflowFeedbackLedger.artifact_name:
            # Runtime settles the mutable ledger after publishing the result.
            # Its existing cycle snapshot is the exact feedback the agent saw.
            batch = output.parent / "workflow_feedback_batch.json"
            if not batch.exists():
                if name in state.artifacts:
                    raise ValueError("local delivery feedback requires its immutable cycle snapshot")
                continue
            batch = batch.resolve()
            if not batch.is_relative_to(issue_dir.resolve()):
                raise ValueError("local delivery feedback must be a workflow artifact")
            content = read_bounded(batch)
            artifacts[name] = {"path": str(batch.relative_to(issue_dir.resolve())),
                               "sha256": hashlib.sha256(content).hexdigest()}
            continue
        entry = state.artifacts.get(name)
        if entry is None:
            if name in required:
                raise ValueError(f"local delivery input is missing: {name}")
            continue
        path = Path(entry.path)
        path = (root / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_relative_to(issue_dir.resolve()):
            raise ValueError("local delivery inputs must be workflow artifacts")
        content = read_bounded(path)
        sha = hashlib.sha256(content).hexdigest()
        if entry.content_sha256 and entry.content_sha256 != sha:
            raise ValueError("local delivery input changed after publication")
        if name == workspace_name:
            verification = verify_workspace_artifact(json.loads(content), repo=root)
            if not verification.valid:
                raise ValueError("local delivery workspace is invalid: " + "; ".join(verification.reasons))
        artifacts[name] = {"path": str(path.relative_to(issue_dir.resolve())),
                           "version": entry.version, "producer": entry.updated_by, "sha256": sha}
    output = output.resolve()
    if not output.is_relative_to(issue_dir.resolve()):
        raise ValueError("local delivery output must be a workflow artifact")
    content = read_bounded(output)
    if not content.strip():
        raise ValueError("local delivery summary is empty")
    return {"version": 1, "mode": "local-only", "workflow_id": state.workflow_id,
            "step": step, "iteration": iteration, "complete": True,
            "repository": str(root), "source_oid": commands.git(root, "rev-parse", "HEAD"),
            "source_branch": commands.git(root, "symbolic-ref", "--short", "HEAD"),
            "artifacts": artifacts, "output": str(output.relative_to(issue_dir.resolve())),
            "output_sha256": hashlib.sha256(content).hexdigest(),
            "actions": {}, "remaining": []}


def validate_result(issue_dir, state, step_definition, binding, task):
    if state.workflow_id != task.workflow_id or task.step != binding.approval_step:
        raise ValueError("local delivery task belongs to another workflow or step")
    path = result_path(issue_dir, task.workflow_id, task.step, task.iteration)
    report = json.loads(read_bounded(path))
    output = issue_dir / task.step / f"iteration_{task.iteration:03d}" / "output.md"
    current = build_result(issue_dir, state, step_definition, binding, task.step, task.iteration, output)
    if report != current or f"Local delivery SHA256: {digest(report)}" not in task.prompt:
        raise ValueError("current local result does not match the shown outcome")
