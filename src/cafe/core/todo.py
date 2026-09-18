"""The strict, portable contract for workflow work items."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Mapping, Sequence

TodoSource = str
MAX_TODO_ITEMS = 100
_HEADING = re.compile(r"^##\s+Todo List\s*$")
_ANY_HEADING = re.compile(r"^#{1,6}\s+")
_INTENTIONALLY_EMPTY = "No actionable work."
_IDENTITY_HEADING = re.compile(r"^##\s+Todo Identity Continuity\s*$")
_IDENTITY_ITEM = re.compile(
    r"^- `(?P<id>PLAN-\d{3})`\s+— Previous work fingerprint: `(?P<fingerprint>[0-9a-f]{64})`\s*$"
)
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
    version: int | None = None
    items: tuple[TodoItem, ...] | None = None


def plan_work_fingerprint(work: str) -> str:
    """Return the canonical identity for a PLAN item's Work text.

    The identity is independent of the mutable PLAN identifier and uses one
    exact normalized payload so plan authors and runtime validators share the
    same durable continuity contract.
    """
    normalized_work = " ".join(str(work).split())
    return sha256(("plan\x1f" + normalized_work).encode("utf-8")).hexdigest()


def resolve_todo_source(
    *,
    artifact: str,
    source: TodoSource,
    artifacts: Mapping[str, object],
) -> TodoSourceArtifact:
    """Resolve one explicitly declared artifact/source pair."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", source):
        raise TodoContractError(f"unsupported Todo source: {source}")
    value = artifacts.get(artifact)
    if value is None:
        raise TodoContractError(f"missing causal Todo artifact: {artifact}")
    path = Path(str(getattr(value, "path", value)))
    if not path.is_file():
        raise TodoContractError(f"unreadable causal Todo artifact: {artifact}")
    return TodoSourceArtifact(
        artifact=artifact,
        source=source,
        path=path,
        version=getattr(value, "version", None),
    )


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
    meaningful = [line.strip() for line in section if line.strip()]
    if meaningful == [_INTENTIONALLY_EMPTY]:
        return ()
    if not meaningful:
        raise TodoContractError("Todo List must declare that it has no actionable work")
    for line_number, line in enumerate(section, start=start + 1):
        if not line.strip():
            continue
        match = _ITEM.fullmatch(line)
        if match is None:
            raise TodoContractError(
                f"Todo List contains a malformed item at line {line_number}"
            )
        source = match.group("source")
        if expected_source is not None and source != expected_source:
            raise TodoContractError(
                "Todo item source does not match the declared projection source"
            )
        item_id = match.group("id")
        if source == "plan" and not re.fullmatch(r"PLAN-\d{3}", item_id):
            raise TodoContractError(
                "plan Todo item IDs must use the canonical PLAN-NNN format"
            )
        if item_id in ids:
            raise TodoContractError(f"duplicate Todo item ID: {item_id}")
        ids.add(item_id)
        work = match.group("work").strip()
        closure = match.group("closure").strip()
        evidence = match.group("evidence").strip()
        if not work or not closure or not evidence:
            raise TodoContractError(
                "Todo item Source, Work, Closure, and Evidence must be non-empty"
            )
        items.append(
            TodoItem(
                item_id=item_id,
                source=source,  # type: ignore[arg-type]
                work=work,
                closure=closure,
                evidence=evidence,
                checked=match.group("checked").lower() == "x",
            )
        )
        if len(items) > MAX_TODO_ITEMS:
            raise TodoContractError(f"Todo List exceeds {MAX_TODO_ITEMS} items")
    return tuple(items)


def parse_todo_identity_continuity(content: str) -> dict[str, str]:
    """Parse optional explicit continuity proofs for revised PLAN work."""
    lines = content.splitlines()
    headings = [index for index, line in enumerate(lines) if _IDENTITY_HEADING.match(line.strip())]
    if not headings:
        return {}
    if len(headings) != 1:
        raise TodoContractError(
            "artifact must contain at most one '## Todo Identity Continuity' section"
        )
    section: list[str] = []
    for line in lines[headings[0] + 1 :]:
        if _ANY_HEADING.match(line):
            break
        section.append(line)
    meaningful = [line.strip() for line in section if line.strip()]
    if not meaningful:
        raise TodoContractError("Todo Identity Continuity must declare at least one proof")
    proofs: dict[str, str] = {}
    for line in meaningful:
        match = _IDENTITY_ITEM.fullmatch(line)
        if match is None:
            raise TodoContractError("Todo Identity Continuity contains a malformed proof")
        item_id = match.group("id")
        if item_id in proofs:
            raise TodoContractError(f"duplicate Todo identity continuity proof: {item_id}")
        proofs[item_id] = match.group("fingerprint")
    return proofs


def validate_todo_identities(
    previous: Sequence[TodoItem],
    current: Sequence[TodoItem],
    *,
    retained: Mapping[str, str] | Sequence[str] | None = None,
) -> None:
    """Validate stable Todo identity metadata across two authoritative versions.

    Reordering and revision of an existing item are valid when the item keeps its
    identifier.  Callers that persist fingerprints can additionally provide a
    mapping of retained identifiers to their expected prior fingerprint; this
    prevents a stale writer from silently reusing an identity for unrelated work.
    """
    previous_by_id = {item.item_id: item for item in previous}
    current_by_id = {item.item_id: item for item in current}
    if len(previous_by_id) != len(previous) or len(current_by_id) != len(current):
        raise TodoContractError("Todo item identities must be unique")
    if retained is None:
        retained_ids = set(previous_by_id) & set(current_by_id)
        expected_fingerprints: dict[str, str] = {}
    elif isinstance(retained, Mapping):
        retained_ids = set(retained)
        expected_fingerprints = {str(key): str(value) for key, value in retained.items()}
    else:
        retained_ids = {str(item) for item in retained}
        expected_fingerprints = {
            item_id: previous_by_id[item_id].fingerprint
            for item_id in retained_ids
            if item_id in previous_by_id
        }
    missing = sorted(retained_ids - set(current_by_id))
    if missing:
        raise TodoContractError(f"Todo identity is missing across versions: {missing[0]}")
    unknown = sorted(retained_ids - set(previous_by_id))
    if unknown:
        raise TodoContractError(f"Todo identity has no prior version: {unknown[0]}")
    for item_id, expected in expected_fingerprints.items():
        if item_id in current_by_id and current_by_id[item_id].fingerprint == expected:
            continue
        raise TodoContractError(f"Todo identity was reassigned: {item_id}")


