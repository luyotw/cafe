#!/usr/bin/env python3
"""Execute one confirmed closeout argv with durable, non-replayable evidence.

This records outcomes, not authority. The Driver must establish action authority,
target/effect checks, worker quiescence, and terminal cleanup choice first.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import stat
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from cafe.core.packet_io import atomic_write_bytes, canonical_json
from cafe.driver._store import load_contract
from cafe.driver.delivery import (
    MAX_CLOSEOUT_EVIDENCE_BYTES,
    closeout_evidence_record,
)

MAX_EVIDENCE_BYTES = MAX_CLOSEOUT_EVIDENCE_BYTES
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
STAGES = ("deliver", "cleanup")
STATUSES = {"not_started", "unknown", "succeeded", "failed"}


def _common_dir(checkout: Path) -> Path:
    result = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        text=True,
        capture_output=True,
        check=True,
    )
    return Path(result.stdout.strip()).resolve(strict=True)


def _paths(project_root: Path, issue_name: str, workflow_id: str) -> tuple[Path, Path]:
    if not IDENTIFIER.fullmatch(issue_name) or not IDENTIFIER.fullmatch(workflow_id):
        raise ValueError("issue and workflow identifiers must be simple path components")
    directory = _common_dir(project_root) / "cafe" / "closeout" / issue_name
    return directory / f"{workflow_id}.json", directory / f"{workflow_id}.lock"


def _safe_directory(directory: Path) -> None:
    for parent in (directory.parent.parent, directory.parent, directory):
        if parent.is_symlink():
            raise ValueError("closeout evidence path must not traverse a symlink")
        parent.mkdir(mode=0o700, exist_ok=True)
        if not parent.is_dir():
            raise ValueError("closeout evidence directory is unsafe")


@contextmanager
def _locked(lock_path: Path) -> Iterator[None]:
    _safe_directory(lock_path.parent)
    if lock_path.is_symlink():
        raise ValueError("closeout lock is unsafe")
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _read_locked(lock_path: Path) -> Iterator[None]:
    if (
        any(
            path.is_symlink()
            for path in (
                lock_path.parent.parent.parent,
                lock_path.parent.parent,
                lock_path.parent,
                lock_path,
            )
        )
        or not lock_path.is_file()
    ):
        raise ValueError("closeout evidence lock is missing or unsafe")
    with lock_path.open("rb") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read(path: Path, *, issue_name: str, workflow_id: str) -> dict[str, Any] | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_EVIDENCE_BYTES:
        raise ValueError("closeout evidence is unsafe or oversized")
    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_EVIDENCE_BYTES + 1)
        if len(content) > MAX_EVIDENCE_BYTES:
            raise ValueError("closeout evidence is oversized")
        record = json.loads(content)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("closeout evidence is unreadable") from exc
    if (
        not isinstance(record, dict)
        or set(record)
        != {"version", "issue_name", "workflow_id", "contract_sha256", "worktree", "commands"}
        or record["version"] != 1
        or record["issue_name"] != issue_name
        or record["workflow_id"] != workflow_id
    ):
        raise ValueError("closeout evidence identity is invalid")
    if not isinstance(record["contract_sha256"], str) or len(record["contract_sha256"]) != 64:
        raise ValueError("closeout contract binding is invalid")
    if not isinstance(record["worktree"], str) or not Path(record["worktree"]).is_absolute():
        raise ValueError("closeout worktree binding is invalid")
    commands = record["commands"]
    if not isinstance(commands, dict) or set(commands) != set(STAGES):
        raise ValueError("closeout command evidence is invalid")
    for stage in STAGES:
        if not isinstance(commands[stage], list):
            raise ValueError("closeout command evidence is invalid")
        for command in commands[stage]:
            if not isinstance(command, dict) or set(command) != {"argv", "status", "returncode"}:
                raise ValueError("closeout command evidence is invalid")
            if (
                not isinstance(command["argv"], list)
                or not command["argv"]
                or any(not isinstance(arg, str) for arg in command["argv"])
                or command["status"] not in STATUSES
            ):
                raise ValueError("closeout command evidence is invalid")
            if command["returncode"] is not None and type(command["returncode"]) is not int:
                raise ValueError("closeout return code is invalid")
    return record


def _write(path: Path, record: dict[str, Any]) -> None:
    content = canonical_json(record)
    if len(content) > MAX_EVIDENCE_BYTES:
        raise ValueError("closeout evidence is oversized")
    atomic_write_bytes(path, content)
    os.chmod(path, 0o600)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _confirmed(
    issue_dir: Path, issue_name: str, workflow_id: str, common_dir: Path
) -> tuple[dict[str, Any], str, Path]:
    issue = issue_dir.resolve(strict=True)
    worktree = issue.parent.parent.parent
    if issue != worktree / ".cafe" / "issues" / issue_name:
        raise ValueError("issue directory does not match the named worktree issue")
    if _common_dir(worktree) != common_dir:
        raise ValueError("issue worktree belongs to another repository")
    if len(os.fsencode(str(worktree))) > 4096:
        raise ValueError("issue worktree path exceeds the bounded evidence projection")
    contract, digest = load_contract(issue, issue_name=issue_name, workflow_id=workflow_id)
    return contract["delivery_contract"]["closeout_plan"], digest, worktree


def _initialize(
    path: Path,
    prior: dict[str, Any] | None,
    *,
    issue_dir: Path,
    issue_name: str,
    workflow_id: str,
    common_dir: Path,
) -> dict[str, Any]:
    plan, digest, worktree = _confirmed(issue_dir, issue_name, workflow_id, common_dir)
    record = closeout_evidence_record(
        plan,
        issue_name=issue_name,
        workflow_id=workflow_id,
        contract_sha256=digest,
        worktree=str(worktree),
    )
    if prior is not None:
        if (
            prior["contract_sha256"] != digest
            or prior["worktree"] != str(worktree)
            or {stage: [item["argv"] for item in prior["commands"][stage]] for stage in STAGES}
            != {stage: [item["argv"] for item in record["commands"][stage]] for stage in STAGES}
        ):
            raise ValueError("closeout evidence differs from the confirmed contract")
        return prior
    _write(path, record)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--issue-dir", type=Path)
    parser.add_argument("--issue-name", required=True)
    parser.add_argument("--workflow-id", required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--initialize", action="store_true")
    action.add_argument("--inspect", action="store_true")
    action.add_argument("--execute", action="store_true")
    parser.add_argument("--stage", choices=STAGES)
    parser.add_argument("--index", type=int)
    args = parser.parse_args()
    try:
        path, lock_path = _paths(args.project_root, args.issue_name, args.workflow_id)
        if args.inspect:
            with _read_locked(lock_path):
                record = _read(path, issue_name=args.issue_name, workflow_id=args.workflow_id)
                if record is None:
                    raise ValueError(
                        "closeout evidence is missing; inspect external state before any action"
                    )
                print(json.dumps(record))
                return 0
        if (
            args.execute
            and _read(path, issue_name=args.issue_name, workflow_id=args.workflow_id) is None
        ):
            raise ValueError("closeout evidence is missing; initialize before the first command")
        with _locked(lock_path):
            record = _read(path, issue_name=args.issue_name, workflow_id=args.workflow_id)
            if args.issue_dir is None:
                raise ValueError("initialization and execution require --issue-dir")
            if args.execute and record is None:
                raise ValueError(
                    "closeout evidence is missing; initialize before the first command"
                )
            record = _initialize(
                path,
                record,
                issue_dir=args.issue_dir,
                issue_name=args.issue_name,
                workflow_id=args.workflow_id,
                common_dir=_common_dir(args.project_root),
            )
            if args.initialize:
                print(json.dumps(record))
                return 0
            if args.stage is None or args.index is None or args.index < 0:
                raise ValueError("execution requires a stage and nonnegative index")
            commands = record["commands"][args.stage]
            if args.index >= len(commands):
                raise ValueError("command index is outside the confirmed plan")
            if any(command["status"] != "succeeded" for command in commands[: args.index]):
                raise ValueError("prior commands in this stage have not succeeded")
            command = commands[args.index]
            if command["status"] != "not_started":
                raise ValueError("closeout command already attempted; never retry or replay")
            command["status"] = "unknown"
            _write(path, record)
            try:
                result = subprocess.run(command["argv"], cwd=record["worktree"], check=False)
            except OSError as exc:
                raise ValueError("closeout command outcome is unknown; inspect read-only") from exc
            command["status"] = "succeeded" if result.returncode == 0 else "failed"
            command["returncode"] = result.returncode
            _write(path, record)
            print(json.dumps(record))
            return 0 if result.returncode == 0 else 1
    except (ValueError, subprocess.CalledProcessError, FileNotFoundError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
