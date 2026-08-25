"""Create and validate test receipts shared between develop and review."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

SCHEMA_VERSION = 1
VALID_SCOPES = frozenset({"full", "targeted"})
PYTEST_EXECUTABLES = frozenset({"pytest", "py.test"})
FOCUSED_SELECTOR_PATTERN = re.compile(
    r"^[A-Za-z0-9_.\-/]+\.py(?:::[A-Za-z0-9_.\[\]/\-]+)*$"
)


class VerificationReceiptError(ValueError):
    """Raised when a receipt cannot be created or parsed safely."""


@dataclass(frozen=True)
class ReceiptCheck:
    """Result of checking a verification receipt against the current checkout."""

    valid: bool
    reasons: tuple[str, ...]
    receipt_path: Path
    receipt: dict[str, Any] | None = None


def receipt_path_for_output(output_file: Path) -> Path:
    """Return the iteration-local receipt path for one phase output."""
    return output_file.resolve().parent / "verification.json"


def _run_git(args: Sequence[str], *, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        raise VerificationReceiptError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def _git_state(cwd: Path) -> tuple[Path, str, str]:
    root = Path(_run_git(["rev-parse", "--show-toplevel"], cwd=cwd)).resolve()
    head = _run_git(["rev-parse", "HEAD"], cwd=root)
    status = _run_git(
        ["status", "--porcelain=v1", "--untracked-files=all"],
        cwd=root,
    )
    return root, head, status


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def run_verification(
    *,
    output_file: Path,
    command: Sequence[str],
    scope: str,
    cwd: Path | None = None,
) -> tuple[int, Path, dict[str, Any]]:
    """Run one verification command and persist a receipt tied to a clean HEAD."""
    if scope not in VALID_SCOPES:
        raise VerificationReceiptError(
            f"scope must be one of: {', '.join(sorted(VALID_SCOPES))}"
        )
    if not command:
        raise VerificationReceiptError("verification command must not be empty")

    run_cwd = (cwd or Path.cwd()).resolve()
    root_before, head_before, status_before = _git_state(run_cwd)
    if run_cwd != root_before:
        raise VerificationReceiptError("verification must run from the Git worktree root")
    if status_before:
        raise VerificationReceiptError(
            "worktree must be clean before final verification; commit the implementation first"
        )

    started_at = datetime.now().astimezone().isoformat()
    started = time.monotonic()
    try:
        result = subprocess.run(list(command), cwd=run_cwd, check=False)
    except OSError as exc:
        raise VerificationReceiptError(
            f"cannot start verification command {command[0]!r}: {exc}"
        ) from exc
    duration_seconds = round(time.monotonic() - started, 3)
    finished_at = datetime.now().astimezone().isoformat()

    root_after, head_after, status_after = _git_state(run_cwd)
    state_unchanged = root_after == root_before and head_after == head_before and not status_after
    valid = result.returncode == 0 and state_unchanged
    receipt_path = receipt_path_for_output(output_file)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "scope": scope,
        "command": list(command),
        "exit_code": result.returncode,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": duration_seconds,
        "git": {
            "root": str(root_before),
            "cwd": str(run_cwd),
            "cwd_relative_to_root": run_cwd.relative_to(root_before).as_posix() or ".",
            "head": head_before,
            "clean_before": True,
            "clean_after": not status_after,
            "head_unchanged": head_after == head_before,
        },
        "valid": valid,
    }
    _write_json_atomic(receipt_path, payload)

    if result.returncode != 0:
        return result.returncode, receipt_path, payload
    if not state_unchanged:
        return 3, receipt_path, payload
    return 0, receipt_path, payload


def check_verification_receipt(
    *,
    output_file: Path,
    required_scope: str = "full",
    cwd: Path | None = None,
) -> ReceiptCheck:
    """Validate a receipt against the current clean checkout and HEAD."""
    if required_scope not in VALID_SCOPES:
        raise VerificationReceiptError(
            f"required_scope must be one of: {', '.join(sorted(VALID_SCOPES))}"
        )

    receipt_path = receipt_path_for_output(output_file)
    if not receipt_path.is_file():
        return ReceiptCheck(False, ("receipt is missing",), receipt_path)

    try:
        loaded = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ReceiptCheck(False, (f"receipt is unreadable: {exc}",), receipt_path)
    if not isinstance(loaded, dict):
        return ReceiptCheck(False, ("receipt root must be an object",), receipt_path)

    reasons: list[str] = []
    if loaded.get("schema_version") != SCHEMA_VERSION:
        reasons.append(f"unsupported schema_version: {loaded.get('schema_version')!r}")
    if loaded.get("scope") != required_scope:
        reasons.append(
            f"scope is {loaded.get('scope')!r}, expected {required_scope!r}"
        )
    if loaded.get("exit_code") != 0 or loaded.get("valid") is not True:
        reasons.append("recorded verification did not pass")
    command = loaded.get("command")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) and part for part in command)
    ):
        reasons.append("recorded verification command is missing or invalid")

    git_record = loaded.get("git")
    if not isinstance(git_record, dict):
        reasons.append("git record is missing")
        git_record = {}
    if git_record.get("clean_before") is not True:
        reasons.append("recorded worktree was not clean before verification")
    if git_record.get("clean_after") is not True:
        reasons.append("recorded worktree was not clean after verification")
    if git_record.get("head_unchanged") is not True:
        reasons.append("HEAD changed while verification was running")
    if required_scope == "full" and git_record.get("cwd_relative_to_root") != ".":
        reasons.append("full verification was not run from the worktree root")

    try:
        current_root, current_head, current_status = _git_state(
            (cwd or Path.cwd()).resolve()
        )
    except VerificationReceiptError as exc:
        reasons.append(str(exc))
    else:
        if current_status:
            reasons.append("current worktree is dirty")
        if git_record.get("root") != str(current_root):
            reasons.append("current worktree root does not match the verified worktree")
        if git_record.get("cwd") != git_record.get("root"):
            reasons.append("recorded verification cwd does not match its worktree root")
        if git_record.get("head") != current_head:
            reasons.append("current HEAD does not match the verified HEAD")

    return ReceiptCheck(not reasons, tuple(reasons), receipt_path, loaded)


def reuse_verification_receipt(
    *,
    source_output_file: Path,
    output_file: Path,
    required_scope: str = "full",
    cwd: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Materialize a checked receipt for a new iteration without rerunning tests."""
    source_receipt_path = receipt_path_for_output(source_output_file)
    target_receipt_path = receipt_path_for_output(output_file)
    if source_receipt_path == target_receipt_path:
        raise VerificationReceiptError(
            "source and target verification receipts must be in different iterations"
        )

    checked = check_verification_receipt(
        output_file=source_output_file,
        required_scope=required_scope,
        cwd=cwd,
    )
    if not checked.valid or checked.receipt is None:
        detail = "; ".join(checked.reasons) or "unknown receipt error"
        raise VerificationReceiptError(f"cannot reuse an invalid receipt: {detail}")

    payload = dict(checked.receipt)
    payload["reused_from"] = str(source_receipt_path)
    payload["reused_at"] = datetime.now().astimezone().isoformat()
    _write_json_atomic(target_receipt_path, payload)
    return target_receipt_path, payload


