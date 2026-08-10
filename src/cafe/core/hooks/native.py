"""Native GenericPhase hooks backed by existing CAFE UI flows."""

from __future__ import annotations

import json
import sys
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from cafe.core.blackboard import BlackboardState, HandoffContract, HandoffIntent, HandoffOwner
from cafe.core.hooks import HookResult, NoOpHook
from cafe.core.initial_input import (
    GITHUB_ISSUE_PROVIDER,
    MANUAL_TEXT_PROVIDER,
    InitialInputResult,
    load_initial_input_selection,
)
from cafe.core.questions_schema import parse_questions_xml, validate_questions_xml
from cafe.core.status_codes import PhaseStatusCode, step_on_declares
from cafe.skills.loader import SkillLoader
from cafe.ui.inquirer_prompts import prompt_list, prompt_multiline, prompt_text
from cafe.ui.interactive_qa import interactive_qa_flow
from cafe.utils.github import (
    GitHubError,
    GitHubOps,
    filter_unresolved_comments,
    format_comments_for_prompt,
    get_all_pr_comments,
    get_processed_comment_ids_from_history,
    load_pr_last_seen_comment_ids,
)


def _get_previous_iteration_status(phase: Any) -> Optional[str]:
    """Load the previous iteration status code for the current step.

    Reads from the blackboard's most recent ``step_completed`` event for the
    phase. The blackboard / baton model is the canonical source of truth for
    cross-iteration step status; do not fall back to ``context.json``.
    """
    if getattr(phase, "iteration", 0) <= 1:
        return None

    issue_dir = getattr(phase, "issue_dir", None)
    phase_name = getattr(phase, "phase_name", "") or ""
    if not isinstance(issue_dir, Path) or not phase_name:
        return None

    from cafe.core.blackboard import BlackboardStore

    try:
        store = BlackboardStore(issue_dir)
        state = store.load_or_create(phase_name)
    except Exception:
        return None

    for event in reversed(getattr(state, "events", []) or []):
        if getattr(event, "event_type", "") != "step_completed":
            continue
        data = getattr(event, "data", {}) or {}
        if data.get("step") != phase_name:
            continue
        status_code = data.get("status_code")
        if isinstance(status_code, str) and status_code:
            return status_code
        return None
    return None


def _hook_status_value(raw_status: Any) -> str:
    """Normalize hook status input to its string value."""
    if isinstance(raw_status, PhaseStatusCode):
        return raw_status.value
    if isinstance(raw_status, str):
        return raw_status
    return ""


def _publish_confirmation_declared(
    *, context: Optional[dict[str, Any]] = None, step_def: Any = None
) -> bool:
    """Return the publish selector supplied by a validated workflow contract."""
    if isinstance(context, dict) and "publish_confirmation" in context:
        return bool(context["publish_confirmation"])
    if isinstance(step_def, dict):
        behavior = step_def.get("behavior")
        if isinstance(behavior, dict):
            return bool(behavior.get("publish_confirmation"))
    return False


def _publish_requested(
    *,
    phase: Any,
    step_name: str,
    status_code: Any,
    context: Optional[dict[str, Any]] = None,
    step_def: Any = None,
) -> bool:
    """Return True when a declared publishing step reaches its handoff."""
    if not _publish_confirmation_declared(context=context, step_def=step_def):
        return False
    if _hook_status_value(status_code) == PhaseStatusCode.CONFIRMED.value:
        return True

    baton_file: Optional[Path] = None
    if isinstance(context, dict):
        next_step_path = context.get("next_step_path")
        if next_step_path:
            baton_file = Path(str(next_step_path))
    if baton_file is None:
        issue_dir = getattr(phase, "issue_dir", None)
        if isinstance(issue_dir, Path):
            baton_file = issue_dir / "next_step.txt"
    if baton_file is None or not baton_file.exists():
        return False

    try:
        raw_baton = baton_file.read_text(encoding="utf-8").strip()
        payload = json.loads(raw_baton)
        if not isinstance(payload, dict):
            return False
        contract = HandoffContract.from_dict_with_current_step(
            payload,
            current_step=step_name,
        )
    except json.JSONDecodeError:
        return raw_baton == "done"
    except Exception:
        return False

    return (
        contract.from_step == step_name
        and contract.to_owner == HandoffOwner.DONE
        and contract.to_step == "done"
        and contract.intent in {HandoffIntent.AWAIT_AGENT, HandoffIntent.WORKFLOW_COMPLETE}
    )


def _declared_capability_ids(step_def: Any) -> list[str]:
    if not isinstance(step_def, dict):
        return []
    raw = step_def.get("capability_requests") or []
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _effective_capability_ids(*, step_name: str, step_def: Any) -> list[str]:
    del step_name
    return _declared_capability_ids(step_def)


