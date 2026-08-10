"""Typed, skill-owned declarations for workflow execution behavior."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cafe.core.human_tasks import HumanTaskPolicy

RUNTIME_OWNED_PROMPT_PLACEHOLDERS = frozenset(
    {
        "agent_file",
        "base_branch",
        "blackboard_digest",
        "blackboard_path",
        "checklist_file",
        "commits",
        "delta_packet",
        "delta_packet_path",
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
    load_policy: Tuple["PromptInputLoadPolicy", ...] = ()

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

    @model_validator(mode="after")
    def _validate_load_policy(self) -> "PromptInputContract":
        if any(
            policy.mode == "packet" and policy.contract_kind is None for policy in self.load_policy
        ):
            raise ValueError("packet prompt input policy requires contract_kind")
        return self


class RuntimeWhen(BaseModel):
    """One shared, strict selector for checklist and input-loading variants."""

    model_config = ConfigDict(extra="forbid")

    step: Optional[str] = None
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

    @field_validator("step")
    @classmethod
    def _validate_step(cls, value: Optional[str]) -> Optional[str]:
        return _safe_token(value, field_name="step") if value is not None else None

    @model_validator(mode="after")
    def _validate_bounds(self) -> "RuntimeWhen":
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

    def matches(
        self,
        *,
        step: Optional[str],
        iteration: int,
        artifacts: Mapping[str, Any],
        feedback: bool,
    ) -> bool:
        return (
            (self.step is None or self.step == step)
            and (self.iteration is None or self.iteration == iteration)
            and (self.min_iteration is None or iteration >= self.min_iteration)
            and (self.max_iteration is None or iteration <= self.max_iteration)
            and (self.feedback is None or self.feedback == feedback)
            and all(bool(artifacts.get(name)) for name in self.artifact_present)
        )


# Retain the public name used by existing skill declarations and imports.
ChecklistWhen = RuntimeWhen


class PromptInputLoadPolicy(BaseModel):
    """A declared full-or-packet policy for an individual consuming relation."""

    model_config = ConfigDict(extra="forbid")

    when: RuntimeWhen = Field(default_factory=RuntimeWhen)
    mode: Literal["full", "packet"] = "full"
    contract_kind: Optional[Literal["spec", "plan"]] = None


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
        if (
            sum(
                value is not None and value is not False
                for value in (self.reference, self.optional_checklist, self.template_catalog)
            )
            != 1
        ):
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
    compact_agent_guidance: bool = False

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

    required_tools: Tuple[str, ...] = ()
    prompt_inputs: Tuple[PromptInputContract, ...] = ()
    prompt_references: dict[str, str] = Field(default_factory=dict)
    checklist: Optional[ChecklistContract] = None
    output_templates: Optional[OutputTemplatesContract] = None
    human_tasks: Tuple[HumanTaskPolicy, ...] = ()

    @field_validator("required_tools")
    @classmethod
    def _validate_required_tools(cls, value: Tuple[str, ...]) -> Tuple[str, ...]:
        cleaned = tuple(item.strip() for item in value)
        if any(not item for item in cleaned):
            raise ValueError("required_tools must contain non-empty tool declarations")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("required_tools must not contain duplicates")
        return cleaned

    @field_validator("prompt_references")
    @classmethod
    def _validate_prompt_references(cls, value: dict[str, str]) -> dict[str, str]:
        return {
            _safe_placeholder(
                placeholder, field_name="prompt reference placeholder"
            ): _safe_reference(reference)
            for placeholder, reference in value.items()
        }

    @model_validator(mode="after")
    def _validate_unique_placeholders(self) -> "SkillWorkflowContract":
        input_placeholders = [item.placeholder for item in self.prompt_inputs]
        if len(set(input_placeholders)) != len(input_placeholders):
            raise ValueError("prompt input placeholders must be unique")
        prompt_reference_placeholders = set(self.prompt_references)
        input_placeholder_set = set(input_placeholders)
        overlap = input_placeholder_set & prompt_reference_placeholders
        if overlap:
            raise ValueError(
                "prompt reference placeholders must not overlap prompt input placeholders: "
                f"{', '.join(sorted(overlap))}"
            )
        task_ids = [task.id for task in self.human_tasks]
        if len(set(task_ids)) != len(task_ids):
            raise ValueError("human task ids must be unique")
        if self.checklist is not None:
            checklist_references = set(self.checklist.context_references)
            overlap = input_placeholder_set & checklist_references
            if overlap:
                raise ValueError(
                    "checklist context reference placeholders must not overlap prompt input "
                    f"placeholders: {', '.join(sorted(overlap))}"
                )
            overlap = prompt_reference_placeholders & checklist_references
            if overlap:
                raise ValueError(
                    "prompt and checklist reference placeholders must not overlap: "
                    f"{', '.join(sorted(overlap))}"
                )
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


def _artifact_version(value: Any) -> int:
    version = (
        value.get("version") if isinstance(value, Mapping) else getattr(value, "version", None)
    )
    return (
        version if isinstance(version, int) and not isinstance(version, bool) and version > 0 else 1
    )


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


def resolve_effective_prompt_inputs(
    contract: SkillWorkflowContract,
    artifacts: Mapping[str, Any],
    *,
    step: str,
    iteration: int,
    feedback: bool,
    packet_dir: str | Path,
) -> dict[str, dict[str, str]]:
    """Resolve declared input relationships without relying on artifact names.

    Safe packet construction failures remain local: other inputs retain their
    declared mode and the affected input uses its complete source. Invalid
    contracts instead reject confirmation before the consumer can start.
    """
    from cafe.core.context_packet import resolve_context_packet

    result: dict[str, dict[str, str]] = {}
    relationships: list[tuple[PromptInputContract, str, str, int, PromptInputLoadPolicy | None]] = (
        []
    )
    for mapping in contract.prompt_inputs:
        source_name = next(
            (name for name in mapping.artifacts if _artifact_path(artifacts.get(name))),
            None,
        )
        if source_name is None:
            if mapping.required:
                raise DeclaredArtifactError(mapping.placeholder, mapping.artifacts)
            continue
        source_value = artifacts[source_name]
        source = _artifact_path(source_value)
        assert source is not None
        selected = next(
            (
                policy
                for policy in mapping.load_policy
                if policy.when.matches(
                    step=step,
                    iteration=iteration,
                    artifacts=artifacts,
                    feedback=feedback,
                )
            ),
            None,
        )
        relationships.append(
            (mapping, source, source_name, _artifact_version(source_value), selected)
        )

    # A paired ``*_file`` / ``*_file_path`` declaration represents one input
    # relationship.  Coalesce any declarations with the same source and
    # packet policy so both placeholders receive the same validated envelope.
    # Explicit full and packet declarations deliberately remain independent.
    packet_groups: dict[
        tuple[str, str, str, int],
        list[tuple[PromptInputContract, str, str, int, PromptInputLoadPolicy]],
    ] = {}
    for mapping, source, source_name, source_version, selected in relationships:
        if selected is None or selected.mode == "full":
            result[mapping.placeholder] = {"mode": "full", "path": source}
            continue
        contract_kind = selected.contract_kind
        assert contract_kind is not None  # validated by PromptInputContract
        packet_groups.setdefault((source, source_name, contract_kind, source_version), []).append(
            (mapping, source, source_name, source_version, selected)
        )

    for (source, source_name, contract_kind, source_version), group in packet_groups.items():
        placeholders = tuple(
            mapping.placeholder
            for mapping, _source, _source_name, _source_version, _policy in group
        )
        packet_path = Path(packet_dir) / f"context_{placeholders[0]}.json"
        resolved = resolve_context_packet(
            source_path=source,
            contract_kind=contract_kind,
            target_step=step,
            iteration=iteration,
            placeholders=placeholders,
            packet_path=packet_path,
            source_artifact_name=source_name,
            source_artifact_version=source_version,
        )
        binding = {
            "requested_mode": "packet",
            "mode": str(resolved["mode"]),
            "path": str(resolved["path"]),
            "reason": str(resolved.get("reason", "")),
            "fallback_reason": str(resolved.get("fallback_reason", "")),
            "detail": str(resolved.get("detail", "")),
            "source": dict(resolved.get("source", {})),
        }
        for mapping, _source, _source_name, _source_version, _policy in group:
            result[mapping.placeholder] = dict(binding)
    return result
