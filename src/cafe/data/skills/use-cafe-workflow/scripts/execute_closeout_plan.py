#!/usr/bin/env python3
"""Execute one exact stage from a confirmed Driver closeout plan."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

_SOURCE_ROOT = Path(__file__).resolve().parents[5]
if str(_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SOURCE_ROOT))

from cafe.driver._store import load_contract  # noqa: E402
from cafe.driver.delivery import normalize_delivery_contract  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_action_authority import assess_confirmed_closeout_command  # noqa: E402, I001


_SHELL_EXECUTABLES = {"bash", "cmd", "cmd.exe", "dash", "fish", "ksh", "pwsh", "sh", "zsh"}
_SHELL_CODE_FLAGS = {"-c", "-lc", "-Command", "/c", "/C"}


class CloseoutCommandError(RuntimeError):
    """A command may have changed an external system but did not succeed."""


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _directory(path: Path, *, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_dir():
        raise ValueError(f"{label} must be an existing directory")
    return resolved


def _confirmed_issue_worktree(contract: dict[str, Any], *, project_root: Path) -> Path:
    checkout = contract.get("checkout")
    if not isinstance(checkout, dict):
        raise ValueError("Driver contract checkout is invalid")
    root = _directory(project_root, label="confirmed project root")
    if checkout.get("kind") == "current_checkout":
        if set(checkout) != {"kind"}:
            raise ValueError("Driver contract checkout is invalid")
        return root
    if checkout.get("kind") != "worktree" or set(checkout) != {"kind", "path"}:
        raise ValueError("Driver contract checkout is invalid")
    path = checkout["path"]
    if not isinstance(path, str) or not path:
        raise ValueError("Driver contract checkout is invalid")
    return _directory(
        Path(path) if Path(path).is_absolute() else root / path, label="issue worktree"
    )


def _receipt_path(path: Path, *, issue_worktree: Path) -> Path:
    if not path.is_absolute():
        raise ValueError("receipt file must use an absolute path outside the issue worktree")
    if _is_within(path.resolve(), issue_worktree):
        raise ValueError("receipt file must be outside the issue worktree")
    if path.exists() and path.is_symlink():
        raise ValueError("receipt file must not be a symlink")
    return path


@contextmanager
def _receipt_lock(receipt_path: Path) -> Iterator[None]:
    """Serialize inspection and updates for one durable closeout receipt."""
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = receipt_path.with_name(f".{receipt_path.name}.lock")
    if lock_path.exists() and lock_path.is_symlink():
        raise ValueError("receipt lock must not be a symlink")
    try:
        descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError as exc:
        raise ValueError("receipt lock is unsafe") from exc
    with os.fdopen(descriptor, "a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except OSError as exc:
            raise ValueError("receipt lock is unavailable") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _empty_receipt(contract_sha256: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "contract_sha256": contract_sha256,
        "stages": {"deliver": [], "cleanup": []},
    }


def _load_receipt(path: Path, *, contract_sha256: str) -> dict[str, Any]:
    if not path.exists():
        return _empty_receipt(contract_sha256)
    if path.is_symlink() or not path.is_file():
        raise ValueError("receipt file is unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("receipt file is unreadable") from exc
    if not isinstance(value, dict) or set(value) != {"schema_version", "contract_sha256", "stages"}:
        raise ValueError("receipt file has an unsupported shape")
    if value["schema_version"] != 1 or value["contract_sha256"] != contract_sha256:
        raise ValueError("receipt belongs to a different confirmed contract")
    stages = value["stages"]
    if not isinstance(stages, dict) or set(stages) != {"deliver", "cleanup"}:
        raise ValueError("receipt stages are invalid")
    for records in stages.values():
        if not isinstance(records, list):
            raise ValueError("receipt command records are invalid")
        for record in records:
            if not isinstance(record, dict) or not {"argv", "status"} <= set(record):
                raise ValueError("receipt command record is invalid")
            if set(record) - {"argv", "status", "returncode", "error"}:
                raise ValueError("receipt command record is invalid")
            if (
                not isinstance(record["argv"], list)
                or not record["argv"]
                or any(not isinstance(item, str) for item in record["argv"])
                or not record["argv"][0]
                or record["status"] not in {"started", "succeeded", "failed"}
            ):
                raise ValueError("receipt command record is invalid")
    return value


def _write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    created = False
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        created = True
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(receipt, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if created and temporary.exists():
            temporary.unlink()


def _validate_command(argv: list[str]) -> None:
    executable = Path(argv[0]).name.lower()
    if executable in _SHELL_EXECUTABLES and any(arg in _SHELL_CODE_FLAGS for arg in argv[1:]):
        raise ValueError("closeout commands must not execute shell code strings")
    if executable == "cafe" and len(argv) > 1 and argv[1] in {"close", "deliver"}:
        raise ValueError("closeout plans must not recursively invoke cafe close or deliver")


def _record_matches(record: Any, argv: list[str]) -> bool:
    return (
        isinstance(record, dict)
        and record.get("argv") == argv
        and record.get("status") in {"started", "succeeded", "failed"}
    )


def execute_stage(
    *,
    closeout_plan: dict[str, Any],
    contract_sha256: str,
    stage: str,
    issue_worktree: Path,
    receipt_file: Path,
) -> dict[str, Any]:
    """Run one ordered stage, writing durable state before every command."""
    if stage not in {"deliver", "cleanup"}:
        raise ValueError("stage must be deliver or cleanup")
    worktree = _directory(issue_worktree, label="issue worktree")
    receipt_path = _receipt_path(receipt_file, issue_worktree=worktree)
    if set(closeout_plan) != {"deliver", "cleanup"}:
        raise ValueError("closeout plan requires deliver and cleanup commands")
    if not all(isinstance(closeout_plan[name], list) for name in ("deliver", "cleanup")):
        raise ValueError("closeout plan commands are invalid")
    with _receipt_lock(receipt_path):
        return _execute_stage_locked(
            closeout_plan=closeout_plan,
            contract_sha256=contract_sha256,
            stage=stage,
            worktree=worktree,
            receipt_path=receipt_path,
        )


def _execute_stage_locked(
    *,
    closeout_plan: dict[str, Any],
    contract_sha256: str,
    stage: str,
    worktree: Path,
    receipt_path: Path,
) -> dict[str, Any]:
    """Execute after holding the receipt lock for the whole stage."""

    receipt = _load_receipt(receipt_path, contract_sha256=contract_sha256)
    deliver_records = receipt["stages"]["deliver"]
    deliver_commands = closeout_plan["deliver"]
    if stage == "cleanup" and (
        len(deliver_records) != len(deliver_commands)
        or any(record.get("status") != "succeeded" for record in deliver_records)
    ):
        raise ValueError("cleanup requires every deliver command to have a recorded success")

    commands = closeout_plan[stage]
    records = receipt["stages"][stage]
    if len(records) > len(commands):
        raise ValueError("receipt has more commands than the confirmed plan")
    for index, command in enumerate(commands):
        argv = command["argv"]
        decision = assess_confirmed_closeout_command(
            {"stage": stage, "index": index, "argv": argv}, closeout_plan
        )
        if decision["decision"] != "confirmed_closeout_command":
            raise ValueError("requested command is not the exact confirmed closeout command")
        _validate_command(argv)
        if index < len(records):
            record = records[index]
            if not _record_matches(record, argv):
                raise ValueError("receipt command does not match the confirmed plan")
            if record["status"] == "succeeded":
                continue
            raise ValueError("previous command outcome is not safe to replay; inspect it first")

        record: dict[str, Any] = {"argv": argv, "status": "started"}
        records.append(record)
        _write_receipt(receipt_path, receipt)
        try:
            result = subprocess.run(argv, cwd=worktree, check=False, shell=False)
        except OSError as exc:
            record.update({"status": "failed", "error": str(exc)})
            _write_receipt(receipt_path, receipt)
            raise CloseoutCommandError(f"closeout command could not start: {argv[0]}") from exc
        if result.returncode != 0:
            record.update({"status": "failed", "returncode": result.returncode})
            _write_receipt(receipt_path, receipt)
            raise CloseoutCommandError(f"closeout command exited {result.returncode}: {argv[0]}")
        record.update({"status": "succeeded", "returncode": 0})
        _write_receipt(receipt_path, receipt)
    return receipt


def _confirmed_plan(args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    contract, contract_sha256 = load_contract(
        args.issue_dir, issue_name=args.issue_name, workflow_id=args.workflow_id
    )
    delivery_contract = normalize_delivery_contract(contract["delivery_contract"])
    if delivery_contract["schema_version"] not in {2, 3}:
        raise ValueError("confirmed Driver contract has no argv closeout plan")
    expected_worktree = _confirmed_issue_worktree(contract, project_root=args.project_root)
    if _directory(args.issue_worktree, label="issue worktree") != expected_worktree:
        raise ValueError("issue worktree does not match the confirmed Driver contract")
    return delivery_contract["closeout_plan"], contract_sha256


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--issue-dir", type=Path, required=True)
    parser.add_argument("--issue-name", required=True)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--stage", choices=("deliver", "cleanup"), required=True)
    parser.add_argument("--issue-worktree", type=Path, required=True)
    parser.add_argument("--receipt-file", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        plan, contract_sha256 = _confirmed_plan(args)
        execute_stage(
            closeout_plan=plan,
            contract_sha256=contract_sha256,
            stage=args.stage,
            issue_worktree=args.issue_worktree,
            receipt_file=args.receipt_file,
        )
    except CloseoutCommandError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
