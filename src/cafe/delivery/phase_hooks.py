"""Delivery-owned action review, operation execution and outcome presentation."""

from __future__ import annotations

import json
from pathlib import Path

from cafe.core.hooks import HookResult, NoOpHook
from cafe.core.hooks.feedback import _register_artifact
from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.core.packet_io import atomic_write_bytes, canonical_json
from cafe.core.status_codes import PhaseStatusCode
from cafe.delivery.contracts import DeliveryBinding, digest
from cafe.delivery.presentation import (DeliveryPresentation, materialize_task, present_action, present_outcome,
                                        _proposals as _proposals, _open_proposal_evidence_ids as _open_proposal_evidence_ids,
                                        _proposal_table_cells as _proposal_table_cells)
from cafe.delivery.selection import ActionReviewRequired, approved_snapshot
from cafe.skills.loader import SkillLoader


def _binding(kwargs):
    return DeliveryBinding.model_validate(kwargs["step_def"]["delivery"])


def _prepare_action_context(kwargs, binding, reason=""):
    entry = kwargs["blackboard_state"].artifacts.get(binding.publication_artifact)
    if entry is None:
        raise ValueError("declared PR publication artifact is missing")
    from cafe.delivery.prepared import prepared_request
    request = prepared_request(kwargs["phase"].issue_dir, kwargs["step_name"])
    fixed = ("Use these preparation-time delivery choices without changing them: "
             + json.dumps(request, ensure_ascii=False) + ". ") if request else ""
    return HookResult(context_updates={
        "delivery_stage": "prepare_action", "delivery_complete": "false",
        "delivery_publication_file": entry.path,
        "continuation_prompt": (
            "Prepare integration into the confirmed base branch and its verification plan. "
            + fixed + "Local mode requires its confirmed clean destination checkout. "
            "Read the declared PR input at " + entry.path + ". Use repository policy "
            "or recommend a concrete strategy in the complete action bundle. "
            "Write delivery_request.json beside the output and request need_permission. "
            "PR content confirmation grants no integration authority; execute no actions. " + reason
        ),
    })


def _task(kwargs, prompt, *, trigger="confirm_output"):
    return materialize_task(DeliveryPresentation.from_hook(kwargs), prompt, trigger=trigger)

def _request_clarification(kwargs):
    """Replace the agent's stale success intent when host delivery validation fails."""
    atomic_write_bytes(
        kwargs["phase"].issue_dir / "next_step.txt",
        canonical_json({
            "version": 1, "to_owner": "user", "to_step": "user",
            "intent": "need_clarification",
        }),
    )




class DevelopmentActionContext(NoOpHook):
    name = "DevelopmentActionContext"

    def run(self, **kwargs):
        if kwargs.get("stage") != "publish_output":
            return HookResult()
        phase = kwargs["phase"]
        binding = _binding(kwargs)
        if binding.publication_artifact:
            try:
                baton = json.loads((phase.issue_dir / "next_step.txt").read_text())
            except (OSError, ValueError):
                return HookResult()
            if not isinstance(baton, dict):
                return HookResult()
            if (kwargs["step_name"] != binding.approval_step
                    or baton.get("intent") != "need_permission"
                    or baton.get("to_owner") != "user" or baton.get("to_step") != "user"):
                return HookResult()
            trigger = "need_permission"
        else:
            # Existing project catalogs retain their original, explicitly shown PR review.
            from cafe.core.hooks.native import _publish_requested
            if not _publish_requested(
                phase=phase, step_name=kwargs["step_name"], status_code=kwargs.get("status_code"),
                context=kwargs.get("context"), step_def=kwargs["step_def"],
            ):
                return HookResult()
            trigger = "confirm_output"
        output = kwargs["output_file"]
        try:
            task = present_action(DeliveryPresentation.from_hook(kwargs), binding, trigger=trigger,
                                  context=kwargs.get("context"),
                                  validated_pr_auto_create=kwargs.get("validated_pr_auto_create"))
            return HookResult(context_updates={"delivery_action_review_task": task.id})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            output.write_text(
                output.read_text() + f"\n\n## Delivery action details required\n\n{str(exc)[:1024]}\n"
            )
            _request_clarification(kwargs)
            return HookResult(
                continue_pipeline=False,
                override_status_code=PhaseStatusCode.NEED_CLARIFICATION,
                context_updates={"delivery_action_error": str(exc)[:1024]},
            )


