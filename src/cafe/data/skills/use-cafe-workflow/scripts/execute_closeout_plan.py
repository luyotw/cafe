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
from cafe.driver.delivery import (  # noqa: E402
    normalize_delivery_contract,
    validate_closeout_plan_policy,
)
from cafe.utils.issue_config import read_issue_config_strict  # noqa: E402

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
        "schema_version": 3,
        "contract_sha256": contract_sha256,
        "close_recovery": None,
        "stages": {"deliver": [], "cleanup": []},
    }


def _load_receipt(path: Path, *, contract_sha256: str | None) -> dict[str, Any]:
    if not path.exists():
        if contract_sha256 is None:
            raise ValueError("receipt file does not exist")
        return _empty_receipt(contract_sha256)
    if path.is_symlink() or not path.is_file():
        raise ValueError("receipt file is unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("receipt file is unreadable") from exc
    if not isinstance(value, dict):
        raise ValueError("receipt file has an unsupported shape")
    if value.get("schema_version") == 1 and set(value) == {
        "schema_version",
        "contract_sha256",
        "stages",
    }:
        value = {
            **value,
            "schema_version": 2,
            "close_recovery": None,
        }
    if value.get("schema_version") == 2:
        recovery = value.get("close_recovery")
        if recovery is not None:
            recovery = {
                **recovery,
                "close_argv": ["cafe", "close"],
                "pr_auto_create": None,
            }
        value = {**value, "schema_version": 3, "close_recovery": recovery}
    if value.get("schema_version") != 3 or set(value) != {
        "schema_version",
        "contract_sha256",
        "close_recovery",
        "stages",
    }:
        raise ValueError("receipt file has an unsupported shape")
    if contract_sha256 is not None and value["contract_sha256"] != contract_sha256:
        raise ValueError("receipt belongs to a different confirmed contract")
    if not isinstance(value["contract_sha256"], str) or not value["contract_sha256"]:
        raise ValueError("receipt contract identity is invalid")
    recovery = value["close_recovery"]
    if recovery is not None:
        if not isinstance(recovery, dict) or set(recovery) != {
            "archive_path",
            "close_argv",
            "cleanup_index",
            "issue_name",
            "issue_worktree",
            "pr_auto_create",
            "project_root",
            "workflow_id",
        }:
            raise ValueError("receipt close recovery context is invalid")
        if (
            any(
                not isinstance(recovery[name], str) or not recovery[name]
                for name in (
                    "archive_path",
                    "issue_name",
                    "issue_worktree",
                    "project_root",
                    "workflow_id",
                )
            )
            or not isinstance(recovery["cleanup_index"], int)
            or isinstance(recovery["cleanup_index"], bool)
            or recovery["cleanup_index"] < 0
            or not isinstance(recovery["close_argv"], list)
            or not recovery["close_argv"]
            or any(not isinstance(item, str) for item in recovery["close_argv"])
            or not (
                recovery["pr_auto_create"] is None
                or type(recovery["pr_auto_create"]) is bool
            )
        ):
            raise ValueError("receipt close recovery context is invalid")
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


def _issue_archive_path(*, project_root: Path, issue_name: str) -> Path:
    project_key = str(project_root.resolve()).lstrip("/").replace("/", "-")
    archive_root = Path.home() / ".cafe" / "projects" / project_key / "archived"
    archive_path = (archive_root / issue_name).resolve()
    if not _is_within(archive_path, archive_root.resolve()):
        raise ValueError("issue archive path escapes the project archive root")
    return archive_path


def _close_recovery_context(
    *,
    issue_name: str,
    workflow_id: str,
    project_root: Path,
    issue_worktree: Path,
    cleanup_index: int,
    close_argv: list[str] | None = None,
    pr_auto_create: bool | None = None,
) -> dict[str, Any]:
    if not issue_name or not workflow_id:
        raise ValueError("cafe close recovery requires issue and workflow identity")
    root = _directory(project_root, label="confirmed project root")
    worktree = issue_worktree.resolve()
    return {
        "archive_path": str(_issue_archive_path(project_root=root, issue_name=issue_name)),
        "close_argv": list(close_argv or ["cafe", "close"]),
        "cleanup_index": cleanup_index,
        "issue_name": issue_name,
        "issue_worktree": str(worktree),
        "pr_auto_create": pr_auto_create,
        "project_root": str(root),
        "workflow_id": workflow_id,
    }


def _local_branch_absent(*, project_root: Path, issue_name: str) -> bool:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(project_root),
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{issue_name}",
        ],
        check=False,
        shell=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode not in {0, 1}:
        raise ValueError("could not verify the local feature branch state")
    return result.returncode == 1