def _capability_execution_requested(
    *,
    phase: Any,
    step_name: str,
    step_def: Any,
    status_code: Any,
    context: Optional[dict[str, Any]] = None,
) -> bool:
    capability_ids = _effective_capability_ids(step_name=step_name, step_def=step_def)
    if not capability_ids:
        return False
    if "cafe.pr.publish" in capability_ids:
        return _publish_requested(
            phase=phase,
            step_name=step_name,
            status_code=status_code,
            context=context,
            step_def=step_def,
        )
    if _hook_status_value(status_code) == PhaseStatusCode.CONFIRMED.value:
        return True

    baton_file: Optional[Path] = None
    if isinstance(context, dict):
        next_step_path = context.get("next_step_path")
        if next_step_path:
            baton_file = Path(str(next_step_path))
    if baton_file is None:
        issue_dir = getattr(phase, "issue_dir", None)
        if isinstance(issue_dir, Path):
            baton_file = issue_dir / "next_step.txt"
    if baton_file is None or not baton_file.exists():
        return False
    try:
        payload = json.loads(baton_file.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return False
        contract = HandoffContract.from_dict_with_current_step(
            payload,
            current_step=step_name,
        )
    except Exception:
        return False
    return (
        contract.from_step == step_name
        and contract.to_step != step_name
        and contract.to_owner in {HandoffOwner.AGENT, HandoffOwner.DONE}
    )


def _extract_pr_comment_ids(comments: list[Any]) -> list[str]:
    comment_ids: list[str] = []
    for comment in comments:
        raw_id = getattr(comment, "id", None)
        if raw_id is None and isinstance(comment, dict):
            raw_id = comment.get("id")
        if raw_id is not None:
            comment_ids.append(str(raw_id))
    return comment_ids


def _persist_pr_last_seen_comment_ids(pr_dir: Path, comment_ids: list[str]) -> None:
    if not comment_ids:
        return

    seen_ids = load_pr_last_seen_comment_ids(pr_dir)
    seen_ids.update(str(comment_id) for comment_id in comment_ids)

    artifact_file = pr_dir / "artifacts" / "pr_last_seen_comments.json"
    artifact_file.parent.mkdir(parents=True, exist_ok=True)
    artifact_file.write_text(
        json.dumps(
            {"last_seen_comment_ids": sorted(seen_ids)},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


class UserInputCollector(NoOpHook):
    """Collect user input before agent execution when the previous round requested it."""

    name = "UserInputCollector"

    @staticmethod
    def _get_previous_output_file(phase: Any, step_name: str) -> Optional[Path]:
        if getattr(phase, "iteration", 0) <= 1:
            return None
        return phase._get_versioned_file_path(step_name, phase.iteration - 1, phase.phase_dir)

    @staticmethod
    def _display_previous_output(
        phase: Any, step_name: str, previous_output_file: Optional[Path]
    ) -> None:
        if previous_output_file is None:
            return
        print(f"\nLoading latest {step_name} output: {previous_output_file}\n")
        if previous_output_file.exists():
            print("=" * 60)
            print(previous_output_file.read_text(encoding="utf-8"))
            print("=" * 60)
            print()

    @staticmethod
    def _display_previous_iteration_delta(phase: Any, previous_output_file: Optional[Path]) -> bool:
        if previous_output_file is None:
            return False
        from cafe.ui.cli import _display_iteration_delta, console

        # Delta view requires at least two historical snapshots.
        # If current review target is the first iteration output, there is no
        # meaningful "previous iteration" to diff against.
        if (phase.iteration - 1) <= 1:
            return False

        _display_iteration_delta(
            phase.iteration - 1,
            str(previous_output_file),
            console,
        )
        return True

    @staticmethod
    def _resolve_review_item_name(step_name: str) -> str:
        """Use the playbook step identifier instead of development-flow labels."""
        return step_name

    @staticmethod
    def _resolve_phase_specific_data(agent_name: str) -> dict[str, str]:
        """Keep review helpers supplied with a generic optional agent name."""
        return {"agent_name": agent_name} if agent_name else {}

    def run(self, **kwargs: Any) -> HookResult:
        stage = kwargs.get("stage")
        if stage != "prepare_input":
            return HookResult()

        phase = kwargs.get("phase")
        if phase is None:
            return HookResult()

        step_name = str(kwargs.get("step_name") or kwargs["step_def"].get("name") or "")
        step_def = kwargs["step_def"]
        agent_name = str(kwargs.get("agent_name") or "")
        role = str(step_def.get("role", "developer"))

        if getattr(phase, "iteration", 0) == 1:
            initial = self._collect_declared_human_task(
                phase=phase,
                step_name=step_name,
                step_def=step_def,
                trigger="initial",
            )
            if initial is not None:
                return initial

        previous_status = _get_previous_iteration_status(phase)
        if previous_status == "no_changes_needed":
            result = self._collect_declared_human_task(
                phase=phase,
                step_name=step_name,
                step_def=step_def,
                trigger="no_changes_needed",
            )
            if result is not None:
                return result

        if previous_status not in {"need_clarification", "ready_for_review"}:
            return HookResult()

        # Publishing steps use ready_for_review to inspect external feedback,
        # not to request a duplicate user confirmation.
        if _publish_confirmation_declared(
            context=kwargs.get("context"), step_def=step_def
        ) and previous_status in {
            "ready_for_review",
            "confirm_output",
        }:
            return HookResult()

        current_iter_dir = phase._get_iteration_dir(phase.iteration)
        current_user_input_file = current_iter_dir / "user_input.md"
        if current_user_input_file.exists():
            existing_user_input = current_user_input_file.read_text(encoding="utf-8").strip()
            if existing_user_input:
                phase.step_user_inputs[step_name] = existing_user_input
                return HookResult(
                    context_updates={"user_input": existing_user_input},
                    events=[
                        {
                            "type": "user_input_collected",
                            "step": step_name,
                            "source": "user_input_file",
                        }
                    ],
                )

        # Non-interactive mode: cannot call InquirerPy prompts.
        # Return empty result — the workflow stops at the user step and the
        # caller provides input via --user-input on resume.
        if not getattr(phase, "interactive", False):
            return HookResult()

        prompt_role = {"pm": "pm", "reviewer": "reviewer"}.get(role, "developer")
        previous_output_file = self._get_previous_output_file(phase, step_name)
        # Steps that declare confirm_output use delta view on READY_FOR_REVIEW (less noisy).
        if not (
            step_on_declares(step_def, "confirm_output") and previous_status == "ready_for_review"
        ):
            self._display_previous_output(phase, step_name, previous_output_file)

        if previous_status in {"confirm_output", "ready_for_review"}:
            delta_displayed = self._display_previous_iteration_delta(phase, previous_output_file)
            if not delta_displayed:
                self._display_previous_output(phase, step_name, previous_output_file)
            prev_data = phase._load_previous_iteration_data() or {}
            # Show diff again after returning from chat/edit, but never print full output.
            if delta_displayed:
                def redisplay_callback() -> None:
                    self._display_previous_iteration_delta(phase, previous_output_file)
            else:
                def redisplay_callback() -> None:
                    self._display_previous_output(phase, step_name, previous_output_file)
            choice = phase._ask_user_for_review_decision(
                self._resolve_review_item_name(step_name),
                agent_name=agent_name,
                role=prompt_role,
                output_file=previous_output_file,
                display_callback=redisplay_callback,
                edit_option_label="Edit manually - Open in editor",
            )
            result_or_input = phase._process_review_decision(
                choice,
                prev_data,
                self._resolve_review_item_name(step_name),
                self._resolve_phase_specific_data(agent_name),
            )
            if choice == "confirm":
                return HookResult(
                    continue_pipeline=False,
                    override_status_code=PhaseStatusCode.CONFIRMED,
                    events=[
                        {"type": "review_confirmed", "step": step_name},
                        {"type": "review_confirmed_advance", "step": step_name},
                    ],
                )

            phase.step_user_inputs[step_name] = str(result_or_input)
            return HookResult(
                context_updates={"user_input": str(result_or_input)},
                events=[
                    {
                        "type": "review_modification_requested",
                        "step": step_name,
                    }
                ],
            )

        prev_iter_dir = phase._get_iteration_dir(phase.iteration - 1)
        questions_xml_path = prev_iter_dir / "questions.xml"
        if questions_xml_path.exists() and validate_questions_xml(questions_xml_path):
            questions = parse_questions_xml(questions_xml_path)
            user_input = interactive_qa_flow(
                questions,
                role=prompt_role,
                issue_name=phase.issue_name,
                agent_name=agent_name,
            )
        else:
            user_input = prompt_multiline(
                f"Answer the pending clarification for {step_name}"
            ).strip()

        phase.step_user_inputs[step_name] = user_input
        return HookResult(
            context_updates={"user_input": user_input},
            events=[
                {
                    "type": "user_input_collected",
                    "step": step_name,
                    "source": "questions_xml" if questions_xml_path.exists() else "prompt",
                }
            ],
        )

    @staticmethod
    def _collect_declared_human_task(
        *,
        phase: Any,
        step_name: str,
        step_def: dict[str, Any],
        trigger: str,
    ) -> Optional[HookResult]:
        """Collect a policy-declared initial or resumed response when available."""
        from cafe.core.human_tasks import HumanTaskCompletion, HumanTaskPolicyError
        from cafe.ui.human_tasks import (
            collect_human_task_payload,
            resolve_step_human_task,
            validate_step_human_task_completion,
        )

        try:
            policy, _binding = resolve_step_human_task(
                playbook_data={"steps": {step_name: step_def}},
                step_name=step_name,
                trigger=trigger,
                iteration=int(getattr(phase, "iteration", 1)),
            )
        except HumanTaskPolicyError:
            return None

        if getattr(phase, "interactive", False):
            payload = collect_human_task_payload(policy)
        else:
            payload = str(getattr(phase, "user_input", "") or "").strip()
            if not payload:
                iteration_dir = phase._get_iteration_dir(getattr(phase, "iteration", 1))
                input_file = iteration_dir / "user_input.md"
                if input_file.exists():
                    payload = input_file.read_text(encoding="utf-8").strip()
            if not payload and trigger == "initial":
                payload = {"task": policy.id, "feedback": ""}

        _policy, _binding, result = validate_step_human_task_completion(
            playbook_data={"steps": {step_name: step_def}},
            step_name=step_name,
            trigger=trigger,
            raw_payload=payload or {},
            iteration=int(getattr(phase, "iteration", 1)),
        )
        if not isinstance(result, HumanTaskCompletion):
            return HookResult(
                continue_pipeline=False,
                events=[
                    {
                        "type": "human_task_rejected",
                        "step": step_name,
                        "trigger": trigger,
                        "task_id": policy.id,
                        "reason": result.message,
                    }
                ],
            )

        if result.decision in {"confirm", "approve", "agree"}:
            return HookResult(
                continue_pipeline=False,
                override_status_code=PhaseStatusCode.CONFIRMED,
                events=[
                    {
                        "type": "human_task_completed",
                        "step": step_name,
                        "trigger": trigger,
                        "task_id": policy.id,
                    }
                ],
            )

        user_input = result.agent_input()
        phase.step_user_inputs[step_name] = user_input
        return HookResult(
            context_updates={"user_input": user_input},
            events=[
                {
                    "type": "human_task_completed",
                    "step": step_name,
                    "trigger": trigger,
                    "task_id": policy.id,
                }
            ],
        )


class NoChangesNeededHandler(NoOpHook):
    """Require reasoning in output.md when the agent returns no_changes_needed."""

    name = "NoChangesNeededHandler"

    def run(self, **kwargs: Any) -> HookResult:
        stage = kwargs.get("stage")
        if stage != "after_execute":
            return HookResult()

        step_name = str(kwargs.get("step_name") or "")
        step_def = kwargs.get("step_def")
        if not _declares_no_changes_task(step_def):
            return HookResult()

        response = str(kwargs.get("response") or "")
        from cafe.core.status_codes import StatusCodeParser

        status = StatusCodeParser.extract(
            response,
            valid_codes=[PhaseStatusCode.NO_CHANGES_NEEDED],
        )
        if status != PhaseStatusCode.NO_CHANGES_NEEDED:
            return HookResult()

        context = kwargs.get("context") or {}
        output_display = str(context.get("output_file") or "")
        if not output_display:
            return HookResult()

        output_file = Path(output_display)
        if not output_file.is_absolute():
            output_file = Path.cwd() / output_file

        has_reasoning = output_file.exists() and output_file.stat().st_size > 0
        if not has_reasoning:
            continuation = (
                "Your response returned no_changes_needed.\n\n"
                "You MUST provide the reasoning that supports this outcome.\n\n"
                f"Please:\n1. Write your detailed reasoning to {output_display}\n"
                "2. Return no_changes_needed again\n\n"
                "Do NOT return any other status code until you have written your reasoning."
            )
            return HookResult(
                retry_requested=True,
                context_updates={"continuation_prompt": continuation},
                events=[{"type": "no_changes_reasoning_required", "step": step_name}],
            )

        return HookResult(
            continue_pipeline=False,
            override_status_code=PhaseStatusCode.NO_CHANGES_NEEDED,
            events=[{"type": "no_changes_awaiting_user", "step": step_name}],
        )


def _declares_no_changes_task(step_def: Any) -> bool:
    """Return whether this step explicitly opts into a no-changes user gate."""
    if not isinstance(step_def, dict):
        return False
    tasks = step_def.get("human_tasks")
    if not isinstance(tasks, (list, tuple)):
        return False
    return any(
        isinstance(task, dict) and task.get("trigger") == "no_changes_needed"
        for task in tasks
    )


class InitialInputProviderResolver(NoOpHook):
    """Resolve declared entry-step input through trusted host-side providers."""

    name = "InitialInputProviderResolver"

    def run(self, **kwargs: Any) -> HookResult:
        if kwargs.get("stage") != "prepare_input":
            return HookResult()

        phase = kwargs.get("phase")
        step_name = str(kwargs.get("step_name") or "")
        step_def = kwargs.get("step_def")
        if phase is None or not isinstance(step_def, dict):
            return HookResult()
        if getattr(phase, "iteration", 0) != 1:
            return HookResult()

        declaration = step_def.get("initial_input")
        if not isinstance(declaration, dict):
            return HookResult()
        legacy_presentation = declaration.get("legacy_presentation") is True
        providers = declaration.get("providers")
        binding = declaration.get("bind")
        if not isinstance(providers, list) or not isinstance(binding, dict):
            raise ValueError(f"initial_input declaration for step {step_name!r} is invalid")

        output_file: Optional[Path] = kwargs.get("output_file")
        artifact = binding.get("artifact")
        if artifact is not None:
            if output_file is None:
                raise ValueError(
                    f"initial_input.bind.artifact for step {step_name!r} requires output_file"
                )
            if output_file.exists() and output_file.read_text(encoding="utf-8").strip():
                return HookResult()

        legacy_adapter = GitHubIssueFetcher() if legacy_presentation else None
        legacy_empty_seed = self._should_seed_empty_legacy_requirements(
            phase=phase,
            step_name=step_name,
            providers=providers,
            context=kwargs.get("context"),
            legacy_presentation=legacy_presentation,
        )
        if legacy_empty_seed:
            result = InitialInputResult(
                content="",
                provider=MANUAL_TEXT_PROVIDER,
                source="non_interactive_no_input",
            )
        else:
            result = self._resolve(
                phase=phase,
                step_name=step_name,
                providers=providers,
                context=kwargs.get("context"),
                prompt_input_method=(
                    kwargs.get("initial_input_prompt_input_method")
                    or (
                        legacy_adapter._prompt_and_save_input_method(phase)
                        if legacy_adapter is not None
                        else None
                    )
                ),
                prompt_manual_input=(
                    kwargs.get("initial_input_prompt_manual_input")
                    or (legacy_adapter._prompt_manual_input if legacy_adapter is not None else None)
                ),
                fetch_github_issue=(
                    kwargs.get("initial_input_fetch_github_issue")
                    or (legacy_adapter._fetch_github_issue if legacy_adapter is not None else None)
                ),
            )
        if artifact is not None:
            assert output_file is not None
            output_file.parent.mkdir(parents=True, exist_ok=True)
            formatter = kwargs.get("initial_input_output_formatter") or (
                legacy_adapter._format_initial_requirements
                if legacy_adapter is not None
                else None
            )
            content = formatter(result.content) if callable(formatter) else result.content
            if legacy_empty_seed:
                output_file.write_text(f"{content}\n", encoding="utf-8")
            else:
                output_file.write_text(f"{content.rstrip()}\n", encoding="utf-8")

        context_updates = (
            {"user_input": result.content}
            if binding.get("prompt_context") == "user_input"
            else {}
        )
        return HookResult(
            context_updates=context_updates,
            events=[
                {
                    "type": "initial_input_resolved",
                    "step": step_name,
                    "provider": result.provider,
                }
            ],
        )

    def _should_seed_empty_legacy_requirements(
        self,
        *,
        phase: Any,
        step_name: str,
        providers: list[Any],
        context: Any,
        legacy_presentation: bool,
    ) -> bool:
        """Keep built-in manual workflows compatible with their empty initial seed."""
        if not legacy_presentation or getattr(phase, "interactive", False):
            return False
        if MANUAL_TEXT_PROVIDER not in {str(provider) for provider in providers}:
            return False
        if self._resolve_prefilled_input(
            phase=phase,
            step_name=step_name,
            context=context,
        ) is not None:
            return False
        configured_provider, _issue_id = load_initial_input_selection(
            self._load_issue_config(phase)
        )
        return configured_provider in (None, MANUAL_TEXT_PROVIDER)

    def _resolve(
        self,
        *,
        phase: Any,
        step_name: str,
        providers: list[Any],
        context: Any,
        prompt_input_method: Any,
        prompt_manual_input: Any,
        fetch_github_issue: Any,
    ) -> InitialInputResult:
        declared = [str(provider) for provider in providers]
        declared_set = set(declared)
        prefilled = self._resolve_prefilled_input(
            phase=phase,
            step_name=step_name,
            context=context,
        )
        if prefilled is not None:
            self._require_declared(MANUAL_TEXT_PROVIDER, declared_set, step_name)
            return InitialInputResult(
                content=prefilled,
                provider=MANUAL_TEXT_PROVIDER,
                source="workflow_user_input",
            )

        config = self._load_issue_config(phase)
        provider, issue_id = load_initial_input_selection(config)
        if provider is None:
            provider, issue_id = self._select_provider(
                phase=phase,
                providers=declared,
                prompt_input_method=prompt_input_method,
            )
            self._require_declared(provider, declared_set, step_name)
            self._save_selection(phase, provider, issue_id)
        else:
            self._require_declared(provider, declared_set, step_name)

        if provider == GITHUB_ISSUE_PROVIDER:
            if issue_id is None:
                raise ValueError("initial_input.provider 'github_issue' requires issue_id")
            fetch = fetch_github_issue or self._fetch_github_issue
            try:
                content = str(fetch(issue_id)).strip()
            except Exception as exc:
                raise ValueError(
                    f"initial_input.provider 'github_issue' could not fetch issue {issue_id}: {exc}"
                ) from exc
            if not content:
                raise ValueError(f"GitHub issue {issue_id} produced no initial input")
            return InitialInputResult(content=content, provider=provider, source="github_issue")

        if not getattr(phase, "interactive", False):
            raise ValueError(
                "initial_input.provider 'manual_text' requires non-empty invocation input "
                "when running non-interactively"
            )
        prompt = prompt_manual_input or self._prompt_manual_input
        content = str(prompt()).strip()
        if not content:
            raise ValueError("initial_input.provider 'manual_text' received empty input")
        return InitialInputResult(content=content, provider=provider, source="manual_text")

    @staticmethod
    def _require_declared(provider: str, declared: set[str], step_name: str) -> None:
        if provider not in declared:
            raise ValueError(
                f"initial_input.provider {provider!r} is not declared for entry step {step_name!r}"
            )

    @staticmethod
    def _load_issue_config(phase: Any) -> dict[str, Any]:
        config_file = getattr(phase, "issue_dir", None)
        if not isinstance(config_file, Path):
            return {}
        config_file = config_file / "issue.yaml"
        if not config_file.exists():
            return {}
        try:
            import yaml

            data = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _save_selection(phase: Any, provider: str, issue_id: Optional[int]) -> None:
        issue_dir = getattr(phase, "issue_dir", None)
        if not isinstance(issue_dir, Path):
            return
        config_file = issue_dir / "issue.yaml"
        data = InitialInputProviderResolver._load_issue_config(phase)
        data["initial_input"] = {"provider": provider}
        if issue_id is not None:
            data["initial_input"]["issue_id"] = issue_id
        import yaml

        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(
            yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8"
        )

    @staticmethod
    def _select_provider(
        *,
        phase: Any,
        providers: list[str],
        prompt_input_method: Any,
    ) -> tuple[str, Optional[int]]:
        if len(providers) == 1:
            provider = providers[0]
            if provider != GITHUB_ISSUE_PROVIDER or not getattr(phase, "interactive", False):
                return provider, None
            prompt = prompt_input_method or InitialInputProviderResolver._prompt_github_issue
            return InitialInputProviderResolver._normalize_provider_selection(prompt())
        if not getattr(phase, "interactive", False):
            raise ValueError(
                "initial_input.provider must be selected before non-interactive execution"
            )
        prompt = prompt_input_method or (
            lambda: InitialInputProviderResolver._prompt_declared_provider(providers)
        )
        return InitialInputProviderResolver._normalize_provider_selection(prompt())

    @staticmethod
    def _normalize_provider_selection(
        selection: tuple[str, Optional[int]],
    ) -> tuple[str, Optional[int]]:
        provider, issue_id = selection
        normalized = {"manual": MANUAL_TEXT_PROVIDER, "github": GITHUB_ISSUE_PROVIDER}.get(
            provider, provider
        )
        return normalized, issue_id

    @staticmethod
    def _resolve_prefilled_input(
        *,
        phase: Any,
        step_name: str,
        context: Any,
    ) -> Optional[str]:
        if hasattr(phase, "step_user_inputs"):
            step_user_inputs = getattr(phase, "step_user_inputs")
            if isinstance(step_user_inputs, dict):
                raw_user_input = step_user_inputs.get(step_name)
                if isinstance(raw_user_input, str) and raw_user_input.strip():
                    return raw_user_input.strip()

        if isinstance(context, dict):
            raw_user_input = context.get("user_input")
            if isinstance(raw_user_input, str) and raw_user_input.strip():
                return raw_user_input.strip()

        return None

    @staticmethod
    def _prompt_declared_provider(providers: list[str]) -> tuple[str, Optional[int]]:
        provider = prompt_list("Select initial input provider:", choices=providers)
        if provider == GITHUB_ISSUE_PROVIDER:
            return InitialInputProviderResolver._prompt_github_issue()
        return provider, None

    @staticmethod
    def _prompt_github_issue() -> tuple[str, Optional[int]]:
        while True:
            issue_input = prompt_text(message="GitHub Issue ID or URL:", default="")
            try:
                issue_id = int(GitHubOps().extract_issue_number(issue_input))
            except (ValueError, GitHubError) as exc:
                print(f"Invalid GitHub Issue ID or URL: {exc}")
                continue
            return GITHUB_ISSUE_PROVIDER, issue_id

    @staticmethod
    def _prompt_manual_input() -> str:
        content = prompt_multiline("Provide the initial input").strip()
        if not content:
            raise ValueError("initial_input.provider 'manual_text' received empty input")
        return content

    @staticmethod
    def _fetch_github_issue(issue_id: int) -> str:
        from cafe.ui.phase_prompts import fetch_github_issue

        fetched_content, _image_urls = fetch_github_issue(GitHubOps(), issue_id)
        lines = fetched_content.split("\n", 1)
        if lines[0].startswith("# "):
            title = lines[0][2:].strip()
            body = lines[1].strip() if len(lines) > 1 else ""
            return f"**Issue Title:** {title}\n\n{body}" if body else f"**Issue Title:** {title}"
        return fetched_content


class GitHubIssueFetcher(NoOpHook):
    """Compatibility adapter for legacy initial-input hooks."""

    name = "GitHubIssueFetcher"

    def run(self, **kwargs: Any) -> HookResult:
        if kwargs.get("stage") != "prepare_input":
            return HookResult()
        phase = kwargs.get("phase")
        step_name = str(kwargs.get("step_name") or "")
        output_file: Optional[Path] = kwargs.get("output_file")
        if phase is None or output_file is None:
            return HookResult()
        if getattr(phase, "iteration", 0) != 1:
            return HookResult()
        if output_file.exists() and output_file.read_text(encoding="utf-8").strip():
            return HookResult()

        prefilled = self._resolve_prefilled_input(
            phase=phase,
            step_name=step_name,
            context=kwargs.get("context"),
        )
        config = InitialInputProviderResolver._load_issue_config(phase)
        configured_provider, _issue_id = load_initial_input_selection(config)
        if (
            prefilled is None
            and not getattr(phase, "interactive", False)
            and configured_provider != GITHUB_ISSUE_PROVIDER
        ):
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text("# Initial Requirements\n\n\n", encoding="utf-8")
            return HookResult(
                context_updates={"user_input": ""},
                events=[
                    {
                        "type": "user_input_collected",
                        "step": step_name,
                        "source": (
                            "github"
                            if configured_provider == GITHUB_ISSUE_PROVIDER
                            else "non_interactive_no_input"
                        ),
                    }
                ],
            )

        legacy_step_def = dict(kwargs.get("step_def") or {})
        legacy_step_def["initial_input"] = {
            "providers": [MANUAL_TEXT_PROVIDER, GITHUB_ISSUE_PROVIDER],
            "bind": {
                "artifact": legacy_step_def.get("output_artifact", step_name),
                "prompt_context": "user_input",
            },
        }
        resolver_kwargs = dict(kwargs)
        resolver_kwargs.update(
            {
                "step_def": legacy_step_def,
                "initial_input_output_formatter": self._format_initial_requirements,
                "initial_input_prompt_input_method": self._prompt_and_save_input_method(phase),
                "initial_input_prompt_manual_input": self._prompt_manual_input,
                "initial_input_fetch_github_issue": self._fetch_github_issue,
            }
        )
        result = InitialInputProviderResolver().run(**resolver_kwargs)
        provider = result.events[0]["provider"] if result.events else MANUAL_TEXT_PROVIDER
        source = (
            "workflow_user_input"
            if prefilled is not None
            else "github" if provider == GITHUB_ISSUE_PROVIDER else "manual"
        )
        return HookResult(
            continue_pipeline=result.continue_pipeline,
            retry_requested=result.retry_requested,
            artifact_ready=result.artifact_ready,
            override_status_code=result.override_status_code,
            context_updates=result.context_updates,
            events=[{"type": "user_input_collected", "step": step_name, "source": source}],
        )

    def _prompt_and_save_input_method(self, phase: Any) -> Any:
        def prompt() -> tuple[str, Optional[int]]:
            method, issue_id = self._prompt_input_method()
            self._save_input_config(phase.issue_dir / "issue.yaml", method, issue_id)
            return method, issue_id

        return prompt

    @staticmethod
    def _resolve_prefilled_input(
        *,
        phase: Any,
        step_name: str,
        context: Any,
    ) -> Optional[str]:
        return InitialInputProviderResolver._resolve_prefilled_input(
            phase=phase,
            step_name=step_name,
            context=context,
        )

    @staticmethod
    def _format_initial_requirements(content: str) -> str:
        return f"# Initial Requirements\n\n{content}"

    @staticmethod
    def _load_input_config(config_file: Path) -> tuple[Optional[str], Optional[int]]:
        if not config_file.exists():
            return None, None
        try:
            import yaml

            data = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
        except Exception:
            return None, None
        spec = data.get("spec", {})
        method = spec.get("input_method")
        raw_id = spec.get("issue_id")
        return method, int(raw_id) if raw_id else None

    @staticmethod
    def _save_input_config(config_file: Path, method: str, issue_id: Optional[int]) -> None:
        import yaml

        try:
            data = (
                yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
                if config_file.exists()
                else {}
            )
        except Exception:
            data = {}
        if "spec" not in data:
            data["spec"] = {}
        data["spec"]["input_method"] = method
        if issue_id is not None:
            data["spec"]["issue_id"] = issue_id
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(
            yaml.dump(data, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )

    @staticmethod
    def _prompt_input_method() -> tuple[str, Optional[int]]:
        from cafe.ui.display import Display
        from cafe.ui.phase_prompts import prompt_for_input_method

        return prompt_for_input_method(Display(), GitHubOps())

    @staticmethod
    def _prompt_manual_input() -> str:
        print()
        print("=" * 70)
        print("Please describe your requirements:")
        print("=" * 70)
        print()
        print("Recommended to write as user stories:")
        print("   Format: As a [role], I want [feature], so that [purpose/value]")
        print()
        print("Or describe requirements in general terms:")
        print("   - Add a CSV export feature")
        print("   - Fix bug where login page cannot submit")
        print()

        content = prompt_multiline("Please enter your requirements").strip()
        if not content:
            raise ValueError("No requirements provided, cannot continue")
        print()
        print("\u2705 Requirements recorded, starting clarification...")
        print()
        return content

    @staticmethod
    def _fetch_github_issue(issue_id: int) -> str:
        from cafe.ui.phase_prompts import fetch_github_issue as _fetch

        gh_ops = GitHubOps()
        fetched_content, _image_urls = _fetch(gh_ops, issue_id)

        lines = fetched_content.split("\n", 1)
        if lines[0].startswith("# "):
            title = lines[0][2:].strip()
            body = lines[1].strip() if len(lines) > 1 else ""
            content = f"**Issue Title:** {title}\n\n{body}" if body else f"**Issue Title:** {title}"
        else:
            content = fetched_content

        print()
        print(f"\u2705 Requirements loaded from GitHub Issue #{issue_id}")
        print("   Starting clarification...")
        print()
        return content


class GitHubPRCreator(NoOpHook):
    """Prepare generic PR iterations for GitHub mode and sync PR metadata."""

    name = "GitHubPRCreator"
    TRUSTED_PR_SCRIPT = "src/cafe/data/skills/cafe-pr/scripts/sync_pr.sh"

    def run(self, **kwargs: Any) -> HookResult:
        stage = kwargs.get("stage")
        if stage == "prepare_input":
            return self._prepare_input(**kwargs)
        if stage == "publish_output":
            return self._publish_output(**kwargs)
        return HookResult()

    def _prepare_input(self, **kwargs: Any) -> HookResult:
        phase = kwargs.get("phase")
        if phase is None:
            return HookResult()

        try:
            branch_name = phase.git_ops.get_current_branch()
        except Exception:
            return HookResult()
        if not branch_name:
            return HookResult()

        try:
            github_ops = GitHubOps()
            existing_pr = github_ops.get_pr_for_branch(branch_name)
        except Exception:
            return HookResult()

        if not existing_pr:
            return HookResult()

        try:
            has_unpushed_commits = phase.git_ops.has_unpushed_commits()
        except Exception:
            has_unpushed_commits = False

        context_updates = {
            "pr_number": str(existing_pr["number"]),
            "pr_url": str(existing_pr["url"]),
        }
        if has_unpushed_commits:
            return HookResult(context_updates=context_updates)

        try:
            exclude_ids = load_pr_last_seen_comment_ids(phase.phase_dir)
            if not exclude_ids:
                exclude_ids = get_processed_comment_ids_from_history(phase.phase_dir)
            comments = get_all_pr_comments(int(existing_pr["number"]), exclude_ids=exclude_ids)
            _persist_pr_last_seen_comment_ids(phase.phase_dir, _extract_pr_comment_ids(comments))
            unresolved_comments = filter_unresolved_comments(comments)
        except Exception:
            return HookResult(context_updates=context_updates)

        if not unresolved_comments:
            return HookResult(context_updates=context_updates)

        formatted_comments = format_comments_for_prompt(unresolved_comments).strip()
        if not formatted_comments:
            return HookResult(context_updates=context_updates)

        phase.step_user_inputs[str(kwargs.get("step_name") or "pr")] = formatted_comments
        context_updates["user_input"] = formatted_comments
        context_updates["pr_comment_count"] = str(len(unresolved_comments))
        context_updates["pr_mode"] = "comments"
        return HookResult(
            context_updates=context_updates,
            events=[
                {
                    "type": "pr_comments_loaded",
                    "count": len(unresolved_comments),
                    "pr_number": str(existing_pr["number"]),
                }
            ],
        )

    def _publish_output(self, **kwargs: Any) -> HookResult:
        from cafe.core.blackboard import BlackboardStore
        from cafe.core.capabilities import (
            CAPABILITY_PR_PUBLISH_ID,
            SCRIPT_EXIT_ERROR,
            TIMEOUT_ERROR,
            VALIDATION_ERROR,
            CapabilityRegistryError,
            capability_receipt_hook_event,
            default_capability_definition_dirs,
            load_capability_registry,
            run_capability_request,
        )

        phase = kwargs.get("phase")
        step_name = str(kwargs.get("step_name") or "")
        step_def = kwargs.get("step_def") or {}
        if phase is None:
            return HookResult()
        if not _capability_execution_requested(
            phase=phase,
            step_name=step_name,
            step_def=step_def,
            status_code=kwargs.get("status_code"),
            context=kwargs.get("context"),
        ):
            return HookResult()
        output_file = kwargs.get("output_file")
        if phase is None or not isinstance(output_file, Path) or not output_file.exists():
            return HookResult()
        if self._is_local_pr_mode(phase):
            return HookResult()

        repo_root = self._resolve_repo_root(phase)
        capability_request_file = kwargs.get("capability_request_file")
        publish_request_file = kwargs.get("publish_request_file")
        blackboard_state = kwargs.get("blackboard_state")
        issue_dir = getattr(phase, "issue_dir", None)
        fallback_capability = (
            _effective_capability_ids(step_name=step_name, step_def=step_def)
            or [CAPABILITY_PR_PUBLISH_ID]
        )[0]

        def persist_receipt(receipt: dict[str, Any]) -> None:
            if isinstance(blackboard_state, BlackboardState) and isinstance(issue_dir, Path):
                BlackboardStore(issue_dir).append_capability_receipt(blackboard_state, receipt)

        try:
            request_payload = self._load_publish_request(
                publish_request_file=(
                    capability_request_file
                    if isinstance(capability_request_file, Path)
                    else publish_request_file if isinstance(publish_request_file, Path) else None
                ),
                repo_root=repo_root,
            )
            requests = self._normalize_capability_requests(request_payload)
        except RuntimeError:
            receipt = {
                "capability": fallback_capability,
                "correlation_id": uuid.uuid4().hex[:20],
                "success": False,
                "category": VALIDATION_ERROR,
                "code": "request_load_error",
                "inputs": {},
                "outputs": {},
                "finished_at": datetime.now().astimezone().isoformat(),
            }
            persist_receipt(receipt)
            return HookResult(events=[capability_receipt_hook_event(receipt)])

        try:
            registry = load_capability_registry(default_capability_definition_dirs(repo_root))
        except CapabilityRegistryError:
            events: list[dict[str, Any]] = []
            for request in requests:
                receipt = {
                    "capability": str(request.get("capability") or fallback_capability),
                    "correlation_id": uuid.uuid4().hex[:20],
                    "success": False,
                    "category": VALIDATION_ERROR,
                    "code": "registry_load_error",
                    "inputs": self._request_inputs_for_receipt(request),
                    "outputs": {},
                    "finished_at": datetime.now().astimezone().isoformat(),
                }
                persist_receipt(receipt)
                events.append(capability_receipt_hook_event(receipt))
            return HookResult(events=events)

        events: list[dict[str, Any]] = []
        context_updates: dict[str, str] = {}
        for request in requests:
            run = run_capability_request(
                repo_root=repo_root,
                registry=registry,
                capability_request=request,
                output_file=output_file,
            )
            persist_receipt(run.receipt)

            if run.pr_synced_event is not None:
                events.append(run.pr_synced_event)
            events.append(capability_receipt_hook_event(run.receipt))

            if not run.receipt.get("success"):
                category = run.receipt.get("category")
                if category == SCRIPT_EXIT_ERROR:
                    details = (run.receipt.get("outputs") or {}).get("stderr") or ""
                    raise RuntimeError(f"PR sync script failed: {details}")
                if category == TIMEOUT_ERROR:
                    raise RuntimeError("PR sync timed out")
                continue

            if run.receipt.get("capability") == CAPABILITY_PR_PUBLISH_ID:
                pr_url = str((run.receipt.get("outputs") or {}).get("pr_url") or "").strip()
                pr_number = str((run.receipt.get("outputs") or {}).get("pr_number") or "").strip()
                action = str((run.receipt.get("outputs") or {}).get("action") or "synced").strip()
                context_updates.update(
                    {
                        key: value
                        for key, value in {
                            "pr_url": pr_url,
                            "pr_number": pr_number,
                            "pr_sync_action": action,
                        }.items()
                        if value
                    }
                )

        return HookResult(context_updates=context_updates, events=events)

    @staticmethod
    def _is_local_pr_mode(phase: Any) -> bool:
        try:
            value = phase._get_issue_config_value(
                phase.issue_dir / "issue.yaml",
                "pr.auto_create",
            )
        except Exception:
            return False
        return value is False

    @staticmethod
    def _resolve_repo_root(phase: Any) -> Path:
        try:
            repo_root = phase.git_ops.get_repo_root()
        except Exception:
            repo_root = Path.cwd()
        return Path(repo_root).resolve()

    @staticmethod
    def _resolve_sync_script(repo_root: Path) -> Path:
        loader = SkillLoader(project_root=repo_root)
        skill_dir = loader.get_skill_dir("cafe-pr")
        script_path = skill_dir / "scripts" / "sync_pr.sh"
        if script_path.exists():
            return script_path

        fallback = (
            Path(__file__).resolve().parents[2]
            / "data"
            / "skills"
            / "cafe-pr"
            / "scripts"
            / "sync_pr.sh"
        )
        if fallback.exists():
            return fallback
        raise FileNotFoundError(f"PR sync script not found: {script_path}")

    @staticmethod
    def _resolve_base_branch(phase: Any) -> Optional[str]:
        try:
            value = phase._get_issue_config_value(
                phase.issue_dir / "issue.yaml",
                "base_branch",
            )
        except Exception:
            return None
        return str(value).strip() if value else None

    @staticmethod
    def _load_publish_request(
        *,
        publish_request_file: Optional[Path],
        repo_root: Path,
    ) -> dict[str, Any]:
        if publish_request_file is None:
            raise RuntimeError("PR publish request is missing")
        request_file = publish_request_file.resolve()
        if not request_file.exists():
            raise RuntimeError(f"PR publish request not found: {request_file}")
        try:
            payload = json.loads(request_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"PR publish request is invalid JSON: {request_file}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("PR publish request must be a JSON object")
        return payload

    @staticmethod
    def _normalize_capability_requests(payload: dict[str, Any]) -> list[dict[str, Any]]:
        raw_requests = payload.get("requests")
        if raw_requests is None:
            GitHubPRCreator._validate_capability_request_payload(payload)
            return [dict(payload)]
        if not isinstance(raw_requests, list) or not raw_requests:
            raise RuntimeError("Capability requests must be a non-empty list")

        requests: list[dict[str, Any]] = []
        for raw_request in raw_requests:
            if not isinstance(raw_request, dict):
                raise RuntimeError("Capability request entries must be JSON objects")
            GitHubPRCreator._validate_capability_request_payload(raw_request)
            requests.append(dict(raw_request))
        return requests

    @staticmethod
    def _validate_capability_request_payload(payload: dict[str, Any]) -> None:
        permissions = payload.get("permissions")
        if permissions is not None and not isinstance(permissions, dict):
            raise RuntimeError("PR publish request permissions must be an object")

    @staticmethod
    def _request_inputs_for_receipt(request: dict[str, Any]) -> dict[str, Any]:
        args = request.get("args")
        return dict(args) if isinstance(args, dict) else {}


class PRCommentPoster(NoOpHook):
    """Post generated PR todo lists back to the PR when comments require follow-up."""

    name = "PRCommentPoster"

    @staticmethod
    def _is_post_todo_enabled(phase: Any) -> bool:
        issue_dir = getattr(phase, "issue_dir", None)
        if not isinstance(issue_dir, Path):
            return True
        issue_yaml = issue_dir / "issue.yaml"
        if not issue_yaml.exists():
            return True
        try:
            import yaml

            data = yaml.safe_load(issue_yaml.read_text(encoding="utf-8")) or {}
            pr_cfg = data.get("pr") or {}
            value = pr_cfg.get("post_todo_list")
            if value is None:
                return True
            return bool(value)
        except Exception:
            return True

    def run(self, **kwargs: Any) -> HookResult:
        if kwargs.get("stage") != "publish_output":
            return HookResult()
        phase = kwargs.get("phase")
        step_name = str(kwargs.get("step_name") or "")
        if not _publish_requested(
            phase=phase,
            step_name=step_name,
            status_code=kwargs.get("status_code"),
            context=kwargs.get("context"),
            step_def=kwargs.get("step_def"),
        ):
            return HookResult()
        output_file = kwargs.get("output_file")
        if phase is None or not isinstance(output_file, Path) or not output_file.exists():
            return HookResult()
        if not self._is_post_todo_enabled(phase):
            return HookResult()

        try:
            from cafe.utils.checklist_validator import validate_checklist

            branch_name = phase.git_ops.get_current_branch()
            if not branch_name:
                return HookResult()
            github_ops = GitHubOps()
            existing_pr = github_ops.get_pr_for_branch(branch_name)
            if not existing_pr:
                return HookResult()
            todo_list = output_file.read_text(encoding="utf-8").strip()
            if not todo_list:
                return HookResult()
            is_todo_list = (
                "## Todo List" in todo_list
                or "## Todo" in todo_list
                or "- [ ]" in todo_list
                or "- [x]" in todo_list
            )
            if not is_todo_list:
                return HookResult()
            if not validate_checklist(output_file).is_complete:
                return HookResult()

            comment_body = (
                "> CAFE organized the latest PR feedback into a follow-up todo list.\n\n"
                f"{todo_list}"
            )
            github_ops.add_pr_comment(str(existing_pr["number"]), comment_body)
        except Exception:
            return HookResult()

        return HookResult(
            events=[
                {
                    "type": "pr_todo_comment_posted",
                    "pr_number": str(existing_pr["number"]),
                }
            ]
        )


class LocalPRReviewer(NoOpHook):
    """Display local code diff and collect user confirmation for local PR mode."""

    name = "LocalPRReviewer"

    @staticmethod
    def _is_local_pr_mode(phase: Any) -> bool:
        issue_dir = getattr(phase, "issue_dir", None)
        if not isinstance(issue_dir, Path):
            return False
        try:
            value = phase._get_issue_config_value(issue_dir / "issue.yaml", "pr.auto_create")
        except Exception:
            return False
        return value is False

    @staticmethod
    def _format_todo_feedback(feedback: str) -> str:
        lines = [line.strip() for line in feedback.splitlines() if line.strip()]
        todos = []
        for line in lines:
            normalized = line
            for prefix in ("- [ ]", "- [x]", "-", "*"):
                if normalized.startswith(prefix):
                    normalized = normalized[len(prefix) :].strip()
                    break
            if normalized:
                todos.append(f"- [ ] {normalized}")
        if not todos:
            todos.append("- [ ] Address local review feedback")
        return "# Local review feedback\n\n## Todo List\n" + "\n".join(todos) + "\n"

    def run(self, **kwargs: Any) -> HookResult:
        if kwargs.get("stage") != "publish_output":
            return HookResult()
        phase = kwargs.get("phase")
        step_name = str(kwargs.get("step_name") or "")
        if not _publish_requested(
            phase=phase,
            step_name=step_name,
            status_code=kwargs.get("status_code"),
            context=kwargs.get("context"),
            step_def=kwargs.get("step_def"),
        ):
            return HookResult()
        output_file = kwargs.get("output_file")
        if not isinstance(output_file, Path):
            return HookResult()
        if not self._is_local_pr_mode(phase):
            return HookResult()
        if not getattr(phase, "interactive", False):
            return HookResult(
                override_status_code=PhaseStatusCode.NEED_CLARIFICATION,
                events=[{"type": "local_pr_review_required", "reason": "non_interactive"}],
            )

        try:
            base_branch = phase._get_issue_config_value(
                phase.issue_dir / "issue.yaml", "base_branch"
            )
            resolved_base = str(base_branch or phase.git_ops.get_default_base_branch())
            diff_output = phase.git_ops.get_diff(resolved_base, "HEAD")
        except Exception:
            return HookResult()

        from rich.console import Console
        from rich.panel import Panel
        from rich.syntax import Syntax

        console = Console()

        def _display_diff() -> None:
            console.print()
            console.print(Panel.fit("Local Review Mode - Code Changes", style="bold cyan"))
            console.print()
            if diff_output.strip():
                console.print(Syntax(diff_output, "diff", theme="monokai", line_numbers=False))
            else:
                console.print("[yellow]No changes to review[/yellow]")
            console.print()

        _display_diff()
        choice = phase._ask_user_for_review_decision(
            "code changes",
            agent_name=str(kwargs.get("agent_name") or ""),
            role="developer",
            output_file=output_file,
            display_callback=_display_diff if diff_output.strip() else None,
        )
        result_or_input = phase._process_review_decision(
            choice=choice,
            prev_data={},
            phase_name="Local review",
            phase_specific_data={"local_review": True},
        )

        if choice == "confirm":
            return HookResult(events=[{"type": "local_pr_review_confirmed"}])

        feedback = str(result_or_input).strip()
        output_file.write_text(self._format_todo_feedback(feedback), encoding="utf-8")
        user_input_file = output_file.parent / "user_input.md"
        user_input_file.write_text(feedback, encoding="utf-8")
        return HookResult(
            override_status_code=PhaseStatusCode.NEEDS_CHANGES,
            events=[
                {
                    "type": "local_pr_review_changes_requested",
                    "user_input_file": str(user_input_file),
                }
            ],
        )


class PRLinkOpener(NoOpHook):
    """Open the created/updated PR in the user's browser."""

    name = "PRLinkOpener"

    def run(self, **kwargs: Any) -> HookResult:
        if kwargs.get("stage") != "publish_output":
            return HookResult()
        phase = kwargs.get("phase")
        step_name = str(kwargs.get("step_name") or "")
        if not _publish_requested(
            phase=phase,
            step_name=step_name,
            status_code=kwargs.get("status_code"),
            context=kwargs.get("context"),
            step_def=kwargs.get("step_def"),
        ):
            return HookResult()

        try:
            pr_url = GitHubOps().get_current_pr_url()
        except GitHubError:
            return HookResult()
        except Exception:
            return HookResult()

        if sys.stdin.isatty():
            try:
                webbrowser.open(pr_url)
            except Exception:
                return HookResult()
            return HookResult(events=[{"type": "pr_link_opened", "url": pr_url}])

        return HookResult()
