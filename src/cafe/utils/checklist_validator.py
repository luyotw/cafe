"""Checklist validation utilities for CAFE workflow."""

import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from cafe.core.todo import MAX_TODO_ITEMS, TodoItem
from cafe.verification.receipt import check_verification_receipt

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
MAX_EVIDENCE_PATHS_PER_ITEM = 32
MAX_EVIDENCE_COMMITS_PER_ITEM = 8
GIT_EVIDENCE_TIMEOUT_SECONDS = 10

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
    errors.extend(
        validate_todo_ledger(
            ledger,
            expected,
            repo_root=repo_root,
            output_path=output_path,
        )
    )
    return errors


def validate_todo_ledger(
    content: str,
    expected: tuple[TodoItem, ...],
    *,
    repo_root: Path | None = None,
    output_path: Path | None = None,
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
    if len(expected) > MAX_TODO_ITEMS:
        return [f"Todo ledger exceeds {MAX_TODO_ITEMS} items"]
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
        evidence_errors = validate_todo_evidence_set(
            {item.item_id: entries.get(item.item_id, {}) for item in expected},
            repo_root,
            output_path=output_path,
        )
        for item in expected:
            errors.extend(evidence_errors.get(item.item_id, ()))
    return errors


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=GIT_EVIDENCE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(
            args=["git", *args], returncode=124, stdout="", stderr=str(exc)
        )


def _command_file_arguments(command: list[str], root: Path) -> set[str]:
    """Return existing repo-relative files passed to a verification command."""
    paths: set[str] = set()
    for argument in command[1:]:
        if argument.startswith("-"):
            continue
        value = argument.split("::", 1)[0]
        candidate_path = Path(value)
        if candidate_path.is_absolute() or ".." in candidate_path.parts:
            continue
        candidate = (root / candidate_path).resolve()
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_file():
            paths.add(relative.as_posix())
    return paths


def validate_todo_evidence_set(
    evidence: Mapping[str, Mapping[str, str]],
    repo_root: Path,
    *,
    output_path: Path | None,
) -> dict[str, list[str]]:
    """Validate all item evidence with a fixed number of repository queries."""
    if not evidence:
        return {}
    root = repo_root.resolve()
    errors = {item_id: [] for item_id in evidence}
    parsed: dict[str, tuple[list[str], list[str], list[str], str]] = {}
    all_commits: set[str] = set()
    for item_id, fields in evidence.items():
        files_value = fields.get("Files", "")
        commit_value = fields.get("Commit", "")
        no_changes = files_value == "N/A (no repository changes)" and bool(
            re.fullmatch(r"N/A \(no repository changes\): \S.*", commit_value)
        )
        paths = [] if no_changes else re.findall(r"`([^`]+)`", files_value)
        commits = [] if no_changes else re.findall(r"`([0-9a-fA-F]{40})`", commit_value)
        if not no_changes and not paths:
            errors[item_id].append(f"Todo ledger files are not canonical for {item_id}")
        if len(paths) > MAX_EVIDENCE_PATHS_PER_ITEM:
            errors[item_id].append(f"Todo ledger files exceed the limit for {item_id}")
        if not no_changes and not commits:
            errors[item_id].append(f"Todo ledger commit is not canonical for {item_id}")
        if len(commits) > MAX_EVIDENCE_COMMITS_PER_ITEM:
            errors[item_id].append(f"Todo ledger commits exceed the limit for {item_id}")
        evidence_match = re.fullmatch(
            r"command=`(?P<command>[^`]+)`; exit=0; head=`(?P<head>[0-9a-fA-F]{40})`",
            fields.get("Targeted evidence", ""),
        )
        if evidence_match is None:
            errors[item_id].append(f"Todo ledger targeted evidence is not canonical for {item_id}")
            command: list[str] = []
            claimed_head = ""
        else:
            try:
                command = shlex.split(evidence_match.group("command"))
            except ValueError:
                command = []
            claimed_head = evidence_match.group("head").lower()
        parsed[item_id] = (paths, commits, command, claimed_head)
        all_commits.update(commits)

    # A preflight failure makes the submitted evidence set indivisible: callers
    # must not reuse siblings which have not reached repository/receipt checks.
    if any(errors.values()):
        for item_id, item_errors in errors.items():
            if not item_errors:
                item_errors.append(
                    f"Todo ledger evidence set failed preflight for {item_id}"
                )
        return errors
    status = _git(root, "status", "--porcelain", "--untracked-files=no")
    head = _git(root, "rev-parse", "HEAD")
    tracked = _git(root, "ls-files", "-z")
    commit_result = (
        _git(
            root,
            "show",
            "--format=__CAFE_COMMIT__%H",
            "--name-only",
            "--no-renames",
            *sorted(all_commits),
        )
        if all_commits
        else None
    )
    if (
        status.returncode
        or head.returncode
        or tracked.returncode
        or (commit_result is not None and commit_result.returncode)
    ):
        return {
            item_id: [f"Todo ledger repository evidence failed for {item_id}"]
            for item_id in evidence
        }

    tracked_paths = set(tracked.stdout.split("\0"))
    commit_files: dict[str, set[str]] = {}
    current_commit: str | None = None
    if commit_result is not None:
        for line in commit_result.stdout.splitlines():
            if line.startswith("__CAFE_COMMIT__"):
                current_commit = line.removeprefix("__CAFE_COMMIT__").lower()
                commit_files.setdefault(current_commit, set())
            elif line and current_commit is not None:
                commit_files[current_commit].add(line)

    checked_receipt = (
        check_verification_receipt(
            output_file=output_path,
            required_scope="targeted",
            cwd=root,
        )
        if output_path is not None
        else None
    )
    receipt_command = (
        checked_receipt.receipt.get("command")
        if checked_receipt is not None and checked_receipt.valid and checked_receipt.receipt
        else None
    )
    for item_id, (paths, commits, command, claimed_head) in parsed.items():
        fields = evidence[item_id]
        no_changes = fields.get("Files") == "N/A (no repository changes)"
        if no_changes and status.stdout.strip():
            errors[item_id].append(
                f"Todo ledger no-change claim conflicts with repository state for {item_id}"
            )
        for value in paths:
            candidate = (root / value).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                errors[item_id].append(f"Todo ledger file escapes the repository for {item_id}")
                continue
            if not candidate.is_file() or value not in tracked_paths:
                errors[item_id].append(f"Todo ledger file is missing or untracked for {item_id}")
        changed = set().union(*(commit_files.get(value.lower(), set()) for value in commits))
        if commits and (
            any(value.lower() not in commit_files for value in commits)
            or any(path not in changed for path in paths)
        ):
            errors[item_id].append(
                f"Todo ledger commit does not contain every claimed file for {item_id}"
            )
        if claimed_head != head.stdout.strip().lower():
            errors[item_id].append(f"Todo ledger targeted evidence is stale for {item_id}")
        if receipt_command != command:
            errors[item_id].append(
                f"Todo ledger targeted evidence has no matching recorded result for {item_id}"
            )
            continue
        command_files = _command_file_arguments(command, root)
        command_tests = {path for path in command_files if path.startswith("tests/")}
        claimed_tests = {path for path in paths if path.startswith("tests/")}
        if not command_tests or (
            not no_changes and not claimed_tests.intersection(command_tests)
        ):
            errors[item_id].append(f"Todo ledger targeted evidence is unrelated for {item_id}")
    return errors
