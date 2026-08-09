"""Strict, source-owned downstream contract extraction."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


class ContractValidationError(ValueError):
    """The complete authority must be used instead of an unsafe packet."""


@dataclass(frozen=True)
class DownstreamContract:
    kind: str
    version: int
    bytes: bytes
    sha256: str
    ids: frozenset[str]


_SCHEMAS = {
    "spec": (
        ("Goals", ("ID", "Statement"), "GOAL-"),
        ("Non-Goals", ("ID", "Statement"), "NONGOAL-"),
        ("Acceptance Criteria", ("ID", "Priority", "Statement"), "AC-"),
        ("Invariants", ("ID", "Statement"), "INV-"),
        ("Trust Boundaries", ("ID", "Statement"), "TRUST-"),
    ),
    "plan": (
        ("Architecture Boundaries", ("ID", "Location", "Responsibility"), "ARCH-"),
        ("Invariants", ("ID", "Statement"), "INV-"),
        ("Test List", ("ID", "Type", "Covers"), ("UT-", "IT-")),
        ("Dependency ADR References", ("ID", "Decision", "Requirement / invariant"), "ADR-"),
        ("Task Status", ("ID", "Status", "Summary", "Depends On"), "TASK-"),
    ),
}
_ID = re.compile(r"[A-Z][A-Z0-9_]*-[0-9]{3,}")
_TABLE_SEPARATOR = re.compile(r":?-{3,}:?")


def _rows(section: str, heading: str, columns: tuple[str, ...]) -> list[list[str]]:
    lines = section.splitlines()
    table_start = next((index for index, line in enumerate(lines) if line.startswith("|")), None)
    if table_start is None or table_start + 2 >= len(lines):
        raise ContractValidationError(f"{heading} requires a non-empty table")
    header = [item.strip() for item in lines[table_start].strip().strip("|").split("|")]
    if tuple(header) != columns:
        raise ContractValidationError(f"{heading} has invalid columns")
    separator = [
        item.strip() for item in lines[table_start + 1].strip().strip("|").split("|")
    ]
    if len(separator) != len(columns) or not all(
        _TABLE_SEPARATOR.fullmatch(cell) for cell in separator
    ):
        raise ContractValidationError(f"{heading} has an invalid table separator")
    result: list[list[str]] = []
    for line in lines[table_start + 2 :]:
        if not line.startswith("|"):
            break
        row = [item.strip() for item in line.strip().strip("|").split("|")]
        if len(row) != len(columns) or not all(row):
            raise ContractValidationError(f"{heading} has an invalid row")
        result.append(row)
    if not result:
        raise ContractValidationError(f"{heading} requires a non-empty table")
    return result


def _required_references(text: str) -> Iterable[str]:
    return _ID.findall(text)


def _markdown_heading_positions(text: str, pattern: re.Pattern[str]) -> list[int]:
    """Return headings outside fenced examples, preserving source byte layout."""
    positions: list[int] = []
    offset = 0
    fence: str | None = None
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)[0]
            if fence is None:
                fence = token
            elif fence == token:
                fence = None
        elif fence is None and pattern.fullmatch(line.rstrip("\r\n")):
            positions.append(offset)
        offset += len(line)
    return positions


def _visible_markdown(text: str) -> str:
    """Exclude fenced examples from authoritative-body ID validation."""
    lines: list[str] = []
    fence: str | None = None
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)[0]
            if fence is None:
                fence = token
            elif fence == token:
                fence = None
        elif fence is None:
            lines.append(line)
    return "".join(lines)


def extract_downstream_contract(path: str | Path, *, kind: str) -> DownstreamContract:
    """Return the exact contract bytes after validating the fixed schema.

    Invalid or legacy sources raise ``ContractValidationError`` so callers can
    expose the original complete file as a relationship-local fallback.
    """
    if kind not in _SCHEMAS:
        raise ContractValidationError(f"Unsupported contract kind: {kind}")
    try:
        source = Path(path).read_bytes()
        text = source.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ContractValidationError("Unreadable contract source") from exc
    starts = _markdown_heading_positions(text, re.compile(r"## Downstream Contract\s*"))
    if len(starts) != 1:
        raise ContractValidationError("Source must contain exactly one Downstream Contract")
    start = starts[0]
    headings = _markdown_heading_positions(text, re.compile(r"## (?!#).+"))
    end = next((position for position in headings if position > start), len(text))
    exact = text[start:end].encode("utf-8")
    contract = text[start:end]
    match = re.match(
        r"## Downstream Contract\n\n- Contract-Version: `([0-9]+)`\n- Artifact-Kind: `([a-z]+)`\n",
        contract,
    )
    if match is None or match.group(1) != "1" or match.group(2) != kind:
        raise ContractValidationError("Unsupported or mismatched contract declaration")
    cursor = match.end()
    if contract[cursor:].startswith("\n"):
        cursor += 1
    ids: set[str] = set()
    task_states: dict[str, str] = {}
    sections: dict[str, list[list[str]]] = {}
    for index, (heading, columns, prefix) in enumerate(_SCHEMAS[kind]):
        expected = f"### {heading}\n"
        if not contract[cursor:].startswith(expected):
            raise ContractValidationError(f"Missing, duplicated, or reordered section: {heading}")
        next_cursor = contract.find("\n### ", cursor + len(expected))
        section_end = len(contract) if next_cursor == -1 else next_cursor + 1
        rows = _rows(contract[cursor:section_end], heading, columns)
        sections[heading] = rows
        for row in rows:
            identifier = row[0]
            valid_prefixes = (prefix,) if isinstance(prefix, str) else prefix
            if (
                not _ID.fullmatch(identifier)
                or not identifier.startswith(valid_prefixes)
                or identifier in ids
            ):
                raise ContractValidationError(f"Invalid or duplicate ID: {identifier}")
            ids.add(identifier)
            if heading == "Task Status" and row[1] not in {"pending", "completed"}:
                raise ContractValidationError("Task Status must be pending or completed")
            if heading == "Task Status":
                task_states[identifier] = row[1]
        cursor = section_end
    if contract[cursor:].strip():
        raise ContractValidationError("Unexpected contract content")
    if kind == "plan":
        _validate_plan_references(sections, ids)
    body = _visible_markdown(text[:start] + text[end:])
    if kind == "plan":
        body_task_states = {
            identifier: "completed" if marker.lower() == "x" else "pending"
            for marker, identifier in re.findall(
                r"(?m)^-\s+\[([ xX])\]\s+\*\*(TASK-[0-9]{3,})\*\*",
                body,
            )
        }
        if not body_task_states:
            raise ContractValidationError(
                "Plan must declare top-level task state in its authoritative body"
            )
        if body_task_states != task_states:
            raise ContractValidationError("Plan task state disagrees with complete plan")
    body_ids = set(_ID.findall(body))
    if not body_ids:
        raise ContractValidationError("Source must declare stable IDs in its authoritative body")
    if body_ids != ids:
        raise ContractValidationError("Contract does not cover source stable IDs")
    return DownstreamContract(
        kind=kind,
        version=1,
        bytes=exact,
        sha256=hashlib.sha256(exact).hexdigest(),
        ids=frozenset(ids),
    )


def _validate_plan_references(sections: dict[str, list[list[str]]], ids: set[str]) -> None:
    """Validate reference columns against the kinds mandated by the schema."""
    allowed = {
        "Test List": (2, ("INV-",)),
        "Dependency ADR References": (2, ("INV-",)),
        "Task Status": (3, ("TASK-",)),
    }
    for section, (column, prefixes) in allowed.items():
        for row in sections[section]:
            references = list(_required_references(row[column]))
            if not references:
                if section == "Task Status" and row[column] == "—":
                    continue
                raise ContractValidationError(f"{section} requires a reference")
            for reference in references:
                if reference not in ids or not reference.startswith(prefixes):
                    raise ContractValidationError(
                        f"{section} has an invalid reference: {reference}"
                    )
