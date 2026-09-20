"""Resolve one source-aware workflow declaration for a playbook step."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from cafe.core.human_tasks import HumanTaskPolicy
from cafe.skills.contracts import (
    ExecutionProfile,
    PromptInputContract,
    SkillWorkflowDeclaration,
)
from cafe.skills.loader import SkillLoader

_REASONING_RANK = {"routine": 0, "standard": 1, "high": 2}
_FALLBACK_RANK = {"equivalent": 0, "equivalent_or_stronger": 1}
DEFAULT_EXECUTION_PROFILE = ExecutionProfile()


class WorkflowCompositionError(ValueError):
    """Raised when independently authored declarations cannot compose safely."""


@dataclass(frozen=True)
class WorkflowDeclarationSource:
    """Catalog and file provenance for one workflow declaration."""

    skill_identity: str
    catalog_source: str
    skill_root: Path
    declaration_file: Path
    field_location: str = "workflow"


@dataclass(frozen=True)
class SkillWorkflowContributor:
    """One primary or injected declaration retained with provenance."""

    source: WorkflowDeclarationSource
    declaration: SkillWorkflowDeclaration
    primary: bool = False


@dataclass(frozen=True)
class ComposedExecutionRequirements:
    """Conservative execution requirements across actual contributors."""

    workloads: tuple[str, ...]
    reasoning: str
    risk_domains: tuple[str, ...]
    fallback_strength: str
    uses_default: bool


@dataclass(frozen=True)
class StepWorkflowComposition:
    """Normalized declaration metadata used by every step-contract consumer."""

    step_name: str
    contributors: tuple[SkillWorkflowContributor, ...]
    required_tools: tuple[str, ...]
    prompt_inputs: tuple[PromptInputContract, ...]
    human_tasks: tuple[HumanTaskPolicy, ...]
    execution_requirements: ComposedExecutionRequirements

    @property
    def skill_names(self) -> tuple[str, ...]:
        return tuple(item.source.skill_identity for item in self.contributors)

    def as_declaration(self) -> SkillWorkflowDeclaration:
        """Expose supported effective fields through the legacy declaration API."""
        return SkillWorkflowDeclaration(
            required_tools=self.required_tools,
            prompt_inputs=self.prompt_inputs,
            human_tasks=self.human_tasks,
        )


def _same(left: object, right: object) -> bool:
    return getattr(left, "model_dump")(mode="json") == getattr(right, "model_dump")(
        mode="json"
    )


def _source_label(contributor: SkillWorkflowContributor) -> str:
    source = contributor.source
    return f"{source.skill_identity!r} ({source.declaration_file}:{source.field_location})"


def _conflict(
    *,
    step_name: str,
    field: str,
    key: str,
    first: SkillWorkflowContributor,
    second: SkillWorkflowContributor,
) -> WorkflowCompositionError:
    return WorkflowCompositionError(
        f"Step {step_name!r} has conflicting workflow {field}.{key} declarations from "
        f"{_source_label(first)} and {_source_label(second)}"
    )


def _execution_requirements(
    contributors: tuple[SkillWorkflowContributor, ...],
) -> ComposedExecutionRequirements:
    primary_profile = contributors[0].declaration.execution_profile
    uses_default = primary_profile is None
    profiles: list[ExecutionProfile] = [primary_profile or DEFAULT_EXECUTION_PROFILE]
    profiles.extend(
        contributor.declaration.execution_profile
        for contributor in contributors[1:]
        if contributor.declaration.execution_profile is not None
    )
    return ComposedExecutionRequirements(
        workloads=tuple(dict.fromkeys(profile.workload for profile in profiles)),
        reasoning=max(profiles, key=lambda profile: _REASONING_RANK[profile.reasoning]).reasoning,
        risk_domains=tuple(
            dict.fromkeys(risk for profile in profiles for risk in profile.risk_domains)
        ),
        fallback_strength=max(
            profiles,
            key=lambda profile: _FALLBACK_RANK[profile.fallback_strength],
        ).fallback_strength,
        uses_default=uses_default,
    )


def resolve_step_workflow_composition(
    skill_loader: SkillLoader,
    *,
    primary_skill: str,
    step_name: str,
    workflow_skills: Iterable[str] = (),
) -> StepWorkflowComposition:
    """Compose primary and resolved workflow-channel skills in stable order."""
    contributors: list[SkillWorkflowContributor] = []
    seen_roots: set[Path] = set()
    for index, requested_name in enumerate((primary_skill, *workflow_skills)):
        entry, declaration = skill_loader.get_workflow_declaration_entry(
            requested_name, validate_resources=False
        )
        identity = entry.directory.resolve()
        if identity in seen_roots:
            continue
        seen_roots.add(identity)
        contributors.append(
            SkillWorkflowContributor(
                source=WorkflowDeclarationSource(
                    skill_identity=entry.name,
                    catalog_source=entry.source,
                    skill_root=entry.directory,
                    declaration_file=entry.directory / "SKILL.md",
                ),
                declaration=declaration,
                primary=index == 0,
            )
        )
    if not contributors:
        raise WorkflowCompositionError(f"Step {step_name!r} has no primary workflow skill")

    tools: list[str] = []
    inputs: dict[str, tuple[PromptInputContract, SkillWorkflowContributor]] = {}
    tasks: dict[str, tuple[HumanTaskPolicy, SkillWorkflowContributor]] = {}
    local_names: dict[str, SkillWorkflowContributor] = {}
    primary = contributors[0]
    skill_loader.validate_workflow_declaration_resources(
        primary.source.skill_root, primary.declaration
    )
    reserved_names = set(primary.declaration.prompt_references)

    for contributor in contributors:
        declaration = contributor.declaration
        if not contributor.primary:
            for field, value in (
                ("prompt_references", declaration.prompt_references),
                ("output_templates", declaration.output_templates),
            ):
                if value:
                    resource_errors = skill_loader.workflow_declaration_resource_errors(
                        contributor.source.skill_root,
                        declaration,
                        fields={field},
                    )
                    resource_context = (
                        f" Malformed resource context: {resource_errors[0]}."
                        if resource_errors
                        else ""
                    )
                    raise WorkflowCompositionError(
                        f"Step {step_name!r} contributor {_source_label(contributor)} declares "
                        f"primary-owned workflow field {field!r}; contributors may only supply "
                        "required_tools, prompt_inputs, human_tasks, execution_profile, and "
                        f"local checklist references.{resource_context}"
                    )
            skill_loader.validate_workflow_declaration_resources(
                contributor.source.skill_root, declaration
            )
        tools.extend(tool for tool in declaration.required_tools if tool not in tools)
        for mapping in declaration.prompt_inputs:
            existing = inputs.get(mapping.placeholder)
            if existing is not None and not _same(existing[0], mapping):
                raise _conflict(
                    step_name=step_name,
                    field="prompt_inputs",
                    key=mapping.placeholder,
                    first=existing[1],
                    second=contributor,
                )
            inputs.setdefault(mapping.placeholder, (mapping, contributor))
        for policy in declaration.human_tasks:
            existing = tasks.get(policy.id)
            if existing is not None and not _same(existing[0], policy):
                raise _conflict(
                    step_name=step_name,
                    field="human_tasks",
                    key=policy.id,
                    first=existing[1],
                    second=contributor,
                )
            tasks.setdefault(policy.id, (policy, contributor))
        if declaration.checklist is not None:
            for name in declaration.checklist.context_references:
                existing = local_names.get(name)
                if name in reserved_names or name in inputs or existing is not None:
                    other = existing or inputs.get(name, (None, primary))[1]
                    raise _conflict(
                        step_name=step_name,
                        field="checklist.context_references",
                        key=name,
                        first=other,
                        second=contributor,
                    )
                local_names[name] = contributor

    retained = tuple(contributors)
    return StepWorkflowComposition(
        step_name=step_name,
        contributors=retained,
        required_tools=tuple(tools),
        prompt_inputs=tuple(value[0] for value in inputs.values()),
        human_tasks=tuple(value[0] for value in tasks.values()),
        execution_requirements=_execution_requirements(retained),
    )
