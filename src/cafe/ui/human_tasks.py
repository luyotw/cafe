"""Policy-backed rendering and coordination for workflow human handoffs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.downstream_contract import ContractValidationError, extract_downstream_contract
from cafe.core.human_tasks import (
    HumanTaskBinding,
    HumanTaskCompletion,
    HumanTaskPolicy,
    HumanTaskPolicyError,
    HumanTaskQuestion,
    HumanTaskRejection,
    resolve_human_task_continuation,
    resolve_human_task_policy,
    validate_human_task_completion,
)
from cafe.core.phase_state_mixin import next_runnable_iteration_number
from cafe.skills.loader import SkillLoader


def resolve_step_human_task(
    *,
    playbook_data: Mapping[str, Any],
    step_name: str,
    trigger: str,
    skill_loader: Optional[SkillLoader] = None,
    iteration: int = 1,
) -> tuple[HumanTaskPolicy, HumanTaskBinding]:
    """Resolve one declared step binding without inferring workflow semantics."""
    raw_steps = playbook_data.get("steps")
    if not isinstance(raw_steps, Mapping):
        raise HumanTaskPolicyError("Playbook has no step declarations")
    raw_step = raw_steps.get(step_name)
    if not isinstance(raw_step, Mapping):
        raise HumanTaskPolicyError(f"Unknown playbook step {step_name!r}")
    raw_bindings = raw_step.get("human_tasks")
    if not isinstance(raw_bindings, Sequence) or isinstance(raw_bindings, (str, bytes)):
        raise HumanTaskPolicyError(
            f"Step {step_name!r} has no declared human task for trigger {trigger!r}"
        )
    bindings = [
        HumanTaskBinding.model_validate(raw)
        for raw in raw_bindings
        if isinstance(raw, Mapping) and raw.get("trigger") == trigger
    ]
    if len(bindings) != 1:
        raise HumanTaskPolicyError(
            f"Step {step_name!r} requires exactly one human task for trigger {trigger!r}"
        )
    skill_name = _select_skill_name(raw_step, iteration)
    contract = (skill_loader or SkillLoader()).get_workflow_contract(skill_name)
    policy = resolve_human_task_policy(defaults=contract.human_tasks, binding=bindings[0])
    return policy, bindings[0]


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
) -> Optional[dict[str, Any]]:
    """Collect one interactive response in the policy's declared input shape."""
    from cafe.ui.inquirer_prompts import prompt_checkbox, prompt_list, prompt_multiline

    if policy.input_schema == "feedback":
        return {"task": policy.id, "feedback": prompt_multiline(policy.prompt).strip()}
    if policy.input_schema == "decision":
        choices = [{"name": item.label, "value": item.id} for item in policy.decisions]
        if role and issue_name:
            choices.append({"name": f"Chat with {agent_name or role}", "value": "chat"})
        decision = prompt_list(policy.prompt, choices, default=None)
        selected = next((item for item in policy.decisions if item.id == decision), None)
        feedback = ""
        if selected is not None and selected.requires_feedback:
            feedback = prompt_multiline(policy.prompt).strip()
        return {"task": policy.id, "decision": decision, "feedback": feedback}
    if policy.input_schema == "target":
        target = prompt_list(
            policy.prompt,
            [{"name": target, "value": target} for target in policy.allowed_targets],
            default=None,
        )
        return {"task": policy.id, "target": target}

    if policy.questions_from_xml:
        if not questions:
            return None
        from cafe.ui.interactive_qa import interactive_qa_answers

        return {
            "task": policy.id,
            "answers": interactive_qa_answers(
                list(questions), role=role, issue_name=issue_name, agent_name=agent_name
            ),
        }

    answers: dict[str, str | list[str]] = {}
    for question in policy.questions:
        if question.multiple and question.options:
            answer = prompt_checkbox(question.prompt, list(question.options))
        elif question.options:
            answer = prompt_list(question.prompt, list(question.options), default=None)
        else:
            answer = prompt_multiline(question.prompt).strip()
        answers[question.id] = answer
    return {"task": policy.id, "answers": answers}


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
    except HumanTaskPolicyError as exc:
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
        return HumanTaskApplication(target=None, policy=None, rejection=rejection)

    if isinstance(completion, HumanTaskRejection):
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

    qualification_rejection = (
        _validate_packet_contracts_before_confirmation(
            playbook_data=playbook_data,
            blackboard=blackboard,
            issue_dir=issue_dir,
            producer_step=from_step,
            correction_guidance=policy.correction_guidance,
        )
        if trigger == "confirm_output" and continuation != from_step
        else None
    )
    if qualification_rejection is not None:
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

    agent_input = completion.agent_input()
    if agent_input:
        _write_next_iteration_user_input(issue_dir=issue_dir, step_name=from_step, text=agent_input)
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
    iteration_dirs = sorted(step_dir.glob("iteration_*")) if step_dir.exists() else []
    target = step_dir / f"iteration_{len(iteration_dirs) + 1:03d}"
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
