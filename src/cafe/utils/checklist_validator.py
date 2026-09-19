"""Checklist validation utilities for CAFE workflow."""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping

from cafe.core.todo import MAX_TODO_ITEMS, TodoItem

_PROJECTED = re.compile(
    r"^\[(?P<state>[ xX])\] `(?P<id>[^`]+)` — (?P<work>.+) "
    r"\(source fingerprint: (?P<fp>[0-9a-f]{64})\)$"
)
_LEDGER_HEADING = re.compile(r"^### (?P<id>[A-Za-z][A-Za-z0-9_-]*)\s*$")
_LEDGER_FIELD = re.compile(
    r"^- (?P<name>Status|Source fingerprint|Files|Commit|"
    r"Remaining work|Next action):(?P<value>.*)$"
)
_TARGETED_EVIDENCE = re.compile(r"^- Targeted evidence:")
EXPECTED_LEDGER_FIELDS = frozenset(
    {"Status", "Source fingerprint", "Files", "Commit", "Remaining work", "Next action"}
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
    errors.extend(validate_todo_ledger(ledger, expected, repo_root=repo_root))
    return errors


def validate_todo_ledger(
    content: str,
    expected: tuple[TodoItem, ...],
    *,
    repo_root: Path | None = None,
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
    # `Targeted evidence` is optional informational text: every occurrence, and any
    # genuinely indented continuation under it, is skipped before duplicate/malformed
    # accounting. Only leading whitespace marks continuation, so a line without it is
    # always a top-level field however much trailing whitespace it carries.
    in_evidence = False
    for line in lines[headings[0] + 1 :]:
        if line.startswith("## "):
            break
        stripped = line.strip()
        heading = _LEDGER_HEADING.fullmatch(stripped)
        if heading:
            in_evidence = False
            current_id = heading.group("id")
            if current_id in entries:
                duplicates.add(current_id)
            entries.setdefault(current_id, {})
            continue
        if stripped.startswith("### "):
            in_evidence = False
            current_id = None
            continue
        field = _LEDGER_FIELD.fullmatch(stripped)
        if field and current_id is not None:
            in_evidence = False
            name = field.group("name")
            if name in entries[current_id]:
                duplicates.add(current_id)
            entries[current_id][name] = field.group("value").strip()
        elif _TARGETED_EVIDENCE.match(stripped):
            in_evidence = True
        elif in_evidence and line != line.lstrip():
            continue
        elif current_id is not None and stripped.startswith("- ") and ":" in line:
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
        if set(fields) != EXPECTED_LEDGER_FIELDS:
            errors.append(f"Todo ledger evidence is incomplete for {item.item_id}")
            continue
        if fields["Status"].lower() != "completed":
            errors.append(f"Todo ledger status is not completed for {item.item_id}")
        if fields["Source fingerprint"] != f"`{item.fingerprint}`":
            errors.append(f"Todo ledger fingerprint is stale for {item.item_id}")
        for name in ("Files", "Commit"):
            if fields[name].strip().lower() in _UNAVAILABLE:
                errors.append(f"Todo ledger {name.lower()} is unavailable for {item.item_id}")
    if repo_root is not None:
        evidence_errors = validate_todo_evidence_set(
            {item.item_id: entries.get(item.item_id, {}) for item in expected},
            repo_root,
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


def _contained_file(root: Path, relative: str, tracked_paths: set[str]) -> str | None:
    """Return `relative` when it is a tracked regular file truly inside `root`.

    Resolves symlinks so that neither the file nor any ancestor may escape the
    repository, and rejects symlinks outright: a tracked test-named link must not
    stand in for a real test module.
    """
    if relative not in tracked_paths:
        return None
    candidate = root / relative
    if candidate.is_symlink() or not candidate.is_file():
        return None
    try:
        real_root = root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(real_root)
    except (OSError, ValueError):
        return None
    # An ancestor symlink would make the literal path differ from the real one,
    # so compare against the same resolved root rather than the caller's spelling.
    if resolved != (real_root / relative):
        return None
    return relative


def validate_todo_evidence_set(
    evidence: Mapping[str, Mapping[str, str]],
    repo_root: Path,
) -> dict[str, list[str]]:
    """Validate Files and Commit evidence with a fixed number of repository queries."""
    if not evidence:
        return {}
    root = repo_root.resolve()
    errors = {item_id: [] for item_id in evidence}
    parsed: dict[str, tuple[list[str], list[str]]] = {}
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
        parsed[item_id] = (paths, commits)
        all_commits.update(commits)

    # A preflight failure makes the submitted evidence set indivisible: callers
    # must not reuse siblings which have not reached the repository checks.
    if any(errors.values()):
        for item_id, item_errors in errors.items():
            if not item_errors:
                item_errors.append(
                    f"Todo ledger evidence set failed preflight for {item_id}"
                )
        return errors
    # Todo completion requires a clean current worktree. This gate is independent
    # of any claimed evidence, so it uses `--untracked-files=all` to also reject
    # stray new files a tracked-only status would silently allow.
    status = _git(root, "status", "--porcelain", "--untracked-files=all")
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

    dirty = bool(status.stdout.strip())
    for item_id, (paths, commits) in parsed.items():
        if dirty:
            errors[item_id].append(
                f"Todo ledger requires a clean worktree, but it is dirty for {item_id}"
            )
        for value in paths:
            if PurePosixPath(value).is_absolute() or ".." in PurePosixPath(value).parts:
                errors[item_id].append(f"Todo ledger file escapes the repository for {item_id}")
                continue
            # Symlinks and ancestor-symlink escapes never count as evidence.
            if _contained_file(root, value, tracked_paths) is None:
                errors[item_id].append(f"Todo ledger file is missing or untracked for {item_id}")
        changed = set().union(*(commit_files.get(value.lower(), set()) for value in commits))
        if commits and (
            any(value.lower() not in commit_files for value in commits)
            or any(path not in changed for path in paths)
        ):
            errors[item_id].append(
                f"Todo ledger commit does not contain every claimed file for {item_id}"
            )
    return errors
