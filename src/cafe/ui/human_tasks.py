"""Policy-backed rendering and coordination for workflow human handoffs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Optional, Sequence

from cafe.core.blackboard import (
    ArtifactEntry,
    ArtifactKind,
    BlackboardStore,
    HandoffIntent,
    HandoffOwner,
)
from cafe.core.downstream_contract import ContractValidationError, extract_downstream_contract
from cafe.core.human_tasks import (
    HumanTaskBinding,
    HumanTaskCompletion,
    HumanTaskPolicy,
    HumanTaskPolicyError,
    HumanTaskQuestion,
    HumanTaskRejection,
    resolve_step_human_task as _resolve_step_human_task,
    resolve_human_task_continuation,
    validate_human_task_completion,
)
from cafe.core.human_task_records import (
    HumanTask,
    HumanTaskCorrelationError,
    HumanTaskRecordStore,
    HumanTaskStatus,
    TaskResult,
)
from cafe.core.phase_state_mixin import next_runnable_iteration_number
from cafe.core.workflow_feedback import WorkflowFeedbackError, WorkflowFeedbackLedger
from cafe.skills.loader import SkillLoader


def resolve_step_human_task(
    *,
    playbook_data: Mapping[str, Any],
    step_name: str,
    trigger: str,
    skill_loader: Optional[SkillLoader] = None,
    iteration: int = 1,
) -> tuple[HumanTaskPolicy, HumanTaskBinding]:
    """UI adapter that preserves the existing configurable skill-loader boundary."""
    return _resolve_step_human_task(
        playbook_data=playbook_data,
        step_name=step_name,
        trigger=trigger,
        skill_loader=skill_loader or SkillLoader(),
        iteration=iteration,
    )


def validate_step_human_task_completion(
    *,
    playbook_data: Mapping[str, Any],
    step_name: str,
    trigger: str,
    raw_payload: str | Mapping[str, Any],
    skill_loader: Optional[SkillLoader] = None,
    questions: Optional[Sequence[HumanTaskQuestion]] = None,
    iteration: int = 1,
) -> tuple[HumanTaskPolicy, HumanTaskBinding, HumanTaskCompletion | HumanTaskRejection]:
    """Resolve and validate a response without mutating the paused workflow."""
    policy, binding = resolve_step_human_task(
        playbook_data=playbook_data,
        step_name=step_name,
        trigger=trigger,
        skill_loader=skill_loader,
        iteration=iteration,
    )
    return policy, binding, validate_human_task_completion(
        policy, raw_payload, questions=questions
    )


def resolve_step_human_task_continuation(
    *,
    playbook_data: Mapping[str, Any],
    policy: HumanTaskPolicy,
    binding: HumanTaskBinding,
    completion: HumanTaskCompletion,
) -> str | HumanTaskRejection:
    """Return the single policy-permitted continuation for a validated response."""
    raw_steps = playbook_data.get("steps")
    if not isinstance(raw_steps, Mapping):
        return HumanTaskRejection(
            message="The playbook has no declared steps.",
            correction_guidance=policy.correction_guidance,
        )
    return resolve_human_task_continuation(
        policy=policy,
        binding=binding,
        completion=completion,
        playbook_steps=list(raw_steps),
    )


def collect_human_task_payload(
    policy: HumanTaskPolicy,
    *,
    questions: Optional[Sequence[Any]] = None,
    role: Optional[str] = None,
    issue_name: Optional[str] = None,
    agent_name: Optional[str] = None,
    human_task_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Collect one interactive response in the policy's declared input shape."""
    from cafe.ui.inquirer_prompts import prompt_checkbox, prompt_list, prompt_multiline

    def with_record_id(payload: dict[str, Any]) -> dict[str, Any]:
        if human_task_id:
            return {**payload, "human_task_id": human_task_id}
        return payload

    if policy.input_schema == "feedback":
        return with_record_id({"task": policy.id, "feedback": prompt_multiline(policy.prompt).strip()})
    if policy.input_schema == "decision":
        choices = [{"name": item.label, "value": item.id} for item in policy.decisions]
        if role and issue_name:
            choices.append({"name": f"Chat with {agent_name or role}", "value": "chat"})
        decision = prompt_list(policy.prompt, choices, default=None)
        selected = next((item for item in policy.decisions if item.id == decision), None)
        target = None
        if selected is not None and selected.requires_target:
            target = prompt_list(
                policy.prompt,
                [{"name": item, "value": item} for item in policy.allowed_targets],
                default=None,
            )
        feedback = ""
        if selected is not None and selected.requires_feedback:
            feedback = prompt_multiline(policy.prompt).strip()
        return with_record_id({
            "task": policy.id,
            "decision": decision,
            "target": target,
            "feedback": feedback,
        })
    if policy.input_schema == "target":
        target = prompt_list(
            policy.prompt,
            [{"name": target, "value": target} for target in policy.allowed_targets],
            default=None,
        )
        return with_record_id({"task": policy.id, "target": target})

    if policy.questions_from_xml:
        if not questions:
            return None
        from cafe.ui.interactive_qa import interactive_qa_answers

        return with_record_id({
            "task": policy.id,
            "answers": interactive_qa_answers(
                list(questions), role=role, issue_name=issue_name, agent_name=agent_name
            ),
        })

    answers: dict[str, str | list[str]] = {}
    for question in policy.questions:
        if question.multiple and question.options:
            answer = prompt_checkbox(question.prompt, list(question.options))
        elif question.options:
            answer = prompt_list(question.prompt, list(question.options), default=None)
        else:
            answer = prompt_multiline(question.prompt).strip()
        answers[question.id] = answer
    return with_record_id({"task": policy.id, "answers": answers})


