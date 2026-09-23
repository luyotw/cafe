"""Source-aware values shared by checklist rendering, validation and recovery."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Any

from cafe.core.todo import TodoItem


def checklist_digest(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class ChecklistGate:
    identity: str
    source: str
    block: str


@dataclass(frozen=True)
class ProjectedTodo:
    """Consumer address with the unchanged authoritative producer identity."""

    item_id: str
    producer: TodoItem

    @property
    def fingerprint(self) -> str:
        return self.producer.fingerprint

    def checklist_row(self) -> str:
        return f"[ ] `{self.item_id}` — {self.producer.work} (source fingerprint: {self.fingerprint})"


@dataclass(frozen=True)
class ChecklistMaterialization:
    content: str
    gates: tuple[ChecklistGate, ...]
    projections: tuple[dict[str, Any], ...]
    overlays: bool

    def to_dict(self) -> dict[str, Any]:
        return {"version": 1, "content": self.content, "gates": [asdict(gate) for gate in self.gates],
                "projections": list(self.projections), "overlays": self.overlays}
