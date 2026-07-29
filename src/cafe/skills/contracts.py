"""Typed, skill-owned declarations for workflow execution behavior."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RUNTIME_OWNED_PROMPT_PLACEHOLDERS = frozenset(
    {
        "agent_file",
        "base_branch",
        "blackboard_digest",
        "blackboard_path",
        "checklist_file",
        "commits",
        "handoff_summary",
        "iteration",
        "next_step_path",
        "output_file",
        "previous_output_file",
        "questions_xml_file",
        "resume_input_artifacts",
        "step_transitions",
        "template_catalog",
        "template_file",
        "user_input",
        "valid_to_steps",
    }
)


def _safe_token(value: str, *, field_name: str) -> str:
    token = value.strip()
    if not token or "/" in token or "\\" in token or token in {".", ".."}:
        raise ValueError(f"{field_name} must be a non-empty name, not a path")
    return token


def _safe_reference(value: str) -> str:
    token = value.strip()
    if (
        not token
        or token.startswith(("/", "\\"))
        or "\\" in token
        or any(part in {"", ".", ".."} for part in token.split("/"))
        or not token.endswith(".md")
    ):
        raise ValueError("checklist reference must be a relative Markdown file inside references")
    return token


def _safe_placeholder(value: str, *, field_name: str) -> str:
    """Validate a skill-owned placeholder without allowing runtime overrides."""
    token = _safe_token(value, field_name=field_name)
    if token in RUNTIME_OWNED_PROMPT_PLACEHOLDERS:
        raise ValueError(f"{field_name} {token!r} is runtime-owned")
    return token


class PromptInputContract(BaseModel):
    """One artifact mapping exposed under a skill-selected placeholder."""

    model_config = ConfigDict(extra="forbid")

    artifacts: Tuple[str, ...]
    placeholder: str
    required: bool = False

    @field_validator("artifacts")
    @classmethod
    def _validate_artifacts(cls, value: Tuple[str, ...]) -> Tuple[str, ...]:
        if not value:
            raise ValueError("prompt input artifacts must not be empty")
        cleaned = tuple(_safe_token(item, field_name="artifact") for item in value)
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("prompt input artifact candidates must be unique")
        return cleaned

    @field_validator("placeholder")
    @classmethod
    def _validate_placeholder(cls, value: str) -> str:
        return _safe_placeholder(value, field_name="placeholder")


class ChecklistWhen(BaseModel):
    """Bounded runtime facts used to select a checklist variant."""

    model_config = ConfigDict(extra="forbid")

    iteration: Optional[int] = None
    min_iteration: Optional[int] = None
    max_iteration: Optional[int] = None
    artifact_present: Tuple[str, ...] = ()
    feedback: Optional[bool] = None

    @field_validator("iteration", "min_iteration", "max_iteration")
    @classmethod
    def _validate_iteration(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value < 1:
            raise ValueError("iteration selectors must be positive")
        return value

    @field_validator("artifact_present")
    @classmethod
    def _validate_artifact_presence(cls, value: Tuple[str, ...]) -> Tuple[str, ...]:
        return tuple(_safe_token(item, field_name="artifact_present") for item in value)

    @model_validator(mode="after")
    def _validate_bounds(self) -> "ChecklistWhen":
        if self.iteration is not None and any(
            bound is not None for bound in (self.min_iteration, self.max_iteration)
        ):
            raise ValueError("iteration cannot be combined with min_iteration or max_iteration")
        if (
            self.min_iteration is not None
            and self.max_iteration is not None
            and self.min_iteration > self.max_iteration
        ):
            raise ValueError("min_iteration must not exceed max_iteration")
        return self


class ChecklistSection(BaseModel):
    """One ordered part of a generated checklist."""

    model_config = ConfigDict(extra="forbid")

    reference: Optional[str] = None
    optional_checklist: Optional[str] = None
    template_catalog: bool = False

    @field_validator("reference", "optional_checklist")
    @classmethod
    def _validate_reference(cls, value: Optional[str]) -> Optional[str]:
        return _safe_reference(value) if value is not None else None

    @model_validator(mode="after")
    def _validate_source(self) -> "ChecklistSection":
        if sum(
            value is not None and value is not False
            for value in (self.reference, self.optional_checklist, self.template_catalog)
        ) != 1:
            raise ValueError("checklist section requires exactly one source")
        return self


class ChecklistVariant(BaseModel):
    """A declaration-selected ordered list of checklist sections."""

    model_config = ConfigDict(extra="forbid")

    when: ChecklistWhen = Field(default_factory=ChecklistWhen)
    sections: Tuple[ChecklistSection, ...]

    @field_validator("sections")
    @classmethod
    def _validate_sections(
        cls, value: Tuple[ChecklistSection, ...]
    ) -> Tuple[ChecklistSection, ...]:
        if not value:
            raise ValueError("checklist variant requires at least one section")
        return value


class ChecklistContract(BaseModel):
    """Checklist references and explicit role-guideline behavior for a skill."""

    model_config = ConfigDict(extra="forbid")

    context_references: dict[str, str] = Field(default_factory=dict)
    variants: Tuple[ChecklistVariant, ...]
    include_role_guidance: bool = False

    @field_validator("context_references")
    @classmethod
    def _validate_context_references(cls, value: dict[str, str]) -> dict[str, str]:
        return {
            _safe_placeholder(
                placeholder, field_name="context reference placeholder"
            ): _safe_reference(ref)
            for placeholder, ref in value.items()
        }

    @field_validator("variants")
    @classmethod
    def _validate_variants(
        cls, value: Tuple[ChecklistVariant, ...]
    ) -> Tuple[ChecklistVariant, ...]:
        if not value:
            raise ValueError("checklist requires at least one variant")
        return value


class OutputTemplatesContract(BaseModel):
    """Skill-owned template catalog exposed by a workflow step."""

    model_config = ConfigDict(extra="forbid")

    catalog: str
    label: Optional[str] = None
    follow_instruction: str = "Follow template structure when writing analysis results"

    @field_validator("catalog")
    @classmethod
    def _validate_catalog(cls, value: str) -> str:
        return _safe_token(value, field_name="template catalog")

    @field_validator("label", "follow_instruction")
    @classmethod
    def _validate_template_copy(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("template guidance must not be empty")
        return value


class SkillWorkflowContract(BaseModel):
    """All optional workflow metadata carried in a skill frontmatter block."""

    model_config = ConfigDict(extra="forbid")

    prompt_inputs: Tuple[PromptInputContract, ...] = ()
    checklist: Optional[ChecklistContract] = None
    output_templates: Optional[OutputTemplatesContract] = None

    @model_validator(mode="after")
    def _validate_unique_placeholders(self) -> "SkillWorkflowContract":
        placeholders = [item.placeholder for item in self.prompt_inputs]
        if len(set(placeholders)) != len(placeholders):
            raise ValueError("prompt input placeholders must be unique")
        return self


@dataclass(frozen=True)
class DeclaredArtifactError(ValueError):
    """A required skill-declared input has no recorded artifact."""

    placeholder: str
    artifacts: Tuple[str, ...]

    def __str__(self) -> str:
        candidates = ", ".join(self.artifacts)
        return (
            f"Missing required prompt input {self.placeholder!r}; "
            f"expected one of declared artifacts: {candidates}"
        )


def _artifact_path(value: Any) -> Optional[str]:
    if value is None:
        return None
    path = getattr(value, "path", value)
    if path is None:
        return None
    text = str(path).strip()
    return text or None


def resolve_prompt_inputs(
    contract: SkillWorkflowContract,
    artifacts: Mapping[str, Any],
) -> dict[str, str]:
    """Resolve declared artifacts in order without any implicit fallback names."""
    resolved: dict[str, str] = {}
    for mapping in contract.prompt_inputs:
        for artifact_name in mapping.artifacts:
            path = _artifact_path(artifacts.get(artifact_name))
            if path:
                resolved[mapping.placeholder] = path
                break
        else:
            if mapping.required:
                raise DeclaredArtifactError(mapping.placeholder, mapping.artifacts)
    return resolved
