"""Durable correction fences for event-driver dispatches."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from cafe.core.packet_io import atomic_write_bytes, canonical_json

_EVENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class EventDispatchFenceStore:
    """Fence event IDs without rewriting their append-only dispatch history."""

    def __init__(self, issue_dir: Path) -> None:
        self.issue_dir = Path(issue_dir)
        self.driver_dir = self.issue_dir / "driver"
        self.state_path = self.driver_dir / "dispatch_state.json"
        self.path = self.driver_dir / "correction_dispatch_fences.json"

    def invalidate(self, event_id: str, *, operation_id: str) -> bool:
        """Record a durable fence only for a dispatch already known to CAFE."""
        self._validate_id(event_id)
        self.driver_dir.mkdir(parents=True, exist_ok=True)
        with self._lock_path().open("a+", encoding="utf-8") as handle:
            self._lock(handle)
            try:
                state = self._read_json(self.state_path)
                events = state.get("events") if isinstance(state, dict) else None
                if not isinstance(events, dict) or event_id not in events:
                    return False
                fences = self._read_json(self.path) if self.path.exists() else {"version": 1, "fences": {}}
                values = fences.get("fences") if isinstance(fences, dict) else None
                if not isinstance(values, dict):
                    raise ValueError("correction dispatch fences are invalid")
                existing = values.get(event_id)
                if existing is not None and existing != operation_id:
                    raise ValueError("event dispatch is already fenced by another correction")
                values[event_id] = operation_id
                atomic_write_bytes(self.path, canonical_json({"version": 1, "fences": values}))
                return True
            finally:
                self._unlock(handle)

    def is_fenced(self, event_id: str) -> bool:
        self._validate_id(event_id)
        if not self.path.exists():
            return False
        fences = self._read_json(self.path)
        values = fences.get("fences") if isinstance(fences, dict) else None
        if not isinstance(values, dict):
            raise ValueError("correction dispatch fences are invalid")
        return event_id in values

    def _lock_path(self) -> Path:
        return self.driver_dir / "session.lock"

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("event dispatch state is invalid") from exc
        if not isinstance(value, dict):
            raise ValueError("event dispatch state is invalid")
        return value

    @staticmethod
    def _validate_id(event_id: str) -> None:
        if not isinstance(event_id, str) or not _EVENT_ID.fullmatch(event_id):
            raise ValueError("event dispatch identifier is invalid")

    @staticmethod
    def _lock(handle: Any) -> None:
        try:
            import fcntl
        except ImportError:  # pragma: no cover - platform fallback.
            return
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

    @staticmethod
    def _unlock(handle: Any) -> None:
        try:
            import fcntl
        except ImportError:  # pragma: no cover - platform fallback.
            return
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
