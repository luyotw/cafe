"""Workflow orchestration for CAFE."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from cafe.core.phase import Phase
from cafe.core.types import PhaseProgress, PhaseResult, PhaseStatus


class WorkflowError(Exception):
    """Workflow execution error."""

    pass


class Workflow:
    """Orchestrates the execution of multiple phases."""

    def __init__(
        self,
        phases: Optional[List[Phase]] = None,
        max_retries: int = 0,
    ) -> None:
        """Initialize workflow.

        Args:
            phases: List of phases to execute
            max_retries: Maximum number of retries for failed phases
        """
        self.phases = phases or []
        self.max_retries = max_retries
        self.context: Dict[str, Any] = {
            "phase_results": [],
        }

    def add_phase(self, phase: Phase) -> None:
        """Add a phase to the workflow.

        Args:
            phase: Phase to add
        """
        self.phases.append(phase)

    def execute(self, skip_phases: Optional[List[int]] = None) -> List[PhaseResult]:
        """Execute all phases in order.

        Args:
            skip_phases: List of phase indices to skip

        Returns:
            List of phase results

        Raises:
            WorkflowError: If phase execution fails with exception
        """
        skip_phases = skip_phases or []
        results: List[PhaseResult] = []

        for index, phase in enumerate(self.phases):
            # Check if this phase should be skipped (user-specified)
            if index in skip_phases:
                skipped_result = PhaseResult(
                    status=PhaseStatus.SKIPPED,
                    message=f"Phase {index} skipped by user",
                )
                results.append(skipped_result)
                self.context["phase_results"].append(skipped_result)
                continue

            # Check if this phase is already completed
            if self._is_phase_completed(phase):
                skipped_result = PhaseResult(
                    status=PhaseStatus.SKIPPED,
                    message=f"Phase already completed, skipping execution",
                )
                results.append(skipped_result)
                self.context["phase_results"].append(skipped_result)
                continue

            # Execute phase with retry logic
            result = self._execute_phase_with_retry(phase)
            results.append(result)
            self.context["phase_results"].append(result)

            # Stop execution if phase failed
            if result.status == PhaseStatus.FAILED:
                break

        return results

    def _is_phase_completed(self, phase: Phase) -> bool:
        """Check if a phase is already completed.

        Args:
            phase: Phase to check

        Returns:
            True if phase is completed, False otherwise
        """
        # Check if phase has get_status_file method
        if not hasattr(phase, "get_status_file"):
            return False

        try:
            status_file = phase.get_status_file()
            if not status_file.exists():
                return False

            # Read and parse status.json
            with open(status_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            progress = PhaseProgress.from_dict(data)

            # Check if phase is completed
            return progress.status == PhaseStatus.COMPLETED

        except Exception:
            # If any error occurs, assume phase is not completed
            return False

    def _execute_phase_with_retry(self, phase: Phase) -> PhaseResult:
        """Execute a phase with retry logic.

        Args:
            phase: Phase to execute

        Returns:
            Phase result

        Raises:
            WorkflowError: If phase raises exception
        """
        last_result = None

        for attempt in range(self.max_retries + 1):
            try:
                result = phase.execute()

                # If successful, return immediately
                if result.status == PhaseStatus.COMPLETED:
                    return result

                # Store the failed result for potential retry
                last_result = result

            except Exception as e:
                raise WorkflowError(f"Phase execution failed: {e}") from e

        # All retries exhausted, return the last failed result
        return last_result or PhaseResult(
            status=PhaseStatus.FAILED,
            message="Phase failed after all retry attempts",
        )