def workflow_feedback_todo_items(
    path: Path,
    *,
    target_step: str,
    source_by_kind: Mapping[str, TodoSource],
    id_prefix_by_kind: Mapping[str, str],
    source_identities: tuple[str, ...] | None = None,
) -> tuple[TodoItem, ...]:
    """Normalize pending target feedback or one exact delivered batch into Todo items."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TodoContractError("workflow feedback Todo source is unreadable") from exc
    entries = raw.get("entries") if isinstance(raw, dict) and raw.get("version") == 1 else None
    if not isinstance(entries, list):
        raise TodoContractError("workflow feedback Todo source has an invalid shape")
    identities = set(source_identities) if source_identities is not None else None
    if identities == set():
        return ()
    from cafe.core.workflow_feedback import WorkflowFeedbackEntry, WorkflowFeedbackError

    selected: list[WorkflowFeedbackEntry] = []
    seen: set[str] = set()
    for entry in entries:
        try:
            normalized = WorkflowFeedbackEntry.from_dict(entry)
        except WorkflowFeedbackError as exc:
            raise TodoContractError("workflow feedback Todo entry has an invalid shape") from exc
        identity = normalized.source_identity
        if not identity or identity in seen:
            raise TodoContractError("workflow feedback Todo identities are missing or duplicate")
        seen.add(identity)
        if identities is None:
            # Pending discovery is step-scoped. An explicit identity set is a
            # previously pinned delivery and remains authoritative after its
            # curator hands the canonical artifact to a different step.
            if normalized.target_step != target_step:
                continue
            if not normalized.actionable:
                continue
        elif identity not in identities:
            continue
        selected.append(normalized)
    if identities is not None and {item.source_identity for item in selected} != identities:
        raise TodoContractError("workflow feedback Todo delivery identities are missing")
    if not selected and identities is None:
        return ()
    if not selected:
        raise TodoContractError("workflow feedback Todo delivery is missing")
    if len(selected) > MAX_TODO_ITEMS:
        raise TodoContractError(f"Todo List exceeds {MAX_TODO_ITEMS} items")

    items: list[TodoItem] = []
    for entry in selected:
        identity = entry.source_identity
        source = source_by_kind.get(entry.source_kind)
        if source is None:
            raise TodoContractError("workflow feedback source kind is not declared")
        prefix = id_prefix_by_kind.get(entry.source_kind)
        if prefix is None or not re.fullmatch(r"[A-Z][A-Z0-9_]*", prefix):
            raise TodoContractError("workflow feedback Todo ID prefix is not declared")
        work = " ".join(entry.content.split())
        if not work:
            raise TodoContractError("workflow feedback Todo work is empty")
        item_id = f"{prefix}-{sha256(identity.encode('utf-8')).hexdigest()[:12].upper()}"
        items.append(
            TodoItem(
                item_id=item_id,
                source=source,
                work=work,
                closure="The causal feedback request is addressed",
                evidence="Targeted verification for this feedback item",
                checked=False,
            )
        )
    return tuple(items)


def workflow_feedback_matching_identities(
    path: Path, *, target_step: str, source_kind: str, identity_prefix: str
) -> tuple[str, ...]:
    """Return bounded pending identities matching one declared inbound route."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TodoContractError("workflow feedback Todo source is unreadable") from exc
    entries = raw.get("entries") if isinstance(raw, dict) and raw.get("version") == 1 else None
    if not isinstance(entries, list):
        raise TodoContractError("workflow feedback Todo source has an invalid shape")
    from cafe.core.workflow_feedback import WorkflowFeedbackEntry, WorkflowFeedbackError

    matches: list[str] = []
    for raw_entry in entries:
        try:
            entry = WorkflowFeedbackEntry.from_dict(raw_entry)
        except WorkflowFeedbackError as exc:
            raise TodoContractError("workflow feedback Todo entry has an invalid shape") from exc
        if (
            entry.target_step == target_step
            and entry.source_kind == source_kind
            and entry.source_identity.startswith(identity_prefix)
            and entry.actionable
        ):
            matches.append(entry.source_identity)
    if len(matches) != 1:
        raise TodoContractError("workflow feedback human-task route is ambiguous")
    return tuple(matches)


def projection_todo_items(
    artifact: object, *, expected_source: TodoSource | None = None
) -> tuple[TodoItem, ...]:
    """Read normalized in-memory or Markdown Todo items from one artifact."""
    normalized = getattr(artifact, "items", None)
    if normalized is not None:
        items = tuple(normalized)
        if expected_source is not None and any(item.source != expected_source for item in items):
            raise TodoContractError(
                "Todo item source does not match the declared projection source"
            )
        return items
    path = Path(str(getattr(artifact, "path", artifact)))
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TodoContractError("authoritative Todo source is unreadable") from exc
    return parse_todo_list(content, expected_source=expected_source)
