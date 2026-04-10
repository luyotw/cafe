"""Playbook-driven workflow runner."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from cafe.core.blackboard import BlackboardState, BlackboardStore
from cafe.core.status_codes import PhaseStatusCode
from cafe.phases.generic_phase import GenericPhase


StepExecutor = Callable[[str, Dict, BlackboardState], Any]


@dataclass
class StepExecutionResult:
    """Normalized executor output for one step."""

    response: str
    artifacts: Dict[str, str]
    status_code: Optional[str] = None
    auto_continue: bool = False


@dataclass
class PlaybookRunResult:
    """Result of one playbook run."""

    final_step: str
    final_status_code: str
    completed: bool


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
        self.blackboard = self.blackboard_store.load_or_create(self.start_step, playbook_id=self.playbook_id)

    def _owner_for_step(self, step_name: str) -> str:
        step_def = self.steps.get(step_name, {})
        role = str(step_def.get("role", "")).strip()
        return f"agent:{role}" if role else "agent"

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

    def _align_current_step_with_saved_progress(self, current_step: str) -> str:
        """Advance stale workflow pointers before executing any step."""
        while current_step in self.steps:
            saved_status_code = self._load_step_status_code(current_step)
            if not saved_status_code or saved_status_code in PAUSE_STATUS_CODES:
                return current_step

            next_step, transition_source = self._resolve_next_step(
                current_step=current_step,
                response=saved_status_code,
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
            self.blackboard_store.set_owner(self.blackboard, self._owner_for_step(next_step))
            current_step = next_step

        return current_step

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
            return None, "terminal"
        return str(transitions[status_code]), "status"

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

        last_status_code = ""
        step_visits: Counter[str] = Counter()

        for hop_count in range(1, max_transitions + 1):
            step_def = self.steps[current_step]
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
                raise RuntimeError(
                    f"Step '{current_step}' exceeded max_iterations={max_iterations}"
                )

            self.blackboard_store.record_event(
                self.blackboard,
                "step_started",
                {
                    "step": current_step,
                    "visit": visit_count,
                    "hop": hop_count,
                },
            )
            self.blackboard_store.set_owner(self.blackboard, self._owner_for_step(current_step))
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
                self.blackboard_store.record_event(
                    self.blackboard,
                    "status_code_missing",
                    {
                        "step": current_step,
                        "response": response,
                    },
                )
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
                },
            )

            if not single_step and status_code in PAUSE_STATUS_CODES and not auto_continue:
                self.blackboard_store.record_event(
                    self.blackboard,
                    "workflow_paused",
                    {
                        "step": current_step,
                        "status_code": status_code,
                        "reason": "awaiting_user_input",
                    },
                )
                self.blackboard_store.set_owner(self.blackboard, "user")
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
            if single_step:
                self.blackboard_store.record_event(
                    self.blackboard,
                    "single_step_completed",
                    {
                        "step": current_step,
                        "status_code": status_code,
                    },
                )
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code=status_code,
                    completed=True,
                )
            if next_step is None:
                self.blackboard_store.record_event(
                    self.blackboard,
                    "workflow_completed",
                    {
                        "step": current_step,
                        "status_code": status_code,
                        "reason": "no_transition",
                    },
                )
                self.blackboard_store.set_owner(self.blackboard, "user")
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code=status_code,
                    completed=True,
                )
            if next_step not in self.steps:
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
                self.blackboard_store.set_owner(self.blackboard, "user")
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code=status_code,
                    completed=True,
                )

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
                    "source": transition_source,
                },
            )
            self.blackboard_store.set_current_step(self.blackboard, next_step)
            self.blackboard_store.set_owner(self.blackboard, self._owner_for_step(next_step))
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