def _close_postcondition_errors(recovery: dict[str, Any], *, contract_sha256: str) -> list[str]:
    errors: list[str] = []
    worktree = Path(recovery["issue_worktree"])
    archive = Path(recovery["archive_path"])
    project_root = Path(recovery["project_root"])
    if worktree.exists():
        errors.append("issue worktree still exists")
    try:
        _, archived_sha256 = load_contract(
            archive,
            issue_name=recovery["issue_name"],
            workflow_id=recovery["workflow_id"],
        )
    except ValueError:
        errors.append("matching archived Driver contract is unavailable")
    else:
        if archived_sha256 != contract_sha256:
            errors.append("archived Driver contract does not match the confirmed contract")
    try:
        branch_absent = _local_branch_absent(
            project_root=project_root, issue_name=recovery["issue_name"]
        )
    except ValueError as exc:
        errors.append(str(exc))
    else:
        if not branch_absent:
            errors.append("local feature branch still exists")
    return errors


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
    if executable != "cafe" or len(argv) <= 1:
        return
    if argv[1] == "deliver":
        raise ValueError("closeout plans must not recursively invoke cafe deliver")
    if argv[1] == "close" and argv[0] != "cafe":
        raise ValueError("cafe close must use the literal `cafe` executable")


def _validate_stage_commands(
    *,
    stage: str,
    commands: list[Any],
    closeout_plan: dict[str, Any],
    pr_auto_create: bool | None,
) -> list[list[str]]:
    """Validate the complete stage before any command can have side effects."""
    validate_closeout_plan_policy(closeout_plan, pr_auto_create=pr_auto_create)
    validated: list[list[str]] = []
    for index, command in enumerate(commands):
        if not isinstance(command, dict) or set(command) != {"argv"}:
            raise ValueError("closeout plan command is invalid")
        argv = command["argv"]
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(item, str) for item in argv)
            or not argv[0]
        ):
            raise ValueError("closeout plan command argv is invalid")
        decision = assess_confirmed_closeout_command(
            {"stage": stage, "index": index, "argv": argv}, closeout_plan
        )
        if decision["decision"] != "confirmed_closeout_command":
            raise ValueError("requested command is not the exact confirmed closeout command")
        _validate_command(argv)
        validated.append(argv)
    return validated


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
    issue_name: str | None = None,
    workflow_id: str | None = None,
    project_root: Path | None = None,
    pr_auto_create: bool | None = None,
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
            issue_name=issue_name,
            workflow_id=workflow_id,
            project_root=project_root,
            pr_auto_create=pr_auto_create,
        )


