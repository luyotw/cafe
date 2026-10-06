"""Closed native task contexts and terminal prerequisites for host-owned workflows.

The host passes paths and durable identities, never executable playbook values or
an unbounded blackboard. Native adapters reload the effective catalog themselves.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkflowHostContext:
    issue_dir: Path
    workflow_id: str
    step: str

    def load(self):
        from cafe.catalogs.resolver import filesystem_project_roots
        from cafe.core.blackboard import BlackboardStore
        from cafe.playbooks.loader import PlaybookLoader, apply_issue_playbook_overrides

        root = self.issue_dir.parent.parent.parent
        board = BlackboardStore(self.issue_dir).load_read_only()
        if board.workflow_id != self.workflow_id:
            raise ValueError("Host context belongs to another workflow")
        playbook = PlaybookLoader(
            project_root=root,
            read_only=True,
            resolve_presentation=False,
            project_roots=filesystem_project_roots(root),
        ).load(board.playbook_id)
        return board, apply_issue_playbook_overrides(playbook, self.issue_dir / "issue.yaml")


def _delivery(context, task_store=None):
    from cafe.core.integration import integration_service

    board, playbook = context.load()
    service = integration_service(context.issue_dir, playbook, board, task_store)
    if service is None:
        raise ValueError("Delivery contract requires a delivery declaration")
    return service


def prepare_task_context(context, binding, policy, key):
    contract = binding.context_contract
    if contract is None:
        return policy.prompt, key, {}
    service = _delivery(context)
    prompt, correlation = service.task_context(context.step, policy.id, policy.prompt, key)
    snapshot = {"contract": contract, "correlation": correlation or key}
    if contract == "delivery_action":
        selected = service.records.current(service.records.read())
        task_id = selected["tasks"].get("action")
        if task_id:
            snapshot["task_id"] = task_id
    return prompt, correlation or key, snapshot


def validate_task_context(context, task, result=None, *, task_store=None):
    contract = task.context.get("contract")
    if contract is None:
        return
    if contract not in {"reviewed_delivery", "delivery_destination", "delivery_action"}:
        raise ValueError("Unknown native task context contract")
    service = _delivery(context, task_store)
    definition = service.playbook["steps"].get(task.step, {})
    bindings = [b for b in definition.get("human_tasks", []) if b["task_id"] == task.policy_id]
    if len(bindings) != 1 or bindings[0].get("context_contract") != contract:
        raise ValueError("Task context no longer matches its declared contract")
    if task.context.get("correlation") != task.handoff_key:
        raise ValueError("Task context correlation is inconsistent")
    service.associate(task)
    if result is not None:
        service.apply_result(task, result)


def task_context_matches_handoff(task, contract, current_key):
    return bool(
        task.context.get("correlation") == task.handoff_key
        and (
            task.handoff_key == current_key
            or (task.trigger == "initial" and contract.intent.value == "manual_handoff")
        )
    )


def verify_delivery(inputs, context):
    from cafe.core.automatic_steps import AutomaticExecutionResult
    from cafe.core.integration import IntegrationReviewRequired

    service = _delivery(context)
    try:
        attempt = service.verify()
        if not attempt["success"]:
            return AutomaticExecutionResult(intent="need_permission")
        if not service.records.current(service.records.read())["tasks"].get("action"):
            raise ValueError("Delivery requires a distinct human action task")
        return AutomaticExecutionResult(
            intent="workflow_complete",
            proof=service.completion_fence(),
            inspection_sequence=service.blackboard.applied_event_sequence,
        )
    except IntegrationReviewRequired:
        selected = service.records.current(service.records.read())
        if selected is not None:
            for task_id in selected["tasks"].values():
                task = service.tasks.get_task(task_id)
                if task.status.value == "pending":
                    service.tasks.cancel(
                        workflow_id=context.workflow_id,
                        task_id=task.id,
                        reason="Reviewed source changed; renewed review required",
                    )
        return AutomaticExecutionResult(intent="manual_handoff")
    except (OSError, ValueError):
        return AutomaticExecutionResult(intent="need_permission")


def publish_terminal(context, prerequisite, proof, publish, *, completed=False):
    """Consume fresh proof without inspection, under the established lock order."""
    if prerequisite is None:
        if publish is not None:
            publish(None)
        return
    if prerequisite != "verified_delivery":
        raise ValueError("Unknown terminal prerequisite")
    service = _delivery(context)
    if completed:
        if not service.completion_allowed(completed=True):
            raise ValueError("Durable completed delivery association is invalid")
        return
    if proof is None:
        raise ValueError("A fresh automatic proof is required before completion")
    if context.step != proof["step"]:
        raise ValueError("Proof belongs to another automatic owner")
    if service.blackboard.applied_event_sequence != proof["workflow_sequence"]:
        raise ValueError("Workflow changed after automatic inspection")
    expected_sequence = proof["workflow_sequence"]
    proof = proof["fence"]
    with service.tasks.transaction():
        if service.completion_fence() != proof:
            raise ValueError("Delivery proof changed before publication")
        service.records.mark_completion()
        selected = proof["selection"]
        task = service.tasks.get_task(selected["tasks"]["action"])
        if task.status.value == "pending":
            service.tasks.cancel(
                workflow_id=context.workflow_id,
                task_id=task.id,
                reason="Destination verified; no further human work required",
            )

        def validate(state):
            if state.applied_event_sequence != expected_sequence:
                raise ValueError("Workflow changed before terminal publication")
            service.validate_completion_fence(proof, state)

        if publish is not None:
            publish(validate)


def terminal_recovery_target(prerequisite, steps):
    executor = {"verified_delivery": "verify_delivery"}.get(prerequisite)
    targets = [
        name
        for name, definition in steps.items()
        if definition.get("assignee_type") == "auto"
        and definition.get("automatic", {}).get("executor") == executor
    ]
    return targets[0] if executor is not None and len(targets) == 1 else None
