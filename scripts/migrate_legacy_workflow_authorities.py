#!/usr/bin/env python3
"""Migrate one verified legacy user-wait workflow to canonical authorities.

This is an explicit recovery tool, not a runtime fallback.  It only accepts a
legacy blackboard that still contains its complete event and capability-receipt
history, and requires the caller to repeat the workflow UUID on the command
line.  The script is safe to rerun after an interrupted publish.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from cafe.core.audit_events import AuditEventStore
from cafe.core.packet_io import atomic_write_bytes, canonical_json


def _read_json(path: Path) -> Any:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"required regular file is absent: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {path}") from exc


def _legacy_event_records(events: Any, workflow_id: str) -> list[dict[str, Any]]:
    if not isinstance(events, list) or not events:
        raise ValueError("legacy blackboard must contain a non-empty events list")
    records: list[dict[str, Any]] = []
    for sequence, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            raise ValueError(f"legacy event {sequence} is not an object")
        required = ("timestamp", "step", "event_type", "message", "data")
        if any(not isinstance(event.get(key), str) or not event[key] for key in required[:4]):
            raise ValueError(f"legacy event {sequence} has invalid identity fields")
        if not isinstance(event["data"], dict):
            raise ValueError(f"legacy event {sequence} data is not an object")
        data = event["data"]
        event_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"cafe-legacy-audit:{workflow_id}:{sequence}:"
            + json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        ))
        record: dict[str, Any] = {
            "workflow_id": workflow_id,
            "event_id": event_id,
            "sequence": sequence,
            "timestamp": event["timestamp"],
            "step": event["step"],
            "event_type": event["event_type"],
            "message": event["message"],
            "data": data,
            "patch": {},
        }
        if event["event_type"] == "workflow_event_callback_enqueued":
            callback_id = data.get("event_id")
            occurred_at = data.get("occurred_at")
            if (
                not isinstance(callback_id, str) or not callback_id
                or occurred_at != event["timestamp"]
                or data.get("workflow_id") != workflow_id
            ):
                raise ValueError(f"legacy callback event {sequence} has invalid identity")
            # The legacy workflow advanced after every callback in this history;
            # marking them closed prevents their deliberate replay on recovery.
            record["event_id"] = callback_id
            record["delivery"] = "closed"
        records.append(record)
    return records


def _legacy_receipts(receipts: Any, workflow_id: str) -> list[dict[str, Any]]:
    if not isinstance(receipts, list):
        raise ValueError("legacy capability_receipts must be a list")
    copied: list[dict[str, Any]] = []
    for index, receipt in enumerate(receipts, start=1):
        if not isinstance(receipt, dict) or receipt.get("workflow_id") != workflow_id:
            raise ValueError(f"legacy capability receipt {index} has a mismatched workflow")
        copied.append(dict(receipt))
    return copied


def _validate_human_tasks(issue_dir: Path, workflow_id: str) -> None:
    records = _read_json(issue_dir / "human_tasks.json")
    tasks = records.get("tasks") if isinstance(records, dict) else None
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("human task authority is absent or empty")
    if any(not isinstance(task, dict) or task.get("workflow_id") != workflow_id for task in tasks):
        raise ValueError("human task workflow identity is inconsistent")
    if not any(task.get("status") == "pending" for task in tasks):
        raise ValueError("legacy authority migration requires a pending human task")


def _expected_receipt_document(workflow_id: str, receipts: list[dict[str, Any]]) -> dict[str, Any]:
    return {"version": 1, "workflow_id": workflow_id, "receipts": receipts}


def _validate_existing_authorities(
    issue_dir: Path,
    workflow_id: str,
    expected_records: list[dict[str, Any]],
    expected_receipts: dict[str, Any],
) -> tuple[bool, bool]:
    audit_root = issue_dir / "audit_events"
    receipts_path = issue_dir / "capability_receipts.json"
    if not audit_root.exists() and not receipts_path.exists():
        return False, False
    if audit_root.is_symlink() or receipts_path.is_symlink():
        raise ValueError("existing authority path is unsafe")
    has_audit = audit_root.exists()
    has_receipts = receipts_path.exists()
    if has_audit:
        if not audit_root.is_dir():
            raise ValueError("existing audit authority is unsafe")
        actual_records = list(AuditEventStore(issue_dir).iter_records(workflow_id))
        if actual_records != expected_records:
            raise ValueError("existing audit authority does not match the verified legacy history")
    if has_receipts:
        if not receipts_path.is_file() or _read_json(receipts_path) != expected_receipts:
            raise ValueError("existing receipt authority does not match the verified legacy history")
    return has_audit, has_receipts


def _write_migrated_blackboard(path: Path, raw: dict[str, Any], high_water: int) -> None:
    migrated = dict(raw)
    migrated.pop("events", None)
    migrated.pop("capability_receipts", None)
    migrated["applied_event_sequence"] = high_water
    atomic_write_bytes(path, json.dumps(migrated, ensure_ascii=False, indent=2).encode("utf-8"))


@contextmanager
def _migration_lock(issue_dir: Path) -> Iterator[None]:
    lock_path = issue_dir / ".authority-migration.lock"
    if lock_path.is_symlink():
        raise ValueError("migration lock path is unsafe")
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except ImportError:  # pragma: no cover - supported CAFE hosts are POSIX.
            raise RuntimeError("authority migration requires POSIX file locking")
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    lock_path.unlink(missing_ok=True)


def migrate(issue_dir: Path, workflow_id: str, *, apply: bool) -> str:
    issue_dir = issue_dir.resolve()
    if issue_dir.is_symlink() or not issue_dir.is_dir():
        raise ValueError("issue directory must be an existing regular directory")
    with _migration_lock(issue_dir):
        board_path = issue_dir / "blackboard.json"
        raw = _read_json(board_path)
        if not isinstance(raw, dict) or raw.get("workflow_id") != workflow_id:
            raise ValueError("blackboard workflow_id does not match --workflow-id")
        _validate_human_tasks(issue_dir, workflow_id)
        if "events" not in raw and "capability_receipts" not in raw:
            audit = AuditEventStore(issue_dir)
            high_water = audit.high_water(workflow_id)
            receipts = _read_json(issue_dir / "capability_receipts.json")
            if (
                not isinstance(receipts, dict)
                or receipts.get("version") != 1
                or receipts.get("workflow_id") != workflow_id
                or not isinstance(receipts.get("receipts"), list)
                or raw.get("applied_event_sequence") != high_water
            ):
                raise ValueError("existing authorities are incomplete or inconsistent")
            return "legacy workflow authorities already migrated"
        if "events" not in raw or "capability_receipts" not in raw:
            raise ValueError("legacy blackboard authority history is incomplete")
        records = _legacy_event_records(raw.get("events"), workflow_id)
        receipts = _expected_receipt_document(
            workflow_id, _legacy_receipts(raw.get("capability_receipts"), workflow_id)
        )
        has_audit, has_receipts = _validate_existing_authorities(
            issue_dir, workflow_id, records, receipts
        )
        if has_audit and has_receipts:
            if raw.get("applied_event_sequence") != len(records) or "events" in raw:
                if not apply:
                    return "validated existing authorities; blackboard finalization required"
                _write_migrated_blackboard(board_path, raw, len(records))
                return "finalized existing legacy workflow authorities"
            return "legacy workflow authorities already migrated"
        if not apply:
            if has_audit or has_receipts:
                return "validated partial authority publish; recovery finalization required"
            return f"validated legacy history: {len(records)} events, {len(receipts['receipts'])} receipts"

        staging_root = Path(tempfile.mkdtemp(prefix=".authority-migration-", dir=issue_dir))
        try:
            staged_audit = AuditEventStore(staging_root)
            staged_audit.initialize(workflow_id)
            for record in records:
                if staged_audit.reserve(workflow_id) != record["sequence"]:
                    raise RuntimeError("staged audit sequence is inconsistent")
                staged_record = dict(record)
                close_after_commit = staged_record.pop("delivery", None) == "closed"
                staged_audit.commit(workflow_id, staged_record)
                if close_after_commit:
                    staged_audit.close(workflow_id, record["sequence"])
            atomic_write_bytes(staging_root / "capability_receipts.json", canonical_json(receipts))
            if not has_audit:
                os.replace(staging_root / "audit_events", issue_dir / "audit_events")
            if not has_receipts:
                os.replace(
                    staging_root / "capability_receipts.json", issue_dir / "capability_receipts.json"
                )
            _write_migrated_blackboard(board_path, raw, len(records))
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)
        return f"migrated legacy workflow authorities: {len(records)} events, {len(receipts['receipts'])} receipts"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue-dir", required=True, type=Path)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--apply", action="store_true", help="write verified authorities")
    args = parser.parse_args()
    try:
        print(migrate(args.issue_dir, args.workflow_id, apply=args.apply))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
