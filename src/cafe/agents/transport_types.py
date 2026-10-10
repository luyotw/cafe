"""Compact, mode-neutral conversation transport values."""

from dataclasses import dataclass
from typing import Iterable, Literal

from cafe.core.types import TokenUsage

Operation = Literal[
    "acquire_session", "deliver_to_exact_session", "open_interactive_session", "run_one_shot"
]
Evidence = Literal["session", "model", "usage", "acceptance"]


def _validated_evidence_scalar(value: object, *, strip: bool = False) -> str:
    """Validate shared scalar bounds; only session identities are normalized."""
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError("evidence must be a bounded nonempty string")
    return value.strip() if strip else value


def _has_evidence_conflict(values: Iterable[str | None]) -> bool:
    """Compare known scalars without treating missing evidence as a conflict."""
    first = None
    for value in values:
        if value is None:
            continue
        if first is not None and value != first:
            return True
        first = value
    return False


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


@dataclass(frozen=True)
class AccountingScope:
    """Caller-admitted identity and durable sink; no execution or pricing authority."""

    workflow_id: str
    caller_id: str
    publish: object

    def __post_init__(self):
        _validated_evidence_scalar(self.workflow_id)
        _validated_evidence_scalar(self.caller_id)
        if not callable(self.publish):
            raise ValueError("accounting publication must be callable")
