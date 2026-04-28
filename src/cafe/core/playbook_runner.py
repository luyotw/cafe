"""Playbook-driven workflow runner."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import yaml

from cafe.core.blackboard import (
    BlackboardState,
    BlackboardStore,
    HandoffContract,
    HandoffIntent,
    HandoffOwner,
)
from cafe.core.status_codes import PhaseStatusCode, StatusCodeParser
from cafe.core.workflow_models import PlaybookRunResult, StepExecutionResult
from cafe.phases.generic_phase import GenericPhase

StepExecutor = Callable[[str, Dict, BlackboardState], Any]


PAUSE_STATUS_CODES = {
    PhaseStatusCode.READY_FOR_REVIEW.value,
    PhaseStatusCode.NEED_CLARIFICATION.value,
    PhaseStatusCode.NEED_PERMISSION.value,
}


class PlaybookRunner:
    """Run workflow steps based on playbook transition rules."""

    def __init__(
        self,
        *,
        issue_dir: Path,
        playbook: Dict,
        generic_phase: GenericPhase,
        executor: StepExecutor,
    ) -> None:
        self.issue_dir = issue_dir
        self.playbook = playbook
        self.generic_phase = generic_phase
        self.executor = executor

        playbook_meta = playbook["playbook"]
        self.playbook_id = str(playbook_meta["id"])
        self.steps: Dict = playbook["steps"]
        self.start_step = str(playbook.get("entry_point") or next(iter(self.steps.keys())))

        self.blackboard_store = BlackboardStore(issue_dir)
        self.blackboard = self.blackboard_store.load_or_create(
            self.start_step,
            playbook_id=self.playbook_id,
        )

    @staticmethod
    def _default_pause_intent(current_step: str, status_code: str) -> HandoffIntent:
        if status_code == PhaseStatusCode.READY_FOR_REVIEW.value and current_step in {"spec", "plan"}:
            return HandoffIntent.CONFIRM_OUTPUT
        if status_code == PhaseStatusCode.NEED_CLARIFICATION.value:
            return HandoffIntent.NEED_CLARIFICATION
        if status_code == PhaseStatusCode.NEED_PERMISSION.value:
            return HandoffIntent.NEED_PERMISSION
        return HandoffIntent.MANUAL_HANDOFF

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

    def _validate_agent_baton(self, *, current_step: str) -> HandoffContract:
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
        return contract

    def _load_step_status_code(self, step_name: str) -> Optional[str]:
        status_file = self.issue_dir / step_name / "status.json"
        if not status_file.exists():
            return None
        try:
            import json

            raw = json.loads(status_file.read_text(encoding="utf-8"))
        except Exception:
            return None
        status_code = raw.get("status_code")
        return str(status_code) if status_code else None

    def _resolve_next_step_from_status(
        self,
        *,
        current_step: str,
        status_code: str,
    ) -> tuple[Optional[str], str]:
        transitions = self.steps[current_step].get("on", {})
        if status_code not in transitions:
            default_target = transitions.get("default")
            if default_target:
                return str(default_target), "default"
            return None, "terminal"
        return str(transitions[status_code]), "status"

    def _align_current_step_with_saved_progress(self, current_step: str) -> str:
        """Advance stale workflow pointers before executing any step."""
        while current_step in self.steps:
            saved_status_code = self._load_step_status_code(current_step)
            if not saved_status_code or saved_status_code in PAUSE_STATUS_CODES:
                return current_step

            next_step, transition_source = self._resolve_next_step_from_status(
                current_step=current_step,
                status_code=saved_status_code,
            )
            if next_step is None or next_step not in self.steps:
                return current_step

            self.blackboard_store.record_event(
                self.blackboard,
                "resume_aligned",
                {
                    "from": current_step,
                    "to": next_step,
                    "status_code": saved_status_code,
                    "source": transition_source,
                },
            )
            self.blackboard_store.set_current_step(self.blackboard, next_step)
            self.blackboard_store.update_handoff_contract(
                self.blackboard,
                from_step=current_step,
                to_owner=HandoffOwner.AGENT,
                to_step=next_step,
                intent=HandoffIntent.AWAIT_AGENT,
                status_code=saved_status_code,
                source="workflow.resume_aligned",
            )
            current_step = next_step

        return current_step

    @staticmethod
    def _contracts_equal(left: HandoffContract, right: HandoffContract) -> bool:
        return left.to_dict() == right.to_dict()

    @staticmethod
    def _status_from_contract(contract: HandoffContract) -> str:
        return contract.status_code or f"BATON_{contract.intent.value.upper()}"

    def _resolve_review_confirmed_successor(self, current_step: str) -> Optional[str]:
        transitions = self.steps[current_step].get("on", {})
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
    def _has_event(execution_result: Any, event_type: str) -> bool:
        events = getattr(execution_result, "events", None)
        if not isinstance(events, list):
            return False
        return any(isinstance(event, dict) and event.get("type") == event_type for event in events)

    def _pr_step_requires_publish_receipt(self, current_step: str) -> bool:
        if current_step != "pr":
            return False
        issue_yaml = self.issue_dir / "issue.yaml"
        if not issue_yaml.exists():
            return True
        try:
            data = yaml.safe_load(issue_yaml.read_text(encoding="utf-8")) or {}
        except Exception:
            return True
        pr_cfg = data.get("pr") or {}
        return pr_cfg.get("auto_create", True) is not False

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

    def run(
        self,
        *,
        max_transitions: int = 30,
        start_step: Optional[str] = None,
        single_step: bool = False,
    ) -> PlaybookRunResult:
        current_step = start_step or self.blackboard.current_step
        if current_step not in self.steps:
            raise ValueError(f"Unknown playbook step '{current_step}'")
        if start_step is None:
            current_step = self._align_current_step_with_saved_progress(current_step)
        else:
            self.blackboard_store.set_current_step(self.blackboard, current_step)
            self.blackboard_store.update_handoff_contract(
                self.blackboard,
                from_step=current_step,
                to_owner=HandoffOwner.AGENT,
                to_step=current_step,
                intent=HandoffIntent.AWAIT_AGENT,
                source="workflow.start_step_override",
            )

        last_status_code = ""
        step_visits: Counter[str] = Counter()

        for hop_count in range(1, max_transitions + 1):
            step_def = self.steps[current_step]
            pre_contract = self._validate_agent_baton(current_step=current_step)
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
                    },
                )
                raise RuntimeError(f"Step '{current_step}' exceeded max_iterations={max_iterations}")

            self.blackboard_store.record_event(
                self.blackboard,
                "step_started",
                {"step": current_step, "visit": visit_count, "hop": hop_count},
            )
            assignee_type = str(step_def.get("assignee_type", "agent"))
            if assignee_type != "agent":
                raise RuntimeError(
                    f"Step '{current_step}' has assignee_type={assignee_type}, which is not supported in v0.2. "
                    "Use v0.3+ or change to assignee_type=agent."
                )

            execution_result = self.executor(current_step, step_def, self.blackboard)
            auto_continue = False
            explicit_status_code: Optional[str] = None
            if isinstance(execution_result, StepExecutionResult):
                response = execution_result.response
                artifacts = execution_result.artifacts
                explicit_status_code = execution_result.status_code
                auto_continue = execution_result.auto_continue
            elif isinstance(execution_result, tuple) and len(execution_result) == 3:
                response, artifacts, metadata = execution_result
                if isinstance(metadata, dict):
                    auto_continue = bool(metadata.get("auto_continue"))
                    raw_status_code = metadata.get("status_code")
                    if isinstance(raw_status_code, str):
                        explicit_status_code = raw_status_code
            else:
                response, artifacts = execution_result
            post_contract = self.blackboard_store.load_handoff_contract(
                self.blackboard,
                allowed_steps=list(self.steps.keys()),
                allow_legacy_text=True,
            )
            baton_updated = (
                post_contract.from_step == current_step
                and not self._contracts_equal(pre_contract, post_contract)
            )

            if baton_updated:
                status_code = self._status_from_contract(post_contract)
                next_step = post_contract.to_step
                transition_source = "baton"
            else:
                if explicit_status_code is None:
                    self.blackboard_store.record_event(
                        self.blackboard,
                        "baton_missing_transition",
                        {"step": current_step, "response": response},
                    )
                    return PlaybookRunResult(
                        final_step=current_step,
                        final_status_code="NO_BATON_TRANSITION",
                        completed=False,
                    )

                status_code = explicit_status_code
                next_step, transition_source = self._resolve_next_step_from_status(
                    current_step=current_step,
                    status_code=status_code,
                )
                if next_step is None:
                    self.blackboard_store.record_event(
                        self.blackboard,
                        "status_code_missing_transition",
                        {
                            "step": current_step,
                            "status_code": status_code,
                            "response": response,
                        },
                    )
                    return PlaybookRunResult(
                        final_step=current_step,
                        final_status_code="NO_STATUS_TRANSITION",
                        completed=False,
                    )

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
                },
            )

            review_confirmed_advance = False
            handoff_intent = self._extract_handoff_intent(execution_result)
            if hasattr(execution_result, "events"):
                review_confirmed_advance = any(
                    isinstance(event, dict) and event.get("type") == "review_confirmed_advance"
                    for event in execution_result.events
                )

            if (
                not single_step
                and baton_updated
                and post_contract.to_owner == HandoffOwner.USER
                and post_contract.to_step == "user"
                and not auto_continue
            ):
                pause_intent = handoff_intent or post_contract.intent
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
                    },
                )
                self.blackboard_store.set_current_step(self.blackboard, "user")
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code=status_code,
                    completed=False,
                )

            if not single_step and not baton_updated and status_code in PAUSE_STATUS_CODES and not auto_continue:
                pause_intent = handoff_intent or self._default_pause_intent(current_step, status_code)
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
                    },
                )
                self.blackboard_store.set_current_step(self.blackboard, "user")
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code=status_code,
                    completed=False,
                )

            if review_confirmed_advance and next_step == current_step:
                advanced_step = self._resolve_review_confirmed_successor(current_step)
                if advanced_step is not None:
                    next_step = advanced_step
                    transition_source = "review_confirmed_advance"

            if single_step:
                self.blackboard_store.update_handoff_contract(
                    self.blackboard,
                    from_step=current_step,
                    to_owner=HandoffOwner.AGENT,
                    to_step=current_step,
                    intent=HandoffIntent.AWAIT_AGENT,
                    status_code=status_code,
                    source="workflow.single_step",
                )
                self.blackboard_store.record_event(
                    self.blackboard,
                    "single_step_completed",
                    {"step": current_step, "status_code": status_code},
                )
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code=status_code,
                    completed=True,
                )

            if next_step not in self.steps:
                if (
                    next_step == "done"
                    and self._pr_step_requires_publish_receipt(current_step)
                    and not self._has_event(execution_result, "pr_synced")
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
                final_owner = HandoffOwner.DONE if next_step == "done" else HandoffOwner.USER
                final_step = "done" if next_step == "done" else "user"
                self.blackboard_store.update_handoff_contract(
                    self.blackboard,
                    from_step=current_step,
                    to_owner=final_owner,
                    to_step=final_step,
                    intent=HandoffIntent.WORKFLOW_COMPLETE,
                    status_code=status_code,
                    source="workflow.external_handoff",
                )
                self.blackboard_store.record_decision(
                    self.blackboard,
                    {
                        "from": current_step,
                        "to": next_step,
                        "status_code": status_code,
                        "type": "external_handoff",
                    },
                )
                self.blackboard_store.record_event(
                    self.blackboard,
                    "workflow_completed",
                    {
                        "step": current_step,
                        "status_code": status_code,
                        "next_step": next_step,
                        "reason": "external_handoff",
                    },
                )
                self.blackboard_store.set_current_step(self.blackboard, final_step)
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code=status_code,
                    completed=True,
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
                    "source": transition_source,
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
