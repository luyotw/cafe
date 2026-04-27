"""Service layer for cafe summary command."""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

from cafe.core.blackboard import HandoffContract, HandoffOwner, BlackboardState
from cafe.core.git import GitOperations
from cafe.core.types import PhaseStatus


class SummaryService:
    """Service for building workflow summary timeline data."""

    def __init__(self, git_ops: Optional[GitOperations] = None):
        """Initialize summary service.

        Args:
            git_ops: GitOperations instance for git context detection
        """
        self.git_ops = git_ops or GitOperations()
        self._load_errors: List[Dict[str, str]] = []

    def get_current_issue(self) -> str:
        """Get current issue name from git branch context.

        Returns:
            Current issue name (branch name)

        Raises:
            RuntimeError: If not in a valid git repository or branch
        """
        try:
            branch_name = self.git_ops.get_current_branch()
            if not branch_name:
                raise RuntimeError("Failed to get current branch")
            return branch_name
        except Exception as e:
            raise RuntimeError(f"Failed to detect current issue from git context: {e}")

    def load_phase_status(self, issue_name: str, phase_name: str) -> Optional[Dict[str, Any]]:
        """Load phase status from status.json or synthesize it from workflow state.

        Args:
            issue_name: Name of the issue
            phase_name: Name of the phase (spec, plan, develop, review, pr)

        Returns:
            Dictionary containing phase status information, or None if no phase data exists
        """
        status_file = Path(".cafe/issues") / issue_name / phase_name / "status.json"

        if status_file.exists():
            try:
                with open(status_file, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                raise RuntimeError(f"Failed to load phase status from {status_file}: {e}")

        iterations = self.load_iteration_statuses(issue_name, phase_name)
        if not iterations:
            return None

        return self._synthesize_phase_status(issue_name, phase_name, iterations)

    def load_iteration_statuses(self, issue_name: str, phase_name: str) -> List[Dict[str, Any]]:
        """Load all iteration statuses for a phase from context.json files.

        Args:
            issue_name: Name of the issue
            phase_name: Name of the phase

        Returns:
            List of iteration context dictionaries with timestamp (start_time),
            end_time, status_code, cli, model, and stats, ordered by iteration number
        """
        phase_dir = Path(".cafe/issues") / issue_name / phase_name

        if not phase_dir.exists():
            return []

        iterations = []

        # Read from context.json files in each iteration directory
        for iteration_dir in sorted(phase_dir.glob("iteration_*")):
            if not iteration_dir.is_dir():
                continue

            context_file = iteration_dir / "context.json"
            if not context_file.exists():
                continue

            try:
                with open(context_file, "r") as f:
                    context_data = json.load(f)
                    # Extract fields needed for summary display including token usage
                    iteration_info = {
                        "iteration": context_data.get("iteration"),
                        "timestamp": context_data.get("timestamp"),
                        "end_time": context_data.get("end_time"),
                        "status_code": context_data.get("status_code"),
                        "cli": context_data.get("cli"),
                        "model": context_data.get("model"),
                        "stats": context_data.get("stats"),
                    }
                    iterations.append(iteration_info)
            except (json.JSONDecodeError, IOError) as e:
                # Track errors for reporting to user later
                self._load_errors.append({
                    'file': str(context_file),
                    'error': str(e),
                    'type': 'iteration'
                })
                continue

        return iterations

    def get_load_errors(self) -> List[Dict[str, str]]:
        """Get any errors that occurred during file loading.

        Returns:
            List of load error dictionaries containing file path and error message
        """
        return self._load_errors

    def _load_workflow_state(self, issue_name: str) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
        """Load the current workflow pointer and baton contract if available."""
        issue_dir = Path(".cafe/issues") / issue_name
        blackboard_file = issue_dir / "blackboard.json"
        next_step_file = issue_dir / "next_step.txt"

        if not blackboard_file.exists():
            return None, None

        try:
            raw = json.loads(blackboard_file.read_text(encoding="utf-8"))
            state = BlackboardState.from_dict(raw, initial_step="spec")
        except Exception:
            return None, None

        if not next_step_file.exists():
            return state.current_step, None

        try:
            payload = json.loads(next_step_file.read_text(encoding="utf-8"))
            contract = HandoffContract.from_dict(payload)
        except Exception:
            return state.current_step, None

        return state.current_step, contract.to_dict()

    def _synthesize_phase_status(
        self, issue_name: str, phase_name: str, iterations: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Derive phase status from iteration contexts and baton state."""
        first_iteration = iterations[0]
        latest_iteration = iterations[-1]
        current_step, baton = self._load_workflow_state(issue_name)

        latest_status_code = latest_iteration.get("status_code")
        latest_end_time = latest_iteration.get("end_time")

        baton_from_step = str(baton.get("from_step", "")) if baton else ""
        baton_to_step = str(baton.get("to_step", "")) if baton else ""
        baton_to_owner = str(baton.get("to_owner", "")) if baton else ""

        paused_in_phase = (
            baton_from_step == phase_name
            and baton_to_step == "user"
            and baton_to_owner == HandoffOwner.USER.value
        )
        awaiting_agent_in_phase = (
            baton_to_step == phase_name
            and baton_to_owner == HandoffOwner.AGENT.value
        )
        active_in_phase = current_step == phase_name or paused_in_phase or awaiting_agent_in_phase

        status = PhaseStatus.IN_PROGRESS if active_in_phase else PhaseStatus.COMPLETED

        synthesized = {
            "timestamp": first_iteration.get("timestamp"),
            "status": status.value,
            "status_code": latest_status_code,
        }

        if status == PhaseStatus.COMPLETED and latest_end_time:
            synthesized["end_time"] = latest_end_time

        return synthesized