@dataclass(frozen=True)
class HumanTaskApplication:
    """Result of attempting to complete a paused human task."""

    target: Optional[str]
    policy: Optional[HumanTaskPolicy]
    rejection: Optional[HumanTaskRejection] = None


def apply_human_task_payload(
    *,
    issue_dir: Path,
    playbook_data: Mapping[str, Any],
    blackboard: Any,
    from_step: str,
    trigger: str,
    raw_payload: str | Mapping[str, Any],
    source: str,
) -> HumanTaskApplication:
    """Validate and apply one response while retaining a pause on rejection."""
    record_store = HumanTaskRecordStore(issue_dir)
    with record_store.transaction():
        return _apply_human_task_payload(
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            blackboard=blackboard,
            from_step=from_step,
            trigger=trigger,
            raw_payload=raw_payload,
            source=source,
            record_store=record_store,
        )


def _apply_human_task_payload(
    *,
    issue_dir: Path,
    playbook_data: Mapping[str, Any],
    blackboard: Any,
    from_step: str,
    trigger: str,
    raw_payload: str | Mapping[str, Any],
    source: str,
    record_store: HumanTaskRecordStore,
) -> HumanTaskApplication:
    """Apply a response while holding the matching durable-record transaction."""
    store = BlackboardStore(issue_dir)
    try:
        iteration = latest_step_iteration(issue_dir=issue_dir, step_name=from_step)
        questions = _load_dynamic_questions(
            issue_dir=issue_dir,
            step_name=from_step,
            playbook_data=playbook_data,
            trigger=trigger,
            iteration=iteration,
        )
        policy, binding, completion = validate_step_human_task_completion(
            playbook_data=playbook_data,
            step_name=from_step,
            trigger=trigger,
            raw_payload=raw_payload,
            questions=questions,
            iteration=iteration,
        )
    except (HumanTaskPolicyError, LookupError, TypeError) as exc:
        rejection = HumanTaskRejection(
            message=str(exc),
            correction_guidance=(
                "Declare a matching human-task policy before resuming the workflow."
            ),
        )
        store.record_event(
            blackboard,
            "human_task_configuration_error",
            {"step": from_step, "trigger": trigger, "reason": rejection.message},
        )
        if record_store.exists:
            record_store.record_configuration_error(
                workflow_id=blackboard.workflow_id,
                step=from_step,
                iteration=latest_step_iteration(issue_dir=issue_dir, step_name=from_step),
                trigger=trigger,
                reason=rejection.message,
            )
        return HumanTaskApplication(target=None, policy=None, rejection=rejection)

    durable_task, durable_result, durable_rejection = _resolve_durable_task(
        record_store=record_store,
        workflow_id=blackboard.workflow_id,
        from_step=from_step,
        trigger=trigger,
        policy=policy,
        raw_payload=raw_payload,
    )
    if durable_rejection is not None:
        store.record_event(
            blackboard,
            "human_task_rejected",
            {
                "step": from_step,
                "trigger": trigger,
                "task_id": policy.id,
                "reason": durable_rejection.message,
            },
        )
        return HumanTaskApplication(target=None, policy=policy, rejection=durable_rejection)

    recovered_agent_input = ""
    if durable_result is not None:
        blackboard = store.load_or_create(
            from_step, playbook_id=getattr(blackboard, "playbook_id", "default")
        )
        if blackboard.current_step != "user":
            rejection = HumanTaskRejection(
                message="This human task has already completed.",
                correction_guidance=policy.correction_guidance,
            )
            record_store.record_rejection(
                workflow_id=blackboard.workflow_id,
                task_id=durable_task.id,
                reason=rejection.message,
            )
            store.record_event(
                blackboard,
                "human_task_rejected",
                {
                    "step": from_step,
                    "trigger": trigger,
                    "task_id": policy.id,
                    "reason": rejection.message,
                },
            )
            return HumanTaskApplication(target=None, policy=policy, rejection=rejection)
        continuation, recovered_agent_input = _recorded_result_continuation(
            durable_result, policy=policy
        )
        if isinstance(continuation, HumanTaskRejection):
            record_store.record_rejection(
                workflow_id=blackboard.workflow_id,
                task_id=durable_task.id,
                reason=continuation.message,
            )
            store.record_event(
                blackboard,
                "human_task_rejected",
                {
                    "step": from_step,
                    "trigger": trigger,
                    "task_id": policy.id,
                    "reason": continuation.message,
                },
            )
            return HumanTaskApplication(target=None, policy=policy, rejection=continuation)
    else:
        if isinstance(completion, HumanTaskRejection):
            if durable_task is not None:
                record_store.record_rejection(
                    workflow_id=blackboard.workflow_id,
                    task_id=durable_task.id,
                    reason=completion.message,
                )
            store.record_event(
                blackboard,
                "human_task_rejected",
                {
                    "step": from_step,
                    "trigger": trigger,
                    "task_id": policy.id,
                    "reason": completion.message,
                },
            )
            return HumanTaskApplication(target=None, policy=policy, rejection=completion)

        continuation = resolve_step_human_task_continuation(
            playbook_data=playbook_data,
            policy=policy,
            binding=binding,
            completion=completion,
        )
        if isinstance(continuation, HumanTaskRejection):
            if durable_task is not None:
                record_store.record_rejection(
                    workflow_id=blackboard.workflow_id,
                    task_id=durable_task.id,
                    reason=continuation.message,
                )
            store.record_event(
                blackboard,
                "human_task_rejected",
                {
                    "step": from_step,
                    "trigger": trigger,
                    "task_id": policy.id,
                    "reason": continuation.message,
                },
            )
            return HumanTaskApplication(target=None, policy=policy, rejection=continuation)

    selected_decision = next(
        (
            item
            for item in policy.decisions
            if durable_result is None and item.id == completion.decision
        ),
        None,
    )
    is_correction = selected_decision is not None and selected_decision.correction
    qualification_rejection = (
        _validate_packet_contracts_before_confirmation(
            playbook_data=playbook_data,
            blackboard=blackboard,
            issue_dir=issue_dir,
            producer_step=from_step,
            correction_guidance=policy.correction_guidance,
        )
        if (
            durable_result is None
            and
            trigger == "confirm_output"
            and continuation != from_step
            and not is_correction
        )
        else None
    )
    if qualification_rejection is not None:
        if durable_task is not None:
            record_store.record_rejection(
                workflow_id=blackboard.workflow_id,
                task_id=durable_task.id,
                reason=qualification_rejection.message,
            )
        store.record_event(
            blackboard,
            "human_task_rejected",
            {
                "step": from_step,
                "trigger": trigger,
                "task_id": policy.id,
                "reason": qualification_rejection.message,
            },
        )
        return HumanTaskApplication(
            target=None, policy=policy, rejection=qualification_rejection
        )

    feedback = (
        durable_result.payload.get("feedback", "")
        if durable_result is not None
        else completion.feedback
    )
    decision = (
        durable_result.payload.get("decision")
        if durable_result is not None
        else completion.decision
    )
    delivery_decision = next(
        (item for item in policy.decisions if item.id == decision),
        None,
    )
    if (
        binding.feedback_delivery is not None
        and isinstance(feedback, str)
        and feedback.strip()
        and (
            policy.input_schema == "feedback"
            or (delivery_decision is not None and delivery_decision.requires_feedback)
        )
    ):
        ledger = WorkflowFeedbackLedger(issue_dir)
        try:
            ledger.record(
                source_identity=(
                    f"{binding.feedback_delivery.source_kind}:{from_step}:{policy.id}:{iteration}"
                ),
                source_kind=binding.feedback_delivery.source_kind,
                target_step=continuation,
                content=feedback,
            )
            previous = getattr(blackboard, "artifacts", {}).get(
                binding.feedback_delivery.artifact
            )
            store.put_artifact(
                blackboard,
                ArtifactEntry(
                    name=binding.feedback_delivery.artifact,
                    kind=ArtifactKind.DOCUMENT,
                    version=(previous.version + 1) if previous else 1,
                    updated_by="human_task",
                    path=str(ledger.path),
                ),
            )
        except WorkflowFeedbackError as exc:
            rejection = HumanTaskRejection(
                message="Feedback could not be stored; the review remains paused.",
                correction_guidance=policy.correction_guidance,
            )
            if durable_task is not None:
                record_store.record_rejection(
                    workflow_id=blackboard.workflow_id,
                    task_id=durable_task.id,
                    reason=rejection.message,
                )
            store.record_event(
                blackboard,
                "human_task_rejected",
                {
                    "step": from_step,
                    "trigger": trigger,
                    "task_id": policy.id,
                    "reason": str(exc),
                },
            )
            return HumanTaskApplication(target=None, policy=policy, rejection=rejection)

    if durable_task is not None:
        permitted_continuations = set(durable_task.continuations.values())
        if not permitted_continuations:
            raw_allowed = durable_task.expected_result.get("allowed_targets", [])
            if isinstance(raw_allowed, list):
                permitted_continuations = {item for item in raw_allowed if isinstance(item, str)}
        if continuation not in permitted_continuations:
            rejection = HumanTaskRejection(
                message="This response does not select the pending task's declared continuation.",
                correction_guidance=policy.correction_guidance,
            )
            record_store.record_rejection(
                workflow_id=blackboard.workflow_id,
                task_id=durable_task.id,
                reason=rejection.message,
            )
            store.record_event(
                blackboard,
                "human_task_rejected",
                {
                    "step": from_step,
                    "trigger": trigger,
                    "task_id": policy.id,
                    "reason": rejection.message,
                },
            )
            return HumanTaskApplication(target=None, policy=policy, rejection=rejection)
        if durable_result is None:
            try:
                record_store.complete(
                    workflow_id=blackboard.workflow_id,
                    task_id=durable_task.id,
                    payload=_validated_completion_payload(completion, continuation),
                    source=source,
                )
            except HumanTaskCorrelationError as exc:
                rejection = HumanTaskRejection(
                    message=str(exc), correction_guidance=policy.correction_guidance
                )
                record_store.record_rejection(
                    workflow_id=blackboard.workflow_id,
                    task_id=durable_task.id,
                    reason=rejection.message,
                )
                return HumanTaskApplication(target=None, policy=policy, rejection=rejection)

    agent_input = (
        ""
        if binding.feedback_delivery is not None
        else recovered_agent_input
        if durable_result is not None
        else completion.agent_input()
    )
    if agent_input:
        has_feedback = (
            isinstance(durable_result.payload.get("feedback"), str)
            if durable_result is not None
            else bool(completion.feedback)
        )
        input_step = continuation if has_feedback and continuation != "_done" else from_step
        _write_next_iteration_user_input(
            issue_dir=issue_dir,
            step_name=input_step,
            text=agent_input,
        )
    is_done = continuation == "_done"
    store.set_current_step(blackboard, "done" if is_done else continuation)
    store.set_handoff_summary(blackboard, f"Completed human task {policy.id} for {from_step}")
    store.update_handoff_contract(
        blackboard,
        from_step=from_step,
        to_owner=HandoffOwner.DONE if is_done else HandoffOwner.AGENT,
        to_step="done" if is_done else continuation,
        intent=HandoffIntent.WORKFLOW_COMPLETE if is_done else HandoffIntent.AWAIT_AGENT,
        status_code="",
        source=f"human_task.{source}",
    )
    store.record_event(
        blackboard,
        "human_task_completed",
        {
            "step": from_step,
            "trigger": trigger,
            "task_id": policy.id,
            "pattern": policy.pattern,
            "to_step": continuation,
            "source": source,
        },
    )
    return HumanTaskApplication(target="done" if is_done else continuation, policy=policy)


