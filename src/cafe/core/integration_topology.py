"""Resolve delivery identities from ordinary declared graph relationships."""

from types import SimpleNamespace
from typing import Any, Mapping


def delivery_topology(identity: Mapping[str, Any], steps: Mapping[str, Any]) -> SimpleNamespace:
    from cafe.core.playbook import IntegrationDeclaration

    declaration = IntegrationDeclaration.model_validate(identity)
    values = declaration.model_dump()

    def binding(step, task=None, contract=None):
        definition = steps.get(step, {})
        candidates = [
            b
            for b in definition.get("human_tasks", [])
            if (task is None or b["task_id"] == task)
            and (contract is None or b.get("context_contract") == contract)
        ]
        if len(candidates) != 1:
            raise ValueError("Delivery needs exactly one matching task context contract")
        return candidates[0]

    def producer(artifact):
        owners = [
            name
            for name, config in steps.items()
            if artifact in (config.get("output_artifact"), config.get("workspace_artifact"))
        ]
        if len(owners) != 1:
            raise ValueError("Delivery artifact needs a unique declared producer")
        return owners[0]

    review = binding(declaration.review_step, declaration.review_task, "reviewed_delivery")
    if any(
        review["outcomes"].get(d) != declaration.selection_step
        for d in declaration.accepted_decisions
    ):
        raise ValueError("Accepted review must continue to the declared destination selection")
    selection = binding(
        declaration.selection_step, declaration.selection_task, "delivery_destination"
    )
    action_step = selection["outcomes"].get("confirm")
    action = binding(action_step, contract="delivery_action")
    if any(
        steps.get(name, {}).get("assignee_type") != "human"
        for name in (declaration.selection_step, action_step)
    ):
        raise ValueError("Delivery confirmation and action must remain human owned")
    targets = {action["outcomes"].get(d) for d in ("performed", "already_performed", "blocked")}
    if len(targets) != 1:
        raise ValueError("Every delivery report must continue through the same verifier")
    verifier = targets.pop()
    definition = steps.get(verifier, {})
    if (
        definition.get("assignee_type") != "auto"
        or definition.get("automatic", {}).get("executor") != "verify_delivery"
    ):
        raise ValueError("Delivery reports require the fixed automatic read-only verifier")
    from cafe.core.automatic_steps import default_automatic_executor_registry

    default_automatic_executor_registry().validate_inputs(
        "verify_delivery", definition["automatic"].get("inputs", {})
    )
    routes = definition.get("on", {})
    if (
        routes.get("need_permission") != action_step
        or routes.get("workflow_complete") not in {*steps, "_done"}
        or routes.get("manual_handoff") not in steps
    ):
        raise ValueError("Verifier must declare verified, blocked and renewed-review routes")
    if (
        steps[action_step].get("resume_intent") != "await_agent"
        or steps[action_step].get("on", {}).get("await_agent") != verifier
    ):
        raise ValueError("Pending human delivery must declare its automatic resume route")
    if len({declaration.review_step, declaration.selection_step, action_step, verifier}) != 4:
        raise ValueError("Delivery owners must be distinct")
    validate_verified_continuation(
        routes["workflow_complete"],
        steps,
        forbidden={declaration.review_step, declaration.selection_step, action_step, verifier},
    )
    values.update(
        source_step=producer(declaration.source_artifact),
        delivery_step=producer(declaration.delivery_artifact),
        action_step=action_step,
        action_task=action["task_id"],
        verification_step=verifier,
        correction_step=routes["manual_handoff"],
        verified_continuation=routes["workflow_complete"],
    )
    return SimpleNamespace(**values)


def validate_verified_continuation(start, steps, *, forbidden):
    """Accept finite ordinary owner paths; reject unsupported graphs at loading."""
    visiting, visited = set(), set()

    def visit(name):
        if name in {"done", "_done"}:
            return
        if name in forbidden or name in visiting:
            raise ValueError("Verified continuation must not cycle or re-enter delivery owners")
        if name in visited:
            return
        definition = steps.get(name)
        if definition is None or definition.get("assignee_type", "agent") not in {
            "auto",
            "human",
            "agent",
        }:
            raise ValueError(
                "Verified continuation requires an ordinary auto, human or agent owner"
            )
        visiting.add(name)
        targets = set()
        for intent, target in definition.get("on", {}).items():
            if target == "_user" or (
                target == name
                and intent
                in {"confirm_output", "need_permission", "need_clarification", "no_changes_needed"}
            ):
                continue
            targets.add(target)
        for binding in definition.get("human_tasks", []):
            targets.update(binding["outcomes"].values())
        if not targets or definition.get("allowed_goto"):
            raise ValueError(
                "Verified continuation needs terminating declared edges without discretionary goto"
            )
        for target in targets:
            visit(target)
        visiting.remove(name)
        visited.add(name)

    visit(start)