class DevelopmentDeliveryExecutor(NoOpHook):
    name = "DevelopmentDeliveryExecutor"

    def run(self, **kwargs):
        if kwargs.get("stage") != "prepare_input":
            return HookResult()
        from cafe.core.capabilities import (
            default_capability_definition_dirs,
            load_capability_registry,
        )
        from cafe.delivery.service import execute_snapshot
        from cafe.delivery.verification import VerificationReviewRequired

        phase = kwargs["phase"]
        binding = _binding(kwargs)
        state = kwargs["blackboard_state"]
        root = Path(phase.git_ops.repo_path).resolve()
        try:
            if binding.publication_artifact:
                tasks = [t for t in HumanTaskRecordStore(phase.issue_dir).tasks()
                         if t.workflow_id == state.workflow_id and t.step == binding.approval_step
                         and t.policy_id == binding.approval_task]
                current = tasks[-1] if tasks else None
                if current and current.status == HumanTaskStatus.PENDING:
                    atomic_write_bytes(phase.issue_dir / "next_step.txt", canonical_json({
                        "version": 1, "to_owner": "user", "to_step": "user", "intent": "need_permission",
                    }))
                    return HookResult(continue_pipeline=False,
                                      override_status_code=PhaseStatusCode.NEED_PERMISSION)
                result = HumanTaskRecordStore(phase.issue_dir).get_result(current.id) if current else None
                if current is None or (current.status == HumanTaskStatus.COMPLETED
                        and result and result.payload.get("decision") in {"fix_now", "review_only"}):
                    return _prepare_action_context(kwargs, binding)
            snapshot = approved_snapshot(
                phase.issue_dir, workflow_id=state.workflow_id, binding=binding
            )
            if binding.publication_artifact:
                from cafe.delivery.selection import validate_publication_reference
                validate_publication_reference(phase.issue_dir, state, binding, snapshot)
            actions_path = phase.issue_dir / "delivery" / snapshot.digest / "actions.json"
            atomic_write_bytes(actions_path, canonical_json(snapshot.model_dump(mode="json")))
            _register_artifact(
                phase=phase,
                blackboard_state=state,
                name=binding.actions_artifact,
                path=actions_path,
                updated_by=kwargs["step_name"],
            )
            report, path = execute_snapshot(
                root=root,
                issue_dir=phase.issue_dir,
                snapshot=snapshot,
                registry=load_capability_registry(default_capability_definition_dirs(root)),
                step=kwargs["step_name"],
                iteration=phase.iteration,
                output_file=kwargs["output_file"],
                timeout=120,
            )
            updates = {
                "delivery_receipts_file": str(path),
                "delivery_complete": str(report["complete"]).lower(),
                "delivery_actions_file": str(actions_path),
            }
            from cafe.core.runtime_locales import render_text

            owner = SkillLoader(project_root=root).get_skill_dir(kwargs["step_def"]["skill"])
            evidence_prompt = render_text(
                "human_task.delivery.runtime_evidence",
                locale=getattr(state, "conversation_locale", "en-US"),
                catalog_root=owner / "locales",
                actions=str(actions_path),
                receipts=str(path),
                complete=updates["delivery_complete"],
            )
            updates["continuation_prompt"] = "\n\n".join(
                filter(
                    None,
                    [(kwargs.get("context") or {}).get("continuation_prompt", ""), evidence_prompt],
                )
            )
            if report["pending_task"]:
                kwargs["output_file"].write_text(
                    "# Delivery awaits host approval\n\n" + json.dumps(report, indent=2)
                )
                return HookResult(
                    continue_pipeline=False,
                    override_status_code=PhaseStatusCode.NEED_PERMISSION,
                    context_updates=updates,
                    events=[
                        {"type": "capability_approval_pending", "task_id": report["pending_task"]}
                    ],
                )
            verification = report["verification"]
            if not report["remaining"] and (verification["state"] == "pending" or (
                verification["state"] == "unknown" and verification.get("retryable")
            )):
                from cafe.core.workflow_models import StepWaiting
                raise StepWaiting(
                    step=kwargs["step_name"],
                    identity=snapshot.digest,
                    delay=max(1, min(120, report["observation"]["next_check_at"]
                                    - report["observation"]["checked_at"])),
                    detail="Waiting for the approved delivery verification tool",
                )
            return HookResult(context_updates=updates)
        except VerificationReviewRequired as exc:
            return HookResult(context_updates={
                "delivery_complete": "false",
                "delivery_stage": "prepare_action",
                "delivery_verification_error": str(exc)[:1024],
                "continuation_prompt": (
                    "The prepared verification contract needs attention or fresh action review: "
                    + str(exc)[:1024]
                    + ". Identify the confirmed requirement and why it applies before proposing any tool work. "
                    "A missing observer does not establish a deployment or release requirement. "
                    "Do not execute unapproved host code or request final acceptance."
                ),
            })
        except ActionReviewRequired as exc:
            if binding.publication_artifact:
                return _prepare_action_context(kwargs, binding, str(exc)[:1024])
            return HookResult(continue_pipeline=False, override_status_code=PhaseStatusCode.NEED_CLARIFICATION)
        except (OSError, ValueError, KeyError) as exc:
            kwargs["output_file"].write_text(f"# Delivery recovery required\n\n{str(exc)[:1024]}\n")
            return HookResult(
                continue_pipeline=False, override_status_code=PhaseStatusCode.NEED_CLARIFICATION
            )


