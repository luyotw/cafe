"""Manager-owned terminal selection, independent of delivery result acceptance."""

from pathlib import Path
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from cafe.manager.delivery import CleanupCloseoutPlan

ARCHIVE_ARGV = ["cafe", "close", "--archive-only"]


class DeliveryResultReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    step: str = Field(min_length=1, max_length=255)
    task_id: str = Field(min_length=1, max_length=255)
    artifact: str = Field(min_length=1, max_length=255)


class CloseoutContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: StrictInt
    choice: Literal["cleanup", "archive", "leave", "pending"]
    plan: CleanupCloseoutPlan
    delivery_result: DeliveryResultReference

    @field_validator("schema_version")
    @classmethod
    def supported_version(cls, value):
        if value != 1:
            raise ValueError("unsupported Manager closeout contract")
        return value

    @model_validator(mode="after")
    def cleanup_requires_commands(self):
        if self.choice == "cleanup" and not self.plan.cleanup:
            raise ValueError("cleanup selection requires a non-empty exact plan")
        return self


def normalize_closeout_contract(value):
    from cafe.manager.delivery import validate_closeout_plan_policy

    normalized = CloseoutContract.model_validate(value).model_dump(mode="json")
    validate_closeout_plan_policy(normalized["plan"], allow_squash=None)
    return normalized


def execution_plan(contract):
    """Resolve exact commands without treating their presence as a selection."""
    closeout = contract.get("closeout_contract")
    if closeout is None:
        return contract["delivery_contract"]["closeout_plan"]
    if closeout["choice"] == "cleanup":
        return closeout["plan"]
    return {"cleanup": [{"argv": ARCHIVE_ARGV}] if closeout["choice"] == "archive" else []}


def completion_authority(records, task, result):
    matches = [
        event.context.get("completion_authority")
        for event in records.lifecycle_events()
        if event.event_type == "completed"
        and event.workflow_id == task.workflow_id
        and event.task_id == task.id
        and event.context.get("result_id") == result.id
    ]
    return matches[0] if len(matches) == 1 and isinstance(matches[0], dict) else None


def accepted_delivery_result(issue_dir, workflow_id, *, archived=False, binding=None):
    """Verify the exact accepted snapshot and receipts, including after archival."""
    issue_dir = Path(issue_dir)
    from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
    from cafe.delivery.closeout import TERMINAL_DECISIONS
    from cafe.delivery.contracts import ActionSnapshot, DeliveryBinding, digest
    from cafe.delivery.records import ActionStore
    from cafe.delivery.selection import approved_snapshot
    from cafe.delivery.verification import validate_observation

    records = HumanTaskRecordStore(issue_dir)
    tasks = [
        t
        for t in records.tasks()
        if t.workflow_id == workflow_id
        and t.trigger == "confirm_output"
        and "Action snapshot SHA256:" in t.prompt
        and not t.capability_approval
        and (binding is None or (t.step == binding["step"] and t.policy_id == binding["task_id"]))
    ]
    if not tasks:
        return None
    task = tasks[-1]
    if task.status != HumanTaskStatus.COMPLETED or task.superseded_by_task_id:
        return None
    result = records.get_result(task.id)
    if result is None or result.workflow_id != workflow_id:
        raise ValueError("archived acceptance has no correlated result")
    decision = result.payload.get("decision")
    if decision not in TERMINAL_DECISIONS:
        return None
    match = re.search(r"Action snapshot SHA256: ([0-9a-f]{64})", task.prompt)
    if not match:
        raise ValueError("archived acceptance has no exact displayed plan and snapshot")
    directory = issue_dir / "delivery" / match[1]
    snapshot = ActionSnapshot.model_validate_json((directory / "actions.json").read_bytes())
    approval_task = records.get_task(snapshot.task_id)
    binding = DeliveryBinding(
        actions_artifact="unused",
        result_artifact="unused",
        approval_step=snapshot.proposal.approval_step,
        approval_task=approval_task.policy_id,
        correction_step="unused",
    )
    if approved_snapshot(issue_dir, workflow_id=workflow_id, binding=binding) != snapshot:
        raise ValueError("archived action approval changed")
    report = json.loads((directory / "result.json").read_text())
    if (
        snapshot.digest != match[1]
        or not report.get("complete")
        or report.get("remaining")
        or report.get("snapshot") != snapshot.digest
        or f"Delivery result SHA256: {digest(report)}" not in task.prompt
    ):
        raise ValueError("archived accepted result changed")
    store = ActionStore(issue_dir, snapshot)
    expected = ["integration", *[p.id for p in snapshot.selected]]
    if set(report.get("actions", {})) != set(expected) or any(
        store.read(action) != report["actions"][action]
        or report["actions"][action].get("state") != "succeeded"
        for action in expected
    ):
        raise ValueError("archived effect receipts differ from the accepted result")
    historical = list((issue_dir / "delivery").glob("*/actions.json"))
    if len(historical) > 100:
        raise ValueError("archived delivery history requires bounded reconciliation")
    retained = {}

    def effect(row):
        return {k: v for k, v in row.items() if k not in {"snapshot", "action"}}

    for manifest in historical:
        if manifest.parent == directory:
            continue
        if manifest.stat().st_size > 1024 * 1024:
            raise ValueError("oversized archived action manifest")
        previous = ActionSnapshot.model_validate_json(manifest.read_bytes())
        if previous.proposal.workflow_id != workflow_id:
            continue
        previous_store = ActionStore(issue_dir, previous)
        for action in ["integration", *[p.id for p in previous.selected]]:
            receipt = previous_store.read(action)
            if receipt and receipt["state"] in {"unknown", "succeeded", "settled"}:
                if receipt["state"] == "unknown":
                    raise ValueError("archived delivery has an unresolved effect")
                retained[f"{previous.digest}:{action}"] = effect(receipt)
    if retained != {k: effect(v) for k, v in report.get("previous_results", {}).items()}:
        raise ValueError("archived historical receipts changed")
    verification = snapshot.proposal.verification
    if verification is not None:
        commit = report["actions"]["integration"].get("commit")
        shown = report.get("verification", {})
        if verification.tool is None:
            if shown != {
                "state": "not_required",
                "commit": commit,
                "reason": verification.not_required_reason,
            }:
                raise ValueError("archived no-verification scope changed")
        elif shown.get("state") != "succeeded":
            raise ValueError("archived verification was not completed")
        else:
            validate_observation(shown, commit)
    continuation = task.continuations.get(decision)
    if not continuation or result.payload.get("continuation", continuation) != continuation:
        raise ValueError("delivery acceptance does not select its declared outcome")
    if not archived:
        from cafe.delivery.selection import validate_complete_report, validate_effect_receipts

        if snapshot.proposal.verification is None:
            # Preserve completed pre-upgrade acceptance; pending legacy tasks
            # still pass the full completion validator and require fresh review.
            validate_effect_receipts(issue_dir, snapshot, report)
        else:
            validate_complete_report(issue_dir, snapshot, report)
    return {
        "task_id": task.id,
        "result_id": result.id,
        "snapshot": snapshot.digest,
        "decision": decision,
        "authority": completion_authority(records, task, result),
    }


