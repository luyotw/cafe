"""Shared workflow execution models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StepExecutionResult:
    """Normalized executor output for one step."""

    response: str
    artifacts: dict[str, str]
    status_code: str | None = None
    auto_continue: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PlaybookRunResult:
    """Result of one workflow run."""

    final_step: str
    final_status_code: str
    completed: bool
