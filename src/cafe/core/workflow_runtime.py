"""Blackboard-first workflow runtime.

The workflow-core entry point. It keeps blackboard/baton state as the
primary source of truth for step transitions.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Dict, Optional

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.status_codes import PhaseStatusCode, StatusCodeParser, transition_map_key
from cafe.core.workflow_models import BatonRejected, PlaybookRunResult, StepExecutionResult, StepInterrupted


STATUS_TOKEN_PATTERN = re.compile(r"\bCAFE_[A-Z0-9_]+\b")
GOTO_PATTERN = re.compile(r"GOTO\s*:\s*([a-zA-Z0-9_-]+)")
PAUSE_STATUS_CODES = {
    PhaseStatusCode.READY_FOR_REVIEW.value,
    PhaseStatusCode.CONFIRM_OUTPUT.value,
    PhaseStatusCode.NEED_CLARIFICATION.value,
    PhaseStatusCode.NEED_PERMISSION.value,
}


@dataclass
class StepIterationFrame:
    execution_result: Any
    response: str
    artifacts: Dict[str, str]
    explicit_status_code: Optional[str]
    auto_continue: bool


@dataclass
class PostContractResult:
    status_code: str
    next_step: Optional[str] = None
    terminal_result: Optional[PlaybookRunResult] = None


class BlackboardWorkflowRuntime:
    """Workflow runtime that prefers blackboard/baton-driven transitions."""

    BATON_DRIVEN_STEPS = {"pr"}

    def __init__(
        self,
        *,
        issue_dir: Path,
        playbook: Dict,
        executor: Any,
    ) -> None:
        self.issue_dir = issue_dir
        self.playbook = playbook
        self.executor = executor

        playbook_meta = playbook["playbook"]
        self.playbook_id = str(playbook_meta["id"])
        self.steps: Dict = playbook["steps"]
        self.start_step = str(playbook.get("entry_point") or next(iter(self.steps.keys())))

        self.blackboard_store = BlackboardStore(issue_dir)
        self.blackboard = self.blackboard_store.load_or_create(self.start_step, playbook_id=self.playbook_id)

    @staticmethod
    def _extract_goto_target(response: str) -> Optional[str]:
        match = GOTO_PATTERN.search(response)
        if not match:
            return None
        return match.group(1)

    @staticmethod
    def _has_event(execution_result: Any, event_type: str) -> bool:
        events = getattr(execution_result, "events", None)
        if not isinstance(events, list):
            return False
        return any(isinstance(event, dict) and event.get("type") == event_type for event in events)

    @staticmethod
    def _pr_publish_receipt_satisfied(execution_result: Any) -> bool:
        """True when PR publish left a host receipt or legacy ``pr_synced`` marker."""
        if BlackboardWorkflowRuntime._has_event(execution_result, "pr_synced"):
            return True
        events = getattr(execution_result, "events", None)
        if not isinstance(events, list):
            return False
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("type") != "capability_receipt":
                continue
            if event.get("capability") != "cafe.pr.publish":
                continue
            if event.get("success") is True:
                return True
        return False

    @staticmethod
    def _default_pause_intent(current_step: str, status_code: str) -> HandoffIntent:
        if status_code in {
            PhaseStatusCode.READY_FOR_REVIEW.value,
            PhaseStatusCode.CONFIRM_OUTPUT.value,
        } and current_step in {"spec", "plan"}:
            return HandoffIntent.CONFIRM_OUTPUT
        if status_code == PhaseStatusCode.NEED_CLARIFICATION.value:
            return HandoffIntent.NEED_CLARIFICATION
        if status_code == PhaseStatusCode.NEED_PERMISSION.value:
            return HandoffIntent.NEED_PERMISSION
        return HandoffIntent.MANUAL_HANDOFF

    def _validate_agent_baton(self, *, current_step: str) -> None:
        contract = self.blackboard_store.load_handoff_contract(
            self.blackboard,
            allowed_steps=list(self.steps.keys()),
        )
        if contract.to_owner != HandoffOwner.AGENT:
            raise RuntimeError(
                f"Baton owner mismatch before step '{current_step}': expected agent, got {contract.to_owner.value}"
            )
        if contract.to_step != current_step:
            raise RuntimeError(
                f"Baton target mismatch before step '{current_step}': baton points to '{contract.to_step}'"
            )

    def _resolve_next_step(
        self,
        *,
        current_step: str,
        response: str,
        status_code: str,
    ) -> tuple[Optional[str], str]:
        step = self.steps[current_step]
        transitions = step.get("on", {})
        goto_target = self._extract_goto_target(response)
        if goto_target:
            allowed_targets = {str(target) for target in step.get("allowed_goto", [])}
            if goto_target in allowed_targets:
                self.blackboard_store.record_event(
                    self.blackboard,
                    "goto",
                    {
                        "step": current_step,
                        "goto_target": goto_target,
                    },
                )
                return goto_target, "goto"
            self.blackboard_store.record_event(
                self.blackboard,
                "goto_ignored",
                {
                    "step": current_step,
                    "goto_target": goto_target,
                    "reason": "not in allowed_goto",
                },
            )

        transition_key = status_code
        if status_code:
            try:
                transition_key = transition_map_key(PhaseStatusCode(status_code))
            except ValueError:
                transition_key = status_code
        target = transitions.get(transition_key) if transition_key else None
        if target is None:
            default_target = transitions.get("default")
            if default_target:
                return str(default_target), "default"
            return None, "terminal"
        return str(target), "status"

    @staticmethod
    def _extract_handoff_intent(execution_result: Any) -> Optional[HandoffIntent]:
        events = getattr(execution_result, "events", None)
        if not isinstance(events, list):
            return None
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("type") != "handoff_intent":
                continue
            raw_intent = event.get("intent")
            if not isinstance(raw_intent, str):
                continue
            try:
                return HandoffIntent(raw_intent)
            except ValueError:
                continue
        return None

    def _resolve_next_step_from_handoff(self, *, current_step: str) -> Optional[str]:
        """Derive the next step from the blackboard handoff contract.

        When an agent omits a status code but the step executor has already
        written a handoff contract (e.g. ``confirm_output`` → ``user``),
        we use the contract's ``to_step`` to advance the workflow.  This
        keeps the engine baton-first rather than status-code-first.
        """
        try:
            contract = self.blackboard_store.load_handoff_contract(
                self.blackboard,
                allowed_steps=list(self.steps.keys()),
            )
        except Exception:
            return None
        to_step = getattr(contract, "to_step", None)
        if to_step is None or to_step == current_step:
            return None
        # Validate the target step exists in the playbook or is a known
        # synthetic step (user, done, _done).
        if to_step in self.steps or to_step in {"user", "done", "_done"}:
            return str(to_step)
        return None

    def _load_agent_written_handoff_contract(self, *, current_step: str):
        """Load and normalize a baton written by a just-finished agent step."""
        contract = self.blackboard_store.load_handoff_contract(
            self.blackboard,
            allowed_steps=list(self.steps.keys()),
            allow_legacy_text=True,
        )
        if contract.source == "legacy_text":
            contract.from_step = current_step
            contract.source = "workflow.legacy_baton_normalized"
            self.blackboard_store.write_handoff_contract(self.blackboard, contract)
        return contract

    def _pr_step_requires_publish_receipt(self, current_step: str) -> bool:
        if current_step != "pr":
            return False
        issue_yaml = self.issue_dir / "issue.yaml"
        if not issue_yaml.exists():
            return True
        try:
            import yaml

            data = yaml.safe_load(issue_yaml.read_text(encoding="utf-8")) or {}
        except Exception:
            return True
        pr_cfg = data.get("pr") or {}
        return pr_cfg.get("auto_create", True) is not False

    def _status_from_contract(self, current_step: str, execution_result: Any) -> str:
        contract = self._load_agent_written_handoff_contract(current_step=current_step)
        if contract.to_owner == HandoffOwner.AGENT and contract.to_step == current_step:
            explicit_status_code = getattr(execution_result, "status_code", None)
            if explicit_status_code:
                return str(explicit_status_code)
            return "NO_BATON_TRANSITION"
        explicit_status_code = getattr(execution_result, "status_code", None)
        return (
            contract.status_code
            or str(explicit_status_code or "")
            or f"BATON_{contract.intent.value.upper()}"
        )

    def _load_step_handoff_contract(self, *, current_step: str):
        contract = self._load_agent_written_handoff_contract(current_step=current_step)
        if contract.from_step != current_step:
            return None
        return contract

    @staticmethod
    def _resolve_step_iteration_limit(step_def: Dict) -> Optional[int]:
        raw_limit = step_def.get("max_iterations")
        if raw_limit is None:
            return None
        if isinstance(raw_limit, int):
            return raw_limit
        if isinstance(raw_limit, str) and raw_limit.isdigit():
            return int(raw_limit)
        return None

    @staticmethod
    def _extract_status_like_tokens(*, response: str, explicit_status_code: Optional[str]) -> set[str]:
        tokens = set(STATUS_TOKEN_PATTERN.findall(response or ""))
        haystack = f"{response or ''}\n{explicit_status_code or ''}"
        if explicit_status_code:
            tokens.add(explicit_status_code)
        for code in PhaseStatusCode:
            token = code.value
            if not token:
                continue
            if re.search(rf"(?<!\w){re.escape(token)}(?!\w)", haystack, flags=re.IGNORECASE):
                tokens.add(token)
        return tokens

    def _resolve_review_confirmed_successor(self, current_step: str) -> Optional[str]:
        step = self.steps[current_step]
        transitions = step.get("on", {})
        if not isinstance(transitions, dict):
            return None

        confirmed_target = transitions.get(transition_map_key(PhaseStatusCode.CONFIRMED))
        if confirmed_target and confirmed_target != current_step:
            return str(confirmed_target)

        for target in transitions.values():
            if target != current_step:
                return str(target)
        return None

    @staticmethod
    def _normalize_execution_result(execution_result: Any) -> tuple[str, Dict[str, str], Optional[str], bool]:
        auto_continue = False
        explicit_status_code: Optional[str] = None
        if isinstance(execution_result, StepExecutionResult):
            return (
                execution_result.response,
                execution_result.artifacts,
                execution_result.status_code,
                execution_result.auto_continue,
            )
        if isinstance(execution_result, tuple) and len(execution_result) == 3:
            response, artifacts, metadata = execution_result
            if isinstance(metadata, dict):
                auto_continue = bool(metadata.get("auto_continue"))
                raw_status_code = metadata.get("status_code")
                if isinstance(raw_status_code, str):
                    explicit_status_code = raw_status_code
            return response, artifacts, explicit_status_code, auto_continue
        response, artifacts = execution_result
        return response, artifacts, explicit_status_code, auto_continue

    def _parse_legacy_status(
        self,
        *,
        step_def: Dict,
        response: str,
        explicit_status_code: Optional[str],
    ) -> tuple[Optional[PhaseStatusCode], Optional[str], list[PhaseStatusCode]]:
        valid_codes = [
            PhaseStatusCode(code)
            for code in step_def.get("valid_intents", [])
            if code in {item.value for item in PhaseStatusCode}
        ]
        goto_target = self._extract_goto_target(response)
        status_code_obj = (
            PhaseStatusCode(explicit_status_code)
            if explicit_status_code in {item.value for item in PhaseStatusCode}
            else None
        )
        if status_code_obj is None:
            status_code_obj = StatusCodeParser.extract(
                response,
                valid_codes=valid_codes or list(PhaseStatusCode),
            )
        return status_code_obj, goto_target, valid_codes

    @staticmethod
    def _validate_assignee_type(step_name: str, step_def: Dict) -> None:
        assignee_type = str(step_def.get("assignee_type", "agent"))
        if assignee_type != "agent":
            raise RuntimeError(
                f"Step '{step_name}' has assignee_type={assignee_type}, which is not supported in v0.2. "
                "Use v0.3+ or change to assignee_type=agent."
            )

    def _execute_one_iteration(
        self,
        *,
        current_step: str,
        step_def: Dict,
        runtime: str,
        hop_count: int,
        visit_count: int,
        validate_assignee_type: bool = False,
        extra_prompt: Optional[str] = None,
    ) -> StepIterationFrame:
        self.blackboard_store.record_event(
            self.blackboard,
            "step_started",
            {
                "step": current_step,
                "visit": visit_count,
                "hop": hop_count,
                "runtime": runtime,
            },
        )

        try:
            try:
                execution_result = self.executor(current_step, step_def, self.blackboard, extra_prompt=extra_prompt)
            except TypeError:
                execution_result = self.executor(current_step, step_def, self.blackboard)
        except KeyboardInterrupt:
            self.blackboard_store.record_event(
                self.blackboard,
                "step_interrupted",
                {
                    "step": current_step,
                    "visit": visit_count,
                    "hop": hop_count,
                    "runtime": runtime,
                    "reason": "keyboard_interrupt",
                },
            )
            raise StepInterrupted(step=current_step, hop=hop_count, reason="interrupted")
        except BaseException as exc:
            # Catch AgentExecutionError (rate_limit, cli_not_found) and any
            # other executor failure so the workflow records a clean
            # interrupted state instead of crashing.
            from cafe.agents.executor import AgentExecutionError

            reason = "agent_error"
            detail = str(exc)
            if isinstance(exc, AgentExecutionError) and getattr(exc, "error_type", None):
                reason = f"agent_{exc.error_type}"
                detail = exc.display_message or str(exc)
            self.blackboard_store.record_event(
                self.blackboard,
                "step_interrupted",
                {
                    "step": current_step,
                    "visit": visit_count,
                    "hop": hop_count,
                    "runtime": runtime,
                    "reason": reason,
                    "detail": detail,
                },
            )
            raise StepInterrupted(step=current_step, hop=hop_count, reason=reason)
        if validate_assignee_type:
            self._validate_assignee_type(current_step, step_def)

        response, artifacts, explicit_status_code, auto_continue = self._normalize_execution_result(execution_result)
        return StepIterationFrame(
            execution_result=execution_result,
            response=response,
            artifacts=artifacts,
            explicit_status_code=explicit_status_code,
            auto_continue=auto_continue,
        )

    def _store_artifacts(self, artifacts: Dict[str, str]) -> None:
        for key, value in artifacts.items():
            self.blackboard_store.set_artifact(self.blackboard, key, value)

    def _record_step_completion(
        self,
        *,
        event_type: str,
        current_step: str,
        status_code: str,
        runtime: str,
        visit_count: Optional[int] = None,
        hop_count: Optional[int] = None,
    ) -> None:
        payload: Dict[str, Any] = {
            "step": current_step,
            "status_code": status_code,
            "runtime": runtime,
        }
        if visit_count is not None:
            payload["visit"] = visit_count
        if hop_count is not None:
            payload["hop"] = hop_count
        self.blackboard_store.record_event(
            self.blackboard,
            event_type,
            payload,
        )

    def _emit_pause(
        self,
        *,
        current_step: str,
        status_code: str,
        runtime: str,
        reason: str,
        pause_intent: Optional[HandoffIntent] = None,
        update_contract: bool = True,
        contract_source: str = "workflow.pause",
        record_event: bool = True,
    ) -> PlaybookRunResult:
        if update_contract:
            self.blackboard_store.update_handoff_contract(
                self.blackboard,
                from_step=current_step,
                to_owner=HandoffOwner.USER,
                to_step="user",
                intent=pause_intent or HandoffIntent.MANUAL_HANDOFF,
                status_code=status_code,
                source=contract_source,
            )
        if record_event:
            self.blackboard_store.record_event(
                self.blackboard,
                "workflow_paused",
                {
                    "step": current_step,
                    "status_code": status_code,
                    "reason": reason,
                    "runtime": runtime,
                },
            )
        self.blackboard_store.set_current_step(self.blackboard, "user")
        return PlaybookRunResult(
            final_step=current_step,
            final_status_code=status_code,
            completed=False,
        )

    def _emit_complete(
        self,
        *,
        current_step: str,
        status_code: str,
        next_step: str,
        runtime: str,
        reason: str,
        update_contract: bool = False,
        contract_source: str = "workflow.transition",
    ) -> PlaybookRunResult:
        self.blackboard_store.record_event(
            self.blackboard,
            "workflow_completed",
            {
                "step": current_step,
                "status_code": status_code,
                "next_step": next_step,
                "reason": reason,
                "runtime": runtime,
            },
        )
        self.blackboard_store.set_current_step(self.blackboard, "done")
        if update_contract:
            self.blackboard_store.update_handoff_contract(
                self.blackboard,
                from_step=current_step,
                to_owner=HandoffOwner.DONE,
                to_step="done",
                intent=HandoffIntent.WORKFLOW_COMPLETE,
                status_code=status_code,
                source=contract_source,
            )
        return PlaybookRunResult(
            final_step=current_step,
            final_status_code=status_code,
            completed=True,
        )

    def _emit_transition(
        self,
        *,
        current_step: str,
        next_step: str,
        status_code: str,
        source: str,
        runtime: str,
        update_contract: bool = True,
        contract_source: str = "workflow.transition",
    ) -> None:
        self.blackboard_store.record_decision(
            self.blackboard,
            {"from": current_step, "to": next_step, "status_code": status_code},
        )
        self.blackboard_store.record_event(
            self.blackboard,
            "transition",
            {
                "from": current_step,
                "to": next_step,
                "status_code": status_code,
                "source": source,
                "runtime": runtime,
            },
        )
        self.blackboard_store.set_current_step(self.blackboard, next_step)
        if update_contract:
            self.blackboard_store.update_handoff_contract(
                self.blackboard,
                from_step=current_step,
                to_owner=HandoffOwner.AGENT,
                to_step=next_step,
                intent=HandoffIntent.AWAIT_AGENT,
                status_code=status_code,
                source=contract_source,
            )

    def _handle_post_contract(
        self,
        *,
        current_step: str,
        status_code: str,
        runtime: str,
        update_contract_on_transition: bool,
    ) -> Optional[PostContractResult]:
        post_contract = self._load_step_handoff_contract(current_step=current_step)
        if post_contract is None:
            return None
        if post_contract.to_owner == HandoffOwner.AGENT and post_contract.to_step == current_step:
            return None

        resolved_status_code = post_contract.status_code or status_code
        if post_contract.to_owner == HandoffOwner.USER:
            result = self._emit_pause(
                current_step=current_step,
                status_code=resolved_status_code,
                runtime=runtime,
                reason="external_handoff",
                update_contract=False,
            )
            return PostContractResult(status_code=resolved_status_code, terminal_result=result)

        if post_contract.to_owner == HandoffOwner.DONE:
            result = self._emit_complete(
                current_step=current_step,
                status_code=resolved_status_code,
                next_step=post_contract.to_step,
                runtime=runtime,
                reason="external_handoff",
                update_contract=False,
            )
            return PostContractResult(status_code=resolved_status_code, terminal_result=result)

        next_step = post_contract.to_step
        if next_step not in self.steps:
            raise RuntimeError(f"Unknown baton target '{next_step}' from step '{current_step}'")
        self._emit_transition(
            current_step=current_step,
            next_step=next_step,
            status_code=resolved_status_code,
            source="baton",
            runtime=runtime,
            update_contract=update_contract_on_transition,
        )
        return PostContractResult(status_code=resolved_status_code, next_step=next_step)

    def _run_baton_driven_pr(
        self,
        *,
        current_step: str,
        max_transitions: int,
        runtime_label: str = "blackboard",
        completion_event_type: str = "step_completed",
        transition_runtime_label: Optional[str] = None,
        transition_contract_source: str = "workflow.transition",
        single_step_mode: bool = False,
    ) -> PlaybookRunResult:
        last_status_code = ""
        step_visits: Counter[str] = Counter()
        effective_transition_runtime = transition_runtime_label or runtime_label

        for hop_count in range(1, max_transitions + 1):
            step_def = self.steps[current_step]
            self._validate_agent_baton(current_step=current_step)
            step_visits[current_step] += 1
            visit_count = step_visits[current_step]
            frame = self._execute_one_iteration(
                current_step=current_step,
                step_def=step_def,
                runtime=runtime_label,
                hop_count=hop_count,
                visit_count=visit_count,
            )
            self._store_artifacts(frame.artifacts)

            status_code = self._status_from_contract(current_step, frame.execution_result)
            last_status_code = status_code
            self._record_step_completion(
                event_type=completion_event_type,
                current_step=current_step,
                status_code=status_code,
                runtime=runtime_label,
                visit_count=visit_count,
                hop_count=hop_count,
            )

            contract = self._load_agent_written_handoff_contract(current_step=current_step)
            next_step = contract.to_step

            if contract.to_owner == HandoffOwner.AGENT and next_step == current_step:
                self.blackboard_store.record_event(
                    self.blackboard,
                    "baton_missing_transition",
                    {
                        "step": current_step,
                        "status_code": status_code,
                    },
                )
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code="NO_BATON_TRANSITION",
                    completed=False,
                )

            if next_step == "done":
                if self._pr_step_requires_publish_receipt(current_step) and not self._pr_publish_receipt_satisfied(
                    frame.execution_result
                ):
                    self.blackboard_store.update_handoff_contract(
                        self.blackboard,
                        from_step=current_step,
                        to_owner=HandoffOwner.AGENT,
                        to_step=current_step,
                        intent=HandoffIntent.AWAIT_AGENT,
                        status_code=status_code,
                        source="workflow.capability_receipt_required",
                    )
                    self.blackboard_store.record_event(
                        self.blackboard,
                        "workflow_blocked",
                        {
                            "step": current_step,
                            "status_code": status_code,
                            "reason": "missing_capability_receipt",
                            "required_event": "pr_synced",
                        },
                    )
                    self.blackboard_store.set_current_step(self.blackboard, current_step)
                    return PlaybookRunResult(
                        final_step=current_step,
                        final_status_code="MISSING_CAPABILITY_RECEIPT",
                        completed=False,
                    )

                return self._emit_complete(
                    current_step=current_step,
                    status_code=status_code,
                    next_step=next_step,
                    runtime=runtime_label,
                    reason="external_handoff",
                    update_contract=False,
                )

            if next_step == "user":
                return self._emit_pause(
                    current_step=current_step,
                    status_code=status_code,
                    runtime=runtime_label,
                    reason="awaiting_user_input",
                    update_contract=False,
                )

            if next_step not in self.steps:
                raise RuntimeError(f"Unknown baton target '{next_step}' from step '{current_step}'")

            self._emit_transition(
                current_step=current_step,
                next_step=next_step,
                status_code=status_code,
                source="baton",
                runtime=effective_transition_runtime,
                update_contract=True,
                contract_source=transition_contract_source,
            )
            if single_step_mode:
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code=status_code,
                    completed=False,
                )
            current_step = next_step

        self.blackboard_store.record_event(
            self.blackboard,
            "hop_limit_reached",
            {
                "step": current_step,
                "max_transitions": max_transitions,
                "last_status_code": last_status_code,
            },
        )
        raise RuntimeError(f"Playbook run reached max transition limit ({max_transitions})")

    def _run_legacy_until_boundary(
        self,
        *,
        current_step: str,
        max_transitions: int,
        runtime_label: str = "legacy_until_boundary",
        completion_event_type: str = "step_completed",
        boundary_transition_runtime: str = "boundary_handoff",
        transition_contract_source: str = "workflow.transition",
        single_step_mode: bool = False,
        pause_record_event: bool = True,
    ) -> PlaybookRunResult:
        last_status_code = ""
        step_visits: Counter[str] = Counter()

        for hop_count in range(1, max_transitions + 1):
            if current_step in self.BATON_DRIVEN_STEPS:
                return self._run_baton_driven_pr(current_step=current_step, max_transitions=max_transitions - hop_count + 1)

            step_def = self.steps[current_step]
            self._validate_agent_baton(current_step=current_step)
            step_visits[current_step] += 1
            visit_count = step_visits[current_step]
            max_iterations = self._resolve_step_iteration_limit(step_def)
            if max_iterations is not None and visit_count > max_iterations:
                self.blackboard_store.record_event(
                    self.blackboard,
                    "loop_detected",
                    {
                        "step": current_step,
                        "visits": visit_count,
                        "max_iterations": max_iterations,
                        "runtime": runtime_label,
                    },
                )
                raise RuntimeError(f"Step '{current_step}' exceeded max_iterations={max_iterations}")

            _baton_retry_extra_prompt: Optional[str] = None
            for _baton_attempt in range(3):
                try:
                    frame = self._execute_one_iteration(
                        current_step=current_step,
                        step_def=step_def,
                        runtime=runtime_label,
                        hop_count=hop_count,
                        visit_count=visit_count,
                        validate_assignee_type=True,
                        extra_prompt=_baton_retry_extra_prompt,
                    )
                except StepInterrupted as si:
                    if pause_record_event:
                        self.blackboard_store.record_event(
                            self.blackboard,
                            "workflow_paused",
                            {
                                "step": current_step,
                                "status_code": "INTERRUPTED",
                                "reason": si.reason,
                                "runtime": runtime_label,
                            },
                        )
                    return PlaybookRunResult(
                        final_step=current_step,
                        final_status_code=f"INTERRUPTED:{si.reason}",
                        completed=False,
                    )
                self._store_artifacts(frame.artifacts)
                try:
                    post_contract = self._load_step_handoff_contract(current_step=current_step)
                    break
                except BatonRejected as br:
                    retry_num = _baton_attempt + 1
                    self.blackboard_store.record_event(
                        self.blackboard,
                        "baton_rejected",
                        {
                            "step": current_step,
                            "field": br.field,
                            "invalid_value": br.invalid_value,
                            "valid_values": br.valid_values,
                            "retry": retry_num,
                            "runtime": runtime_label,
                        },
                    )
                    if retry_num >= 3:
                        raise RuntimeError(
                            f"Step '{current_step}' wrote invalid baton 3 times; "
                            f"last error: field '{br.field}' got '{br.invalid_value}', "
                            f"valid values are {br.valid_values}"
                        ) from br
                    _baton_retry_extra_prompt = (
                        f"[BATON ERROR] Your baton was rejected because field '{br.field}' "
                        f"has invalid value '{br.invalid_value}'. "
                        f"Valid values are: {br.valid_values}. "
                        f"Please rewrite next_step.txt with a correct baton."
                    )
            else:
                post_contract = None
            if post_contract is not None and not (
                post_contract.to_owner == HandoffOwner.AGENT and post_contract.to_step == current_step
            ):
                status_code = (
                    post_contract.status_code
                    or frame.explicit_status_code
                    or f"BATON_{post_contract.intent.value.upper()}"
                )
                last_status_code = status_code
                self._record_step_completion(
                    event_type=completion_event_type,
                    current_step=current_step,
                    status_code=status_code,
                    runtime=runtime_label,
                    visit_count=visit_count,
                    hop_count=hop_count,
                )
                post_contract_result = self._handle_post_contract(
                    current_step=current_step,
                    status_code=status_code,
                    runtime=runtime_label,
                    update_contract_on_transition=False,
                )
                if post_contract_result is not None:
                    status_code = post_contract_result.status_code
                    last_status_code = status_code
                    if post_contract_result.terminal_result is not None:
                        return post_contract_result.terminal_result
                    if post_contract_result.next_step is not None:
                        if single_step_mode:
                            return PlaybookRunResult(
                                final_step=current_step,
                                final_status_code=status_code,
                                completed=False,
                            )
                        current_step = post_contract_result.next_step
                        continue

            status_code_obj, goto_target, valid_codes = self._parse_legacy_status(
                step_def=step_def,
                response=frame.response,
                explicit_status_code=frame.explicit_status_code,
            )
            allowed_status_codes = {
                code.value for code in (valid_codes or list(PhaseStatusCode))
            }
            if status_code_obj is None:
                handoff_next_step: Optional[str] = None
                handoff_transition_source = "terminal"
                if goto_target:
                    handoff_next_step, handoff_transition_source = self._resolve_next_step(
                        current_step=current_step,
                        response=f"{frame.response}\nGOTO:{goto_target}",
                        status_code="",
                    )
                if handoff_next_step is not None:
                    status_code = "NO_STATUS_CODE"
                    last_status_code = status_code
                else:
                    status_like_tokens = self._extract_status_like_tokens(
                        response=frame.response,
                        explicit_status_code=frame.explicit_status_code,
                    )
                    invalid_intents = sorted(
                        token for token in status_like_tokens if token not in allowed_status_codes
                    )
                    default_next_step, _ = self._resolve_next_step(
                        current_step=current_step,
                        response="",
                        status_code="",
                    )
                    if default_next_step is not None:
                        event_name = "status_code_invalid" if invalid_intents else "status_code_missing"
                        event_data: Dict[str, Any] = {"step": current_step, "response": frame.response}
                        if invalid_intents:
                            event_data["invalid_intents"] = invalid_intents
                            event_data["allowed_status_codes"] = sorted(allowed_status_codes)
                        event_data["default_transition"] = default_next_step
                        event_data["runtime"] = runtime_label
                        self.blackboard_store.record_event(self.blackboard, event_name, event_data)
                        handoff_next_step = default_next_step
                        handoff_transition_source = "default"
                        status_code = "NO_STATUS_CODE"
                        last_status_code = status_code
                    elif invalid_intents:
                        self.blackboard_store.record_event(
                            self.blackboard,
                            "status_code_invalid",
                            {
                                "step": current_step,
                                "invalid_intents": invalid_intents,
                                "allowed_status_codes": sorted(allowed_status_codes),
                                "response": frame.response,
                                "runtime": runtime_label,
                            },
                        )
                        return PlaybookRunResult(
                            final_step=current_step,
                            final_status_code="INVALID_STATUS_CODE",
                            completed=False,
                        )
                    else:
                        # Baton-first fallback: when the agent omits a
                        # status code, use the handoff contract on the
                        # blackboard (written by the step executor) to
                        # determine the next step.  This advances the
                        # engine deterministically via the baton model
                        # rather than falling back to status-code transitions.
                        baton_next = self._resolve_next_step_from_handoff(
                            current_step=current_step,
                        )
                        if baton_next is not None:
                            self.blackboard_store.record_event(
                                self.blackboard,
                                "status_code_missing",
                                {
                                    "step": current_step,
                                    "response": frame.response,
                                    "runtime": runtime_label,
                                    "baton_fallback": True,
                                    "fallback_next_step": baton_next,
                                },
                            )
                            handoff_next_step = baton_next
                            handoff_transition_source = "baton"
                            status_code = "NO_STATUS_CODE"
                            last_status_code = status_code
                        else:
                            self.blackboard_store.record_event(
                                self.blackboard,
                                "status_code_missing",
                                {
                                    "step": current_step,
                                    "response": frame.response,
                                    "runtime": runtime_label,
                                },
                            )
                            return PlaybookRunResult(
                                final_step=current_step,
                                final_status_code="NO_STATUS_CODE",
                                completed=False,
                            )
            else:
                handoff_next_step = None
                handoff_transition_source = "terminal"
                status_code = status_code_obj.value
                last_status_code = status_code

            self._record_step_completion(
                event_type=completion_event_type,
                current_step=current_step,
                status_code=status_code,
                runtime=runtime_label,
                visit_count=visit_count,
                hop_count=hop_count,
            )

            post_contract_result = self._handle_post_contract(
                current_step=current_step,
                status_code=status_code,
                runtime=runtime_label,
                update_contract_on_transition=False,
            )
            if post_contract_result is not None:
                status_code = post_contract_result.status_code
                last_status_code = status_code
                if post_contract_result.terminal_result is not None:
                    return post_contract_result.terminal_result
                if post_contract_result.next_step is not None:
                    if single_step_mode:
                        return PlaybookRunResult(
                            final_step=current_step,
                            final_status_code=status_code,
                            completed=False,
                        )
                    current_step = post_contract_result.next_step
                    continue

            if not frame.auto_continue and status_code in PAUSE_STATUS_CODES:
                pause_intent = self._extract_handoff_intent(frame.execution_result) or self._default_pause_intent(
                    current_step, status_code
                )
                return self._emit_pause(
                    current_step=current_step,
                    status_code=status_code,
                    runtime=runtime_label,
                    reason="awaiting_user_input",
                    pause_intent=pause_intent,
                    update_contract=True,
                    contract_source="workflow.pause",
                    record_event=pause_record_event,
                )

            review_confirmed_advance = False
            if hasattr(frame.execution_result, "events"):
                review_confirmed_advance = any(
                    isinstance(event, dict) and event.get("type") == "review_confirmed_advance"
                    for event in frame.execution_result.events
                )

            if handoff_next_step is not None:
                next_step, transition_source = handoff_next_step, handoff_transition_source
            else:
                next_step, transition_source = self._resolve_next_step(
                    current_step=current_step,
                    response=(
                        frame.response if goto_target is None else f"{frame.response}\nGOTO:{goto_target}"
                    ),
                    status_code=status_code,
                )
            if review_confirmed_advance and next_step == current_step:
                advanced_step = self._resolve_review_confirmed_successor(current_step)
                if advanced_step is not None:
                    next_step = advanced_step
                    transition_source = "review_confirmed_advance"
            if next_step is None:
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code=status_code,
                    completed=False,
                )
            if next_step in self.BATON_DRIVEN_STEPS:
                self._emit_transition(
                    current_step=current_step,
                    next_step=next_step,
                    status_code=status_code,
                    source=transition_source,
                    runtime=boundary_transition_runtime,
                    update_contract=True,
                    contract_source=transition_contract_source,
                )
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code=status_code,
                    completed=False,
                )

            if next_step not in self.steps:
                if next_step in {"done", "_done"}:
                    return self._emit_complete(
                        current_step=current_step,
                        status_code=status_code,
                        next_step=next_step,
                        runtime=runtime_label,
                        reason="status_transition",
                        update_contract=True,
                        contract_source=transition_contract_source,
                    )

                if next_step == "user":
                    return self._emit_pause(
                        current_step=current_step,
                        status_code=status_code,
                        runtime=runtime_label,
                        reason="status_transition_to_user",
                        pause_intent=HandoffIntent.MANUAL_HANDOFF,
                        update_contract=True,
                        contract_source=transition_contract_source,
                    )

                raise RuntimeError(f"Unknown terminal target '{next_step}' from step '{current_step}'")

            self._emit_transition(
                current_step=current_step,
                next_step=next_step,
                status_code=status_code,
                source=transition_source,
                runtime=runtime_label,
                update_contract=True,
                contract_source=transition_contract_source,
            )
            if single_step_mode:
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code=status_code,
                    completed=False,
                )
            current_step = next_step

        self.blackboard_store.record_event(
            self.blackboard,
            "hop_limit_reached",
            {
                "step": current_step,
                "max_transitions": max_transitions,
                "last_status_code": last_status_code,
            },
        )
        raise RuntimeError(f"Playbook run reached max transition limit ({max_transitions})")

    def _run_single_step(self, *, current_step: str) -> PlaybookRunResult:
        if current_step in self.BATON_DRIVEN_STEPS:
            return self._run_baton_driven_pr(
                current_step=current_step,
                max_transitions=1,
                runtime_label="single_step",
                completion_event_type="single_step_completed",
                transition_contract_source="workflow.single_step",
                single_step_mode=True,
            )

        return self._run_legacy_until_boundary(
            current_step=current_step,
            max_transitions=1,
            runtime_label="single_step",
            completion_event_type="single_step_completed",
            boundary_transition_runtime="single_step",
            transition_contract_source="workflow.single_step",
            single_step_mode=True,
            pause_record_event=False,
        )

    def run(
        self,
        *,
        max_transitions: int = 30,
        start_step: Optional[str] = None,
        single_step: bool = False,
    ) -> PlaybookRunResult:
        if single_step:
            current_step = start_step or self.blackboard.current_step
            if current_step not in self.steps:
                raise ValueError(f"Unknown playbook step '{current_step}'")
            if start_step is not None:
                self.blackboard_store.set_current_step(self.blackboard, current_step)
                self.blackboard_store.update_handoff_contract(
                    self.blackboard,
                    from_step=current_step,
                    to_owner=HandoffOwner.AGENT,
                    to_step=current_step,
                    intent=HandoffIntent.AWAIT_AGENT,
                    source="workflow.start_step_override",
                )
            return self._run_single_step(current_step=current_step)

        current_step = start_step or self.blackboard.current_step
        if current_step not in self.steps:
            raise ValueError(f"Unknown playbook step '{current_step}'")

        if start_step is not None:
            self.blackboard_store.set_current_step(self.blackboard, current_step)
            self.blackboard_store.update_handoff_contract(
                self.blackboard,
                from_step=current_step,
                to_owner=HandoffOwner.AGENT,
                to_step=current_step,
                intent=HandoffIntent.AWAIT_AGENT,
                source="workflow.start_step_override",
            )

        if current_step not in self.BATON_DRIVEN_STEPS:
            return self._run_legacy_until_boundary(current_step=current_step, max_transitions=max_transitions)

        return self._run_baton_driven_pr(current_step=current_step, max_transitions=max_transitions)
