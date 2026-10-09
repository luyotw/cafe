"""Correlate shown immutable proposals with durable workflow task results."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.core.packet_io import atomic_write_bytes, canonical_json
from cafe.delivery.contracts import ActionProposal, ActionSnapshot, approve_selection, digest


def proposal_path(issue_dir, task_id):
    return issue_dir / "delivery" / "proposals" / f"{digest({'task_id': task_id})}.json"


def save_shown_proposal(issue_dir: Path, task, proposal: ActionProposal):
    path = proposal_path(issue_dir, task.id)
    content = canonical_json(proposal.model_dump(mode="json"))
    if path.exists() and path.read_bytes() != content:
        raise ValueError("shown action proposal is immutable")
    atomic_write_bytes(path, content)


def validate_reviewed_artifact(issue_dir, proposal):
    if not proposal.reviewed_artifact:
        return
    path = (issue_dir / proposal.reviewed_artifact).resolve()
    if (
        not path.is_relative_to(issue_dir.resolve())
        or hashlib.sha256(path.read_bytes()).hexdigest() != proposal.reviewed_artifact_sha256
    ):
        raise ValueError("reviewed artifact changed; fresh action review is required")


def approved_snapshot(issue_dir: Path, *, workflow_id: str, binding) -> ActionSnapshot:
    records = HumanTaskRecordStore(issue_dir)
    tasks = [
        task
        for task in records.tasks()
        if task.workflow_id == workflow_id
        and task.step == binding.approval_step
        and task.policy_id == binding.approval_task
    ]
    if not tasks:
        raise ValueError("explicit action review is missing")
    task = tasks[-1]
    if task.status != HumanTaskStatus.COMPLETED or task.superseded_by_task_id:
        raise ValueError("current action review is not completed")
    result = records.get_result(task.id)
    if result is None or result.workflow_id != workflow_id:
        raise ValueError("action review has no correlated result")
    proposal = ActionProposal.model_validate_json(proposal_path(issue_dir, task.id).read_bytes())
    if f"Action proposal SHA256: {proposal.digest}" not in task.prompt:
        raise ValueError("task did not display this action proposal")
    if proposal.capability_review is not None:
        from cafe.delivery.approvals import review_text

        if review_text(proposal.capability_review) not in task.prompt:
            raise ValueError("task did not display the reviewed host capability boundary")
    validate_reviewed_artifact(issue_dir, proposal)
    return approve_selection(
        proposal,
        {
            "workflow_id": task.workflow_id,
            "step": task.step,
            "iteration": task.iteration,
            "task_id": task.id,
            "result_id": result.id,
            "proposal_digest": proposal.digest,
            "decision": result.payload.get("decision"),
            "feedback": result.payload.get("feedback", ""),
        },
    )


def validate_source_identity(issue_dir, proposal):
    if not proposal.reviewed_artifact:
        return
    from cafe.delivery.operations import Commands

    commands = Commands(15)
    root = issue_dir.resolve().parents[2]
    if (
        commands.git(root, "rev-parse", "HEAD") != proposal.source_oid
        or commands.git(root, "symbolic-ref", "--short", "HEAD") != proposal.source_branch
    ):
        raise ValueError("reviewed source changed; fresh action review is required")


def validate_snapshot_authority(issue_dir: Path, snapshot: ActionSnapshot):
    """A caller-supplied manifest cannot manufacture the correlated human decision."""
    from cafe.delivery.contracts import DeliveryBinding

    records = HumanTaskRecordStore(issue_dir)
    task = records.get_task(snapshot.task_id)
    binding = DeliveryBinding(
        actions_artifact="unused",
        result_artifact="unused",
        approval_step=snapshot.proposal.approval_step,
        approval_task=task.policy_id,
        correction_step="unused",
    )
    current = approved_snapshot(
        issue_dir, workflow_id=snapshot.proposal.workflow_id, binding=binding
    )
    if current != snapshot:
        raise ValueError("action snapshot no longer matches current authority")
    validate_source_identity(issue_dir, snapshot.proposal)


def snapshot_from_args(args) -> ActionSnapshot:
    raw = args.get("snapshot")
    if not isinstance(raw, str) or len(raw.encode()) > 1024 * 1024:
        raise ValueError("invalid action snapshot argument")
    return ActionSnapshot.model_validate(json.loads(raw))


def validate_task_response(issue_dir, step_definition, policy, task, raw_payload):
    """The existing response entry delegates delivery rules without a generic registry."""
    declaration = step_definition.get("delivery")
    if not declaration:
        return
    from cafe.core.human_tasks import _parse_payload
    from cafe.delivery.contracts import DeliveryBinding

    binding = DeliveryBinding.model_validate(declaration)
    if task is None:
        if policy.id in {binding.approval_task, binding.result_task}:
            raise ValueError(
                "The exact host-produced action/result task is required before this response."
            )
        return
    validate_response(issue_dir, binding, task, _parse_payload(policy, raw_payload))


def validate_response(issue_dir, binding, task, payload):
    """Validate only the declared delivery tasks; no universal terminal validator."""
    if not isinstance(payload, dict):
        raise ValueError("use the declared response format")
    if task.policy_id == binding.approval_task and payload.get("decision") in {
        "integrate_selected",
        "integrate_only",
    }:
        proposal = ActionProposal.model_validate_json(
            proposal_path(issue_dir, task.id).read_bytes()
        )
        if f"Action proposal SHA256: {proposal.digest}" not in task.prompt:
            raise ValueError("shown proposal bytes changed")
        if proposal.capability_review is not None:
            from cafe.delivery.approvals import review_text

            if review_text(proposal.capability_review) not in task.prompt:
                raise ValueError("shown host capability review changed")
        validate_reviewed_artifact(issue_dir, proposal)
        validate_source_identity(issue_dir, proposal)
        approve_selection(
            proposal,
            {
                "workflow_id": task.workflow_id,
                "step": task.step,
                "iteration": task.iteration,
                "proposal_digest": proposal.digest,
                "task_id": task.id,
                "result_id": "prospective",
                "decision": payload.get("decision"),
                "feedback": payload.get("feedback", ""),
            },
        )
    from cafe.delivery.closeout import TERMINAL_DECISIONS, validate_choice

    if task.policy_id == binding.result_task and payload.get("decision") in TERMINAL_DECISIONS:
        snapshot = approved_snapshot(issue_dir, workflow_id=task.workflow_id, binding=binding)
        validate_snapshot_authority(issue_dir, snapshot)
        path = issue_dir / "delivery" / snapshot.digest / "result.json"
        report = json.loads(path.read_text())
        validate_complete_report(issue_dir, snapshot, report)
        if (
            report.get("snapshot") != snapshot.digest
            or not report.get("complete")
            or report.get("remaining")
            or f"Delivery result SHA256: {digest(report)}" not in task.prompt
        ):
            raise ValueError("current complete result does not match the shown outcome")
        validate_choice(issue_dir, snapshot, task, payload["decision"])


def validate_complete_report(issue_dir, snapshot, report):
    validate_effect_receipts(issue_dir, snapshot, report)
    from cafe.delivery.verification import validate_verification

    validate_verification(issue_dir, snapshot, report)


def validate_effect_receipts(issue_dir, snapshot, report):
    """Completion derives from all current and retained effect receipts, never flags alone."""
    from cafe.delivery.records import ActionStore

    validate_snapshot_authority(issue_dir, snapshot)
    store = ActionStore(issue_dir, snapshot)
    expected = ["integration", *[item.id for item in snapshot.selected]]

    def effect(row):
        return {k: v for k, v in row.items() if k not in {"snapshot", "action"}}

    if (
        report.get("snapshot") != snapshot.digest
        or not report.get("complete")
        or report.get("remaining")
    ):
        raise ValueError("delivery has no current complete outcome")
    if set(report.get("actions", {})) != set(expected) or any(
        not store.read(action)
        or store.read(action)["state"] != "succeeded"
        or effect(store.read(action)) != effect(report["actions"][action])
        for action in expected
    ):
        raise ValueError("current per-action receipts do not prove the shown result")
    history = list((issue_dir / "delivery").glob("*/actions.json"))
    if len(history) > 100:
        raise ValueError("delivery history requires bounded manual reconciliation")
    retained = {}
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
            if receipt and receipt["state"] in {"unknown", "succeeded", "settled"}:
                if receipt["state"] not in {"succeeded", "settled"}:
                    raise ValueError("an earlier effect is still unknown")
                retained[f"{previous.digest}:{action}"] = effect(receipt)
    shown = {key: effect(value) for key, value in report.get("previous_results", {}).items()}
    if retained != shown:
        raise ValueError("retained effects differ from the shown result")
