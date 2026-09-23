"""Source-aware values shared by checklist rendering, validation and recovery."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re
from pathlib import Path
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


_CHECKBOX_LINE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<bullet>[-*][ \t]+)?\[(?P<state>[ xX])\](?P<body>.*)$"
)


def _normalized_item_block(lines: list[str], start: int, end: int) -> str:
    """Return one checklist item's block with all completion markers cleared."""
    normalized: list[str] = []
    for line in lines[start:end]:
        match = _CHECKBOX_LINE.match(line.rstrip("\r\n"))
        if match:
            ending = line[len(line.rstrip("\r\n")) :]
            normalized.append(
                f"{match.group('indent')}{match.group('bullet') or ''}[ ]"
                f"{match.group('body')}{ending}"
            )
        else:
            normalized.append(line)
    return "".join(normalized).rstrip() + "\n"


def _checklist_item_blocks(content: str) -> list[tuple[int, str, bool]]:
    """Return checklist item starts, complete blocks, and their completion state.

    Continuation and nested lines are part of an item's identity.  A changed
    subordinate rule must therefore reopen its parent gate rather than retain
    a stale ``[x]`` merely because the leading checkbox text still matches.
    """
    lines = content.splitlines(keepends=True)
    items: list[tuple[int, str, bool]] = []
    for start, line in enumerate(lines):
        match = _CHECKBOX_LINE.match(line.rstrip("\r\n"))
        if match is None:
            continue
        indent = len(match.group("indent").expandtabs(4))
        end = start + 1
        while end < len(lines):
            continuation = lines[end]
            nested = _CHECKBOX_LINE.match(continuation.rstrip("\r\n"))
            if nested is not None and len(nested.group("indent").expandtabs(4)) <= indent:
                break
            if continuation.strip() and not continuation.startswith((" ", "\t")):
                break
            end += 1
        items.append(
            (
                start,
                _normalized_item_block(lines, start, end),
                match.group("state").lower() == "x",
            )
        )
    return items




def normalized_checklist(content: str) -> str:
    """Clear completion marks without discarding instructions or provenance labels."""
    return "\n".join(
        re.sub(r"^(\s*(?:[-*]\s+)?\[)[xX](\])", r"\1 \2", line)
        for line in content.splitlines()
    ).rstrip()


def load_materialization(path: Path) -> ChecklistMaterialization | None:
    """Read bounded metadata; malformed records cannot authorize restoration."""
    if not path.is_file():
        return None
    try:
        # Iteration files can contain unrelated logs; only bound our derived record.
        raw = json.loads(path.read_text(encoding="utf-8"))
        record = raw.get("effective_checklist") if isinstance(raw, dict) else None
        if record is None:
            return None
        if not isinstance(record, dict) or record.get("version") != 1:
            raise ValueError("Unsupported effective checklist metadata")
        content = record["content"]
        if not isinstance(content, str) or len(content.encode()) > 2 * 1024 * 1024:
            raise ValueError("Invalid effective checklist content")
        gates = tuple(ChecklistGate(**gate) for gate in record["gates"])
        blocks = _checklist_item_blocks(content)
        if len(gates) > 10000 or len(gates) != len(blocks):
            raise ValueError("Effective checklist gate count is inconsistent")
        if len({gate.identity for gate in gates}) != len(gates):
            raise ValueError("Effective checklist gate identities are duplicated")
        for gate, (_, block, _) in zip(gates, blocks):
            if not re.fullmatch(r"[0-9a-f]{64}", gate.identity) or not gate.source or gate.block != block:
                raise ValueError("Effective checklist gate identity is inconsistent")
        return ChecklistMaterialization(content, gates, tuple(record["projections"]), record["overlays"])
    except (KeyError, TypeError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid effective checklist metadata") from exc
