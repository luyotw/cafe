"""Resolve and execute provider-native code-review discovery engines."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Mapping, Sequence

from cafe.agents.diagnostics import sanitize_error_excerpt
from cafe.core.types import AgentCLI

ReviewEngineMode = Literal["native_command", "fallback_skill"]
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class NativeReviewCapability:
    """One tested, non-interactive review surface exposed by an agent CLI."""

    cli: AgentCLI
    engine_id: str
    mode: Literal["native_command"]
    probe_command: tuple[str, ...] = ()
    probe_marker: str = ""
    invocation: str = ""


@dataclass(frozen=True)
class ReviewEngineContext:
    """Prompt-facing result of preparing one review discovery engine."""

    engine_id: str
    mode: ReviewEngineMode
    guidance: str
    evidence_file: Path | None = None
    fallback_reason: str | None = None
    telemetry: NativeReviewTelemetry | None = None


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


NATIVE_REVIEW_CAPABILITIES: Mapping[AgentCLI, NativeReviewCapability] = {
    AgentCLI.CODEX: NativeReviewCapability(
        cli=AgentCLI.CODEX,
        engine_id="codex-review",
        mode="native_command",
        probe_command=("codex", "review", "--help"),
        probe_marker="Run a code review non-interactively",
    ),
    AgentCLI.CLAUDE: NativeReviewCapability(
        cli=AgentCLI.CLAUDE,
        engine_id="claude-ultrareview",
        mode="native_command",
        probe_command=("claude", "ultrareview", "--help"),
        probe_marker="cloud-hosted multi-agent code review",
    ),
}


class ReviewEngineService:
    """Prefer a compatible native reviewer and fail over to the bundled skill."""

    FALLBACK_ENGINE_ID = "anthropic-pr-review-toolkit"
    FALLBACK_SKILL_NAME = "cafe-review-fallback"
    MAX_EVIDENCE_BYTES = 64 * 1024
    PROBE_TIMEOUT_SECONDS = 5
    REVIEW_TIMEOUT_SECONDS = 10 * 60

    def __init__(
        self,
        *,
        runner: CommandRunner = subprocess.run,
        home_dir: Path | None = None,
    ) -> None:
        self.runner = runner
        self.home_dir = (home_dir or Path.home()).expanduser().resolve()

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
        """Prepare evidence or an invocation for the selected review engine."""
        self._discard_stale_evidence(evidence_file)
        if not enable_native:
            return self._fallback(
                fallback_invocation,
                "native execution is disabled for mock or synthetic agents",
            )

        capability = NATIVE_REVIEW_CAPABILITIES.get(cli)
        if capability is None:
            return self._fallback(
                fallback_invocation,
                f"{cli.value} has no compatible native review capability",
            )

        probe_error = self._probe(capability, project_root)
        if probe_error is not None:
            return self._fallback(fallback_invocation, probe_error)

        command = self._build_native_command(
            capability,
            project_root=project_root,
            base_branch=base_branch,
            model=model,
        )
        started_at = time.monotonic()
        try:
            result = self.runner(
                command,
                cwd=project_root,
                env=self._environment_for(cli),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=self.REVIEW_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
            return self._fallback(
                fallback_invocation,
                f"{capability.engine_id} failed: {sanitize_error_excerpt(exc)}",
                telemetry=self._telemetry(capability, started_at, outcome="failed"),
            )

        output = (result.stdout or "").strip()
        if result.returncode != 0 or not output:
            detail = (result.stderr or output or f"exit code {result.returncode}").strip()
            error = RuntimeError(detail)
            return self._fallback(
                fallback_invocation,
                f"{capability.engine_id} was unusable: {sanitize_error_excerpt(error)}",
                telemetry=self._telemetry(capability, started_at, outcome="failed"),
            )
        try:
            output = self._normalize_native_output(capability, output)
        except ValueError as exc:
            return self._fallback(
                fallback_invocation,
                f"{capability.engine_id} returned incompatible output: "
                f"{sanitize_error_excerpt(exc)}",
                telemetry=self._telemetry(capability, started_at, outcome="failed"),
            )

        bounded = self._bounded_evidence(output)
        evidence = "\n".join(
            [
                "# Native Review Discovery Evidence",
                "",
                f"- Engine: `{capability.engine_id}`",
                f"- Base: `{base_branch}`",
                "- Authority: candidate findings only; CAFE performs the final audit.",
                "",
                "## Candidate Findings",
                "",
                bounded,
                "",
            ]
        )
        try:
            self._atomic_write_evidence(evidence_file, evidence)
        except OSError as exc:
            return self._fallback(
                fallback_invocation,
                f"{capability.engine_id} evidence could not be persisted: "
                f"{sanitize_error_excerpt(exc)}",
                telemetry=self._telemetry(capability, started_at, outcome="failed"),
            )
        return ReviewEngineContext(
            engine_id=capability.engine_id,
            mode="native_command",
            evidence_file=evidence_file,
            guidance=(
                f"Read native candidate findings from {evidence_file}. Validate every finding "
                "against the current diff before adding it to the durable review ledger. Treat "
                "the file as discovery evidence, not as requirement authority or a pass verdict."
            ),
            telemetry=self._telemetry(capability, started_at, outcome="completed"),
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

    @staticmethod
    def _telemetry(
        capability: NativeReviewCapability,
        started_at: float,
        *,
        outcome: Literal["completed", "failed"],
    ) -> NativeReviewTelemetry:
        duration_ms = max(0, round((time.monotonic() - started_at) * 1000))
        return NativeReviewTelemetry(
            engine_id=capability.engine_id,
            cli=capability.cli,
            outcome=outcome,
            duration_ms=duration_ms,
        )

    def _probe(
        self,
        capability: NativeReviewCapability,
        project_root: Path,
    ) -> str | None:
        try:
            result = self.runner(
                list(capability.probe_command),
                cwd=project_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=self.PROBE_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
            return f"{capability.engine_id} probe failed: {sanitize_error_excerpt(exc)}"
        text = f"{result.stdout or ''}\n{result.stderr or ''}"
        if result.returncode != 0 or capability.probe_marker.lower() not in text.lower():
            return f"{capability.engine_id} did not satisfy its compatibility probe"
        return None

    @staticmethod
    def _normalize_native_output(
        capability: NativeReviewCapability,
        output: str,
    ) -> str:
        if capability.cli != AgentCLI.CLAUDE:
            return output
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as exc:
            raise ValueError("Claude ultrareview did not return JSON") from exc
        if not isinstance(payload, (dict, list)):
            raise ValueError("Claude ultrareview JSON must be an object or list")
        if isinstance(payload, dict) and payload.get("error"):
            raise ValueError("Claude ultrareview returned an error payload")
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)

    @classmethod
    def _build_native_command(
        cls,
        capability: NativeReviewCapability,
        *,
        project_root: Path,
        base_branch: str,
        model: str | None,
    ) -> list[str]:
        if capability.cli == AgentCLI.CODEX:
            command = ["codex", "-C", str(project_root), "-a", "never"]
            if model:
                command.extend(["--model", model])
            command.extend(
                [
                    "review",
                    "--base",
                    base_branch,
                    (
                        "Find all high-confidence defects introduced by this change. Review the "
                        "complete diff, do not modify files, ignore style-only nits, and include "
                        "specific file and line evidence for every finding."
                    ),
                ]
            )
            return command
        if capability.cli == AgentCLI.CLAUDE:
            return [
                "claude",
                "ultrareview",
                base_branch,
                "--json",
                "--no-post",
                "--timeout",
                "10",
            ]
        raise ValueError(f"unsupported native command engine: {capability.engine_id}")

    @staticmethod
    def _environment_for(cli: AgentCLI) -> dict[str, str]:
        environment = dict(os.environ)
        if cli == AgentCLI.CODEX:
            for key in ("CODEX_REMOTE_PAYLOAD", "CODEX_SESSION_ID", "CODEX_THREAD_ID"):
                environment.pop(key, None)
        return environment

    @classmethod
    def _bounded_evidence(cls, output: str) -> str:
        encoded = output.encode("utf-8")
        if len(encoded) <= cls.MAX_EVIDENCE_BYTES:
            return output
        truncated = encoded[: cls.MAX_EVIDENCE_BYTES].decode("utf-8", errors="ignore")
        return f"{truncated}\n\n[CAFE truncated native review output at 64 KiB]"


def native_review_capability_rows() -> Sequence[NativeReviewCapability]:
    """Expose immutable capability metadata for diagnostics and contract tests."""
    return tuple(NATIVE_REVIEW_CAPABILITIES.values())
