"""Trusted provider adapters for native code-review discovery."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Mapping, Sequence

from cafe.agents.capabilities.contracts import CapabilityProvider, CapabilityRequest
from cafe.agents.capabilities.registry import CapabilityRegistry
from cafe.core.types import AgentCLI

REVIEW_DISCOVERY_CAPABILITY_ID = "code_review.discovery"


@dataclass(frozen=True)
class CodexReviewProvider:
    """Codex's built-in non-interactive review command."""

    capability_id: str = REVIEW_DISCOVERY_CAPABILITY_ID
    provider_id: str = "codex-review"
    cli: AgentCLI = AgentCLI.CODEX

    def probe_command(self, request: CapabilityRequest) -> Sequence[str]:
        del request
        return ("codex", "review", "--help")

    def accepts_probe(self, result: subprocess.CompletedProcess[str]) -> bool:
        text = f"{result.stdout or ''}\n{result.stderr or ''}"
        return result.returncode == 0 and "run a code review non-interactively" in text.lower()

    def build_command(self, request: CapabilityRequest) -> Sequence[str]:
        command = ["codex", "-C", str(request.project_root), "-a", "never"]
        if request.model:
            command.extend(["--model", request.model])
        command.extend(
            [
                "review",
                "--base",
                request.require_parameter("base_branch"),
                (
                    "Find all high-confidence defects introduced by this change. Review the "
                    "complete diff, do not modify files, ignore style-only nits, and include "
                    "specific file and line evidence for every finding."
                ),
            ]
        )
        return command

    def build_environment(self, request: CapabilityRequest) -> Mapping[str, str]:
        del request
        environment = dict(os.environ)
        for key in ("CODEX_REMOTE_PAYLOAD", "CODEX_SESSION_ID", "CODEX_THREAD_ID"):
            environment.pop(key, None)
        return environment

    def normalize_output(self, output: str) -> str:
        return output


@dataclass(frozen=True)
class ClaudeReviewProvider:
    """Claude's built-in ultrareview command."""

    capability_id: str = REVIEW_DISCOVERY_CAPABILITY_ID
    provider_id: str = "claude-ultrareview"
    cli: AgentCLI = AgentCLI.CLAUDE

    def probe_command(self, request: CapabilityRequest) -> Sequence[str]:
        del request
        return ("claude", "ultrareview", "--help")

    def accepts_probe(self, result: subprocess.CompletedProcess[str]) -> bool:
        text = f"{result.stdout or ''}\n{result.stderr or ''}"
        return result.returncode == 0 and "cloud-hosted multi-agent code review" in text.lower()

    def build_command(self, request: CapabilityRequest) -> Sequence[str]:
        return (
            "claude",
            "ultrareview",
            request.require_parameter("base_branch"),
            "--json",
            "--no-post",
            "--timeout",
            "10",
        )

    def build_environment(self, request: CapabilityRequest) -> Mapping[str, str]:
        del request
        return dict(os.environ)

    def normalize_output(self, output: str) -> str:
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as exc:
            raise ValueError("Claude ultrareview did not return JSON") from exc
        if not isinstance(payload, (dict, list)):
            raise ValueError("Claude ultrareview JSON must be an object or list")
        if isinstance(payload, dict) and payload.get("error"):
            raise ValueError("Claude ultrareview returned an error payload")
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


REVIEW_PROVIDERS: tuple[CapabilityProvider, ...] = (
    CodexReviewProvider(),
    ClaudeReviewProvider(),
)


def review_capability_registry() -> CapabilityRegistry:
    """Build an isolated registry so callers cannot mutate global routing."""
    return CapabilityRegistry(REVIEW_PROVIDERS)
