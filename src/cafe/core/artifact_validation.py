"""Producer-owned syntax diagnostics and a finite report-correction budget.

Only current-report parser failures qualify. File access, identity continuity
against earlier artifacts, and workflow authority remain the caller's concern.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from cafe.core.downstream_contract import visible_markdown
from cafe.core.todo import (
    PlanTodoDocument,
    PlanTodoDocumentKind,
    TodoContractError,
    TodoItem,
    parse_plan_todo_document,
    parse_todo_identity_continuity,
    parse_todo_list,
)

MAX_ARTIFACT_CORRECTIONS = 2
_REVIEW_REGISTRY_HEADER = (
    "id", "kind", "root-cause identity", "first-seen iteration/head", "impact",
    "confidence", "current status", "linked id", "evidence head",
)
_REVIEW_BLOCKER_ID = re.compile(r"BLK-\d{3}")
_FINDING_REGISTRY_HEADING = "## Finding Registry"
_MARKDOWN_SECTION = re.compile(r"^ {0,3}#{1,6}(?:[ \t]|$)")
_TABLE_DIVIDER = re.compile(r":?-+:?")


class ArtifactFormatError(ValueError):
    """A known syntax rejection of the current producer's declared report."""

    def __init__(self, producer: str, artifact: str, path: str, diagnostic: str):
        self.producer = producer
        self.artifact = artifact
        self.path = path
        self.diagnostic = diagnostic
        super().__init__(f"artifact {artifact!r} from {producer!r} at {path}: {diagnostic}")

    def to_dict(self) -> dict[str, str]:
        return {"producer": self.producer, "artifact": self.artifact,
                "path": self.path, "diagnostic": self.diagnostic}

    def correction_prompt(self, *, remaining: int) -> str:
        return (
            f"Correct only the current report for producer {self.producer!r}, "
            f"artifact {self.artifact!r}, at {self.path}.\n"
            f"Validation rejected it: {self.diagnostic}\n"
            f"Correction opportunities remaining after this call: {remaining}.\n"
            "Submit one self-contained current report preserving all substantive prior and "
            "current acceptance evidence and all follow-up proposals. "
            "Keep exactly one designated Todo List when required; do not append an older report.\n"
            "Keep the same phase, iteration, session, model chain, scope and authority. "
            "Do not alter checklist bytes, completion marks or pinned metadata. "
            "Do not repeat unrelated work, tests, commits, hooks or external actions. "
            "Do not infer answers to human confirmation, permission or scope decisions. "
            "Reread and resubmit the ordinary handoff; all existing gates still apply."
        )


@dataclass(frozen=True)
class ArtifactSyntax:
    plan_document: PlanTodoDocument | None = None
    items: tuple[TodoItem, ...] | None = None
    continuity: dict[str, str] = field(default_factory=dict)


def _review_registry_cells(line: str) -> tuple[str, ...]:
    cells = re.split(r"(?<!\\)\|", line.strip("|"))
    return tuple(cell.strip().strip("`") for cell in cells)


