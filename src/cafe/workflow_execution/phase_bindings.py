"""Resolve phase execution config into catalog-validated agent bindings.

``cafe.utils.phase_config`` owns YAML shape and local-to-repository field
merging.  This module deliberately adds the semantic boundary that requires
the resolved agent name to exist for the effective playbook role.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from cafe.catalogs.resolver import (
    CatalogEntry,
    CatalogKind,
    CatalogResolver,
    CatalogValidationError,
)
from cafe.utils.phase_config import PhaseStepModelResolution, load_phase_step_model


_AGENT_ASSIGNEE_TYPES = frozenset({"agent", "hybrid"})


@dataclass(frozen=True)
class PhaseConfigPaths:
    """The effective local and repository phase-config locations."""

    local_path: Path
    repo_path: Optional[Path]


@dataclass(frozen=True)
class ResolvedPhaseBinding:
    """One phase config resolved against its playbook role and agent catalog."""

    step_name: str
    role: str
    agent_name: str
    phase: PhaseStepModelResolution
    agent: CatalogEntry


def phase_config_paths_for_project(
    *,
    project_root: Path,
    catalog_resolver: CatalogResolver | None = None,
) -> PhaseConfigPaths:
    """Return the paths used by an active checkout and its canonical repository."""
    resolver = catalog_resolver or CatalogResolver(project_root=project_root)
    local_path = resolver.project_root / ".cafe" / "phases.yaml"
    repo_path = (
        resolver.canonical_root / ".cafe" / "phases.yaml"
        if resolver.canonical_root != resolver.project_root
        else None
    )
    return PhaseConfigPaths(local_path=local_path, repo_path=repo_path)


def default_agent_for_step(*, playbook: Any, step_name: str) -> str:
    """Return the effective playbook default agent for an agent-executed step."""
    step = _step_definition(playbook, step_name)
    _require_agent_executed_step(step_name, step)
    role = _step_role(step_name, step)
    roles = _field(playbook, "roles")
    if not isinstance(roles, Mapping):
        raise ValueError(f"phase binding for step '{step_name}' has no playbook roles mapping")
    role_definition = roles.get(role)
    default_agent = _field(role_definition, "default_agent")
    if not isinstance(default_agent, str) or not default_agent.strip():
        raise ValueError(
            f"phase binding for step '{step_name}' has no default_agent for role '{role}'"
        )
    return default_agent.strip()


def resolve_phase_binding(
    *,
    playbook: Any,
    step_name: str,
    local_path: Path | None,
    repo_path: Path | None = None,
    project_root: Path | None = None,
    catalog_resolver: CatalogResolver | None = None,
) -> ResolvedPhaseBinding:
    """Resolve one agent/hybrid step and verify its selected catalog agent."""
    active_step = _required_token(step_name, label="phase binding step name")
    step = _step_definition(playbook, active_step)
    _require_agent_executed_step(active_step, step)
    role = _step_role(active_step, step)
    phase = load_phase_step_model(
        step_name=active_step,
        local_path=local_path,
        repo_path=repo_path,
    )
    if phase.role is not None and phase.role != role:
        raise ValueError(
            f"phase binding role mismatch for '{active_step}': expected '{role}', got '{phase.role}'"
        )
    if phase.name is None:
        raise ValueError(f"phase binding for step '{active_step}' has no resolved agent name")

    agent = resolve_catalog_agent(
        role=role,
        agent_name=phase.name,
        project_root=project_root,
        catalog_resolver=catalog_resolver,
    )
    return ResolvedPhaseBinding(
        step_name=active_step,
        role=role,
        agent_name=phase.name,
        phase=phase,
        agent=agent,
    )


def resolve_phase_bindings(
    *,
    playbook: Any,
    local_path: Path | None,
    repo_path: Path | None = None,
    step_names: Iterable[str] | None = None,
    project_root: Path | None = None,
    catalog_resolver: CatalogResolver | None = None,
) -> dict[str, ResolvedPhaseBinding]:
    """Resolve selected bindings, or every agent/hybrid step when none are selected."""
    if step_names is None:
        selected_steps = [
            step_name
            for step_name, step in _steps(playbook).items()
            if _assignee_type(step) in _AGENT_ASSIGNEE_TYPES
        ]
    else:
        selected_steps = [
            _required_token(step_name, label="phase binding step name") for step_name in step_names
        ]

    resolver = catalog_resolver or CatalogResolver(project_root=project_root)
    bindings: dict[str, ResolvedPhaseBinding] = {}
    errors: list[str] = []
    for step_name in selected_steps:
        try:
            bindings[step_name] = resolve_phase_binding(
                playbook=playbook,
                step_name=step_name,
                local_path=local_path,
                repo_path=repo_path,
                catalog_resolver=resolver,
            )
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        raise ValueError("invalid phase bindings: " + "; ".join(errors))
    return bindings


def resolve_catalog_agent(
    *,
    role: str,
    agent_name: str,
    project_root: Path | None = None,
    catalog_resolver: CatalogResolver | None = None,
) -> CatalogEntry:
    """Resolve a role/name pair without making an assumption about a playbook step."""
    resolved_role = _required_token(role, label="phase binding role")
    resolved_agent_name = _required_token(agent_name, label="phase binding agent name")
    resolver = catalog_resolver or CatalogResolver(project_root=project_root)
    key = f"{resolved_role}/{resolved_agent_name}"
    try:
        return resolver.resolve(CatalogKind.AGENT, key)
    except (CatalogValidationError, FileNotFoundError, OSError) as exc:
        raise ValueError(
            f"configured agent '{resolved_agent_name}' for role '{resolved_role}' does not "
            f"resolve: {exc}"
        ) from exc


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _steps(playbook: Any) -> Mapping[str, Any]:
    steps = _field(playbook, "steps")
    if not isinstance(steps, Mapping):
        raise ValueError("phase binding playbook has no steps mapping")
    return steps


def _step_definition(playbook: Any, step_name: str) -> Any:
    step = _steps(playbook).get(step_name)
    if step is None:
        raise ValueError(f"phase binding names unknown playbook step '{step_name}'")
    return step


def _assignee_type(step: Any) -> str:
    value = _field(step, "assignee_type")
    return "agent" if value is None else str(value).strip()


def _require_agent_executed_step(step_name: str, step: Any) -> None:
    assignee_type = _assignee_type(step)
    if assignee_type not in _AGENT_ASSIGNEE_TYPES:
        raise ValueError(
            f"phase binding for step '{step_name}' is not agent-executed "
            f"(assignee_type='{assignee_type}')"
        )


def _step_role(step_name: str, step: Any) -> str:
    return _required_token(_field(step, "role"), label=f"playbook role for step '{step_name}'")


def _required_token(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()
