"""Playbook-driven workflow runner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

from cafe.core.blackboard import BlackboardState, BlackboardStore
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.workflow_instance import WorkflowInstance
from cafe.phases.generic_phase import GenericPhase


StepExecutor = Callable[[str, Dict, BlackboardState], tuple[str, Dict[str, str]]]


@dataclass
class PlaybookRunResult:
    """Result of one playbook run."""

    final_step: str
    final_status_code: str
    completed: bool


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
        self.start_step = next(iter(self.steps.keys()))

        self.blackboard_store = BlackboardStore(issue_dir)
        self.blackboard = self.blackboard_store.load_or_create(self.start_step)
        self.instance = WorkflowInstance.load_or_create(
            issue_dir=issue_dir,
            playbook_id=self.playbook_id,
            initial_step=self.start_step,
        )

    def _resolve_next_step(
        self,
        *,
        current_step: str,
        response: str,
        status_code: str,
    ) -> Optional[str]:
        step = self.steps[current_step]
        transitions = step.get("on", {})
        goto_target = self.generic_phase.extract_goto_target(response)
        if goto_target:
            allowed_targets = {str(target) for target in transitions.values()}
            if goto_target not in allowed_targets:
                raise ValueError(
                    f"Invalid CAFE_GOTO target '{goto_target}' in step '{current_step}': not in allowed transitions"
                )
            return goto_target

        if status_code not in transitions:
            return None
        return str(transitions[status_code])

    def run(self, *, max_transitions: int = 30) -> PlaybookRunResult:
        current_step = self.instance.current_step
        last_status_code = ""

        for _ in range(max_transitions):
            step_def = self.steps[current_step]
            response, artifacts = self.executor(current_step, step_def, self.blackboard)
            valid_codes = [
                PhaseStatusCode(code)
                for code in step_def.get("valid_status_codes", [])
                if code in {item.value for item in PhaseStatusCode}
            ]
            status_code_obj, _ = self.generic_phase.parse_response(
                response=response,
                valid_status_codes=valid_codes or list(PhaseStatusCode),
            )
            if status_code_obj is None:
                raise ValueError(f"Step '{current_step}' did not return a valid status code")
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
                },
            )

            next_step = self._resolve_next_step(
                current_step=current_step,
                response=response,
                status_code=status_code,
            )
            if next_step is None:
                self.instance.mark_completed(status_code)
                return PlaybookRunResult(
                    final_step=current_step,
                    final_status_code=status_code,
                    completed=True,
                )
            if next_step not in self.steps:
                self.instance.mark_completed(status_code)
                self.blackboard_store.record_decision(
                    self.blackboard,
                    {
                        "from": current_step,
                        "to": next_step,
                        "status_code": status_code,
                        "type": "external_handoff",
                    },
                )
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
            self.blackboard_store.set_current_step(self.blackboard, next_step)
            self.instance.transition_to(next_step, status_code)
            current_step = next_step

        raise RuntimeError("Playbook run reached max transition limit")
