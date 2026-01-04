"""Service layer for cafe summary command."""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

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
        """Load phase status from status.json file.

        Args:
            issue_name: Name of the issue
            phase_name: Name of the phase (spec, plan, develop, review, pr)

        Returns:
            Dictionary containing phase status information, or None if file doesn't exist
        """
        status_file = Path(".cafe/issues") / issue_name / phase_name / "status.json"

        if not status_file.exists():
            return None

        try:
            with open(status_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            raise RuntimeError(f"Failed to load phase status from {status_file}: {e}")

    def load_iteration_statuses(self, issue_name: str, phase_name: str) -> List[Dict[str, Any]]:
        """Load all iteration statuses for a phase.

        Args:
            issue_name: Name of the issue
            phase_name: Name of the phase

        Returns:
            List of iteration status dictionaries, ordered by iteration number
        """
        phase_dir = Path(".cafe/issues") / issue_name / phase_name

        if not phase_dir.exists():
            return []

        iterations = []

        # Find all iteration status files (format: iteration_NNN/status.json)
        for iteration_dir in sorted(phase_dir.glob("iteration_*")):
            if not iteration_dir.is_dir():
                continue

            status_file = iteration_dir / "status.json"
            if not status_file.exists():
                continue

            try:
                with open(status_file, "r") as f:
                    status_data = json.load(f)
                    iterations.append(status_data)
            except (json.JSONDecodeError, IOError) as e:
                # Track errors for reporting to user later
                self._load_errors.append({
                    'file': str(status_file),
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
