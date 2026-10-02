"""Compact, mode-neutral conversation transport values."""

from dataclasses import dataclass
from typing import Literal

from cafe.core.types import TokenUsage

Operation = Literal[
    "acquire_session", "deliver_to_exact_session", "open_interactive_session", "run_one_shot"
]
Evidence = Literal["session", "model", "usage", "acceptance"]


@dataclass(frozen=True)
class TransportCapabilities:
    supported: bool = False
    session: bool = False
    model: bool = False
    usage: bool = False
    acceptance: bool = False


@dataclass(frozen=True)
class TransportResult:
    observed_session_id: str | None = None
    reported_model: str | None = None
    accepted: bool | None = None
    completed: bool | None = None
    usage: TokenUsage | None = None
    failure_code: str | None = None
    error_excerpt: str | None = None
    returncode: int | None = None