def review_finding_lineages(
    content: str, items: tuple[TodoItem, ...],
) -> dict[str, tuple[str, str]]:
    """Validate the optional registry and return stable identities for its open findings."""
    if _FINDING_REGISTRY_HEADING not in content:
        return {}
    lines = visible_markdown(content).splitlines()
    start = next(
        (i for i, line in enumerate(lines)
         if line.strip() == _FINDING_REGISTRY_HEADING and _MARKDOWN_SECTION.match(line)),
        None,
    )
    if start is None:
        return {}
    section = []
    for line in lines[start + 1:]:
        if _MARKDOWN_SECTION.match(line):
            break
        if line.strip():
            section.append(line.strip())
    # The existing table schema opts in. Legacy and custom reports can use prose
    # or other tables in a section with the same name.
    if not section:
        return {}
    header = tuple(cell.lower() for cell in _review_registry_cells(section[0]))
    if header != _REVIEW_REGISTRY_HEADER:
        return {}
    divider = _review_registry_cells(section[1]) if len(section) > 1 else ()
    if len(divider) != len(header) or not all(_TABLE_DIVIDER.fullmatch(cell) for cell in divider):
        raise TodoContractError("Finding Registry has an invalid table separator")

    blockers: dict[str, tuple[str, str]] = {}
    lineages = {}
    for line in section[2:]:
        cells = _review_registry_cells(line)
        if len(cells) == 1:
            break
        blocker = cells[0]
        if not _REVIEW_BLOCKER_ID.fullmatch(blocker):
            continue
        if len(cells) != len(_REVIEW_REGISTRY_HEADER) or cells[1] != "BLK":
            raise TodoContractError(f"Finding Registry blocker {blocker} has an invalid row")
        status, linked = cells[6:8]
        if blocker in blockers or status not in {"open", "closed"}:
            raise TodoContractError(
                f"Finding Registry blocker {blocker} has a duplicate ID or invalid status"
            )
        blockers[blocker] = (status, linked)
        if status == "open" and cells[2] and cells[3]:
            lineages[blocker] = (cells[2], cells[3])

    for blocker, (status, linked) in blockers.items():
        if not _REVIEW_BLOCKER_ID.fullmatch(linked):
            continue
        if linked not in blockers:
            raise TodoContractError(f"Finding Registry blocker {blocker} links to missing {linked}")
        linked_status, _ = blockers[linked]
        if {status, linked_status} == {"open", "closed"}:
            closed, opened = (blocker, linked) if status == "closed" else (linked, blocker)
            raise TodoContractError(
                f"Finding Registry marks {closed} closed but links it to open {opened}. "
                "Keep the original blocker ID open and put the remaining case and evidence "
                "in its Todo."
            )

    open_ids = {blocker for blocker, (status, _) in blockers.items() if status == "open"}
    todo_ids = {item.item_id for item in items if item.source == "review" and not item.checked}
    if open_ids != todo_ids:
        difference = ", ".join(sorted(open_ids ^ todo_ids))
        raise TodoContractError(
            f"Finding Registry and unchecked Review Todos disagree: {difference}"
        )
    return lineages


def validate_artifact_syntax(
    content: str, *, producer: str, artifact: str, path: str,
) -> ArtifactSyntax:
    """Apply the publication parser's existing selection rules without I/O."""
    first = next((line.strip() for line in content.splitlines() if line.strip()), "")
    plan = None
    items = None
    continuity: dict[str, str] = {}
    try:
        if first.startswith("<!-- plan-stage:"):
            plan = parse_plan_todo_document(content)
            items = plan.items
        elif "## Todo List" in content:
            items = parse_todo_list(content)
        if items is not None and (
            plan is None or plan.kind is not PlanTodoDocumentKind.PROVISIONAL_ALIGNMENT
        ):
            continuity = parse_todo_identity_continuity(content)
            review_finding_lineages(content, items)
    except TodoContractError as exc:
        raise ArtifactFormatError(producer, artifact, path, str(exc)) from exc
    return ArtifactSyntax(plan, items, continuity)


@dataclass
class ArtifactCorrectionBudget:
    """Persisted per-iteration sequence; a successful parse does not replenish it."""

    consumed: int = 0
    rejections: list[dict[str, str]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ArtifactCorrectionBudget:
        consumed = value.get("consumed", 0)
        rejections = value.get("rejections", [])
        if type(consumed) is not int or not 0 <= consumed <= MAX_ARTIFACT_CORRECTIONS:
            raise ValueError("invalid artifact correction count")
        if not isinstance(rejections, list) or any(
            not isinstance(item, dict)
            or set(item) != {"producer", "artifact", "path", "diagnostic"}
            or any(not isinstance(v, str) for v in item.values())
            for item in rejections
        ):
            raise ValueError("invalid artifact correction rejection history")
        return cls(consumed, [dict(item) for item in rejections])

    def to_dict(self) -> dict[str, Any]:
        return {"consumed": self.consumed, "rejections": list(self.rejections)}

    def reject(self, error: ArtifactFormatError) -> None:
        self.rejections.append(error.to_dict())

    def consume(self) -> None:
        if self.consumed >= MAX_ARTIFACT_CORRECTIONS:
            raise ArtifactCorrectionExhausted(self)
        self.consumed += 1


class ArtifactCorrectionExhausted(RuntimeError):
    """No automatic producer calls remain; use the existing user recovery route."""

    def __init__(self, budget: ArtifactCorrectionBudget):
        self.budget = budget
        latest = budget.rejections[-1]
        super().__init__(
            f"Artifact format correction exhausted after {budget.consumed} corrections; "
            f"producer {latest['producer']!r}, artifact {latest['artifact']!r}, "
            f"path {latest['path']}: {latest['diagnostic']}. "
            f"Rejection history: {budget.rejections}"
        )
