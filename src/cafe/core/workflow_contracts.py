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
    board, playbook = context.load()
    if board.current_step != context.step and not (
        board.current_step == "user" and board.handoff_contract.from_step == context.step
    ):
        raise ValueError("Terminal owner is not the current workflow position")
    if proof.get("resume") is not None:
        if (
            context.step != proof["step"]
            or board.applied_event_sequence != proof["workflow_sequence"]
            or pending_terminal_reinspection(context, proof["fence"]) != proof["resume"]
        ):
            raise ValueError("Terminal recovery no longer matches its exact completed edge")
    else:
        position, witness = _proof_position(context, proof, board, playbook)
        if position != context.step or witness is None:
            raise ValueError("Terminal edge has no matching verified owner completion")
    expected_sequence = board.applied_event_sequence
    expected_graph = proof["graph"]
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
            _, current_playbook = context.load()
            if _graph_identity(current_playbook) != expected_graph:
                raise ValueError("Workflow graph changed before terminal publication")
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


def _graph_identity(playbook):
    import hashlib
    import json

    return hashlib.sha256(
        json.dumps(
            {key: playbook.get(key) for key in ("steps", "integration", "terminal_prerequisite")},
            sort_keys=True,
        ).encode()
    ).hexdigest()


def automatic_proof(context, fence, *, resume=None):
    """Journal a proof with its exact effective graph, not a reusable capability."""
    _, playbook = context.load()
    return {
        "fence": fence,
        "step": context.step,
        "graph": _graph_identity(playbook),
        "resume": resume,
    }


def _route(definition, intent):
    from cafe.core.status_codes import PhaseStatusCode, transition_map_key

    try:
        intent = transition_map_key(PhaseStatusCode(intent))
    except ValueError:
        if intent.startswith("BATON_"):
            intent = intent[6:].lower()
    routes = definition.get("on", {})
    return routes.get(intent, routes.get("default"))


def _human_edge(context, playbook, step, task_id, result_id, task_store=None):
    from cafe.core.human_task_records import HumanTaskRecordStore

    records = task_store or HumanTaskRecordStore(context.issue_dir)
    task = records.get_task(task_id)
    result = records.get_result(task.id)
    if (
        task.workflow_id != context.workflow_id
        or task.step != step
        or task.status.value != "completed"
        or result is None
        or result.id != result_id
    ):
        raise ValueError("Terminal answer belongs to another owner or result")
    bindings = [
        b
        for b in playbook["steps"][step].get("human_tasks", [])
        if b["task_id"] == task.policy_id and b["trigger"] == task.trigger
    ]
    if len(bindings) != 1:
        raise ValueError("Human result has no matching declared binding")
    return bindings[0]["outcomes"].get(result.payload.get("decision"))


def _human_terminal(context, playbook, step, task_id, result_id, task_store=None):
    if _human_edge(context, playbook, step, task_id, result_id, task_store) != "_done":
        raise ValueError("Human result has no declared terminal edge")


def _proof_position(context, proof, board, playbook, *, stop=None, task_store=None):
    """Follow only declared lifecycle edges since this immutable observation."""
    if proof.get("graph") != _graph_identity(playbook):
        raise ValueError("Verified continuation declaration changed")
    index = proof["event_index"]
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ValueError("Proof has no valid lifecycle position")
    if proof["step"] != terminal_recovery_target("verified_delivery", playbook["steps"]):
        raise ValueError("Proof belongs to another automatic owner")
    events = board.events
    saved = events[index].data.get("terminal_proof")
    expected = {k: v for k, v in proof.items() if k not in {"event_index", "workflow_sequence"}}
    if saved != expected:
        raise ValueError("Proof has no matching durable automatic completion")
    position = proof["step"]
    witness = None
    seen = {position}
    # The proof's own automatic completion is also a terminal-edge witness.
    for event in events[index:stop]:
        data = event.data
        if event.event_type in {
            "automatic_step_completed",
            "step_completed",
            "single_step_completed",
        }:
            if data.get("step") != position:
                raise ValueError("Completion belongs to another continuation owner")
            intent = data.get("intent", data.get("status_code", ""))
            if _route(playbook["steps"][position], intent) in {"done", "_done"}:
                witness = {"step": position, "intent": intent}
        elif event.event_type in {"transition", "transition_recovered"}:
            source, target = data.get("from"), data.get("to")
            if (
                source != position
                or target in seen
                or _route(
                    playbook["steps"][position],
                    data.get("transition_intent") or data.get("status_code", ""),
                )
                != target
            ):
                raise ValueError("Intervening transition invalidated the verified continuation")
            position = target
            seen.add(position)
            witness = None
        elif event.event_type == "human_task_completed":
            if data.get("step") != position or data.get("supervisor_handoff"):
                raise ValueError("Human continuation no longer belongs to the verified path")
            target = data.get("to_step")
            bindings = [
                b
                for b in playbook["steps"][position].get("human_tasks", [])
                if b["task_id"] == data.get("task_id") and b["trigger"] == data.get("trigger")
            ]
            if (
                len(bindings) != 1
                or target in seen
                or _human_edge(
                    context,
                    playbook,
                    position,
                    data.get("human_task_id"),
                    data.get("result_id"),
                    task_store,
                )
                != target
            ):
                raise ValueError("Human continuation has no matching declared edge")
            position = target
            seen.add(position)
            witness = None
    return position, witness


