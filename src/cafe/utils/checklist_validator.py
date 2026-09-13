"""Checklist validation utilities for CAFE workflow."""

import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from cafe.core.todo import TodoItem

_PROJECTED = re.compile(
    r"^\[(?P<state>[ xX])\] `(?P<id>[^`]+)` — (?P<work>.+) "
    r"\(source fingerprint: (?P<fp>[0-9a-f]{64})\)$"
)
_LEDGER_HEADING = re.compile(r"^### (?P<id>[A-Za-z][A-Za-z0-9_-]*)\s*$")
_LEDGER_FIELD = re.compile(
    r"^- (?P<name>Status|Source fingerprint|Files|Commit|Targeted evidence|"
    r"Remaining work|Next action):(?P<value>.*)$"
)
_UNAVAILABLE = frozenset({"", "n/a", "none", "unavailable", "unknown"})

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


def validate_projected_todos(
    checklist_path: Path,
    output_path: Path,
    expected: tuple[TodoItem, ...],
    *,
    repo_root: Path | None = None,
) -> list[str]:
    """Return fail-closed integrity errors for projected rows and their ledger."""
    rows: list[str] = []
    for line in checklist_path.read_text(encoding="utf-8").splitlines():
        match = _PROJECTED.fullmatch(line.strip())
        if match:
            rows.append(line.strip())
    expected_rows = [item.checklist_row().replace("[ ]", "[x]", 1) for item in expected]
    errors: list[str] = []
    if rows != expected_rows:
        errors.append("projected Todo rows do not match the authoritative set")
    ledger = output_path.read_text(encoding="utf-8") if output_path.is_file() else ""
    errors.extend(validate_todo_ledger(ledger, expected, repo_root=repo_root))
    return errors


def validate_todo_ledger(
    content: str, expected: tuple[TodoItem, ...], *, repo_root: Path | None = None
) -> list[str]:
    """Validate exact, non-empty per-item evidence inside one Todo Progress section."""
    lines = content.splitlines()
    headings = [index for index, line in enumerate(lines) if line.strip() == "## Todo Progress"]
    if len(headings) != 1:
        return ["Todo ledger must contain exactly one Todo Progress section"] if expected else []
    entries: dict[str, dict[str, str]] = {}
    duplicates: set[str] = set()
    malformed: set[str] = set()
    current_id: str | None = None
    for line in lines[headings[0] + 1 :]:
        if line.startswith("## "):
            break
        heading = _LEDGER_HEADING.fullmatch(line.strip())
        if heading:
            current_id = heading.group("id")
            if current_id in entries:
                duplicates.add(current_id)
            entries.setdefault(current_id, {})
            continue
        if line.strip().startswith("### "):
            current_id = None
            continue
        field = _LEDGER_FIELD.fullmatch(line.strip())
        if field and current_id is not None:
            name = field.group("name")
            if name in entries[current_id]:
                duplicates.add(current_id)
            entries[current_id][name] = field.group("value").strip()
        elif current_id is not None and line.strip().startswith("- ") and ":" in line:
            malformed.add(current_id)

    errors: list[str] = []
    expected_ids = {item.item_id for item in expected}
    if set(entries) != expected_ids:
        errors.append("Todo ledger item set does not match the authoritative set")
    for item in expected:
        fields = entries.get(item.item_id, {})
        if item.item_id in duplicates or item.item_id in malformed:
            errors.append(f"Todo ledger evidence is malformed or duplicated for {item.item_id}")
            continue
        required = {
            "Status",
            "Source fingerprint",
            "Files",
            "Commit",
            "Targeted evidence",
            "Remaining work",
            "Next action",
        }
        if set(fields) != required:
            errors.append(f"Todo ledger evidence is incomplete for {item.item_id}")
            continue
        if fields["Status"].lower() != "completed":
            errors.append(f"Todo ledger status is not completed for {item.item_id}")
        if fields["Source fingerprint"] != f"`{item.fingerprint}`":
            errors.append(f"Todo ledger fingerprint is stale for {item.item_id}")
        for name in ("Files", "Commit", "Targeted evidence"):
            if fields[name].strip().lower() in _UNAVAILABLE:
                errors.append(f"Todo ledger {name.lower()} is unavailable for {item.item_id}")
        if repo_root is not None:
            errors.extend(validate_todo_evidence(item.item_id, fields, repo_root))
    return errors


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def validate_todo_evidence(item_id: str, fields: dict[str, str], repo_root: Path) -> list[str]:
    """Verify that a completion claim is bound to the current repository state."""
    errors: list[str] = []
    root = repo_root.resolve()
    files_value = fields.get("Files", "")
    commit_value = fields.get("Commit", "")
    no_changes = files_value == "N/A (no repository changes)" and bool(
        re.fullmatch(r"N/A \(no repository changes\): \S.*", commit_value)
    )
    if no_changes:
        paths: list[str] = []
        if _git(root, "status", "--porcelain", "--untracked-files=no").stdout.strip():
            errors.append(
                f"Todo ledger no-change claim conflicts with repository state for {item_id}"
            )
    else:
        paths = re.findall(r"`([^`]+)`", files_value)
        if not paths:
            errors.append(f"Todo ledger files are not canonical for {item_id}")
            return errors
    for value in paths:
        candidate = (root / value).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            errors.append(f"Todo ledger file escapes the repository for {item_id}")
            continue
        if not candidate.is_file() or _git(root, "ls-files", "--error-unmatch", value).returncode:
            errors.append(f"Todo ledger file is missing or untracked for {item_id}")

    commits = re.findall(r"`([0-9a-fA-F]{7,40})`", commit_value)
    if not commits and not no_changes:
        errors.append(f"Todo ledger commit is not canonical for {item_id}")
    changed: set[str] = set()
    for commit in commits:
        if _git(root, "cat-file", "-e", f"{commit}^{{commit}}").returncode:
            errors.append(f"Todo ledger commit does not exist for {item_id}")
            continue
        result = _git(root, "show", "--format=", "--name-only", commit)
        changed.update(line for line in result.stdout.splitlines() if line)
    if commits and any(path not in changed for path in paths):
        errors.append(f"Todo ledger commit does not contain every claimed file for {item_id}")

    evidence = fields.get("Targeted evidence", "")
    match = re.fullmatch(
        r"command=`(?P<command>[^`]+)`; exit=0; head=`(?P<head>[0-9a-fA-F]{40})`",
        evidence,
    )
    if match is None:
        errors.append(f"Todo ledger targeted evidence is not canonical for {item_id}")
        return errors
    head = _git(root, "rev-parse", "HEAD")
    if head.returncode or match.group("head").lower() != head.stdout.strip().lower():
        errors.append(f"Todo ledger targeted evidence is stale for {item_id}")
    try:
        command_parts = shlex.split(match.group("command"))
    except ValueError:
        command_parts = []
    test_paths = [part for part in command_parts if part.startswith("tests/")]
    if not test_paths or any(not (root / path).is_file() for path in test_paths):
        errors.append(f"Todo ledger targeted evidence is unrelated for {item_id}")
    return errors
