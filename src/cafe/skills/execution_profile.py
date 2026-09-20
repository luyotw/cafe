"""Resolve provider-neutral execution requirements from skill selectors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

from cafe.skills.loader import SkillLoader
from cafe.skills.selectors import resolve_skill_selector, skill_selector_names
from cafe.skills.workflow_composition import resolve_step_workflow_composition

_REASONING_RANK = {"routine": 0, "standard": 1, "high": 2}
_FALLBACK_RANK = {"equivalent": 0, "equivalent_or_stronger": 1}

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
    workflow_skills: Iterable[str] = (),
    step_name: str = "<execution-profile>",
) -> ResolvedExecutionProfile:
    """Resolve one iteration or conservatively aggregate every selector variant."""
    skill_names = (
        (resolve_skill_selector(selector, iteration),)
        if iteration is not None
        else skill_selector_names(selector)
    )
    uses_default = False
    composed_skill_names: list[str] = []
    workloads: list[str] = []
    reasonings: list[str] = []
    fallbacks: list[str] = []
    risks: list[str] = []
    for skill_name in skill_names:
        composition = resolve_step_workflow_composition(
            skill_loader,
            primary_skill=skill_name,
            workflow_skills=workflow_skills,
            step_name=step_name,
        )
        composed_skill_names.extend(
            name for name in composition.skill_names if name not in composed_skill_names
        )
        requirements = composition.execution_requirements
        uses_default = uses_default or requirements.uses_default
        workloads.extend(
            workload for workload in requirements.workloads if workload not in workloads
        )
        reasonings.append(requirements.reasoning)
        fallbacks.append(requirements.fallback_strength)
        risks.extend(risk for risk in requirements.risk_domains if risk not in risks)
    reasoning = max(reasonings, key=_REASONING_RANK.__getitem__)
    fallback_strength = max(fallbacks, key=_FALLBACK_RANK.__getitem__)
    return ResolvedExecutionProfile(
        skill_names=tuple(composed_skill_names),
        workloads=tuple(workloads),
        reasoning=reasoning,
        risk_domains=tuple(risks),
        fallback_strength=fallback_strength,
        uses_default=uses_default,
    )
