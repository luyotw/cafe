"""Blackboard-first workflow runtime.

The workflow-core entry point. It keeps blackboard/baton state as the
primary source of truth for step transitions.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
from typing import Any, Dict, Optional

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.status_codes import PhaseStatusCode, StatusCodeParser
from cafe.core.workflow_models import PlaybookRunResult, StepExecutionResult


STATUS_TOKEN_PATTERN = re.compile(r"\bCAFE_[A-Z0-9_]+\b")
GOTO_PATTERN = re.compile(r"CAFE_GOTO\s*:\s*([a-zA-Z0-9_-]+)")
PAUSE_STATUS_CODES = {
    PhaseStatusCode.READY_FOR_REVIEW.value,
    PhaseStatusCode.NEED_CLARIFICATION.value,
    PhaseStatusCode.NEED_PERMISSION.value,
}


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
    def _default_pause_intent(current_step: str, status_code: str) -> HandoffIntent:
        if status_code == PhaseStatusCode.READY_FOR_REVIEW.value and current_step in {"spec", "plan"}:
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
            allow_legacy_text=True,
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

        if status_code not in transitions:
            default_target = transitions.get("default")
            if default_target:
                return str(default_target), "default"
            return None, "terminal"
        return str(transitions[status_code]), "status"

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
        contract = self.blackboard_store.load_handoff_contract(
            self.blackboard,
            allowed_steps=list(self.steps.keys()),
            allow_legacy_text=True,
        )
        if contract.to_owner == HandoffOwner.AGENT and contract.to_step == current_step:
            explicit_status_code = getattr(execution_result, "status_code", None)
            if explicit_status_code:
                return str(explicit_status_code)
            return "NO_BATON_TRANSITION"
        return contract.status_code or f"BATON_{contract.intent.value.upper()}"

    def _load_step_handoff_contract(self, *, current_step: str):
        contract = self.blackboard_store.load_handoff_contract(
            self.blackboard,
            allowed_steps=list(self.steps.keys()),
            allow_legacy_text=True,
        )
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
        if explicit_status_code and explicit_status_code.startswith("CAFE_"):
            tokens.add(explicit_status_code)
        return tokens

    def _resolve_review_confirmed_successor(self, current_step: str) -> Optional[str]:
        step = self.steps[current_step]
        transitions = step.get("on", {})
        if not isinstance(transitions, dict):
            return None

        confirmed_target = transitions.get(PhaseStatusCode.CONFIRMED.value)
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
            for code in step_def.get("valid_status_codes", [])
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

    def _run_baton_driven_pr(
        self,
        *,
        current_step: str,
        max_transitions: int,
    ) -> PlaybookRunResult:
        last_status_code = ""
        step_visits: Counter[str] = Counter()

        for hop_count in range(1, max_transitions + 1):
            step_def = self.steps[current_step]
            self._validate_agent_baton(current_step=current_step)
            step_visits[current_step] += 1
            visit_count = step_visits[current_step]

            self.blackboard_store.record_event(
                self.blackboard,
                "step_started",
                {
                    "step": current_step,
                    "visit": visit_count,
                    "hop": hop_count,
                    "runtime": "blackboard",
                },
            )

            execution_result = self.executor(current_step, step_def, self.blackboard)
            if isinstance(execution_result, StepExecutionResult):
                artifacts = execution_result.artifacts
            elif isinstance(execution_result, tuple) and len(execution_result) >= 2:
                _, artifacts = execution_result[:2]
            else:
                _, artifacts = execution_result

            for key, value in artifacts.items():
                self.blackboard_store.set_artifact(self.blackboard, key, value)

            status_code = self._status_from_contract(current_step, execution_result)
            last_status_code = status_code
            self.blackboard_store.record_event(
                self.blackboard,
                "step_completed",
                {
                    "step": current_step,
                    "status_code": status_code,
                    "visit": visit_count,
                    "hop": hop_count,
                    "runtime": "blackboard",
                },
            )

            contract = self.blackboard_store.load_handoff_contract(
                self.blackboard,
                allowed_steps=list(self.steps.keys()),
                allow_legacy_text=True,
            )
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
                if self._pr_step_requires_publish_receipt(current_step) and not self._has_event(
                    execution_result, "pr_synced"
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

                self.blackboard_store.record_event(
                    self.blackboard,
                    "workflow_completed",
                    {
                        "step": current_step,
                        "status_code": status_code,
                        "next_step": next_step,
                        "reason": "external_handoff",
                        "runtime": "blackboard",
                    },
                )
                self.blackboard_store.set_current_step(self.blackboard, "done")
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code=status_code,
                    completed=True,
                )

            if next_step == "user":
                self.blackboard_store.record_event(
                    self.blackboard,
                    "workflow_paused",
                    {
                        "step": current_step,
                        "status_code": status_code,
                        "reason": "awaiting_user_input",
                        "runtime": "blackboard",
                    },
                )
                self.blackboard_store.set_current_step(self.blackboard, "user")
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code=status_code,
                    completed=False,
                )

            if next_step not in self.steps:
                raise RuntimeError(f"Unknown baton target '{next_step}' from step '{current_step}'")

            self.blackboard_store.record_decision(
                self.blackboard,
                {
                    "from": current_step,
                    "to": next_step,
                    "status_code": status_code,
                },
            )
            self.blackboard_store.record_event(
                self.blackboard,
                "transition",
                {
                    "from": current_step,
                    "to": next_step,
                    "status_code": status_code,
                    "source": "baton",
                    "runtime": "blackboard",
                },
            )
            self.blackboard_store.set_current_step(self.blackboard, next_step)
            self.blackboard_store.update_handoff_contract(
                self.blackboard,
                from_step=current_step,
                to_owner=HandoffOwner.AGENT,
                to_step=next_step,
                intent=HandoffIntent.AWAIT_AGENT,
                status_code=status_code,
                source="workflow.transition",
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
                        "runtime": "legacy_until_boundary",
                    },
                )
                raise RuntimeError(f"Step '{current_step}' exceeded max_iterations={max_iterations}")

            self.blackboard_store.record_event(
                self.blackboard,
                "step_started",
                {
                    "step": current_step,
                    "visit": visit_count,
                    "hop": hop_count,
                    "runtime": "legacy_until_boundary",
                },
            )

            execution_result = self.executor(current_step, step_def, self.blackboard)
            self._validate_assignee_type(current_step, step_def)
            response, artifacts, explicit_status_code, auto_continue = self._normalize_execution_result(
                execution_result
            )
            status_code_obj, goto_target, valid_codes = self._parse_legacy_status(
                step_def=step_def,
                response=response,
                explicit_status_code=explicit_status_code,
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
                        response=f"{response}\nCAFE_GOTO:{goto_target}",
                        status_code="",
                    )
                if handoff_next_step is not None:
                    status_code = "NO_STATUS_CODE"
                    last_status_code = status_code
                else:
                    status_like_tokens = self._extract_status_like_tokens(
                        response=response,
                        explicit_status_code=explicit_status_code,
                    )
                    invalid_status_codes = sorted(
                        token for token in status_like_tokens if token not in allowed_status_codes
                    )
                    default_next_step, _ = self._resolve_next_step(
                        current_step=current_step,
                        response="",
                        status_code="",
                    )
                    if default_next_step is not None:
                        event_name = "status_code_invalid" if invalid_status_codes else "status_code_missing"
                        event_data: Dict[str, Any] = {"step": current_step, "response": response}
                        if invalid_status_codes:
                            event_data["invalid_status_codes"] = invalid_status_codes
                            event_data["allowed_status_codes"] = sorted(allowed_status_codes)
                        event_data["default_transition"] = default_next_step
                        event_data["runtime"] = "legacy_until_boundary"
                        self.blackboard_store.record_event(self.blackboard, event_name, event_data)
                        handoff_next_step = default_next_step
                        handoff_transition_source = "default"
                        status_code = "NO_STATUS_CODE"
                        last_status_code = status_code
                    elif invalid_status_codes:
                        self.blackboard_store.record_event(
                            self.blackboard,
                            "status_code_invalid",
                            {
                                "step": current_step,
                                "invalid_status_codes": invalid_status_codes,
                                "allowed_status_codes": sorted(allowed_status_codes),
                                "response": response,
                                "runtime": "legacy_until_boundary",
                            },
                        )
                        return PlaybookRunResult(
                            final_step=current_step,
                            final_status_code="INVALID_STATUS_CODE",
                            completed=False,
                        )
                    else:
                        self.blackboard_store.record_event(
                            self.blackboard,
                            "status_code_missing",
                            {
                                "step": current_step,
                                "response": response,
                                "runtime": "legacy_until_boundary",
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

            for key, value in artifacts.items():
                self.blackboard_store.set_artifact(self.blackboard, key, value)

            self.blackboard_store.record_event(
                self.blackboard,
                "step_completed",
                {
                    "step": current_step,
                    "status_code": status_code,
                    "visit": visit_count,
                    "hop": hop_count,
                    "runtime": "legacy_until_boundary",
                },
            )

            post_contract = self._load_step_handoff_contract(current_step=current_step)
            if post_contract is not None and (
                post_contract.to_owner != HandoffOwner.AGENT or post_contract.to_step != current_step
            ):
                status_code = post_contract.status_code or status_code
                last_status_code = status_code

                if post_contract.to_owner == HandoffOwner.USER:
                    self.blackboard_store.record_event(
                        self.blackboard,
                        "workflow_paused",
                        {
                            "step": current_step,
                            "status_code": status_code,
                            "reason": "external_handoff",
                            "runtime": "legacy_until_boundary",
                        },
                    )
                    self.blackboard_store.set_current_step(self.blackboard, "user")
                    return PlaybookRunResult(
                        final_step=current_step,
                        final_status_code=status_code,
                        completed=False,
                    )

                if post_contract.to_owner == HandoffOwner.DONE:
                    self.blackboard_store.record_event(
                        self.blackboard,
                        "workflow_completed",
                        {
                            "step": current_step,
                            "status_code": status_code,
                            "next_step": post_contract.to_step,
                            "reason": "external_handoff",
                            "runtime": "legacy_until_boundary",
                        },
                    )
                    self.blackboard_store.set_current_step(self.blackboard, "done")
                    return PlaybookRunResult(
                        final_step=current_step,
                        final_status_code=status_code,
                        completed=True,
                    )

                next_step = post_contract.to_step
                if next_step not in self.steps:
                    raise RuntimeError(
                        f"Unknown baton target '{next_step}' from step '{current_step}'"
                    )

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
                        "source": "baton",
                        "runtime": "legacy_until_boundary",
                    },
                )
                self.blackboard_store.set_current_step(self.blackboard, next_step)
                current_step = next_step
                continue

            if not auto_continue and status_code in {
                PhaseStatusCode.READY_FOR_REVIEW.value,
                PhaseStatusCode.NEED_CLARIFICATION.value,
                PhaseStatusCode.NEED_PERMISSION.value,
            }:
                pause_intent = self._extract_handoff_intent(execution_result) or self._default_pause_intent(
                    current_step, status_code
                )
                self.blackboard_store.update_handoff_contract(
                    self.blackboard,
                    from_step=current_step,
                    to_owner=HandoffOwner.USER,
                    to_step="user",
                    intent=pause_intent,
                    status_code=status_code,
                    source="workflow.pause",
                )
                self.blackboard_store.record_event(
                    self.blackboard,
                    "workflow_paused",
                    {
                        "step": current_step,
                        "status_code": status_code,
                        "reason": "awaiting_user_input",
                        "runtime": "legacy_until_boundary",
                    },
                )
                self.blackboard_store.set_current_step(self.blackboard, "user")
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code=status_code,
                    completed=False,
                )

            review_confirmed_advance = False
            if hasattr(execution_result, "events"):
                review_confirmed_advance = any(
                    isinstance(event, dict) and event.get("type") == "review_confirmed_advance"
                    for event in execution_result.events
                )

            if handoff_next_step is not None:
                next_step, transition_source = handoff_next_step, handoff_transition_source
            else:
                next_step, transition_source = self._resolve_next_step(
                    current_step=current_step,
                    response=response if goto_target is None else f"{response}\nCAFE_GOTO:{goto_target}",
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
                        "source": transition_source,
                        "runtime": "boundary_handoff",
                    },
                )
                self.blackboard_store.set_current_step(self.blackboard, next_step)
                self.blackboard_store.update_handoff_contract(
                    self.blackboard,
                    from_step=current_step,
                    to_owner=HandoffOwner.AGENT,
                    to_step=next_step,
                    intent=HandoffIntent.AWAIT_AGENT,
                    status_code=status_code,
                    source="workflow.transition",
                )
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code=status_code,
                    completed=False,
                )

            if next_step not in self.steps:
                if next_step in {"done", "_done"}:
                    self.blackboard_store.record_event(
                        self.blackboard,
                        "workflow_completed",
                        {
                            "step": current_step,
                            "status_code": status_code,
                            "next_step": next_step,
                            "reason": "status_transition",
                            "runtime": "legacy_until_boundary",
                        },
                    )
                    self.blackboard_store.set_current_step(self.blackboard, "done")
                    self.blackboard_store.update_handoff_contract(
                        self.blackboard,
                        from_step=current_step,
                        to_owner=HandoffOwner.DONE,
                        to_step="done",
                        intent=HandoffIntent.WORKFLOW_COMPLETE,
                        status_code=status_code,
                        source="workflow.transition",
                    )
                    return PlaybookRunResult(
                        final_step=current_step,
                        final_status_code=status_code,
                        completed=True,
                    )

                if next_step == "user":
                    self.blackboard_store.record_event(
                        self.blackboard,
                        "workflow_paused",
                        {
                            "step": current_step,
                            "status_code": status_code,
                            "reason": "status_transition_to_user",
                            "runtime": "legacy_until_boundary",
                        },
                    )
                    self.blackboard_store.set_current_step(self.blackboard, "user")
                    self.blackboard_store.update_handoff_contract(
                        self.blackboard,
                        from_step=current_step,
                        to_owner=HandoffOwner.USER,
                        to_step="user",
                        intent=HandoffIntent.MANUAL_HANDOFF,
                        status_code=status_code,
                        source="workflow.transition",
                    )
                    return PlaybookRunResult(
                        final_step=current_step,
                        final_status_code=status_code,
                        completed=False,
                    )

                raise RuntimeError(f"Unknown terminal target '{next_step}' from step '{current_step}'")

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
                    "source": transition_source,
                    "runtime": "legacy_until_boundary",
                },
            )
            self.blackboard_store.set_current_step(self.blackboard, next_step)
            self.blackboard_store.update_handoff_contract(
                self.blackboard,
                from_step=current_step,
                to_owner=HandoffOwner.AGENT,
                to_step=next_step,
                intent=HandoffIntent.AWAIT_AGENT,
                status_code=status_code,
                source="workflow.transition",
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
        step_def = self.steps[current_step]
        self._validate_agent_baton(current_step=current_step)
        self._validate_assignee_type(current_step, step_def)
        self.blackboard_store.record_event(
            self.blackboard,
            "step_started",
            {
                "step": current_step,
                "visit": 1,
                "hop": 1,
                "runtime": "single_step",
            },
        )
        execution_result = self.executor(current_step, step_def, self.blackboard)

        response, artifacts, explicit_status_code, auto_continue = self._normalize_execution_result(execution_result)

        if current_step in self.BATON_DRIVEN_STEPS:
            status_code = self._status_from_contract(current_step, execution_result)
        else:
            status_code_obj, _, _ = self._parse_legacy_status(
                step_def=step_def,
                response=response,
                explicit_status_code=explicit_status_code,
            )
            if status_code_obj is None:
                self.blackboard_store.record_event(
                    self.blackboard,
                    "status_code_missing",
                    {
                        "step": current_step,
                        "response": response,
                        "runtime": "single_step",
                    },
                )
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code="NO_STATUS_CODE",
                    completed=False,
                )
            status_code = status_code_obj.value

        for key, value in artifacts.items():
            self.blackboard_store.set_artifact(self.blackboard, key, value)

        self.blackboard_store.record_event(
            self.blackboard,
            "single_step_completed",
            {
                "step": current_step,
                "status_code": status_code,
                "runtime": "single_step",
            },
        )

        if current_step in self.BATON_DRIVEN_STEPS:
            contract = self.blackboard_store.load_handoff_contract(
                self.blackboard,
                allowed_steps=list(self.steps.keys()),
                allow_legacy_text=True,
            )
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
                if self._pr_step_requires_publish_receipt(current_step) and not self._has_event(
                    execution_result, "pr_synced"
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

                self.blackboard_store.record_event(
                    self.blackboard,
                    "workflow_completed",
                    {
                        "step": current_step,
                        "status_code": status_code,
                        "next_step": next_step,
                        "reason": "external_handoff",
                        "runtime": "single_step",
                    },
                )
                self.blackboard_store.set_current_step(self.blackboard, "done")
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code=status_code,
                    completed=True,
                )

            if next_step == "user":
                self.blackboard_store.record_event(
                    self.blackboard,
                    "workflow_paused",
                    {
                        "step": current_step,
                        "status_code": status_code,
                        "reason": "awaiting_user_input",
                        "runtime": "single_step",
                    },
                )
                self.blackboard_store.set_current_step(self.blackboard, "user")
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code=status_code,
                    completed=False,
                )

            if next_step not in self.steps:
                raise RuntimeError(f"Unknown baton target '{next_step}' from step '{current_step}'")

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
                    "source": "baton",
                    "runtime": "single_step",
                },
            )
            self.blackboard_store.set_current_step(self.blackboard, next_step)
            self.blackboard_store.update_handoff_contract(
                self.blackboard,
                from_step=current_step,
                to_owner=HandoffOwner.AGENT,
                to_step=next_step,
                intent=HandoffIntent.AWAIT_AGENT,
                status_code=status_code,
                source="workflow.single_step",
            )
            return PlaybookRunResult(
                final_step=current_step,
                final_status_code=status_code,
                completed=False,
            )

        post_contract = self._load_step_handoff_contract(current_step=current_step)
        if post_contract is not None and (
            post_contract.to_owner != HandoffOwner.AGENT or post_contract.to_step != current_step
        ):
            status_code = post_contract.status_code or status_code

            if post_contract.to_owner == HandoffOwner.USER:
                self.blackboard_store.record_event(
                    self.blackboard,
                    "workflow_paused",
                    {
                        "step": current_step,
                        "status_code": status_code,
                        "reason": "external_handoff",
                        "runtime": "single_step",
                    },
                )
                self.blackboard_store.set_current_step(self.blackboard, "user")
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code=status_code,
                    completed=False,
                )

            if post_contract.to_owner == HandoffOwner.DONE:
                self.blackboard_store.record_event(
                    self.blackboard,
                    "workflow_completed",
                    {
                        "step": current_step,
                        "status_code": status_code,
                        "next_step": post_contract.to_step,
                        "reason": "external_handoff",
                        "runtime": "single_step",
                    },
                )
                self.blackboard_store.set_current_step(self.blackboard, "done")
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code=status_code,
                    completed=True,
                )

            next_step = post_contract.to_step
            if next_step not in self.steps:
                raise RuntimeError(f"Unknown baton target '{next_step}' from step '{current_step}'")

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
                    "source": "baton",
                    "runtime": "single_step",
                },
            )
            self.blackboard_store.set_current_step(self.blackboard, next_step)
            return PlaybookRunResult(
                final_step=current_step,
                final_status_code=status_code,
                completed=False,
            )

        if not auto_continue and status_code in {
            PhaseStatusCode.READY_FOR_REVIEW.value,
            PhaseStatusCode.NEED_CLARIFICATION.value,
            PhaseStatusCode.NEED_PERMISSION.value,
        }:
            pause_intent = self._extract_handoff_intent(execution_result) or self._default_pause_intent(
                current_step, status_code
            )
            self.blackboard_store.update_handoff_contract(
                self.blackboard,
                from_step=current_step,
                to_owner=HandoffOwner.USER,
                to_step="user",
                intent=pause_intent,
                status_code=status_code,
                source="workflow.single_step",
            )
            self.blackboard_store.set_current_step(self.blackboard, "user")
            return PlaybookRunResult(
                final_step=current_step,
                final_status_code=status_code,
                completed=False,
            )

        review_confirmed_advance = False
        if hasattr(execution_result, "events"):
            review_confirmed_advance = any(
                isinstance(event, dict) and event.get("type") == "review_confirmed_advance"
                for event in execution_result.events
            )

        next_step, transition_source = self._resolve_next_step(
            current_step=current_step,
            response=response,
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
                    "source": transition_source,
                    "runtime": "single_step",
                },
            )
            self.blackboard_store.set_current_step(self.blackboard, next_step)
            self.blackboard_store.update_handoff_contract(
                self.blackboard,
                from_step=current_step,
                to_owner=HandoffOwner.AGENT,
                to_step=next_step,
                intent=HandoffIntent.AWAIT_AGENT,
                status_code=status_code,
                source="workflow.single_step",
            )
            return PlaybookRunResult(
                final_step=current_step,
                final_status_code=status_code,
                completed=False,
            )

        if next_step not in self.steps:
            if next_step in {"done", "_done"}:
                self.blackboard_store.record_event(
                    self.blackboard,
                    "workflow_completed",
                    {
                        "step": current_step,
                        "status_code": status_code,
                        "next_step": next_step,
                        "reason": "status_transition",
                        "runtime": "single_step",
                    },
                )
                self.blackboard_store.set_current_step(self.blackboard, "done")
                self.blackboard_store.update_handoff_contract(
                    self.blackboard,
                    from_step=current_step,
                    to_owner=HandoffOwner.DONE,
                    to_step="done",
                    intent=HandoffIntent.WORKFLOW_COMPLETE,
                    status_code=status_code,
                    source="workflow.single_step",
                )
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code=status_code,
                    completed=True,
                )

            if next_step == "user":
                self.blackboard_store.record_event(
                    self.blackboard,
                    "workflow_paused",
                    {
                        "step": current_step,
                        "status_code": status_code,
                        "reason": "status_transition_to_user",
                        "runtime": "single_step",
                    },
                )
                self.blackboard_store.set_current_step(self.blackboard, "user")
                self.blackboard_store.update_handoff_contract(
                    self.blackboard,
                    from_step=current_step,
                    to_owner=HandoffOwner.USER,
                    to_step="user",
                    intent=HandoffIntent.MANUAL_HANDOFF,
                    status_code=status_code,
                    source="workflow.single_step",
                )
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code=status_code,
                    completed=False,
                )

            raise RuntimeError(f"Unknown terminal target '{next_step}' from step '{current_step}'")

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
                "source": transition_source,
                "runtime": "single_step",
            },
        )
        self.blackboard_store.set_current_step(self.blackboard, next_step)
        self.blackboard_store.update_handoff_contract(
            self.blackboard,
            from_step=current_step,
            to_owner=HandoffOwner.AGENT,
            to_step=next_step,
            intent=HandoffIntent.AWAIT_AGENT,
            status_code=status_code,
            source="workflow.single_step",
        )
        return PlaybookRunResult(
            final_step=current_step,
            final_status_code=status_code,
            completed=False,
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
