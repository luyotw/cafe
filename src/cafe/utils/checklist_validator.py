"""Checklist validation utilities for CAFE workflow."""

from dataclasses import dataclass
from pathlib import Path

CHECKLIST_COMPLETION_INTENTS = frozenset(
    {
        "await_agent",
        "confirm_output",
        "workflow_complete",
    }
)
CHECKLIST_COMPLETION_STATUS_CODES = frozenset(
    {
        "confirmed",
        "ready_for_review",
        *CHECKLIST_COMPLETION_INTENTS,
    }
)


@dataclass
class ChecklistValidationResult:
    """Result of checklist validation.

    Attributes:
        is_complete: True if all checklist items are checked
        unchecked_count: Number of unchecked items found
        checklist_path: Path to the checklist file
    """
    is_complete: bool
    unchecked_count: int
    checklist_path: Path


def completion_requires_checklist(
    *,
    baton_intent: str | None = None,
    status_code: str | None = None,
) -> bool:
    """Return whether an agent result represents checklist-gated completion.

    A valid outbound baton is the canonical completion signal. Status codes are
    retained only as the legacy fallback when no baton intent is available.
    """
    if baton_intent is not None:
        return baton_intent in CHECKLIST_COMPLETION_INTENTS
    return status_code in CHECKLIST_COMPLETION_STATUS_CODES


def validate_checklist(checklist_path: Path) -> ChecklistValidationResult:
    """Validate that all checklist items are completed.

    Checks for unchecked items by searching for lines that start with "[ ]"
    or "- [ ]" (after trimming whitespace). This avoids false positives from
    "[ ]" appearing in descriptive text within a line.

    Supported formats:
    - `[ ] Task name` - Direct checkbox
    - `- [ ] Task name` - Markdown list with checkbox
    - `  - [ ] Nested task` - Indented checkbox

    Args:
        checklist_path: Path to the checklist.md file to validate

    Returns:
        ChecklistValidationResult with validation status and unchecked count

    Raises:
        FileNotFoundError: If checklist file does not exist
    """
    if not checklist_path.exists():
        raise FileNotFoundError(f"Checklist file not found: {checklist_path}")

    # Read checklist content
    content = checklist_path.read_text(encoding="utf-8")

    # Count unchecked items - only lines starting with "[ ]" or "- [ ]" count as unchecked
    # This avoids false positives from "[ ]" in descriptive text
    unchecked_count = 0
    for line in content.splitlines():
        stripped = line.lstrip()
        # Check for both "[ ]" and "- [ ]" formats
        if stripped.startswith("[ ]") or stripped.startswith("- [ ]"):
            unchecked_count += 1

    return ChecklistValidationResult(
        is_complete=(unchecked_count == 0),
        unchecked_count=unchecked_count,
        checklist_path=checklist_path,
    )
