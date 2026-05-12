"""Playbook schema, loading, and semantic validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cafe.skills.exceptions import SkillDiscoveryError
from cafe.skills.loader import SkillLoader


DONE_TARGET = "_done"


class PlaybookMeta(BaseModel):
    """Metadata for one playbook."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: Optional[str] = None


class PlaybookRole(BaseModel):
    """Role defaults declared by a playbook."""

    model_config = ConfigDict(extra="forbid")

    description: Optional[str] = None
    default_agent: Optional[str] = None
    default_cli: Optional[str] = None


class StepHooks(BaseModel):
    """Lifecycle hook configuration."""

    model_config = ConfigDict(extra="forbid")

    before_execute: List[Union[str, Dict[str, Any]]] = Field(default_factory=list)
    prepare_input: List[Union[str, Dict[str, Any]]] = Field(default_factory=list)
    after_execute: List[Union[str, Dict[str, Any]]] = Field(default_factory=list)
    publish_output: List[Union[str, Dict[str, Any]]] = Field(default_factory=list)


SkillSelector = Union[str, Dict[str, str]]


class StepConfig(BaseModel):
    """One playbook step."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["skill", "subflow"] = "skill"
    skill: SkillSelector
    role: str
    assignee_type: Literal["agent", "human", "auto"] = "agent"
    input_artifacts: List[str] = Field(default_factory=list)
    output_artifact: Optional[str] = None
    allowed_tools: List[str] = Field(default_factory=list)
    valid_status_codes: List[str] = Field(default_factory=list)
    max_iterations: Optional[Union[int, str]] = None
    allowed_goto: List[str] = Field(default_factory=list)
    hooks: StepHooks = Field(default_factory=StepHooks)
    auto_snapshot: bool = True
    on: Dict[str, str]

    @field_validator("skill")
    @classmethod
    def _validate_skill(cls, value: SkillSelector) -> SkillSelector:
        if isinstance(value, str):
            if not value.strip():
                raise ValueError("skill must not be empty")
            return value

        if not value:
            raise ValueError("skill mapping must not be empty")

        normalized: Dict[str, str] = {}
        for raw_key, raw_skill in value.items():
            key = str(raw_key)
            skill = str(raw_skill).strip()
            if not skill:
                raise ValueError("skill mapping values must not be empty")
            normalized[key] = skill

        if "default" not in normalized and not any(key.isdigit() for key in normalized):
            raise ValueError("skill mapping must include 'default' or numbered iteration keys")
        return normalized


class PlaybookDefinition(BaseModel):
    """Top-level playbook definition."""

    model_config = ConfigDict(extra="forbid")

    playbook: PlaybookMeta
    roles: Dict[str, PlaybookRole] = Field(default_factory=dict)
    steps: Dict[str, StepConfig]
    entry_point: Optional[str] = None

    @model_validator(mode="after")
    def _default_entry_point(self) -> "PlaybookDefinition":
        if self.entry_point is None:
            self.entry_point = next(iter(self.steps.keys()))
        return self


@dataclass(frozen=True)
class LoadedPlaybook:
    """Loaded playbook plus validation metadata."""

    model: PlaybookDefinition
    path: Path
    source: str
    warnings: List[str]

    def as_dict(self) -> Dict:
        return self.model.model_dump(exclude_none=True)


def normalize_playbook_yaml(data: Dict) -> Dict:
    """Normalize YAML 1.1 bool-converted keys like `on` -> True."""
    steps = data.get("steps")
    if not isinstance(steps, dict):
        return data

    for step in steps.values():
        if not isinstance(step, dict):
            continue
        if "on" not in step and True in step and isinstance(step[True], dict):
            step["on"] = step.pop(True)
        skill = step.get("skill")
        if isinstance(skill, dict):
            step["skill"] = {str(key): value for key, value in skill.items()}
    return data


def load_playbook_file(
    path: Path,
    *,
    source: str,
    skill_loader: SkillLoader,
    strict: bool = False,
) -> LoadedPlaybook:
    """Load one playbook file and apply schema + semantic validation."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        raise ValueError(f"Playbook is empty: {path}")
    data = normalize_playbook_yaml(data)
    model = PlaybookDefinition.model_validate(data)
    warnings = validate_playbook(
        model,
        skill_loader=skill_loader,
        source=source,
        path=path,
        strict=strict,
    )
    return LoadedPlaybook(model=model, path=path, source=source, warnings=warnings)


