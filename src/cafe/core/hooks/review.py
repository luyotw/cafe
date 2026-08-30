"""Trusted host orchestration selected by the cafe-review skill."""

from __future__ import annotations

import errno
import json
import os
import stat
from pathlib import Path
from typing import Any, Sequence

from cafe.core.hooks import HookResult
from cafe.core.types import AgentCLI
from cafe.skills.native_bridge import NativeSkillBridge
from cafe.skills.review_engines import ReviewEngineContext, ReviewEngineService
from cafe.skills.review_fallback import ReviewFallbackUpdater


class ReviewDiscoveryHook:
    """Prepare a native or portable candidate-finding engine before review."""

    name = "ReviewDiscoveryHook"
    skill_stages = frozenset({"prepare_input"})
    service_factory = ReviewEngineService

    @staticmethod
    def _read_existing_stats(path: Path) -> dict[str, Any]:
        try:
            mode = os.lstat(path).st_mode
        except FileNotFoundError:
            return {}
        if not stat.S_ISREG(mode):
            return {}
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError as exc:
            if exc.errno in {errno.ENOENT, errno.ELOOP}:
                return {}
            raise
        try:
            with os.fdopen(descriptor, "rb") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    return {}
                raw = handle.read()
        except OSError:
            return {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        if not isinstance(parsed, dict) or not isinstance(parsed.get("stats"), dict):
            return {}
        return dict(parsed["stats"])

    @staticmethod
    def _instructions(context: ReviewEngineContext) -> str:
        lines = [
            "Review discovery engine:",
            f"- id: {context.engine_id}",
            f"- mode: {context.mode}",
            context.guidance,
            "The discovery engine supplies candidates only. Complete the phase skill's full "
            "acceptance, risk, ledger, and handoff procedure even when discovery already found "
            "a blocker.",
        ]
        return "\n".join(lines)

    def run(self, **kwargs: Any) -> HookResult:
        phase = kwargs.get("phase")
        bridge = getattr(getattr(phase, "generic_phase", None), "skill_bridge", None)
        if not isinstance(bridge, NativeSkillBridge):
            raise RuntimeError("ReviewDiscoveryHook requires the native skill bridge")

        primary_cli = kwargs.get("runtime_agent_cli")
        if not isinstance(primary_cli, AgentCLI):
            raise RuntimeError("ReviewDiscoveryHook requires runtime_agent_cli")
        raw_clis = kwargs.get("runtime_agent_clis")
        agent_clis: Sequence[AgentCLI] = (
            raw_clis
            if isinstance(raw_clis, (list, tuple))
            and all(isinstance(cli, AgentCLI) for cli in raw_clis)
            else (primary_cli,)
        )

        fallback_skill_name = ReviewEngineService.FALLBACK_SKILL_NAME
        fallback_invocations: dict[AgentCLI, str] = {}
        for cli in agent_clis:
            bridge.install_builtin_skill(
                fallback_skill_name,
                cli,
                verifier=lambda skill_dir: ReviewFallbackUpdater(skill_dir).verify_local(),
            )
            fallback_invocations[cli] = bridge.get_builtin_invocation(
                fallback_skill_name,
                cli,
            )
        fallback_invocation = bridge.provider_aware_invocation(fallback_invocations)

        project_root = kwargs.get("runtime_project_root")
        iteration_dir = kwargs.get("iteration_dir")
        base_branch = kwargs.get("runtime_base_branch")
        if not isinstance(project_root, Path) or not isinstance(iteration_dir, Path):
            raise RuntimeError("ReviewDiscoveryHook requires runtime paths")
        if not isinstance(base_branch, str) or not base_branch:
            raise RuntimeError("ReviewDiscoveryHook requires runtime_base_branch")

        engine_context = self.service_factory().prepare(
            cli=primary_cli,
            project_root=project_root,
            base_branch=base_branch,
            model=kwargs.get("runtime_agent_model"),
            evidence_file=iteration_dir / "review_engine.md",
            fallback_invocation=fallback_invocation,
            enable_native=bool(kwargs.get("runtime_native_execution_enabled")),
        )

        discovery_record: dict[str, Any] = {
            "engine_id": engine_context.engine_id,
            "mode": engine_context.mode,
        }
        if engine_context.fallback_reason is not None:
            discovery_record["fallback_reason"] = engine_context.fallback_reason

        phase_specific_data = kwargs.get("phase_specific_data")
        if isinstance(phase_specific_data, dict):
            if engine_context.telemetry is not None:
                unmetered = engine_context.telemetry.to_dict()
                discovery_record["native_invocation"] = unmetered
                context_file = kwargs.get("iteration_context_file")
                existing_stats = (
                    self._read_existing_stats(context_file)
                    if isinstance(context_file, Path)
                    else {}
                )
                existing_stats["usage_complete"] = False
                prior = existing_stats.get("unmetered_invocations")
                invocations = list(prior) if isinstance(prior, list) else []
                invocations.append(unmetered)
                existing_stats["unmetered_invocations"] = invocations
                phase_specific_data["stats"] = existing_stats
            phase_specific_data["review_discovery"] = discovery_record

        return HookResult(
            prompt_instructions=[self._instructions(engine_context)],
            context_updates={
                "review_engine_id": engine_context.engine_id,
                "review_engine_mode": engine_context.mode,
            },
            events=[
                {
                    "type": "review_discovery",
                    "engine_id": engine_context.engine_id,
                    "mode": engine_context.mode,
                }
            ],
        )