def _resolve_durable_task(
    *,
    record_store: HumanTaskRecordStore,
    workflow_id: str,
    from_step: str,
    trigger: str,
    policy: HumanTaskPolicy,
    raw_payload: str | Mapping[str, Any],
) -> tuple[Optional[HumanTask], Optional[TaskResult], Optional[HumanTaskRejection]]:
    """Resolve the exact durable task, including one interrupted continuation."""
    if not record_store.exists:
        return None, None, None
    matching = [
        task
        for task in record_store.tasks()
        if task.workflow_id == workflow_id
        and task.step == from_step
        and task.trigger == trigger
        and task.policy_id == policy.id
    ]
    active = next(
        (
            task
            for task in matching
            if task.status is HumanTaskStatus.PENDING
            and record_store.get_wait_state(task.id).released_at is None
        ),
        None,
    )
    submitted_id = _submitted_human_task_id(raw_payload)
    if active is not None:
        if submitted_id is None:
            record_store.record_rejection(
                workflow_id=workflow_id,
                task_id=active.id,
                reason="response omits the required durable task id",
            )
            return None, None, HumanTaskRejection(
                message="This response must identify the pending durable human task.",
                correction_guidance=policy.correction_guidance,
            )
        if submitted_id != active.id:
            record_store.record_rejection(
                workflow_id=workflow_id,
                task_id=active.id,
                reason="response references a different durable task",
            )
            return None, None, HumanTaskRejection(
                message="This response belongs to a different durable human task.",
                correction_guidance=policy.correction_guidance,
            )
        return active, None, None

    if not matching:
        return None, None, HumanTaskRejection(
            message="This response cannot be correlated to a durable human task.",
            correction_guidance=policy.correction_guidance,
        )

    if submitted_id is None:
        record_store.record_rejection(
            workflow_id=workflow_id,
            task_id=matching[0].id,
            reason="response omits the required durable task id",
        )
        return None, None, HumanTaskRejection(
            message="This response must identify the pending durable human task.",
            correction_guidance=policy.correction_guidance,
        )

    completed = next(
        (task for task in matching if task.id == submitted_id and task.status is HumanTaskStatus.COMPLETED),
        None,
    )
    if completed is not None:
        result = record_store.get_result(completed.id)
        if result is not None:
            return completed, result, None

    rejected_task = next((task for task in matching if task.id == submitted_id), matching[0])
    record_store.record_rejection(
        workflow_id=workflow_id,
        task_id=rejected_task.id,
        reason="the task is no longer pending",
    )
    return None, None, HumanTaskRejection(
        message="This workflow is not waiting for a matching human task.",
        correction_guidance=policy.correction_guidance,
    )


