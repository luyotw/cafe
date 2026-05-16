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


class StepInterrupted(Exception):
    """Raised when an in-flight step execution is interrupted (e.g. Ctrl-C or agent timeout)."""

    def __init__(self, step: str, hop: int = 0, reason: str = "interrupted") -> None:
        self.step = step
        self.hop = hop
        self.reason = reason
        super().__init__(f"Step '{step}' {reason} at hop {hop}")


class BatonRejected(Exception):
    """Raised when a baton payload contains an invalid enum value.

    Carries enough detail for the runtime to build a feedback prompt so the
    agent can correct the baton on retry.
    """

    def __init__(self, *, field: str, invalid_value: str, valid_values: list[str]) -> None:
        self.field = field
        self.invalid_value = invalid_value
        self.valid_values = list(valid_values)
        super().__init__(
            f"Invalid baton field '{field}': got '{invalid_value}', "
            f"valid values are {self.valid_values}"
        )
