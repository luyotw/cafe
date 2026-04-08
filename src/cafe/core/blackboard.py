"""Shared workflow blackboard state."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


BLACKBOARD_FILENAME = "blackboard.json"


@dataclass
class BlackboardState:
    """Shared state across workflow steps."""

    current_step: str
    artifacts: Dict[str, str] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    decisions: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "current_step": self.current_step,
            "artifacts": self.artifacts,
            "events": self.events,
            "decisions": self.decisions,
            "updated_at": datetime.now().astimezone().isoformat(),
        }


class BlackboardStore:
    """Persist blackboard data in issue directory."""

    def __init__(self, issue_dir: Path) -> None:
        self.issue_dir = issue_dir
        self.file_path = issue_dir / BLACKBOARD_FILENAME

    def load_or_create(self, initial_step: str) -> BlackboardState:
        if self.file_path.exists():
            raw = json.loads(self.file_path.read_text(encoding="utf-8"))
            return BlackboardState(
                current_step=raw.get("current_step", initial_step),
                artifacts=raw.get("artifacts", {}),
                events=raw.get("events", []),
                decisions=raw.get("decisions", []),
            )

        state = BlackboardState(current_step=initial_step)
        self.save(state)
        return state

    def save(self, state: BlackboardState) -> None:
        self.issue_dir.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def set_current_step(self, state: BlackboardState, step: str) -> None:
        state.current_step = step
        self.save(state)

    def set_artifact(self, state: BlackboardState, key: str, path: str) -> None:
        state.artifacts[key] = path
        self.save(state)

    def record_event(self, state: BlackboardState, event_type: str, payload: Dict[str, Any]) -> None:
        event = {
            "type": event_type,
            "payload": payload,
            "timestamp": datetime.now().astimezone().isoformat(),
        }
        state.events.append(event)
        self.save(state)

    def record_decision(self, state: BlackboardState, decision: Dict[str, Any]) -> None:
        value = {
            **decision,
            "timestamp": datetime.now().astimezone().isoformat(),
        }
        state.decisions.append(value)
        self.save(state)
