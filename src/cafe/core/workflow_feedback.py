"""Durable, repo-first feedback state for workflow handoffs.

The ledger is deliberately independent of a particular source hook or human
task.  It is the single authority for whether a feedback item is new,
actionable, consumed, or resolved.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


class WorkflowFeedbackError(RuntimeError):
    """Raised when the durable feedback ledger cannot be validated or stored."""


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _required(value: str, *, field: str) -> str:
    cleaned = str(value).strip()
    if not cleaned:
        raise WorkflowFeedbackError(f"workflow feedback {field} must not be empty")
    return cleaned


def _required_bool(value: Any, *, field: str) -> bool:
    if type(value) is not bool:
        raise WorkflowFeedbackError(f"workflow feedback {field} must be a boolean")
    return value


def _validate_lifecycle(*, actionable: bool, consumed: bool, resolved: bool) -> None:
    _required_bool(actionable, field="actionable")
    _required_bool(consumed, field="consumed")
    _required_bool(resolved, field="resolved")
    if actionable and (consumed or resolved):
        raise WorkflowFeedbackError("consumed or resolved feedback cannot be actionable")
    if not actionable and not consumed and not resolved:
        raise WorkflowFeedbackError("inactive feedback must be consumed or resolved")


@dataclass(frozen=True)
class WorkflowFeedbackEntry:
    """One source item and its workflow lifecycle state."""

    source_identity: str
    source_kind: str
    target_step: str
    content: str
    actionable: bool
    consumed: bool
    resolved: bool
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        _validate_lifecycle(
            actionable=self.actionable,
            consumed=self.consumed,
            resolved=self.resolved,
        )

    @classmethod
    def new(
        cls,
        *,
        source_identity: str,
        source_kind: str,
        target_step: str,
        content: str,
        resolved: bool = False,
    ) -> "WorkflowFeedbackEntry":
        timestamp = _now()
        return cls(
            source_identity=_required(source_identity, field="source_identity"),
            source_kind=_required(source_kind, field="source_kind"),
            target_step=_required(target_step, field="target_step"),
            content=_required(content, field="content"),
            actionable=not resolved,
            consumed=False,
            resolved=_required_bool(resolved, field="resolved"),
            created_at=timestamp,
            updated_at=timestamp,
        )

    @classmethod
    def from_dict(cls, raw: Any) -> "WorkflowFeedbackEntry":
        if not isinstance(raw, dict):
            raise WorkflowFeedbackError("workflow feedback entries must be objects")
        try:
            entry = cls(
                source_identity=_required(raw["source_identity"], field="source_identity"),
                source_kind=_required(raw["source_kind"], field="source_kind"),
                target_step=_required(raw["target_step"], field="target_step"),
                content=_required(raw["content"], field="content"),
                actionable=_required_bool(raw["actionable"], field="actionable"),
                consumed=_required_bool(raw["consumed"], field="consumed"),
                resolved=_required_bool(raw["resolved"], field="resolved"),
                created_at=_required(raw["created_at"], field="created_at"),
                updated_at=_required(raw["updated_at"], field="updated_at"),
            )
        except KeyError as exc:
            raise WorkflowFeedbackError(f"workflow feedback entry misses {exc.args[0]}") from exc
        return entry


class WorkflowFeedbackLedger:
    """Atomic JSON-backed lifecycle store for reusable feedback."""

    artifact_name = "workflow_feedback"

    def __init__(self, issue_dir: Path) -> None:
        self.issue_dir = Path(issue_dir)
        self.path = self.issue_dir / "artifacts" / "workflow_feedback.json"

    def load(self) -> list[WorkflowFeedbackEntry]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkflowFeedbackError("could not read workflow feedback ledger") from exc
        if not isinstance(raw, dict) or raw.get("version") != 1 or not isinstance(raw.get("entries"), list):
            raise WorkflowFeedbackError("workflow feedback ledger has an invalid shape")
        entries = [WorkflowFeedbackEntry.from_dict(item) for item in raw["entries"]]
        identities = [entry.source_identity for entry in entries]
        if len(identities) != len(set(identities)):
            raise WorkflowFeedbackError("workflow feedback ledger has duplicate source identities")
        return entries

    def record(
        self,
        *,
        source_identity: str,
        source_kind: str,
        target_step: str,
        content: str,
        resolved: bool = False,
    ) -> tuple[bool, WorkflowFeedbackEntry]:
        """Record one source item and return whether it was newly persisted."""
        candidate = WorkflowFeedbackEntry.new(
            source_identity=source_identity,
            source_kind=source_kind,
            target_step=target_step,
            content=content,
            resolved=resolved,
        )
        entries = self.load()
        for current in entries:
            if current.source_identity == candidate.source_identity:
                return False, current
        self._store([*entries, candidate])
        return True, candidate

    def reconcile_resolved(self, source_identities: Iterable[str]) -> int:
        """Mark source identities resolved without resurrecting prior feedback."""
        resolved = {str(identity).strip() for identity in source_identities if str(identity).strip()}
        entries = self.load()
        changed = 0
        updated: list[WorkflowFeedbackEntry] = []
        for entry in entries:
            if entry.source_identity not in resolved or entry.resolved:
                updated.append(entry)
                continue
            changed += 1
            updated.append(
                WorkflowFeedbackEntry(
                    **{
                        **asdict(entry),
                        "actionable": False,
                        "resolved": True,
                        "updated_at": _now(),
                    }
                )
            )
        if changed:
            self._store(updated)
        return changed

    def consume(self, source_identity: str) -> bool:
        """Mark one delivered item consumed; consuming twice is idempotent."""
        identity = _required(source_identity, field="source_identity")
        entries = self.load()
        updated: list[WorkflowFeedbackEntry] = []
        changed = False
        for entry in entries:
            if entry.source_identity != identity or entry.consumed:
                updated.append(entry)
                continue
            changed = True
            updated.append(
                WorkflowFeedbackEntry(
                    **{
                        **asdict(entry),
                        "actionable": False,
                        "consumed": True,
                        "updated_at": _now(),
                    }
                )
            )
        if changed:
            self._store(updated)
        return changed

    def consume_delivered(
        self, source_identities: Iterable[str]
    ) -> list[WorkflowFeedbackEntry]:
        """Atomically complete delivery only for feedback the consumer received."""
        identities = {
            _required(identity, field="source_identity")
            for identity in source_identities
        }
        if not identities:
            return []
        entries = self.load()
        delivered = [
            entry
            for entry in entries
            if entry.source_identity in identities and entry.actionable
        ]
        if not delivered:
            return []
        delivered_identities = {entry.source_identity for entry in delivered}
        updated = [
            WorkflowFeedbackEntry(
                **{
                    **asdict(entry),
                    "actionable": False,
                    "consumed": True,
                    "updated_at": _now(),
                }
            )
            if entry.source_identity in delivered_identities
            else entry
            for entry in entries
        ]
        self._store(updated)
        return delivered

    def consume_pending_for_target(self, target_step: str) -> list[WorkflowFeedbackEntry]:
        """Complete an explicitly requested delivery for every pending target item."""
        target = _required(target_step, field="target_step")
        return self.consume_delivered(
            entry.source_identity for entry in self.pending(target_step=target)
        )

    def pending(self, *, target_step: str | None = None) -> list[WorkflowFeedbackEntry]:
        """Return only items that are still actionable, optionally for one target."""
        return [
            entry
            for entry in self.load()
            if entry.actionable and (target_step is None or entry.target_step == target_step)
        ]

    def _store(self, entries: list[WorkflowFeedbackEntry]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "entries": [asdict(entry) for entry in entries]}
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_name = handle.name
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        except OSError as exc:
            raise WorkflowFeedbackError("could not persist workflow feedback ledger") from exc
        finally:
            if temp_name:
                Path(temp_name).unlink(missing_ok=True)
