"""Checklist validation utilities for CAFE workflow."""

import json
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping

import yaml

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
# A path counts as a test only when it is an executable test module. Outside the
# root `tests/` tree the conventions are language specific, so that helper
# modules (`src/test-utils.ts`, `src/test_utils.ts`) and documentation or data
# (`docs/spec/api.yaml`, `src/api.spec.json`, `config/test-data.json`) can never
# qualify as targeted test evidence.
_JS_TEST_SUFFIXES = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"})
_JS_TEST_STEM = re.compile(r".+\.(test|spec)", re.IGNORECASE)
_PY_TEST_STEM = re.compile(r"test_.+|.+_test", re.IGNORECASE)
_PHP_TEST_STEM = re.compile(r".+Test")
_RUBY_TEST_STEM = re.compile(r"test_.+|.+_test|.+_spec", re.IGNORECASE)
_GO_TEST_STEM = re.compile(r".+_test")
# Root `tests/` retains the historical behaviour: anything pytest can target
# there is accepted, including names such as `tests/check_feature.py`.
_ROOT_TEST_TREE = "tests/"
_ROOT_TEST_SUFFIXES = frozenset(
    {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".php", ".rb", ".go"}
)
# Package managers whose commands may execute inside a filtered workspace package.
_PACKAGE_MANAGERS_WITH_FILTER = frozenset({"pnpm"})
_PNPM_WORKSPACE_FILE = "pnpm-workspace.yaml"
# Dependency trees are never workspace members, however the globs are written.
_NEVER_MEMBER_SEGMENTS = frozenset({"node_modules", "bower_components"})
# pnpm word-form aliases for `--recursive`: these run across workspace packages,
# so they never denote repository-root context.
_PNPM_RECURSIVE_WORDS = frozenset({"recursive", "multi", "m"})
#: Sentinel meaning "unsupported pnpm argument shape; fail closed".
_UNSUPPORTED_CONTEXT = object()
#: Sentinel meaning "repository-root context".
_ROOT_CONTEXT = object()
# A workspace `packages:` entry: literal segments with an optional bare `*`.
_WORKSPACE_PATTERN = re.compile(r"(?:[A-Za-z0-9._-]+|\*)(?:/(?:[A-Za-z0-9._-]+|\*))*")
# A plain pnpm package name only. Glob and selector syntax (`*`, `...`, `^`, `{`,
# `[`, `/` paths) is deliberately unsupported and fails closed.
_PNPM_PLAIN_NAME = re.compile(
    r"(@[A-Za-z0-9][A-Za-z0-9._-]*/)?[A-Za-z0-9][A-Za-z0-9._-]*"
)
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


def _is_test_path(path: str) -> bool:
    """Report whether a repo-relative path denotes an executable test module."""
    name = PurePosixPath(path).name
    suffix = PurePosixPath(name).suffix
    lowered = suffix.lower()
    if not lowered:
        return False
    stem = name[: -len(suffix)]
    # Backwards compatibility: the root `tests/` tree keeps its historical reach.
    if path.startswith(_ROOT_TEST_TREE):
        return lowered in _ROOT_TEST_SUFFIXES
    if lowered in _JS_TEST_SUFFIXES:
        return bool(_JS_TEST_STEM.fullmatch(stem))
    if lowered == ".py":
        return bool(_PY_TEST_STEM.fullmatch(stem))
    if lowered == ".php":
        return bool(_PHP_TEST_STEM.fullmatch(stem))
    if lowered == ".rb":
        return bool(_RUBY_TEST_STEM.fullmatch(stem))
    if lowered == ".go":
        return bool(_GO_TEST_STEM.fullmatch(stem))
    return False


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


