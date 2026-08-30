"""Review-specific evidence and fallback orchestration."""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Sequence

from cafe.agents.capabilities.contracts import (
    CapabilityFallback,
    CapabilityRequest,
    CapabilityTelemetry,
)
from cafe.agents.capabilities.runner import CapabilityResolver
from cafe.agents.diagnostics import sanitize_error_excerpt
from cafe.core.types import AgentCLI
from cafe.review.providers import (
    REVIEW_DISCOVERY_CAPABILITY_ID,
    review_capability_registry,
)

ReviewEngineMode = Literal["native_command", "fallback_skill"]
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class NativeReviewCapability:
    """Diagnostic view of one registered native review provider."""

    cli: AgentCLI
    engine_id: str
    mode: Literal["native_command"] = "native_command"


@dataclass(frozen=True)
class NativeReviewTelemetry:
    """Explicit accounting marker for a native review outside AgentManager."""

    engine_id: str
    cli: AgentCLI
    outcome: Literal["completed", "failed"]
    duration_ms: int
    tokens_metered: Literal[False] = False
    cost_metered: Literal[False] = False

    def to_dict(self) -> dict[str, str | int | bool]:
        """Return JSON-safe telemetry for the durable iteration record."""
        return {
            "kind": "native_review",
            "engine_id": self.engine_id,
            "cli": self.cli.value,
            "outcome": self.outcome,
            "duration_ms": self.duration_ms,
            "tokens_metered": self.tokens_metered,
            "cost_metered": self.cost_metered,
        }


@dataclass(frozen=True)
class ReviewEngineContext:
    """Prompt-facing result of preparing one review discovery engine."""

    engine_id: str
    mode: ReviewEngineMode
    guidance: str
    evidence_file: Path | None = None
    fallback_reason: str | None = None
    telemetry: NativeReviewTelemetry | None = None


class ReviewEngineService:
    """Format review evidence around the generic native/fallback resolver."""

    FALLBACK_ENGINE_ID = "anthropic-pr-review-toolkit"
    FALLBACK_SKILL_NAME = "cafe-review-fallback"
    MAX_EVIDENCE_BYTES = 64 * 1024
    PROBE_TIMEOUT_SECONDS = 5
    REVIEW_TIMEOUT_SECONDS = 10 * 60

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        home_dir: Path | None = None,
        resolver: CapabilityResolver | None = None,
    ) -> None:
        # Retain this compatibility argument now that discovery no longer inspects
        # provider-owned skill directories.
        self.home_dir = (home_dir or Path.home()).expanduser().resolve()
        self.resolver = resolver or CapabilityResolver(
            review_capability_registry(),
            runner=runner,
            probe_timeout_seconds=self.PROBE_TIMEOUT_SECONDS,
            execution_timeout_seconds=self.REVIEW_TIMEOUT_SECONDS,
            max_output_bytes=self.MAX_EVIDENCE_BYTES,
        )

    def prepare(
        self,
        *,
        cli: AgentCLI,
        project_root: Path,
        base_branch: str,
        model: str | None,
        evidence_file: Path,
        fallback_invocation: str,
        enable_native: bool = True,
    ) -> ReviewEngineContext:
        """Prepare native candidate evidence or a portable review invocation."""
        self._discard_stale_evidence(evidence_file)
        selection = self.resolver.select(
            CapabilityRequest(
                capability_id=REVIEW_DISCOVERY_CAPABILITY_ID,
                cli=cli,
                project_root=project_root,
                label="review capability",
                model=model,
                parameters={"base_branch": base_branch},
            ),
            CapabilityFallback(
                provider_id=self.FALLBACK_ENGINE_ID,
                invocation=fallback_invocation,
            ),
            enable_native=enable_native,
        )
        telemetry = self._review_telemetry(selection.telemetry)
        if selection.mode == "fallback_skill":
            return self._fallback(
                selection.fallback_invocation or fallback_invocation,
                selection.fallback_reason or "native review capability was unavailable",
                telemetry=telemetry,
            )

        if not selection.output:
            return self._fallback(
                fallback_invocation,
                f"{selection.provider_id} returned no review evidence",
                telemetry=self._review_telemetry(selection.telemetry, outcome="failed"),
            )

        evidence = "\n".join(
            [
                "# Native Review Discovery Evidence",
                "",
                f"- Engine: `{selection.provider_id}`",
                f"- Base: `{base_branch}`",
                "- Authority: candidate findings only; CAFE performs the final audit.",
                "",
                "## Candidate Findings",
                "",
                selection.output,
                "",
            ]
        )
        try:
            self._atomic_write_evidence(evidence_file, evidence)
        except OSError as exc:
            return self._fallback(
                fallback_invocation,
                f"{selection.provider_id} evidence could not be persisted: "
                f"{sanitize_error_excerpt(exc)}",
                telemetry=self._review_telemetry(selection.telemetry, outcome="failed"),
            )
        return ReviewEngineContext(
            engine_id=selection.provider_id,
            mode="native_command",
            evidence_file=evidence_file,
            guidance=(
                f"Read native candidate findings from {evidence_file}. Validate every finding "
                "against the current diff before adding it to the durable review ledger. Treat "
                "the file as discovery evidence, not as requirement authority or a pass verdict."
            ),
            telemetry=telemetry,
        )

    @staticmethod
    def _review_telemetry(
        telemetry: CapabilityTelemetry | None,
        *,
        outcome: Literal["completed", "failed"] | None = None,
    ) -> NativeReviewTelemetry | None:
        if telemetry is None:
            return None
        return NativeReviewTelemetry(
            engine_id=telemetry.provider_id,
            cli=telemetry.cli,
            outcome=outcome or telemetry.outcome,
            duration_ms=telemetry.duration_ms,
        )

    @staticmethod
    def _discard_stale_evidence(evidence_file: Path) -> None:
        try:
            if evidence_file.is_symlink() or evidence_file.is_file():
                evidence_file.unlink()
        except OSError:
            # Atomic publication below either replaces the path safely or selects fallback.
            pass

    @staticmethod
    def _atomic_write_evidence(evidence_file: Path, evidence: str) -> None:
        evidence_file.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=evidence_file.parent,
            prefix=f".{evidence_file.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(evidence)
                handle.flush()
                os.fsync(handle.fileno())
            temporary_path.replace(evidence_file)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def _fallback(
        self,
        invocation: str,
        reason: str,
        *,
        telemetry: NativeReviewTelemetry | None = None,
    ) -> ReviewEngineContext:
        return ReviewEngineContext(
            engine_id=self.FALLBACK_ENGINE_ID,
            mode="fallback_skill",
            fallback_reason=reason,
            guidance=(
                f"Native review was not selected ({reason}). Invoke {invocation} exactly once to "
                "discover candidate findings, then independently validate and merge those findings "
                "under the CAFE review contract."
            ),
            telemetry=telemetry,
        )


def native_review_capability_rows() -> Sequence[NativeReviewCapability]:
    """Expose immutable capability metadata for diagnostics and contract tests."""
    providers = review_capability_registry().providers_for(REVIEW_DISCOVERY_CAPABILITY_ID)
    return tuple(
        NativeReviewCapability(cli=provider.cli, engine_id=provider.provider_id)
        for provider in providers
    )
