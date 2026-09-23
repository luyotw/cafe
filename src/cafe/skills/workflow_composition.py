"""Resolve one source-aware workflow declaration for a playbook step."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from cafe.catalogs.resolver import global_catalog_lock
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
    catalog_root: Path | None = None

    @property
    def skill_names(self) -> tuple[str, ...]:
        return tuple(item.source.skill_identity for item in self.contributors)

    @property
    def causal_todo_artifacts(self) -> tuple[str, ...]:
        """All checklist-local causal aliases share the same inbound transition."""
        return tuple(
            dict.fromkeys(
                section.todo_projection.artifact
                for contributor in self.contributors
                for checklist in [
                    (
                        contributor.declaration.checklist
                        if contributor.primary
                        else contributor.declaration.checklist_overlay
                    )
                ]
                if checklist is not None
                for variant in checklist.variants
                for section in variant.sections
                if section.todo_projection and section.todo_projection.causal
            )
        )

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
    """Compose one step against a stable catalog reader snapshot."""
    with global_catalog_lock(skill_loader.global_root):
        return _resolve_step_workflow_composition_locked(
            skill_loader,
            primary_skill=primary_skill,
            step_name=step_name,
            workflow_skills=workflow_skills,
        )


def _resolve_step_workflow_composition_locked(
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
        entry, raw_declaration = skill_loader.get_workflow_declaration_data(requested_name)
        if index == 0:
            declaration = skill_loader.parse_workflow_declaration(entry, raw_declaration)
        else:
            try:
                declaration = SkillWorkflowDeclaration.model_validate(raw_declaration)
            except Exception as exc:
                declaration_file = entry.directory / "SKILL.md"
                raise WorkflowCompositionError(
                    f"Step {step_name!r} contributor {entry.name!r} "
                    f"({declaration_file}:workflow) has an invalid workflow declaration: {exc}"
                ) from exc
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
    primary = contributors[0]
    reserved_names = {name: primary for name in primary.declaration.prompt_references}
    # Collect all common names first so contributor order cannot hide a collision.
    for contributor in contributors:
        for mapping in contributor.declaration.prompt_inputs:
            reserved_names.setdefault(mapping.placeholder, contributor)

    def validate_resources(contributor: SkillWorkflowContributor) -> None:
        try:
            skill_loader.validate_workflow_declaration_resources(
                contributor.source.skill_root, contributor.declaration
            )
        except ValueError as exc:
            raise WorkflowCompositionError(
                f"Step {step_name!r} contributor {_source_label(contributor)}: {exc}"
            ) from exc

    validate_resources(primary)

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
            validate_resources(contributor)
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
        for field in ("checklist", "checklist_overlay"):
            checklist = getattr(declaration, field)
            if checklist is None:
                continue
            for name in checklist.context_references:
                if name in reserved_names:
                    raise _conflict(
                        step_name=step_name,
                        field=f"{field}.context_references",
                        key=name,
                        first=reserved_names[name],
                        second=contributor,
                    )

    retained = tuple(contributors)
    return StepWorkflowComposition(
        step_name=step_name,
        contributors=retained,
        required_tools=tuple(tools),
        prompt_inputs=tuple(value[0] for value in inputs.values()),
        human_tasks=tuple(value[0] for value in tasks.values()),
        execution_requirements=_execution_requirements(retained),
        catalog_root=skill_loader.global_root,
    )
