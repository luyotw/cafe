"""The strict, portable contract for workflow work items."""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, Mapping

TodoSource = Literal["plan", "review", "qa", "pr_comment", "workflow_feedback"]
TODO_SOURCES = frozenset({"plan", "review", "qa", "pr_comment", "workflow_feedback"})
_HEADING = re.compile(r"^#{1,6}\s+Todo List\s*$", re.IGNORECASE)
_ANY_HEADING = re.compile(r"^#{1,6}\s+")
_ITEM = re.compile(
    r"^- \[(?P<checked>[ xX])\] `(?P<id>[A-Za-z][A-Za-z0-9_-]*)`\s+— "
    r"Source: `(?P<source>[a-z_]+)`\s+— Work: (?P<work>.+?)\s+— "
    r"Closure: (?P<closure>.+?)\s+— Evidence: (?P<evidence>.+?)\s*$"
)


class TodoContractError(ValueError):
    """Raised when an authoritative Todo List is not safe to project."""


@dataclass(frozen=True)
class TodoItem:
    item_id: str
    source: TodoSource
    work: str
    closure: str
    evidence: str
    checked: bool

    @property
    def fingerprint(self) -> str:
        normalized = "\x1f".join(
            " ".join(value.split())
            for value in (self.source, self.item_id, self.work, self.closure, self.evidence)
        )
        return sha256(normalized.encode("utf-8")).hexdigest()

    def checklist_row(self) -> str:
        return f"[ ] `{self.item_id}` — {self.work} (source fingerprint: {self.fingerprint})"


@dataclass(frozen=True)
class TodoSourceArtifact:
    """The one artifact causally selected for a Todo projection."""

    artifact: str
    source: TodoSource
    path: Path


def resolve_todo_source(
    *,
    correction_artifact: str | None,
    artifacts: Mapping[str, object],
) -> TodoSourceArtifact:
    """Resolve an explicit causal artifact; never prioritize historical feedback."""
    source_by_artifact: dict[str, TodoSource] = {
        "plan": "plan",
        "review_feedback": "review",
        "qa_feedback": "qa",
        "pr_result": "pr_comment",
        "workflow_feedback": "workflow_feedback",
    }
    artifact = correction_artifact or "plan"
    source = source_by_artifact.get(artifact)
    if source is None:
        raise TodoContractError(f"unsupported causal Todo artifact: {artifact}")
    value = artifacts.get(artifact)
    if value is None:
        raise TodoContractError(f"missing causal Todo artifact: {artifact}")
    path = Path(str(getattr(value, "path", value)))
    if not path.is_file():
        raise TodoContractError(f"unreadable causal Todo artifact: {artifact}")
    return TodoSourceArtifact(artifact=artifact, source=source, path=path)


def parse_todo_list(
    content: str, *, expected_source: TodoSource | None = None
) -> tuple[TodoItem, ...]:
    """Parse exactly one designated Todo List section, failing closed on bad rows."""
    lines = content.splitlines()
    headings = [index for index, line in enumerate(lines) if _HEADING.match(line.strip())]
    if len(headings) != 1:
        raise TodoContractError("artifact must contain exactly one '## Todo List' section")
    start = headings[0] + 1
    section: list[str] = []
    for line in lines[start:]:
        if _ANY_HEADING.match(line):
            break
        section.append(line)
    items: list[TodoItem] = []
    ids: set[str] = set()
    for line in section:
        if not line.strip():
            continue
        match = _ITEM.fullmatch(line)
        if match is None:
            raise TodoContractError("Todo List contains a malformed item")
        source = match.group("source")
        if source not in TODO_SOURCES:
            raise TodoContractError(f"unsupported Todo source: {source}")
        if expected_source is not None and source != expected_source:
            raise TodoContractError(
                "Todo item source does not match the declared projection source"
            )
        item_id = match.group("id")
        if item_id in ids:
            raise TodoContractError(f"duplicate Todo item ID: {item_id}")
        ids.add(item_id)
        items.append(
            TodoItem(
                item_id=item_id,
                source=source,  # type: ignore[arg-type]
                work=match.group("work").strip(),
                closure=match.group("closure").strip(),
                evidence=match.group("evidence").strip(),
                checked=match.group("checked").lower() == "x",
            )
        )
    return tuple(items)
