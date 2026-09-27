"""Workflow-bound, sequence-addressed canonical audit records."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterator

from cafe.core.packet_io import atomic_write_bytes, canonical_json

MAX_EVENT_BYTES = 256 * 1024
MAX_SEQUENCE = 999_999_999
_EVENT_TYPE_PREFIX = re.compile(rb'(?m)^  "event_type":\s*"([^"\\]+)"')


class AuditEventStore:
    def __init__(self, issue_dir: Path):
        self.root = issue_dir / "audit_events"
        self.binding = self.root / "workflow.json"
        self.events_dir = self.root / "events"

    def initialize(self, workflow_id: str) -> None:
        if self.root.is_symlink():
            raise ValueError("audit authority path is unsafe")
        if self.binding.exists():
            self.high_water(workflow_id)
            return
        if self.root.exists() and any(self.root.iterdir()):
            raise ValueError("audit authority is incomplete")
        atomic_write_bytes(
            self.binding,
            canonical_json({"version": 1, "workflow_id": workflow_id, "next_sequence": 1}),
        )

    def high_water(self, workflow_id: str) -> int:
        if self.root.is_symlink() or self.binding.is_symlink() or not self.binding.is_file():
            raise ValueError("audit authority is absent")
        if self.binding.stat().st_size > 4096:
            raise ValueError("audit authority is oversized")
        raw = json.loads(self.binding.read_text(encoding="utf-8"))
        value = raw.get("next_sequence") if isinstance(raw, dict) else None
        if (
            raw.get("version") != 1
            or raw.get("workflow_id") != workflow_id
            or not isinstance(value, int)
            or isinstance(value, bool)
            or value < 1
            or value > MAX_SEQUENCE + 1
        ):
            raise ValueError("audit authority identity is invalid")
        return value - 1

    def _path(self, sequence: int) -> Path:
        if (
            not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or not 1 <= sequence <= MAX_SEQUENCE
        ):
            raise ValueError("audit sequence is invalid")
        if self.root.is_symlink() or self.events_dir.is_symlink():
            raise ValueError("audit authority path is unsafe")
        return self.events_dir / f"{sequence:010d}.json"

    def reserve(self, workflow_id: str) -> int:
        sequence = self.high_water(workflow_id) + 1
        if sequence > MAX_SEQUENCE:
            raise ValueError("audit sequence exhausted")
        atomic_write_bytes(
            self.binding,
            canonical_json(
                {"version": 1, "workflow_id": workflow_id, "next_sequence": sequence + 1}
            ),
        )
        return sequence

    def read(
        self, workflow_id: str, sequence: int, *, bounded: bool = False
    ) -> dict[str, Any] | None:
        self.high_water(workflow_id)
        path = self._path(sequence)
        if path.is_symlink():
            raise ValueError("audit event is symlinked")
        if not path.exists():
            return None
        if not path.is_file() or (bounded and path.stat().st_size > MAX_EVENT_BYTES):
            raise ValueError("audit event is unsafe or oversized")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(raw, dict)
            or raw.get("workflow_id") != workflow_id
            or raw.get("sequence") != sequence
            or not isinstance(raw.get("event_id"), str)
            or not raw["event_id"]
            or not all(
                isinstance(raw.get(k), str) for k in ("timestamp", "step", "event_type", "message")
            )
            or not isinstance(raw.get("data"), dict)
            or not isinstance(raw.get("patch", {}), dict)
            or raw.get("delivery", "open") not in {"open", "closed"}
        ):
            raise ValueError("audit event is malformed or mismatched")
        return raw

    def commit(self, workflow_id: str, record: dict[str, Any]) -> None:
        sequence = record["sequence"]
        path = self._path(sequence)
        if path.exists() or path.is_symlink():
            raise ValueError("audit sequence already committed")
        if record.get("workflow_id") != workflow_id or sequence > self.high_water(workflow_id):
            raise ValueError("audit event identity is invalid")
        if record["event_type"] == "workflow_event_callback_enqueued":
            if record.get("delivery", "open") != "open":
                raise ValueError("new callback delivery marker is invalid")
            record["delivery"] = "open"
        content = self._event_bytes(record)
        if (
            record["event_type"] == "workflow_event_callback_enqueued"
            and len(content) > MAX_EVENT_BYTES - 2
        ):
            raise ValueError("audit event exceeds bounded record size")
        atomic_write_bytes(path, content)

    def iter_records(self, workflow_id: str) -> Iterator[dict[str, Any]]:
        for sequence in range(1, self.high_water(workflow_id) + 1):
            record = self.read(workflow_id, sequence)
            if record is not None:
                yield record

    def latest_record(
        self, workflow_id: str, event_types: set[str], *, step: str | None = None
    ) -> dict[str, Any] | None:
        """Find the newest relevant event without hydrating unrelated audit bodies."""
        wanted = {kind.encode("ascii") for kind in event_types}
        for sequence in range(self.high_water(workflow_id), 0, -1):
            path = self._path(sequence)
            if path.is_symlink():
                raise ValueError("audit event is symlinked")
            if not path.exists():
                continue
            if not path.is_file():
                raise ValueError("audit event is unsafe")
            with path.open("rb") as handle:
                prefix = handle.read(4096)
            match = _EVENT_TYPE_PREFIX.search(prefix)
            if match is None:
                raise ValueError("audit event has no bounded type prefix")
            if match.group(1) in wanted:
                record = self.read(workflow_id, sequence)
                if record is not None and (step is None or record["step"] == step):
                    return record
        return None

    @staticmethod
    def _event_bytes(record: dict[str, Any]) -> bytes:
        return (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    def iter_open_callbacks(self, workflow_id: str, high_water: int) -> Iterator[dict[str, Any]]:
        """Inspect a fixed sequence range without loading ordinary event bodies."""
        if high_water > self.high_water(workflow_id):
            raise ValueError("audit callback high-water mark is invalid")
        for sequence in range(1, high_water + 1):
            path = self._path(sequence)
            if path.is_symlink():
                raise ValueError("audit event is symlinked")
            if not path.exists():
                continue
            if not path.is_file():
                raise ValueError("audit event is unsafe")
            with path.open("rb") as handle:
                prefix = handle.read(4096)
            match = _EVENT_TYPE_PREFIX.search(prefix)
            if match is None:
                raise ValueError("audit event has no bounded type prefix")
            if match.group(1) != b"workflow_event_callback_enqueued":
                continue
            record = self.read(workflow_id, sequence, bounded=True)
            if record is not None and record.get("delivery", "open") == "open":
                yield record

    def validate_callback(self, workflow_id: str, event: dict[str, Any]) -> dict[str, Any]:
        record = self.read(workflow_id, event.get("sequence"), bounded=True)
        if (
            record is None
            or record["event_type"] != "workflow_event_callback_enqueued"
            or record["event_id"] != event.get("event_id")
            or record["timestamp"] != event.get("occurred_at")
            or record["data"] != event
        ):
            raise ValueError("workflow callback identity is not durable")
        return record

    def close(self, workflow_id: str, sequence: int) -> None:
        record = self.read(workflow_id, sequence)
        if record is None or record["event_type"] != "workflow_event_callback_enqueued":
            raise ValueError("callback event is absent")
        record["delivery"] = "closed"
        atomic_write_bytes(self._path(sequence), self._event_bytes(record))
