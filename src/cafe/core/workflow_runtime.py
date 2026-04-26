"""Blackboard-first workflow runtime.

This runtime is the new workflow-core entry point. It keeps blackboard/baton
state as the primary source of truth, while temporarily delegating non-migrated
steps to the legacy ``PlaybookRunner`` until they are rewritten.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Dict, Optional

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.playbook_runner import PlaybookRunResult, PlaybookRunner, StepExecutionResult
from cafe.core.status_codes import PhaseStatusCode, StatusCodeParser
from cafe.phases.generic_phase import GenericPhase


class BlackboardWorkflowRuntime:
    """Workflow runtime that prefers blackboard/baton-driven transitions."""

    BATON_DRIVEN_STEPS = {"pr"}

    def __init__(
        self,
        *,
        issue_dir: Path,
        playbook: Dict,
        generic_phase: GenericPhase,
        executor: Any,
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
        self.blackboard = self.blackboard_store.load_or_create(self.start_step, playbook_id=self.playbook_id)

    @staticmethod
    def _has_event(execution_result: Any, event_type: str) -> bool:
        events = getattr(execution_result, "events", None)
        if not isinstance(events, list):
            return False
        return any(isinstance(event, dict) and event.get("type") == event_type for event in events)

    @staticmethod
    def _default_pause_intent(current_step: str, status_code: str) -> HandoffIntent:
        return PlaybookRunner._default_pause_intent(current_step, status_code)

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
        goto_target = self.generic_phase.extract_goto_target(response)
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
        return PlaybookRunner._extract_handoff_intent(execution_result)

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

            valid_codes = [
                PhaseStatusCode(code)
                for code in step_def.get("valid_status_codes", [])
                if code in {item.value for item in PhaseStatusCode}
            ]
            status_code_obj = (
                PhaseStatusCode(explicit_status_code)
                if explicit_status_code in {item.value for item in PhaseStatusCode}
                else None
            )
            _, goto_target = self.generic_phase.parse_response(
                response=response,
                valid_status_codes=valid_codes or list(PhaseStatusCode),
            )
            if status_code_obj is None:
                status_code_obj, _ = self.generic_phase.parse_response(
                    response=response,
                    valid_status_codes=valid_codes or list(PhaseStatusCode),
                )
            if status_code_obj is None:
                status_code_obj = StatusCodeParser.coerce_completion_alias(
                    response,
                    valid_codes or list(PhaseStatusCode),
                )
            if status_code_obj is None:
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code="NO_STATUS_CODE",
                    completed=False,
                )

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
                self.blackboard_store.set_current_step(self.blackboard, "user")
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code=status_code,
                    completed=False,
                )

            next_step, transition_source = self._resolve_next_step(
                current_step=current_step,
                response=response if goto_target is None else f"{response}\nCAFE_GOTO:{goto_target}",
                status_code=status_code,
            )
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
                final_step = "done" if next_step == "done" else "user"
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

    def run(
        self,
        *,
        max_transitions: int = 30,
        start_step: Optional[str] = None,
        single_step: bool = False,
    ) -> PlaybookRunResult:
        if single_step:
            return PlaybookRunner(
                issue_dir=self.issue_dir,
                playbook=self.playbook,
                generic_phase=self.generic_phase,
                executor=self.executor,
            ).run(max_transitions=max_transitions, start_step=start_step, single_step=single_step)

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