def _execute_stage_locked(
    *,
    closeout_plan: dict[str, Any],
    contract_sha256: str,
    stage: str,
    worktree: Path,
    receipt_path: Path,
    issue_name: str | None,
    workflow_id: str | None,
    project_root: Path | None,
    pr_auto_create: bool | None,
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
    command_argv = _validate_stage_commands(
        stage=stage,
        commands=commands,
        closeout_plan=closeout_plan,
        pr_auto_create=pr_auto_create,
    )
    close_indexes = [
        index
        for index, argv in enumerate(command_argv)
        if Path(argv[0]).name.lower() == "cafe" and len(argv) > 1 and argv[1] == "close"
    ]
    close_recovery = None
    if close_indexes:
        if issue_name is None or workflow_id is None or project_root is None:
            raise ValueError("cafe close requires durable recovery identity")
        close_recovery = _close_recovery_context(
            issue_name=issue_name,
            workflow_id=workflow_id,
            project_root=project_root,
            issue_worktree=worktree,
            cleanup_index=close_indexes[0],
            close_argv=command_argv[close_indexes[0]],
            pr_auto_create=pr_auto_create,
        )
        existing_recovery = receipt["close_recovery"]
        if existing_recovery is not None and existing_recovery != close_recovery:
            raise ValueError("receipt cafe close recovery context does not match this execution")
        receipt["close_recovery"] = close_recovery
    records = receipt["stages"][stage]
    if len(records) > len(commands):
        raise ValueError("receipt has more commands than the confirmed plan")
    for index, record in enumerate(records):
        if not _record_matches(record, command_argv[index]):
            raise ValueError("receipt command does not match the confirmed plan")
        if record["status"] != "succeeded":
            raise ValueError("previous command outcome is not safe to replay; inspect it first")

    for index in range(len(records), len(commands)):
        argv = command_argv[index]
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
        if close_recovery is not None and index == close_recovery["cleanup_index"]:
            errors = _close_postcondition_errors(close_recovery, contract_sha256=contract_sha256)
            if errors:
                detail = "; ".join(errors)
                record.update({"status": "failed", "returncode": 0, "error": detail})
                _write_receipt(receipt_path, receipt)
                raise CloseoutCommandError(f"cafe close postconditions failed: {detail}")
        record.update({"status": "succeeded", "returncode": 0})
        _write_receipt(receipt_path, receipt)
    return receipt


def _reconcile_lifecycle_close(args: argparse.Namespace) -> dict[str, Any]:
    """Finish a recorded cafe close after its worktree and live contract are gone."""
    if args.stage != "cleanup":
        raise ValueError("cafe close recovery is available only for cleanup")
    project_root = _directory(args.project_root, label="confirmed project root")
    issue_worktree = args.issue_worktree.resolve()
    receipt_path = _receipt_path(args.receipt_file, issue_worktree=issue_worktree)
    with _receipt_lock(receipt_path):
        receipt = _load_receipt(receipt_path, contract_sha256=None)
        recovery = receipt["close_recovery"]
        if recovery is None:
            raise ValueError("receipt has no cafe close recovery context")
        expected = _close_recovery_context(
            issue_name=args.issue_name,
            workflow_id=args.workflow_id,
            project_root=project_root,
            issue_worktree=issue_worktree,
            cleanup_index=recovery["cleanup_index"],
            close_argv=recovery["close_argv"],
            pr_auto_create=recovery["pr_auto_create"],
        )
        if recovery != expected:
            raise ValueError("receipt cafe close recovery context does not match this request")
        cleanup_records = receipt["stages"]["cleanup"]
        index = recovery["cleanup_index"]
        if index != len(cleanup_records) - 1:
            raise ValueError("receipt cafe close record is not the final cleanup command")
        if any(record.get("status") != "succeeded" for record in cleanup_records[:index]):
            raise ValueError("receipt has incomplete cleanup commands before cafe close")
        record = cleanup_records[index]
        if record.get("argv") != recovery["close_argv"]:
            raise ValueError("receipt cafe close command is invalid")
        validate_closeout_plan_policy(
            {"deliver": [], "cleanup": [{"argv": record["argv"]}]},
            pr_auto_create=recovery["pr_auto_create"],
        )
        if "--squash" in record["argv"] and recovery["pr_auto_create"] is not False:
            raise ValueError("receipt cafe close mode is invalid")
        if record.get("status") == "succeeded":
            return receipt
        if record.get("status") != "started":
            raise ValueError("failed cafe close requires manual inspection")
        errors = _close_postcondition_errors(recovery, contract_sha256=receipt["contract_sha256"])
        if errors:
            raise ValueError("cafe close recovery is incomplete: " + "; ".join(errors))
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


def _confirmed_pr_auto_create(issue_dir: Path) -> bool:
    config = read_issue_config_strict(issue_dir / "issue.yaml")
    pr = config.get("pr")
    if pr is None:
        return False
    if not isinstance(pr, dict):
        raise ValueError("issue.yaml requires a Boolean pr.auto_create for closeout")
    value = pr.get("auto_create", False)
    if type(value) is not bool:
        raise ValueError("issue.yaml requires a Boolean pr.auto_create for closeout")
    return value


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
        try:
            plan, contract_sha256 = _confirmed_plan(args)
            pr_auto_create = _confirmed_pr_auto_create(args.issue_dir)
        except ValueError:
            if args.stage != "cleanup" or (
                args.issue_dir.is_dir() and args.issue_worktree.is_dir()
            ):
                raise
            _reconcile_lifecycle_close(args)
            return 0
        execute_stage(
            closeout_plan=plan,
            contract_sha256=contract_sha256,
            stage=args.stage,
            issue_worktree=args.issue_worktree,
            receipt_file=args.receipt_file,
            issue_name=args.issue_name,
            workflow_id=args.workflow_id,
            project_root=args.project_root,
            pr_auto_create=pr_auto_create,
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