def inspect_closeout(issue_dir, workflow_id, *, archived=False):
    from cafe.manager._store import load_contract
    from cafe.delivery.closeout import accepted_choice

    issue_dir = Path(issue_dir)
    contract, sha = load_contract(issue_dir, workflow_id=workflow_id)
    state = json.loads((issue_dir / "blackboard.json").read_text())
    if state.get("workflow_id") != workflow_id or state.get("current_step") != "done":
        raise ValueError("workflow completion must be verified before terminal actions")
    if "closeout_contract" in contract:
        accepted = accepted_delivery_result(
            issue_dir,
            workflow_id,
            archived=archived,
            binding=contract["closeout_contract"]["delivery_result"],
        )
        choice = contract["closeout_contract"]["choice"]
        if accepted is None:
            return {"status": "not_recorded", "selection": None}
        if choice == "pending":
            return {"status": "pending", "selection": None}
        return {
            "status": "accepted",
            "selection": {
                **accepted,
                "choice": choice,
                "contract_sha256": sha,
                "cleanup": [c["argv"] for c in contract["closeout_contract"]["plan"]["cleanup"]],
                "archive": list(ARCHIVE_ARGV),
                "authority": contract["provenance"],
            },
        }
    if contract["delivery_contract"].get("terminal_selection") != "delivery_outcome":
        return {"status": "legacy", "selection": None}
    binding = None
    if not archived:
        from cafe.playbooks.loader import PlaybookLoader, apply_issue_playbook_overrides
        from cafe.manager.delivery import delivery_result_steps

        graph = PlaybookLoader(
            project_root=issue_dir.parent.parent.parent, read_only=True, resolve_presentation=False
        ).load(state["playbook_id"])
        graph = apply_issue_playbook_overrides(graph, issue_dir / "issue.yaml")
        owners = delivery_result_steps(graph)
        if len(owners) != 1:
            raise ValueError("legacy closeout requires one declared delivery result owner")
        step = next(iter(owners))
        binding = {"step": step, "task_id": graph["steps"][step]["delivery"]["result_task"]}
    accepted = accepted_delivery_result(issue_dir, workflow_id, archived=archived, binding=binding)
    if accepted is None:
        return {"status": "not_recorded", "selection": None}
    # Historical untyped records remain readable but cannot authorize new effects.
    if not archived and accepted["authority"] != {"kind": "user_submission"}:
        return {"status": "pending", "selection": None}
    cleanup = [c["argv"] for c in contract["delivery_contract"]["closeout_plan"]["cleanup"]]
    if archived:
        from cafe.delivery.closeout import TERMINAL_DECISIONS, read_plan, plan_text
        from cafe.core.human_task_records import HumanTaskRecordStore

        plan = read_plan(issue_dir, workflow_id)
        task = HumanTaskRecordStore(issue_dir).get_task(accepted["task_id"])
        if (
            plan is None
            or plan["contract_sha256"] != sha
            or plan["cleanup"] != cleanup
            or plan_text(plan) not in task.prompt
        ):
            raise ValueError("archived terminal plan differs from its confirmed contract")
        result = {
            **accepted,
            "choice": TERMINAL_DECISIONS[accepted["decision"]],
            "contract_sha256": sha,
            "cleanup": cleanup,
            "archive": list(ARCHIVE_ARGV),
        }
    else:
        result = accepted_choice(
            issue_dir, workflow_id=workflow_id, contract_sha256=sha, cleanup=cleanup
        )
    return {"status": "accepted" if result else "pending", "selection": result}
