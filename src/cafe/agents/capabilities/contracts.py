"""Contracts for registered agent-native providers and skill fallbacks."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping, Protocol, Sequence

from cafe.core.types import AgentCLI

CapabilityMode = Literal["native_command", "fallback_skill"]
CapabilityOutcome = Literal["completed", "failed"]


@dataclass(frozen=True)
class CapabilityRequest:
    """Provider-neutral inputs for one registered host capability."""

    capability_id: str
    cli: AgentCLI
    project_root: Path
    label: str
    model: str | None = None
    parameters: Mapping[str, str] = field(default_factory=dict)

    def require_parameter(self, name: str) -> str:
        """Return one required feature parameter or expose a host integration defect."""
        value = self.parameters.get(name)
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"capability {self.capability_id!r} requires parameter {name!r}"
            )
        return value


class CapabilityProvider(Protocol):
    """One trusted provider registered in CAFE source code."""

    @property
    def capability_id(self) -> str:
        """Stable capability route identifier."""

    @property
    def provider_id(self) -> str:
        """Stable native provider identifier."""

    @property
    def cli(self) -> AgentCLI:
        """Agent CLI that exposes this native provider."""

    def probe_command(self, request: CapabilityRequest) -> Sequence[str]:
        """Build the bounded compatibility probe command."""

    def accepts_probe(self, result: subprocess.CompletedProcess[str]) -> bool:
        """Return whether the installed CLI exposes the expected native surface."""

    def build_command(self, request: CapabilityRequest) -> Sequence[str]:
        """Build the non-interactive native invocation."""

    def build_environment(self, request: CapabilityRequest) -> Mapping[str, str]:
        """Build the isolated child environment."""

    def normalize_output(self, output: str) -> str:
        """Validate and normalize successful provider output."""


@dataclass(frozen=True)
class CapabilityFallback:
    """A prepared skill invocation selected when native execution is unavailable."""

    provider_id: str
    invocation: str


@dataclass(frozen=True)
class CapabilityTelemetry:
    """Unmetered accounting for a native command outside AgentManager."""

    capability_id: str
    provider_id: str
    cli: AgentCLI
    outcome: CapabilityOutcome
    duration_ms: int
    tokens_metered: Literal[False] = False
    cost_metered: Literal[False] = False


@dataclass(frozen=True)
class CapabilitySelection:
    """Resolved native output or deterministic skill fallback."""

    capability_id: str
    provider_id: str
    mode: CapabilityMode
    output: str | None = None
    fallback_invocation: str | None = None
    fallback_reason: str | None = None
    telemetry: CapabilityTelemetry | None = None