def _recorded_result_continuation(
    result: TaskResult, *, policy: HumanTaskPolicy
) -> tuple[str | HumanTaskRejection, str]:
    """Restore only the previously validated continuation and agent input."""
    payload = result.payload
    continuation = payload.get("continuation")
    if payload.get("task") != policy.id or not isinstance(continuation, str) or not continuation:
        return HumanTaskRejection(
            message="The completed durable human task has an invalid continuation.",
            correction_guidance=policy.correction_guidance,
        ), ""
    feedback = payload.get("feedback")
    if isinstance(feedback, str) and feedback:
        return continuation, feedback
    answers = payload.get("answers")
    if answers is None:
        return continuation, ""
    if not isinstance(answers, Mapping):
        return HumanTaskRejection(
            message="The completed durable human task has invalid recorded answers.",
            correction_guidance=policy.correction_guidance,
        ), ""
    lines = []
    for question, answer in answers.items():
        if not isinstance(question, str) or not isinstance(answer, list) or not all(
            isinstance(item, str) for item in answer
        ):
            return HumanTaskRejection(
                message="The completed durable human task has invalid recorded answers.",
                correction_guidance=policy.correction_guidance,
            ), ""
        lines.append(f"{question}: {', '.join(answer)}")
    return continuation, "\n".join(lines)