def _workspace_member_dirs(root: Path, tracked_paths: set[str]) -> list[str] | None:
    """Return repo-relative directories declared by the root pnpm workspace file.

    Only a small auditable subset of the `packages:` syntax is supported: literal
    paths and a single trailing `*` segment (`apps/*`), plus `!` exclusions of the
    same shape. Any other pattern, a missing or untracked workspace file, or a
    malformed document returns None so filtered commands fail closed.
    """
    if _contained_file(root, _PNPM_WORKSPACE_FILE, tracked_paths) is None:
        return None
    try:
        document = yaml.safe_load((root / _PNPM_WORKSPACE_FILE).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(document, dict):
        return None
    entries = document.get("packages")
    if not isinstance(entries, list) or not entries:
        return None
    includes: list[str] = []
    excludes: list[str] = []
    for entry in entries:
        if not isinstance(entry, str) or not entry:
            return None
        negated = entry.startswith("!")
        pattern = entry[1:] if negated else entry
        pattern = pattern.rstrip("/")
        if not pattern or not _WORKSPACE_PATTERN.fullmatch(pattern):
            return None
        (excludes if negated else includes).append(pattern)
    if not includes:
        return None

    def matches(pattern: str, directory: str) -> bool:
        pattern_parts = PurePosixPath(pattern).parts
        directory_parts = PurePosixPath(directory).parts
        if len(pattern_parts) != len(directory_parts):
            return False
        for expected, actual in zip(pattern_parts, directory_parts):
            if expected == "*":
                # A wildcard never matches a dot-prefixed segment; only an
                # explicit literal entry may name one.
                if actual.startswith("."):
                    return False
                continue
            if expected != actual:
                return False
        return True

    members: list[str] = []
    for relative in tracked_paths:
        if PurePosixPath(relative).name != "package.json":
            continue
        directory = PurePosixPath(relative).parent.as_posix()
        if directory == ".":
            continue
        # Dependency trees are never workspace members, however they are declared.
        if any(segment in _NEVER_MEMBER_SEGMENTS for segment in PurePosixPath(directory).parts):
            continue
        if any(matches(pattern, directory) for pattern in includes) and not any(
            matches(pattern, directory) for pattern in excludes
        ):
            members.append(directory)
    return members


def _pnpm_package_selector(command: list[str]) -> object:
    """Return the package selector a pnpm command executes under.

    The supported contract is deliberately narrow, and matched positionally so a
    child argument can never steer the parse. Exactly three prefixes bind a
    package context: `pnpm exec ...` (root), `pnpm --filter NAME exec ...`, and
    `pnpm --filter=NAME exec ...`. Everything after that positional `exec` is
    child argv. NAME may itself be the literal `exec`.

    Any other command splits at the first explicit `--`; if a pre-boundary token
    begins with `-` the shape is unsupported, otherwise it is ordinary unfiltered
    root context. So `-F`, `--filter-prod`, `-C`/`--dir`, `-r`, workspace-root
    switches, repeated or mixed flags and `pnpm run --filter ...` fail closed,
    while `pnpm run test -- --grep exec tests/...` stays at the root.

    Returns a selector string, `_ROOT_CONTEXT`, or `_UNSUPPORTED_CONTEXT`.
    """
    arguments = command[1:]
    if arguments[:1] == ["exec"]:
        return _ROOT_CONTEXT
    if arguments[:1] == ["--filter"] and arguments[2:3] == ["exec"]:
        return arguments[1]
    if (
        len(arguments) >= 2
        and arguments[0].startswith("--filter=")
        and arguments[1] == "exec"
    ):
        return arguments[0].removeprefix("--filter=")
    # Not a supported prefix: anything after an explicit `--` is child argv.
    leading_boundary = arguments[:1] == ["--"]
    head = arguments[: arguments.index("--")] if "--" in arguments else arguments
    if any(argument.startswith("-") for argument in head):
        return _UNSUPPORTED_CONTEXT
    # The word-form recursive aliases dispatch across every workspace package, so
    # they are not root context. A leading `--` does not suppress that dispatch.
    dispatch = arguments[1:2] if leading_boundary else head[:1]
    if dispatch and dispatch[0] in _PNPM_RECURSIVE_WORDS:
        return _UNSUPPORTED_CONTEXT
    return _ROOT_CONTEXT


def _pnpm_filtered_package_prefix(
    command: list[str], root: Path, tracked_paths: set[str]
) -> str | None:
    """Return the repo-relative directory a pnpm command executes in.

    Returns `""` for repository-root context and None when the command must fail
    closed: an unsupported argument shape, a selector that is not a plain package
    name, or a selector that does not name exactly one included workspace member.
    """
    selector = _pnpm_package_selector(command)
    if selector is _UNSUPPORTED_CONTEXT:
        return None
    if selector is _ROOT_CONTEXT:
        return ""
    # Only a plain package name is supported; any selector syntax fails closed.
    # `web...` would otherwise slip through, since `.` is legal in a package name.
    if (
        not isinstance(selector, str)
        or selector.endswith("...")
        or not _PNPM_PLAIN_NAME.fullmatch(selector)
    ):
        return None
    members = _workspace_member_dirs(root, tracked_paths)
    if members is None:
        return None
    matches: list[str] = []
    for directory in members:
        relative = f"{directory}/package.json"
        if _contained_file(root, relative, tracked_paths) is None:
            continue
        try:
            data = json.loads((root / relative).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("name") == selector:
            matches.append(directory)
    if len(matches) != 1:
        return None
    return matches[0]


def _command_file_arguments(command: list[str], root: Path, tracked_paths: set[str]) -> set[str]:
    """Return existing tracked repo-relative files passed to a verification command."""
    base: str | None = ""
    if command and PurePosixPath(command[0]).name in _PACKAGE_MANAGERS_WITH_FILTER:
        # A filtered command names paths relative to the selected package, so it
        # must resolve there and never fall back to the repository root.
        base = _pnpm_filtered_package_prefix(command, root, tracked_paths)
    paths: set[str] = set()
    if base is None:
        return paths
    for argument in command[1:]:
        if argument.startswith("-"):
            continue
        value = argument.split("::", 1)[0]
        candidate = PurePosixPath(value)
        if candidate.is_absolute() or ".." in candidate.parts:
            continue
        relative = PurePosixPath(base, candidate).as_posix() if base else candidate.as_posix()
        resolved = _contained_file(root, relative, tracked_paths)
        if resolved is not None:
            paths.add(resolved)
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
            if PurePosixPath(value).is_absolute() or ".." in PurePosixPath(value).parts:
                errors[item_id].append(f"Todo ledger file escapes the repository for {item_id}")
                continue
            # Shares the canonical containment checks used for command arguments:
            # symlinks and ancestor-symlink escapes never count as evidence.
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
        if claimed_head != head.stdout.strip().lower():
            errors[item_id].append(f"Todo ledger targeted evidence is stale for {item_id}")
        if receipt_command != command:
            errors[item_id].append(
                f"Todo ledger targeted evidence has no matching recorded result for {item_id}"
            )
            continue
        command_files = _command_file_arguments(command, root, tracked_paths)
        command_tests = {path for path in command_files if _is_test_path(path)}
        claimed_tests = {path for path in paths if _is_test_path(path)}
        if not command_tests or (
            not no_changes and not claimed_tests.intersection(command_tests)
        ):
            errors[item_id].append(f"Todo ledger targeted evidence is unrelated for {item_id}")
    return errors
