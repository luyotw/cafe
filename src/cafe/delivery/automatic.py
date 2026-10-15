"""Native automatic adapter for the existing reviewed delivery service."""

from __future__ import annotations

import json

from cafe.core.automatic_steps import AutomaticExecutionResult
from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.core.packet_io import atomic_write_bytes, canonical_json
from cafe.core.workflow_models import StepWaiting
from cafe.core.workspace_inputs import validate_workspace_inputs
from cafe.core.workspace_lock import workspace_execution_lock
from cafe.delivery.contracts import DeliveryBinding
from cafe.delivery.prepared import prepared_request
from cafe.delivery.presentation import (
    DeliveryPresentation,
    materialize_task,
    present_action,
    present_outcome,
)
from cafe.delivery.runner import execute_delivery


def validate_inputs(inputs):
    if inputs:
        raise ValueError(
            "delivery executor uses the declared preparation-time choices; inputs must be empty"
        )


def _presentation(context):
    directory = context.issue_dir / context.step
    path = directory / "systematic" / f"{context.execution_id}.json"
    if path.exists():
        iteration = json.loads(path.read_text())["iteration"]
    else:
        iteration = (
            max((int(p.name[10:]) for p in directory.glob("iteration_[0-9]*")), default=0) + 1
        )
        atomic_write_bytes(path, canonical_json({"iteration": iteration}))
    output = directory / f"iteration_{iteration:03d}" / "output.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    return DeliveryPresentation(
        context.issue_dir,
        context.project_root,
        dict(context.playbook),
        context.blackboard_state,
        context.step,
        iteration,
        output,
    ), path


def execute(inputs, *, context):
    validate_inputs(inputs)
    ctx, visit = _presentation(context)
    binding = DeliveryBinding.model_validate(ctx.step_def["delivery"])
    records = HumanTaskRecordStore(ctx.issue_dir)
    tasks = [
        t
        for t in records.tasks()
        if t.workflow_id == context.workflow_id
        and t.step == ctx.step
        and t.iteration == ctx.iteration
    ]
    pending = [t for t in tasks if t.status == HumanTaskStatus.PENDING]
    actions = [t for t in tasks if t.policy_id == binding.approval_task]
    current = actions[-1] if actions else None
    result = records.get_result(current.id) if current else None
    if result and result.payload.get("decision") == "review_only":
        # No action authority. A fresh presentation pauses once instead of spinning.
        iteration = ctx.iteration + 1
        atomic_write_bytes(visit, canonical_json({"iteration": iteration}))
        from dataclasses import replace

        output = ctx.issue_dir / ctx.step / f"iteration_{iteration:03d}" / "output.md"
        output.parent.mkdir(parents=True, exist_ok=True)
        ctx = replace(ctx, iteration=iteration, output=output)
        current = None
    try:
        with workspace_execution_lock(ctx.root):
            if context.execution_guard:
                context.execution_guard()
            inputs = ctx.state.artifacts
            if "input_artifacts" in ctx.step_def:
                inputs = {
                    name: inputs[name] for name in ctx.step_def["input_artifacts"] if name in inputs
                }
            validate_workspace_inputs(inputs, root=ctx.root, step_def=ctx.step_def)
            if pending:
                task = pending[-1]
                # Repair publication after a crash between task creation and artifact registration.
                artifacts = {}
                if task.policy_id == binding.approval_task:
                    task = present_action(ctx, binding)
                if task.policy_id == binding.result_task:
                    from cafe.delivery.selection import approved_snapshot

                    snapshot = approved_snapshot(
                        ctx.issue_dir, workflow_id=context.workflow_id, binding=binding
                    )
                    task = present_outcome(ctx, binding)
                    artifacts = {
                        binding.result_artifact: str(ctx.output),
                        binding.actions_artifact: str(
                            ctx.issue_dir / "delivery" / snapshot.digest / "actions.json"
                        ),
                    }
                if not task.capability_approval:
                    atomic_write_bytes(ctx.output, task.prompt.encode("utf-8"))
                return AutomaticExecutionResult(
                    intent="need_permission" if task.capability_approval else task.trigger,
                    task_id=task.id,
                    artifacts=artifacts,
                )
            if current is None:
                if prepared_request(ctx.issue_dir, ctx.step) is None:
                    raise ValueError("delivery choices must be supplied during cafe prepare")
                task = present_action(ctx, binding)
                atomic_write_bytes(ctx.output, task.prompt.encode("utf-8"))
                return AutomaticExecutionResult(intent=task.trigger, task_id=task.id)
            snapshot, report, path = execute_delivery(
                root=ctx.root,
                issue_dir=ctx.issue_dir,
                step=ctx.step,
                iteration=ctx.iteration,
                playbook=ctx.playbook,
                state=ctx.state,
            )
            actions_path = path.parent / "actions.json"
            if report["pending_task"]:
                return AutomaticExecutionResult(
                    intent="need_permission",
                    task_id=report["pending_task"],
                    artifacts={binding.actions_artifact: str(actions_path)},
                )
            verification = report["verification"]
            if not report["remaining"] and (
                verification["state"] == "pending"
                or (verification["state"] == "unknown" and verification.get("retryable"))
            ):
                raise StepWaiting(
                    step=ctx.step,
                    identity=snapshot.digest,
                    delay=max(
                        1,
                        min(
                            120,
                            report["observation"]["next_check_at"]
                            - report["observation"]["checked_at"],
                        ),
                    ),
                    detail="Waiting for the approved delivery verification tool",
                )
            # This validates all effects and verification before either success text or acceptance exists.
            task = present_outcome(ctx, binding)
            atomic_write_bytes(ctx.output, task.prompt.encode("utf-8"))
            return AutomaticExecutionResult(
                intent=task.trigger,
                task_id=task.id,
                artifacts={
                    binding.actions_artifact: str(actions_path),
                    binding.result_artifact: str(ctx.output),
                },
            )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        detail = str(exc)[:1024]
        task = materialize_task(ctx, detail, trigger="need_clarification")
        atomic_write_bytes(ctx.output, task.prompt.encode("utf-8"))
        return AutomaticExecutionResult(intent=task.trigger, task_id=task.id)
