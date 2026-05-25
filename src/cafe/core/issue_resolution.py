"""Resolve the active issue from Git branch health or runtime fallback marker."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cafe.core import active_issue
from cafe.core.git import GitOperations


@dataclass(frozen=True)
class ResolvedActiveIssue:
    """Result of resolving which issue to run."""

    issue_name: str
    cafe_dir: Path
    source: str  # explicit | branch | fallback


class ActiveIssueResolutionError(Exception):
    """Could not determine which issue to use."""

    def __init__(self, message: str, guidance: str) -> None:
        super().__init__(message)
        self.message = message
        self.guidance = guidance


_RECOVERY_GUIDANCE = (
    "Run `cafe prepare <issue>` on a healthy branch, or fix Git state "
    "(finish rebase/merge, checkout a feature branch), then run `cafe make` again."
)


def resolve_active_issue(
    *,
    cafe_dir: Path,
    git_ops: GitOperations,
    explicit_issue: Optional[str] = None,
) -> ResolvedActiveIssue:
    """Resolve the active issue for workflow startup.

    Explicit ``--issue`` bypasses branch and fallback logic. Healthy Git branch
    with a prepared issue wins and synchronizes the marker. Unhealthy Git uses
    the marker only when it references an existing prepared issue.
    """
    if explicit_issue:
        name = explicit_issue.strip()
        if not name:
            raise ActiveIssueResolutionError(
                "Issue name cannot be empty.",
                _RECOVERY_GUIDANCE,
            )
        return ResolvedActiveIssue(name, cafe_dir, "explicit")

    health = git_ops.get_branch_health()
    if health.is_healthy:
        branch = health.branch_name
        assert branch is not None
        if active_issue.issue_exists(cafe_dir, branch):
            active_issue.write_marker(cafe_dir, branch)
            return ResolvedActiveIssue(branch, cafe_dir, "branch")
        raise ActiveIssueResolutionError(
            f"No prepared issue found for branch '{branch}'.",
            f"Run `cafe prepare {branch}` or switch to a prepared feature branch.",
        )

    marker = active_issue.read_marker(cafe_dir)
    if marker and active_issue.issue_exists(cafe_dir, marker):
        return ResolvedActiveIssue(marker, cafe_dir, "fallback")

    if marker and not active_issue.issue_exists(cafe_dir, marker):
        raise ActiveIssueResolutionError(
            f"Active issue marker points to missing issue '{marker}'.",
            _RECOVERY_GUIDANCE,
        )

    raise ActiveIssueResolutionError(
        "Cannot determine active issue: Git branch detection is unavailable.",
        _RECOVERY_GUIDANCE,
    )