def terminal_reinspection(context, *, task_id=None, result_id=None, task_store=None):
    """Checkpoint a completed declared edge before normal prerequisite re-entry."""
    board, playbook = context.load()
    for index in range(len(board.events) - 1, -1, -1):
        saved = board.events[index].data.get("terminal_proof")
        if saved is None:
            continue
        proof = dict(saved, event_index=index)
        if proof.get("resume") is not None:
            resumed = pending_terminal_reinspection(context, proof["fence"], task_store=task_store)
            if resumed != proof["resume"]:
                raise ValueError("Terminal recovery checkpoint changed")
            return board.events[resumed["checkpoint_event"]].data["terminal_resume"]
        position, witness = _proof_position(context, proof, board, playbook, task_store=task_store)
        if position != context.step:
            raise ValueError("Terminal owner is outside the verified continuation")
        if task_id is not None:
            _human_terminal(context, playbook, context.step, task_id, result_id, task_store)
            witness = {"step": context.step, "human_task_id": task_id, "result_id": result_id}
        if witness is None:
            raise ValueError("Continuation work has no durable terminal completion")
        # Do not consume old destination facts. The normal verifier will inspect
        # again; only the exact selected/reviewed identity can resume this edge.
        return {"proof": proof, "completion": witness}
    raise ValueError("No durable verified continuation is available")


def pending_terminal_reinspection(context, fence, *, task_store=None):
    """Recover a checkpoint only for its original selection and terminal edge."""
    board, playbook = context.load()
    for index in range(len(board.events) - 1, -1, -1):
        checkpoint = board.events[index].data.get("terminal_resume")
        if checkpoint is None:
            continue
        try:
            proof = checkpoint["proof"]
            if proof["fence"]["selection"] != fence["selection"]:
                return None
            anchor = next(
                i
                for i in range(proof["event_index"] + 1, index + 1)
                if board.events[i].data.get("terminal_resume") == checkpoint
            )
            position, witness = _proof_position(
                context, proof, board, playbook, stop=anchor, task_store=task_store
            )
            completion = checkpoint["completion"]
            if position != completion["step"]:
                return None
            if "human_task_id" in completion:
                _human_terminal(
                    context,
                    playbook,
                    position,
                    completion["human_task_id"],
                    completion["result_id"],
                    task_store,
                )
            elif witness != completion:
                return None
        except (ValueError, KeyError, IndexError, TypeError, StopIteration):
            return None
        position = context.step
        action = playbook["steps"][context.step]["on"]["need_permission"]
        for event in board.events[anchor + 1 :]:
            data = event.data
            if event.event_type in {"transition", "transition_recovered"}:
                target = data.get("to")
                if (
                    data.get("source") == "workflow.prerequisite_recovery"
                    and data.get("terminal_resume") == checkpoint
                    and data.get("from") == position == target == context.step
                ):
                    continue
                if (
                    data.get("from") != position
                    or target not in {action, context.step}
                    or _route(
                        playbook["steps"][position],
                        data.get("transition_intent") or data.get("status_code", ""),
                    )
                    != target
                ):
                    return None
                position = target
            elif event.event_type == "human_task_completed":
                if data.get("step") != action or data.get("to_step") != context.step:
                    return None
                position = context.step
            elif (
                event.event_type == "automatic_step_completed" and data.get("step") != context.step
            ):
                return None
        if position != context.step:
            return None
        return {"checkpoint_event": index, "completion": completion}
    return None
