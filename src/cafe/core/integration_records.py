"""Atomic integration snapshots; HumanTask remains the task/result authority."""

from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.packet_io import atomic_write_bytes, canonical_json


def now() -> str:
    return datetime.now().astimezone().isoformat()


class IntegrationRecordStore:
    """Serialize cross-file associations after acquiring the HumanTask lock.

    Use the existing reentrant/process lock on every mutation, with a consistent
    HumanTask-before-integration order. Inspection happens outside this lock.
    """

    def __init__(self, issue_dir: Path, workflow_id: str):
        self.issue_dir = Path(issue_dir)
        self.workflow_id = workflow_id
        self.file_path = self.issue_dir / "integration.json"
        self.tasks = HumanTaskRecordStore(self.issue_dir)

    def read(self) -> dict[str, Any]:
        if not self.file_path.exists():
            return {
                "schema_version": 1,
                "workflow_id": self.workflow_id,
                "current_revision": None,
                "selections": [],
                "reviews": {},
                "reports": [],
                "attempts": [],
                "completion": None,
            }
        try:
            record = json.loads(self.file_path.read_text(encoding="utf-8"))
            if (
                record["schema_version"] != 1
                or record["workflow_id"] != self.workflow_id
                or not all(
                    isinstance(record[k], list) for k in ("selections", "reports", "attempts")
                )
                or not isinstance(record["reviews"], dict)
            ):
                raise ValueError("foreign or unsupported integration record")
            revision = record["current_revision"]
            if (
                revision is not None
                and len([s for s in record["selections"] if s["revision"] == revision]) != 1
            ):
                raise ValueError("invalid current integration selection")
            return record
        except (KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
            raise ValueError("integration records unavailable or corrupt") from exc

    @contextmanager
    def transaction(self) -> Iterator[dict[str, Any]]:
        with self.tasks.transaction():
            record = self.read()
            yield record
            atomic_write_bytes(self.file_path, canonical_json(record))

    @staticmethod
    def current(record: dict[str, Any]) -> dict[str, Any] | None:
        return next(
            (s for s in record["selections"] if s["revision"] == record["current_revision"]), None
        )

    def _selection(
        self, record: dict[str, Any], revision: str, *, confirmed: bool = False
    ) -> dict[str, Any]:
        selected = self.current(record)
        if selected is None or selected["revision"] != revision:
            raise ValueError("stale integration selection")
        if confirmed and selected.get("confirmation") is None:
            raise ValueError("integration selection is not human confirmed")
        return selected

    def stage_review(self, task_id: str, snapshot: dict[str, Any]) -> None:
        with self.transaction() as record:
            prior = record["reviews"].get(task_id)
            if prior is not None and prior != snapshot:
                raise ValueError("review source cannot change under an existing task")
            record["reviews"][task_id] = copy.deepcopy(snapshot)

    def propose(self, selection: dict[str, Any], review: dict[str, Any]) -> str:
        with self.transaction() as record:
            prior = self.current(record)
            if prior and prior["selection"] == selection and prior["review"] == review:
                return prior["revision"]
            revision = str(uuid4())
            record["selections"].append(
                {
                    "revision": revision,
                    "selection": copy.deepcopy(selection),
                    "review": copy.deepcopy(review),
                    "confirmation": None,
                    "tasks": {},
                    "created_at": now(),
                }
            )
            record["current_revision"] = revision
            record["completion"] = None
            return revision

    def associate_task(self, revision: str, task_id: str, kind: str) -> None:
        if kind not in {"confirmation", "action"}:
            raise ValueError("unknown integration task kind")
        with self.transaction() as record:
            selected = self._selection(record, revision, confirmed=kind == "action")
            prior = selected["tasks"].get(kind)
            if prior is not None and prior != task_id:
                raise ValueError("integration task association already exists")
            selected["tasks"][kind] = task_id

    def confirm(self, revision: str, task_id: str, result_id: str) -> None:
        with self.transaction() as record:
            selected = self._selection(record, revision)
            if selected["tasks"].get("confirmation") != task_id:
                raise ValueError("confirmation task does not match selection")
            confirmation = {"task_id": task_id, "result_id": result_id}
            if selected["confirmation"] not in (None, confirmation):
                raise ValueError("selection already confirmed by a different result")
            selected["confirmation"] = confirmation

    def report(self, revision: str, task_id: str, result_id: str, outcome: str) -> None:
        if outcome not in {"performed", "already_performed", "blocked"}:
            raise ValueError("unknown human integration outcome")
        with self.transaction() as record:
            selected = self._selection(record, revision, confirmed=True)
            if selected["tasks"].get("action") != task_id:
                raise ValueError("report task does not match selection")
            report = {
                "revision": revision,
                "task_id": task_id,
                "result_id": result_id,
                "outcome": outcome,
            }
            prior = next((r for r in record["reports"] if r["result_id"] == result_id), None)
            if prior is not None:
                if prior != report:
                    raise ValueError("duplicate result has different correlation")
                return
            record["reports"].append(report)
            record["completion"] = None

    def record_attempt(self, revision: str, attempt: dict[str, Any]) -> dict[str, Any]:
        with self.transaction() as record:
            selected = self._selection(record, revision, confirmed=True)
            reports = [r for r in record["reports"] if r["revision"] == revision]
            stored = dict(
                attempt,
                id=str(uuid4()),
                revision=revision,
                review=copy.deepcopy(selected["review"]),
                verified_at=now(),
                report_result_id=reports[-1]["result_id"] if reports else None,
            )
            if (
                not isinstance(stored.get("success"), bool)
                or not stored.get("reason")
                or not isinstance(stored.get("observed"), dict)
            ):
                raise ValueError("verification requires observed facts and an explanation")
            record["attempts"].append(stored)
            record["completion"] = None
            return stored

    def qualifies(self, record: dict[str, Any] | None = None) -> bool:
        record = self.read() if record is None else record
        selected = self.current(record)
        if selected is None or selected.get("confirmation") is None:
            return False
        attempts = [a for a in record["attempts"] if a.get("revision") == selected["revision"]]
        if not attempts:
            return False
        last = attempts[-1]
        return (
            last.get("success") is True
            and last.get("review") == selected["review"]
            and bool(last.get("verified_at"))
            and bool(last.get("id"))
        )

    def mark_completion(self) -> None:
        with self.transaction() as record:
            if not self.qualifies(record):
                raise ValueError("durable current integration proof required")
            record["completion"] = {
                "revision": record["current_revision"],
                "attempt_id": record["attempts"][-1]["id"],
                "completed_at": now(),
            }