def _submitted_human_task_id(raw_payload: str | Mapping[str, Any]) -> Optional[str]:
    """Read a durable id while preserving taskless #345 payloads."""
    if isinstance(raw_payload, str):
        try:
            raw_payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw_payload, Mapping):
        return None
    value = raw_payload.get("human_task_id")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _validated_completion_payload(completion: HumanTaskCompletion, continuation: str) -> dict[str, Any]:
    """Store the validated, transport-neutral completion rather than raw input."""
    payload: dict[str, Any] = {"task": completion.task_id, "continuation": continuation}
    if completion.decision is not None:
        payload["decision"] = completion.decision
    if completion.answers is not None:
        payload["answers"] = {key: list(value) for key, value in completion.answers.items()}
    if completion.feedback is not None:
        payload["feedback"] = completion.feedback
    if completion.target is not None:
        payload["target"] = completion.target
    return payload


def _validate_packet_contracts_before_confirmation(
    *,
    playbook_data: Mapping[str, Any],
    blackboard: Any,
    issue_dir: Path,
    producer_step: str,
    correction_guidance: str,
) -> Optional[HumanTaskRejection]:
    """Reject confirmation when a declared packet consumer lacks a valid source contract."""
    raw_steps = playbook_data.get("steps")
    if not isinstance(raw_steps, Mapping):
        return None
    producer = raw_steps.get(producer_step)
    if not isinstance(producer, Mapping):
        return None
    artifact_name = producer.get("output_artifact")
    if not isinstance(artifact_name, str):
        return None
    artifact = getattr(blackboard, "artifacts", {}).get(artifact_name)
    # Legacy callers that only exercise routing have no produced artifact to
    # qualify. Runtime confirmation always records the producer output first.
    if artifact is None:
        return None
    source_path = getattr(artifact, "path", None)
    for consumer_step, consumer in raw_steps.items():
        if not isinstance(consumer_step, str) or not isinstance(consumer, Mapping):
            continue
        input_artifacts = consumer.get("input_artifacts")
        if (
            not isinstance(input_artifacts, Sequence)
            or isinstance(input_artifacts, (str, bytes))
            or artifact_name not in input_artifacts
        ):
            continue
        consumer_iteration = next_runnable_iteration_number(issue_dir / consumer_step)
        skill_name = _select_skill_name(consumer, consumer_iteration)
        contract = SkillLoader().get_workflow_contract(skill_name)
        packet_kinds = {
            policy.contract_kind
            for mapping in contract.prompt_inputs
            if artifact_name in mapping.artifacts
            for policy in mapping.load_policy
            if policy.mode == "packet" and policy.contract_kind in {"spec", "plan"}
        }
        for kind in sorted(packet_kinds):
            try:
                extract_downstream_contract(str(source_path or ""), kind=kind)
            except ContractValidationError as exc:
                return HumanTaskRejection(
                    message=(
                        f"Cannot confirm {producer_step} -> {consumer_step} packet relation "
                        f"for {artifact_name!r}: {exc}"
                    ),
                    correction_guidance=correction_guidance,
                )
    return None