def validate_playbook(
    model: PlaybookDefinition,
    *,
    skill_loader: SkillLoader,
    source: str,
    path: Path,
    strict: bool = False,
) -> List[str]:
    """Apply semantic validation and return non-fatal warnings."""
    warnings: List[str] = []
    steps = model.steps

    _report_structural_issue(
        playbook_id=model.playbook.id,
        filename=path.stem,
        source=source,
        strict=strict,
        warnings=warnings,
    )

    for step_name, step in steps.items():
        _validate_step_role(step_name, step, model.roles)
        _validate_step_skills(step_name, step, skill_loader)
        _validate_targets(step_name, step.allowed_goto, steps, "allowed_goto")
        _validate_transition_targets(step_name, step.on, steps)
        warnings.extend(_collect_tool_warnings(step_name, step.allowed_tools))
        if step.assignee_type != "agent":
            warnings.append(
                f"Step '{step_name}': assignee_type={step.assignee_type} (reserved for v0.3)"
            )

    if warnings and strict:
        raise ValueError("\n".join(warnings))
    return warnings


def _report_structural_issue(
    *,
    playbook_id: str,
    filename: str,
    source: str,
    strict: bool,
    warnings: List[str],
) -> None:
    if playbook_id == filename:
        return

    message = f"Playbook id '{playbook_id}' does not match file name '{filename}'"
    if source == "builtin" or strict:
        raise ValueError(message)
    warnings.append(message)


def _validate_step_role(step_name: str, step: StepConfig, roles: Dict[str, PlaybookRole]) -> None:
    if roles and step.role not in roles:
        raise ValueError(f"Step '{step_name}' references unknown role '{step.role}'")


def _validate_step_skills(step_name: str, step: StepConfig, skill_loader: SkillLoader) -> None:
    selectors = [step.skill] if isinstance(step.skill, str) else list(step.skill.values())
    for skill_name in selectors:
        try:
            skill_loader.get_skill_dir(skill_name)
        except (SkillDiscoveryError, FileNotFoundError) as exc:
            raise ValueError(
                f"Step '{step_name}' references unknown skill '{skill_name}'"
            ) from exc


def _validate_targets(
    step_name: str,
    targets: List[str],
    steps: Dict[str, StepConfig],
    field_name: str,
) -> None:
    for target in targets:
        if target not in steps:
            raise ValueError(
                f"Step '{step_name}' has invalid {field_name} target '{target}'"
            )


def _validate_transition_targets(
    step_name: str,
    transitions: Dict[str, str],
    steps: Dict[str, StepConfig],
) -> None:
    if not transitions:
        return
    for status_code, target in transitions.items():
        if target == DONE_TARGET:
            continue
        if target not in steps:
            raise ValueError(
                f"Step '{step_name}' has invalid transition for '{status_code}': '{target}'"
            )


def _collect_tool_warnings(step_name: str, allowed_tools: List[str]) -> List[str]:
    warnings: List[str] = []
    seen: Dict[str, str] = {}
    broad_tools = {
        tool.strip(): tool.strip()
        for tool in allowed_tools
        if "(" not in tool and tool.strip()
    }

    for tool in allowed_tools:
        normalized = tool.strip()
        if not normalized:
            continue
        if normalized in seen:
            warnings.append(f"Step '{step_name}': duplicate allowed_tools entry '{normalized}'")
            continue
        seen[normalized] = normalized

        if "(" not in normalized:
            continue

        tool_name = normalized.split("(", 1)[0].strip()
        if tool_name in broad_tools:
            warnings.append(
                f"Step '{step_name}': redundant allowed_tools entry '{normalized}' because '{tool_name}' already allows it"
            )

    return warnings
