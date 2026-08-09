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


def _rows(section: str, heading: str, columns: tuple[str, ...]) -> list[list[str]]:
    lines = section.splitlines()
    tables = [index for index, line in enumerate(lines) if line.startswith("|")]
    if len(tables) < 2:
        raise ContractValidationError(f"{heading} requires a non-empty table")
    header = [item.strip() for item in lines[tables[0]].strip().strip("|").split("|")]
    if tuple(header) != columns:
        raise ContractValidationError(f"{heading} has invalid columns")
    result: list[list[str]] = []
    for line in lines[tables[2]:]:
        if not line.startswith("|"):
            break
        row = [item.strip() for item in line.strip().strip("|").split("|")]
        if len(row) != len(columns) or not all(row):
            raise ContractValidationError(f"{heading} has an invalid row")
        result.append(row)
    if not result:
        raise ContractValidationError(f"{heading} requires rows")
    return result


def _required_references(text: str) -> Iterable[str]:
    return _ID.findall(text)


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
    starts = [match.start() for match in re.finditer(r"(?m)^## Downstream Contract\s*$", text)]
    if len(starts) != 1:
        raise ContractValidationError("Source must contain exactly one Downstream Contract")
    start = starts[0]
    following = re.search(r"(?m)^## (?!#)", text[start + 1 :])
    end = start + 1 + following.start() if following else len(text)
    exact = text[start:end].encode("utf-8")
    contract = text[start:end]
    match = re.match(r"## Downstream Contract\n\n- Contract-Version: `([0-9]+)`\n- Artifact-Kind: `([a-z]+)`\n", contract)
    if match is None or match.group(1) != "1" or match.group(2) != kind:
        raise ContractValidationError("Unsupported or mismatched contract declaration")
    cursor = match.end()
    if contract[cursor:].startswith("\n"):
        cursor += 1
    ids: set[str] = set()
    task_states: dict[str, str] = {}
    for index, (heading, columns, prefix) in enumerate(_SCHEMAS[kind]):
        expected = f"### {heading}\n"
        if not contract[cursor:].startswith(expected):
            raise ContractValidationError(f"Missing, duplicated, or reordered section: {heading}")
        next_cursor = contract.find("\n### ", cursor + len(expected))
        section_end = len(contract) if next_cursor == -1 else next_cursor + 1
        rows = _rows(contract[cursor:section_end], heading, columns)
        for row in rows:
            identifier = row[0]
            valid_prefixes = (prefix,) if isinstance(prefix, str) else prefix
            if not _ID.fullmatch(identifier) or not identifier.startswith(valid_prefixes) or identifier in ids:
                raise ContractValidationError(f"Invalid or duplicate ID: {identifier}")
            ids.add(identifier)
            if heading == "Task Status" and row[1] not in {"pending", "completed"}:
                raise ContractValidationError("Task Status must be pending or completed")
            if heading == "Task Status":
                task_states[identifier] = row[1]
        cursor = section_end
    if contract[cursor:].strip():
        raise ContractValidationError("Unexpected contract content")
    for heading, _columns, _prefix in _SCHEMAS[kind]:
        # references are only meaningful in dependency columns; all references
        # must resolve to a declared stable ID, except a plan task's own ID.
        if heading in {"Test List", "Dependency ADR References", "Task Status"}:
            position = contract.find(f"### {heading}")
            end_position = contract.find("\n### ", position + 1)
            for reference in _required_references(contract[position:end_position if end_position != -1 else len(contract)]):
                if reference not in ids:
                    raise ContractValidationError(f"Unresolved contract reference: {reference}")
    body_ids = set(_ID.findall(text[:start]))
    if body_ids - ids:
        raise ContractValidationError("Contract does not cover source stable IDs")
    if kind == "plan":
        body_task_states = {
            identifier: "completed" if marker.lower() == "x" else "pending"
            for marker, identifier in re.findall(
                r"(?m)^-\s+\[([ xX])\]\s+\*\*(TASK-[0-9]{3,})\*\*",
                text[:start],
            )
        }
        if body_task_states and body_task_states != task_states:
            raise ContractValidationError("Plan task state disagrees with complete plan")
    return DownstreamContract(kind=kind, version=1, bytes=exact, sha256=hashlib.sha256(exact).hexdigest(), ids=frozenset(ids))
