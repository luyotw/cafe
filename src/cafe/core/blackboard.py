"""Shared workflow blackboard state and persistence."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


BLACKBOARD_FILENAME = "blackboard.json"
BLACKBOARD_SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


class ArtifactKind(str, Enum):
    """Supported artifact kinds for v0.2."""

    DOCUMENT = "document"
    WORKSPACE = "workspace"
    METADATA = "metadata"


@dataclass
class ArtifactEntry:
    """One persisted artifact pointer."""

    name: str
    kind: ArtifactKind
    version: int
    updated_by: str
    path: str
    updated_at: str = field(default_factory=_now_iso)
    summary: str = ""
    base_sha: Optional[str] = None
    head_sha: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ArtifactEntry":
        return cls(
            name=str(data["name"]),
            kind=ArtifactKind(str(data["kind"])),
            version=int(data["version"]),
            updated_by=str(data.get("updated_by", "")),
            updated_at=str(data.get("updated_at", _now_iso())),
            path=str(data.get("path", "")),
            summary=str(data.get("summary", "")),
            base_sha=data.get("base_sha"),
            head_sha=data.get("head_sha"),
        )


@dataclass
class EventEntry:
    """One blackboard event."""

    timestamp: str
    step: str
    event_type: str
    message: str
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EventEntry":
        payload = dict(data)
        if "event_type" not in payload and "type" in payload:
            payload["event_type"] = payload.pop("type")
        if "step" not in payload:
            payload["step"] = str(payload.get("payload", {}).get("step", "system"))
        if "message" not in payload:
            payload["message"] = str(payload.get("payload", {}))
        if "data" not in payload:
            payload["data"] = payload.pop("payload", {})
        return cls(
            timestamp=str(payload.get("timestamp", _now_iso())),
            step=str(payload.get("step", "system")),
            event_type=str(payload.get("event_type", "event")),
            message=str(payload.get("message", "")),
            data=dict(payload.get("data", {})),
        )


@dataclass
class DecisionEntry:
    """One recorded decision."""

    timestamp: str
    step: str
    decision: str
    rationale: str
    made_by: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DecisionEntry":
        if "decision" in data:
            return cls(
                timestamp=str(data.get("timestamp", _now_iso())),
                step=str(data.get("step", "system")),
                decision=str(data["decision"]),
                rationale=str(data.get("rationale", "")),
                made_by=str(data.get("made_by", data.get("step", "system"))),
            )

        return cls(
            timestamp=str(data.get("timestamp", _now_iso())),
            step=str(data.get("from", "system")),
            decision="transition",
            rationale=json.dumps({k: v for k, v in data.items() if k != "timestamp"}, ensure_ascii=False),
            made_by=str(data.get("from", "system")),
        )


@dataclass
class BlackboardState:
    """Shared state across workflow steps."""

    current_step: str
    playbook_id: str = "default"
    schema_version: int = BLACKBOARD_SCHEMA_VERSION
    artifacts: Dict[str, ArtifactEntry] = field(default_factory=dict)
    events: List[EventEntry] = field(default_factory=list)
    decisions: List[DecisionEntry] = field(default_factory=list)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "current_step": self.current_step,
            "playbook_id": self.playbook_id,
            "artifacts": {name: entry.to_dict() for name, entry in self.artifacts.items()},
            "events": [entry.to_dict() for entry in self.events],
            "decisions": [entry.to_dict() for entry in self.decisions],
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], *, initial_step: str) -> "BlackboardState":
        raw_artifacts = data.get("artifacts", {})
        artifacts: Dict[str, ArtifactEntry] = {}
        if isinstance(raw_artifacts, dict):
            for name, value in raw_artifacts.items():
                if isinstance(value, dict):
                    value.setdefault("name", name)
                    artifacts[name] = ArtifactEntry.from_dict(value)
                else:
                    artifacts[name] = ArtifactEntry(
                        name=name,
                        kind=ArtifactKind.DOCUMENT,
                        version=1,
                        updated_by="unknown",
                        path=str(value),
                    )

        return cls(
            current_step=str(data.get("current_step", initial_step)),
            playbook_id=str(data.get("playbook_id", "default")),
            schema_version=int(data.get("schema_version", BLACKBOARD_SCHEMA_VERSION)),
            artifacts=artifacts,
            events=[EventEntry.from_dict(entry) for entry in data.get("events", [])],
            decisions=[DecisionEntry.from_dict(entry) for entry in data.get("decisions", [])],
            updated_at=str(data.get("updated_at", _now_iso())),
        )


class BlackboardStore:
    """Persist blackboard data in issue directory."""

    def __init__(self, issue_dir: Path) -> None:
        self.issue_dir = issue_dir
        self.file_path = issue_dir / BLACKBOARD_FILENAME

    def load_or_create(self, initial_step: str, playbook_id: str = "default") -> BlackboardState:
        if self.file_path.exists():
            raw = json.loads(self.file_path.read_text(encoding="utf-8"))
            state = BlackboardState.from_dict(raw, initial_step=initial_step)
            if not getattr(state, "playbook_id", None):
                state.playbook_id = playbook_id
                self.save(state)
            return state

        state = BlackboardState(current_step=initial_step, playbook_id=playbook_id)
        self.save(state)
        return state

    def save(self, state: BlackboardState) -> None:
        self.issue_dir.mkdir(parents=True, exist_ok=True)
        state.updated_at = _now_iso()
        self.file_path.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_artifact(self, state: BlackboardState, name: str) -> Optional[ArtifactEntry]:
        return state.artifacts.get(name)

    def list_artifacts(self, state: BlackboardState) -> Dict[str, ArtifactEntry]:
        return dict(state.artifacts)

    def put_artifact(self, state: BlackboardState, entry: ArtifactEntry) -> None:
        state.artifacts[entry.name] = entry
        self.save(state)

    def set_current_step(self, state: BlackboardState, step: str) -> None:
        state.current_step = step
        self.save(state)

    def set_artifact(self, state: BlackboardState, key: str, path: str) -> None:
        previous = state.artifacts.get(key)
        version = previous.version + 1 if previous else 1
        kind = previous.kind if previous else ArtifactKind.DOCUMENT
        updated_by = state.current_step
        self.put_artifact(
            state,
            ArtifactEntry(
                name=key,
                kind=kind,
                version=version,
                updated_by=updated_by,
                path=path,
            ),
        )

    def log_event(
        self,
        state: BlackboardState,
        step: str,
        event_type: str,
        message: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        state.events.append(
            EventEntry(
                timestamp=_now_iso(),
                step=step,
                event_type=event_type,
                message=message,
                data=data or {},
            )
        )
        self.save(state)

    def record_event(self, state: BlackboardState, event_type: str, payload: Dict[str, Any]) -> None:
        step = str(payload.get("step", state.current_step))
        self.log_event(state, step, event_type, json.dumps(payload, ensure_ascii=False), payload)

    def get_events_since(self, state: BlackboardState, timestamp: str) -> List[EventEntry]:
        return [entry for entry in state.events if entry.timestamp >= timestamp]

    def record_decision(
        self,
        state: BlackboardState,
        step_or_decision: str | Dict[str, Any],
        decision: Optional[str] = None,
        rationale: Optional[str] = None,
        made_by: Optional[str] = None,
    ) -> None:
        if isinstance(step_or_decision, dict):
            entry = DecisionEntry.from_dict(step_or_decision)
        else:
            entry = DecisionEntry(
                timestamp=_now_iso(),
                step=step_or_decision,
                decision=decision or "",
                rationale=rationale or "",
                made_by=made_by or step_or_decision,
            )
        state.decisions.append(entry)
        self.save(state)

    def generate_digest(
        self,
        state: BlackboardState,
        *,
        for_step: str,
        since: Optional[str] = None,
        max_events: int = 20,
    ) -> str:
        lines = ["## Blackboard", "", "### Artifacts", "| Name | Kind | Ver | Updated By | When |", "|------|------|-----|-----------|------|"]
        for name, entry in sorted(state.artifacts.items()):
            when = entry.updated_at.split("T", 1)[1][:5] if "T" in entry.updated_at else entry.updated_at
            lines.append(
                f"| {name} | {entry.kind.value} | v{entry.version} | {entry.updated_by} | {when} |"
            )

        lines.extend(["", f"### Recent Events (since your last run, max {max_events})"])
        events = state.events if since is None else self.get_events_since(state, since)
        for entry in events[-max_events:]:
            when = entry.timestamp.split("T", 1)[1][:5] if "T" in entry.timestamp else entry.timestamp
            lines.append(f"- [{when}] {entry.step}: {entry.message}")

        lines.extend(["", "### Input Files"])
        for name, entry in sorted(state.artifacts.items()):
            if entry.kind == ArtifactKind.WORKSPACE and entry.base_sha and entry.head_sha:
                lines.append(f"- {name}: git diff {entry.base_sha}...{entry.head_sha}")
            else:
                lines.append(f"- {name}: {entry.path}")
        return "\n".join(lines)

    def rebuild_from_iterations(self, *, initial_step: str) -> BlackboardState:
        state = BlackboardState(current_step=initial_step)
        latest_step = initial_step
        latest_timestamp = ""

        for artifact_file in sorted(self.issue_dir.glob("*/iteration_*/artifact.json")):
            raw = json.loads(artifact_file.read_text(encoding="utf-8"))
            entry = ArtifactEntry.from_dict(raw)
            current = state.artifacts.get(entry.name)
            if current is None or entry.version >= current.version:
                state.artifacts[entry.name] = entry
            if entry.updated_at >= latest_timestamp:
                latest_timestamp = entry.updated_at
                latest_step = artifact_file.parent.parent.name

        state.current_step = latest_step
        state.events.append(
            EventEntry(
                timestamp=_now_iso(),
                step=latest_step,
                event_type="rebuild",
                message="Rebuilt blackboard state from iteration artifacts",
            )
        )
        self.save(state)
        return state