def _write_next_iteration_user_input(*, issue_dir: Path, step_name: str, text: str) -> None:
    step_dir = issue_dir / step_name
    iteration = next_runnable_iteration_number(step_dir)
    target = step_dir / f"iteration_{iteration:03d}"
    target.mkdir(parents=True, exist_ok=True)
    (target / "user_input.md").write_text(text, encoding="utf-8")


def _load_dynamic_questions(
    *,
    issue_dir: Path,
    step_name: str,
    playbook_data: Mapping[str, Any],
    trigger: str,
    iteration: int,
) -> Optional[tuple[HumanTaskQuestion, ...]]:
    """Return the current XML question contract for a dynamic answer task."""
    try:
        policy, _binding = resolve_step_human_task(
            playbook_data=playbook_data,
            step_name=step_name,
            trigger=trigger,
            iteration=iteration,
        )
    except HumanTaskPolicyError:
        return None
    if not policy.questions_from_xml:
        return None

    from cafe.core.questions_schema import parse_questions_xml, validate_questions_xml

    step_dir = issue_dir / step_name
    iteration_dirs = sorted(step_dir.glob("iteration_*")) if step_dir.exists() else []
    questions_file = iteration_dirs[-1] / "questions.xml" if iteration_dirs else None
    if questions_file is None or not validate_questions_xml(questions_file):
        return ()
    return tuple(
        HumanTaskQuestion(
            id=question.id,
            prompt=question.title,
            options=tuple(question.options),
            multiple=question.multi_select,
        )
        for question in parse_questions_xml(questions_file)
    )


def latest_step_iteration(*, issue_dir: Path, step_name: str) -> int:
    """Return the latest numeric iteration for policy-skill selection."""
    step_dir = issue_dir / step_name
    iterations = []
    if step_dir.exists():
        for candidate in step_dir.glob("iteration_*"):
            if not candidate.is_dir():
                continue
            try:
                iterations.append(int(candidate.name.removeprefix("iteration_")))
            except ValueError:
                continue
    return max(iterations, default=1)


def _select_skill_name(step_def: Mapping[str, Any], iteration: int) -> str:
    raw_skill = step_def.get("skill")
    if isinstance(raw_skill, str) and raw_skill.strip():
        return raw_skill
    if isinstance(raw_skill, Mapping):
        exact = raw_skill.get(str(iteration))
        if isinstance(exact, str) and exact.strip():
            return exact
        default = raw_skill.get("default")
        if isinstance(default, str) and default.strip():
            return default
        for value in raw_skill.values():
            if isinstance(value, str) and value.strip():
                return value
    raise HumanTaskPolicyError("Human-task step must select a skill")