def _pytest_argument_index(command: Sequence[str]) -> int | None:
    """Return the pytest token index for a deliberately narrow runner grammar."""
    if not command:
        return None
    executable = command[0]
    if executable in PYTEST_EXECUTABLES:
        return 0
    if executable in {"python", "python3"} and list(command[1:3]) == ["-m", "pytest"]:
        return 2
    if executable != "uv" or len(command) < 3 or command[1] != "run":
        return None

    index = 2
    while index < len(command):
        token = command[index]
        if token in PYTEST_EXECUTABLES:
            return index
        if token == "--with" and index + 1 < len(command):
            if command[index + 1] != "pytest":
                return None
            index += 2
            continue
        if token in {"--frozen", "--isolated", "--no-project"}:
            index += 1
            continue
        return None
    return None


def _focused_pytest_command(
    command: Sequence[str], selectors: Sequence[str]
) -> list[str]:
    pytest_index = _pytest_argument_index(command)
    if pytest_index is None:
        raise VerificationReceiptError(
            "focused verification only supports direct pytest, python -m pytest, "
            "or uv run pytest receipts"
        )
    existing_pytest_args = command[pytest_index + 1 :]
    if any(not arg.startswith("-") or arg == "--" for arg in existing_pytest_args):
        raise VerificationReceiptError(
            "focused verification requires a full pytest receipt with no existing "
            "path or node selectors"
        )
    for selector in selectors:
        if (
            not FOCUSED_SELECTOR_PATTERN.fullmatch(selector)
            or Path(selector.split("::", 1)[0]).is_absolute()
            or ".." in Path(selector.split("::", 1)[0]).parts
        ):
            raise VerificationReceiptError(
                "focused selectors must be relative pytest file paths or node ids; "
                f"got {selector!r}"
            )
    return [*command, *selectors]


def run_focused_verification(
    *,
    output_file: Path,
    selectors: Sequence[str],
    cwd: Path | None = None,
) -> tuple[int, list[str]]:
    """Replay a verified full command with additional focused selectors only."""
    if not selectors:
        raise VerificationReceiptError("focused verification requires at least one selector")
    run_cwd = (cwd or Path.cwd()).resolve()
    checked = check_verification_receipt(
        output_file=output_file,
        required_scope="full",
        cwd=run_cwd,
    )
    if not checked.valid or checked.receipt is None:
        detail = "; ".join(checked.reasons) or "unknown receipt error"
        raise VerificationReceiptError(f"cannot focus an invalid full receipt: {detail}")

    command = _focused_pytest_command(checked.receipt["command"], selectors)
    root_before, head_before, status_before = _git_state(run_cwd)
    if status_before:
        raise VerificationReceiptError("worktree must be clean before focused verification")
    try:
        result = subprocess.run(command, cwd=root_before, check=False)
    except OSError as exc:
        raise VerificationReceiptError(
            f"cannot start focused verification command {command[0]!r}: {exc}"
        ) from exc
    root_after, head_after, status_after = _git_state(run_cwd)
    if (
        result.returncode == 0
        and (root_after != root_before or head_after != head_before or status_after)
    ):
        return 3, command
    return result.returncode, command