class DevelopmentDeliveryOutcome(NoOpHook):
    name = "DevelopmentDeliveryOutcome"

    def run(self, **kwargs):
        if kwargs.get("stage") != "publish_output":
            return HookResult()
        phase = kwargs["phase"]
        try:
            baton = json.loads((phase.issue_dir / "next_step.txt").read_text())
        except (OSError, ValueError):
            return HookResult()
        if baton.get("intent") != "confirm_output":
            return HookResult()
        try:
            binding = _binding(kwargs)
            from cafe.delivery import local_result

            if local_result.requested(kwargs["output_file"]):
                state = kwargs["blackboard_state"]
                report = local_result.build_result(
                    phase.issue_dir, state, kwargs["step_def"], binding,
                    kwargs["step_name"], phase.iteration, kwargs["output_file"],
                )
                path = local_result.result_path(
                    phase.issue_dir, state.workflow_id, kwargs["step_name"], phase.iteration,
                )
                if path.exists() and json.loads(local_result.read_bounded(path)) != report:
                    raise ValueError("shown local delivery evidence changed; revise in a new iteration")
                atomic_write_bytes(path, canonical_json(report))
                _task(kwargs, f"Local delivery SHA256: {digest(report)}\n\n"
                      + kwargs["output_file"].read_text()
                      + "\n\n" + json.dumps(report, ensure_ascii=False, indent=2))
                return HookResult(context_updates={"delivery_receipts_file": str(path),
                                                   "delivery_complete": "true"})
            present_outcome(DeliveryPresentation.from_hook(kwargs), binding)
            snapshot = approved_snapshot(phase.issue_dir, workflow_id=kwargs["blackboard_state"].workflow_id, binding=binding)
            path = phase.issue_dir / "delivery" / snapshot.digest / "result.json"
            return HookResult(context_updates={"delivery_receipts_file": str(path)})
        except (OSError, ValueError, KeyError) as exc:
            kwargs["output_file"].write_text(f"# Delivery recovery required\n\n{str(exc)[:1024]}\n")
            _request_clarification(kwargs)
            return HookResult(
                continue_pipeline=False, override_status_code=PhaseStatusCode.NEED_CLARIFICATION
            )
