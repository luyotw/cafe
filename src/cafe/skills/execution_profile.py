"""Resolve provider-neutral execution requirements from skill selectors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from cafe.skills.contracts import ExecutionProfile
from cafe.skills.loader import SkillLoader
from cafe.skills.selectors import resolve_skill_selector, skill_selector_names

_REASONING_RANK = {"routine": 0, "standard": 1, "high": 2}
_FALLBACK_RANK = {"equivalent": 0, "equivalent_or_stronger": 1}

DEFAULT_EXECUTION_PROFILE = ExecutionProfile()


@dataclass(frozen=True)
class ResolvedExecutionProfile:
    """Effective requirements for one concrete or iteration-selected step."""

    skill_names: tuple[str, ...]
    workloads: tuple[str, ...]
    reasoning: str
    risk_domains: tuple[str, ...]
    fallback_strength: str
    uses_default: bool


def resolve_execution_profile(
    skill_loader: SkillLoader,
    selector: str | Mapping[str, str],
    *,
    iteration: Optional[int] = None,
) -> ResolvedExecutionProfile:
    """Resolve one iteration or conservatively aggregate every selector variant."""
    skill_names = (
        (resolve_skill_selector(selector, iteration),)
        if iteration is not None
        else skill_selector_names(selector)
    )
    profiles: list[ExecutionProfile] = []
    uses_default = False
    for skill_name in skill_names:
        declared = skill_loader.get_workflow_contract(skill_name).execution_profile
        if declared is None:
            uses_default = True
            declared = DEFAULT_EXECUTION_PROFILE
        profiles.append(declared)

    workloads = tuple(dict.fromkeys(profile.workload for profile in profiles))
    reasoning = max(profiles, key=lambda item: _REASONING_RANK[item.reasoning]).reasoning
    fallback_strength = max(
        profiles,
        key=lambda item: _FALLBACK_RANK[item.fallback_strength],
    ).fallback_strength
    risks = tuple(dict.fromkeys(risk for profile in profiles for risk in profile.risk_domains))
    return ResolvedExecutionProfile(
        skill_names=skill_names,
        workloads=workloads,
        reasoning=reasoning,
        risk_domains=risks,
        fallback_strength=fallback_strength,
        uses_default=uses_default,
    )
