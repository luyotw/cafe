"""Playbook schema, loading, and semantic validation."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cafe.core.human_tasks import HumanTaskBinding
from cafe.core.initial_input import (
    SUPPORTED_INITIAL_INPUT_PROVIDERS,
    registered_initial_input_providers,
)
from cafe.core.prepare_fields import (
    PrepareField,
    assert_prepare_semantics_match,
    resolve_prepare_fields,
    validate_field_semantics,
)
from cafe.core.status_codes import PLAYBOOK_INTENT_KEYS, PhaseStatusCode
from cafe.skills.exceptions import SkillDiscoveryError
from cafe.skills.loader import SkillLoader, canonical_skill_name
from cafe.templates.manager import TemplateManager

DONE_TARGET = "_done"
SCRIPT_HOOK_STAGES = {"before_execute", "after_execute"}
RUNTIME_CONTEXT_PROVIDERS = frozenset({"workflow_metadata", "git_history", "local_review"})
RUNTIME_TOOL_GRANTS = frozenset({"web_research", "git_inspection"})

RigorLevel = Literal["low", "medium", "high"]
InputMethodDefault = Literal["manual", "github"]
CONVERSATION_LOCALE_PATTERN = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")


def _non_empty(value: str, *, field_name: str) -> str:
    token = value.strip()
    if not token:
        raise ValueError(f"{field_name} must not be empty")
    return token


class PlaybookMeta(BaseModel):
    """Metadata for one playbook."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: Optional[str] = None
    conversation_locale: str = "auto"

    @field_validator("conversation_locale")
    @classmethod
    def _validate_conversation_locale(cls, value: str) -> str:
        token = value.strip()
        if token.lower() == "auto":
            return "auto"
        if not CONVERSATION_LOCALE_PATTERN.fullmatch(token):
            raise ValueError(
                "playbook.conversation_locale must be 'auto' or a BCP 47 language tag "
                "such as 'zh-TW' or 'en'"
            )
        return token


class PlaybookRole(BaseModel):
    """Role defaults declared by a playbook."""

    model_config = ConfigDict(extra="forbid")

    description: Optional[str] = None
    default_agent: Optional[str] = None
    default_cli: Optional[str] = None


SkillEnvironmentMode = Literal["extend", "replace"]


class SkillEnvironmentOverlay(BaseModel):
    """One role or step addition to a declared skill environment."""

    model_config = ConfigDict(extra="forbid")

    mode: SkillEnvironmentMode
    skills: List[str]

    @field_validator("skills")
    @classmethod
    def _validate_skill_names(cls, value: List[str]) -> List[str]:
        return _clean_declared_skill_names(value)


class SkillEnvironmentChannel(BaseModel):
    """Shared skills plus optional role and step overlays for one channel."""

    model_config = ConfigDict(extra="forbid")

    shared: List[str]
    roles: Dict[str, SkillEnvironmentOverlay] = Field(default_factory=dict)
    steps: Dict[str, SkillEnvironmentOverlay] = Field(default_factory=dict)

    @field_validator("shared")
    @classmethod
    def _validate_shared_skill_names(cls, value: List[str]) -> List[str]:
        return _clean_declared_skill_names(value)


class PlaybookSkillEnvironments(BaseModel):
    """Workflow and chat skill declarations owned by a playbook."""

    model_config = ConfigDict(extra="forbid")

    workflow: Optional[SkillEnvironmentChannel] = None
    chat: Optional[SkillEnvironmentChannel] = None


def _clean_declared_skill_names(value: List[str]) -> List[str]:
    cleaned: List[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("skill declarations must contain non-empty skill names")
        cleaned.append(item.strip())
    return cleaned


class StepHooks(BaseModel):
    """Lifecycle hook configuration."""

    model_config = ConfigDict(extra="forbid")

    before_execute: List[Union[str, Dict[str, Any]]] = Field(default_factory=list)
    prepare_input: List[Union[str, Dict[str, Any]]] = Field(default_factory=list)
    after_execute: List[Union[str, Dict[str, Any]]] = Field(default_factory=list)
    publish_output: List[Union[str, Dict[str, Any]]] = Field(default_factory=list)


SkillSelector = Union[str, Dict[str, str]]


class StepAlignmentConfig(BaseModel):
    """Opt-in compatibility config for pre-execution alignment checkpoints."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    trigger_policy: Literal["policy", "disabled"] = "policy"
    pause_threshold: int = 5
    note_threshold: int = 2
    affected_document_categories: List[str] = Field(default_factory=list)
    reuse_approved: bool = True

    @field_validator("pause_threshold", "note_threshold")
    @classmethod
    def _validate_thresholds(cls, value: int) -> int:
        if value < 0:
            raise ValueError("alignment thresholds must be non-negative")
        return value

    @field_validator("affected_document_categories")
    @classmethod
    def _validate_categories(cls, value: List[str]) -> List[str]:
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        return list(dict.fromkeys(cleaned))


class InitialInputBinding(BaseModel):
    """Declared destinations for an entry step's trusted initial input."""

    model_config = ConfigDict(extra="forbid")

    artifact: Optional[str] = None
    prompt_context: Optional[Literal["user_input"]] = None

    @field_validator("artifact")
    @classmethod
    def _validate_artifact(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("initial_input.bind.artifact must not be empty")
        return value

    @model_validator(mode="after")
    def _require_a_target(self) -> "InitialInputBinding":
        if self.artifact is None and self.prompt_context is None:
            raise ValueError("initial_input.bind must declare artifact or prompt_context")
        return self


class InitialInputDeclaration(BaseModel):
    """Trusted providers permitted for an entry step's first iteration."""

    model_config = ConfigDict(extra="forbid")

    providers: List[str]
    bind: InitialInputBinding
    legacy_presentation: bool = False

    @field_validator("providers")
    @classmethod
    def _validate_providers(cls, value: List[str]) -> List[str]:
        providers = [provider.strip() for provider in value if provider.strip()]
        if not providers:
            raise ValueError("initial_input.providers must include at least one provider")
        if len(providers) != len(set(providers)):
            raise ValueError("initial_input.providers must not contain duplicates")
        unsupported = [
            provider for provider in providers if provider not in SUPPORTED_INITIAL_INPUT_PROVIDERS
        ]
        if unsupported:
            raise ValueError(
                "initial_input.providers contains unsupported provider " f"{unsupported[0]!r}"
            )
        return providers


CompletionMode = Literal["status_code", "baton"]


class StepBehaviorDeclaration(BaseModel):
    """Optional behavior selectors declared by a playbook or one step.

    This model intentionally contains only runtime-owned identifiers.  A
    playbook can select existing providers and grants, but cannot introduce
    import paths or executable host behavior.
    """

    model_config = ConfigDict(extra="forbid")

    completion: Optional[CompletionMode] = None
    publish_confirmation: Optional[bool] = None
    feedback_target: Optional[str] = None
    context_providers: Optional[List[str]] = None
    runtime_tool_grants: Optional[List[str]] = None

    @field_validator("context_providers", "runtime_tool_grants")
    @classmethod
    def _validate_runtime_owned_identifiers(
        cls, value: Optional[List[str]], info: Any
    ) -> Optional[List[str]]:
        if value is None:
            return None
        allowed = (
            RUNTIME_CONTEXT_PROVIDERS
            if info.field_name == "context_providers"
            else RUNTIME_TOOL_GRANTS
        )
        cleaned = [str(item).strip() for item in value]
        if not all(cleaned) or len(cleaned) != len(set(cleaned)):
            raise ValueError(f"{info.field_name} must contain unique non-empty identifiers")
        unknown = [item for item in cleaned if item not in allowed]
        if unknown:
            raise ValueError(f"{info.field_name} contains unknown runtime-owned id {unknown[0]!r}")
        return cleaned


class EffectiveStepBehavior(BaseModel):
    """Fully resolved, name-independent runtime behavior for one step."""

    model_config = ConfigDict(frozen=True)

    completion: CompletionMode = "status_code"
    publish_confirmation: bool = False
    feedback_target: Optional[str] = None
    context_providers: List[str] = Field(default_factory=list)
    runtime_tool_grants: List[str] = Field(default_factory=list)


class AutomaticStepConfig(BaseModel):
    """A declarative selector for a runtime-owned automatic executor."""

    model_config = ConfigDict(extra="forbid")

    executor: str
    inputs: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("executor")
    @classmethod
    def _validate_executor(cls, value: str) -> str:
        token = _non_empty(value, field_name="automatic.executor")
        if "/" in token or "\\" in token or token.startswith("."):
            raise ValueError("automatic.executor must name a registered executor id, not a path")
        return token

    @field_validator("inputs")
    @classmethod
    def _validate_json_inputs(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        """Keep declarative automatic input data portable and untrusted."""

        def is_json_value(candidate: Any) -> bool:
            if candidate is None or isinstance(candidate, (str, bool, int)):
                return True
            if isinstance(candidate, float):
                return math.isfinite(candidate)
            if isinstance(candidate, list):
                return all(is_json_value(item) for item in candidate)
            if isinstance(candidate, dict):
                return all(
                    isinstance(key, str) and is_json_value(item) for key, item in candidate.items()
                )
            return False

        if not is_json_value(value):
            raise ValueError("automatic.inputs must contain JSON values only")
        return value


class HybridTarget(BaseModel):
    """One typed continuation from a hybrid portion."""

    model_config = ConfigDict(extra="forbid")

    step: Optional[str] = None
    portion: Optional[str] = None

    @model_validator(mode="after")
    def _validate_single_target(self) -> "HybridTarget":
        values = [value for value in (self.step, self.portion) if value is not None]
        if len(values) != 1:
            raise ValueError("hybrid target requires exactly one of step or portion")
        if not values[0].strip():
            raise ValueError("hybrid target must not be empty")
        return self


class HybridPortion(BaseModel):
    """An explicit agent or human portion of one hybrid step."""

    model_config = ConfigDict(extra="forbid")

    id: str
    owner: Literal["agent", "human"]
    instruction: Optional[str] = None
    on: Dict[str, HybridTarget]

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        return _non_empty(value, field_name="hybrid portion id")

    @field_validator("on")
    @classmethod
    def _validate_on(cls, value: Dict[str, HybridTarget]) -> Dict[str, HybridTarget]:
        if not value:
            raise ValueError("hybrid portion requires at least one declared continuation")
        for key in value:
            _non_empty(key, field_name="hybrid continuation key")
        return value


class HybridStepConfig(BaseModel):
    """Validated portion graph for a mixed agent/human step."""

    model_config = ConfigDict(extra="forbid")

    entry_portion: str
    portions: tuple[HybridPortion, ...]

    @model_validator(mode="after")
    def _validate_portions(self) -> "HybridStepConfig":
        ids = [portion.id for portion in self.portions]
        if not ids:
            raise ValueError("hybrid.portions must not be empty")
        if len(set(ids)) != len(ids):
            raise ValueError("hybrid portion ids must be unique")
        if self.entry_portion not in ids:
            raise ValueError("hybrid.entry_portion must name a declared portion")
        owners = {portion.owner for portion in self.portions}
        if owners != {"agent", "human"}:
            raise ValueError("hybrid.portions must declare both agent and human work")
        for portion in self.portions:
            for target in portion.on.values():
                if target.portion is not None and target.portion not in ids:
                    raise ValueError(
                        f"hybrid portion {portion.id!r} targets unknown portion {target.portion!r}"
                    )
        return self


def _behavior_value(
    defaults: StepBehaviorDeclaration,
    override: StepBehaviorDeclaration,
    field_name: str,
    fallback: Any,
) -> Any:
    value = getattr(override, field_name)
    if value is not None:
        return value
    value = getattr(defaults, field_name)
    return fallback if value is None else value


class StepConfig(BaseModel):
    """One playbook step."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["skill", "subflow"] = "skill"
    skill: SkillSelector
    role: str
    assignee_type: Literal["agent", "human", "auto", "hybrid"] = "agent"
    automatic: Optional[AutomaticStepConfig] = None
    hybrid: Optional[HybridStepConfig] = None
    # ``None`` means the legacy playbook omitted this field and therefore
    # intentionally receives every recorded artifact. An explicit empty list
    # remains the opt-in isolated scope.
    input_artifacts: Optional[List[str]] = None
    output_artifact: Optional[str] = None
    initial_input: Optional[InitialInputDeclaration] = None
    template: Optional[str] = None
    allowed_tools: List[str] = Field(default_factory=list)
    capability_requests: List[str] = Field(default_factory=list)
    behavior: StepBehaviorDeclaration = Field(default_factory=StepBehaviorDeclaration)
    valid_intents: List[str] = Field(default_factory=list)
    max_iterations: Optional[Union[int, str]] = None
    correction_session: Literal["fresh", "resume"] = "fresh"
    allowed_goto: List[str] = Field(default_factory=list)
    hooks: StepHooks = Field(default_factory=StepHooks)
    auto_snapshot: bool = True
    handoff_label: Optional[str] = None
    chat_role: Optional[str] = None
    alignment: Optional[StepAlignmentConfig] = None
    human_tasks: tuple[HumanTaskBinding, ...] = ()
    on: Dict[str, str]

    @model_validator(mode="after")
    def _validate_input_artifact_scope(self) -> "StepConfig":
        if "input_artifacts" in self.model_fields_set and self.input_artifacts is None:
            raise ValueError("input_artifacts must be a list when specified")
        if self.automatic is not None and self.assignee_type != "auto":
            raise ValueError("automatic requires matching assignee_type=auto")
        if self.hybrid is not None and self.assignee_type != "hybrid":
            raise ValueError("hybrid requires matching assignee_type=hybrid")
        if self.assignee_type == "auto" and self.automatic is None:
            raise ValueError("assignee_type=auto requires automatic executor declaration")
        if self.assignee_type == "human":
            initial = [binding for binding in self.human_tasks if binding.trigger == "initial"]
            if len(initial) != 1 or not initial[0].outcomes:
                raise ValueError(
                    "assignee_type=human requires exactly one initial human task with "
                    "declared outcomes"
                )
        if self.assignee_type == "hybrid" and self.hybrid is None:
            raise ValueError("assignee_type=hybrid requires hybrid portion declaration")
        if self.assignee_type == "auto" and self.human_tasks:
            raise ValueError("assignee_type=auto cannot declare human_tasks")
        return self

    @field_validator("human_tasks")
    @classmethod
    def _validate_human_task_bindings(
        cls, value: tuple[HumanTaskBinding, ...]
    ) -> tuple[HumanTaskBinding, ...]:
        triggers = [binding.trigger for binding in value]
        if len(set(triggers)) != len(triggers):
            raise ValueError("human task triggers must be unique per step")
        return value

    @field_validator("on")
    @classmethod
    def _validate_on_intents(cls, value: Dict[str, str]) -> Dict[str, str]:
        for key in value:
            if key == "default":
                continue
            if key.startswith("CAFE_"):
                raise ValueError(
                    f"Legacy CAFE_ transition key is not allowed in playbook on: {key!r}"
                )
            if key not in PLAYBOOK_INTENT_KEYS:
                raise ValueError(
                    f"Invalid playbook transition key {key!r}; "
                    f"must be one of {sorted(PLAYBOOK_INTENT_KEYS)} or 'default'"
                )
        return value

    @field_validator("valid_intents")
    @classmethod
    def _validate_valid_intents(cls, value: List[str]) -> List[str]:
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("valid_intents entries must be non-empty strings")
            token = item.strip()
            if token.startswith("CAFE_"):
                raise ValueError(f"Legacy CAFE_ value is not allowed in valid_intents: {token!r}")
            try:
                PhaseStatusCode(token)
            except ValueError as exc:
                raise ValueError(f"Unknown step outcome token in valid_intents: {token!r}") from exc
        return [item.strip() for item in value]

    @field_validator("capability_requests")
    @classmethod
    def _validate_capability_requests(cls, value: List[str]) -> List[str]:
        cleaned: List[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("capability_requests entries must be non-empty strings")
            token = item.strip()
            if token in cleaned:
                raise ValueError(f"duplicate capability_requests entry: {token!r}")
            cleaned.append(token)
        return cleaned

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


class PrepareSetupModeEntry(BaseModel):
    """One setup mode offered during interactive prepare."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    label: str


class PrepareSetupModes(BaseModel):
    """Quick setup vs custom configuration choices."""

    model_config = ConfigDict(extra="forbid")

    quick: PrepareSetupModeEntry = Field(
        default_factory=lambda: PrepareSetupModeEntry(
            label="Quick setup (use recommended defaults)"
        )
    )
    custom: PrepareSetupModeEntry = Field(
        default_factory=lambda: PrepareSetupModeEntry(label="Custom configuration")
    )


class PrepareQuickSetupSpec(BaseModel):
    """Spec defaults applied when the user picks quick setup."""

    model_config = ConfigDict(extra="forbid")

    rigor: RigorLevel = "medium"
    template: str = "auto"


class PrepareQuickSetupPlan(BaseModel):
    """Plan defaults applied when the user picks quick setup."""

    model_config = ConfigDict(extra="forbid")

    template: str = "auto"


class PrepareSyncGithubDefaults(BaseModel):
    """GitHub sync defaults derived from input method during quick setup."""

    model_config = ConfigDict(extra="forbid")

    when_issue_id_present: bool = True
    when_manual_input: bool = False


class PrepareQuickSetupPr(BaseModel):
    """PR defaults applied when the user picks quick setup."""

    model_config = ConfigDict(extra="forbid")

    auto_create_on_github_repo: bool = True
    post_todo_list_when_auto_create: bool = True


class PrepareQuickSetup(BaseModel):
    """Recommended defaults for interactive quick setup."""

    model_config = ConfigDict(extra="forbid")

    spec: PrepareQuickSetupSpec = Field(default_factory=PrepareQuickSetupSpec)
    plan: PrepareQuickSetupPlan = Field(default_factory=PrepareQuickSetupPlan)
    sync_github: PrepareSyncGithubDefaults = Field(default_factory=PrepareSyncGithubDefaults)
    pr: PrepareQuickSetupPr = Field(default_factory=PrepareQuickSetupPr)


class PrepareNonInteractiveDefaults(BaseModel):
    """Defaults used when prepare runs with --no-interactive."""

    model_config = ConfigDict(extra="forbid")

    rigor: RigorLevel = "medium"
    spec_template: str = "auto"
    plan_template: str = "default"


class PrepareInputMethod(BaseModel):
    """Input-method prompt behavior for prepare."""

    model_config = ConfigDict(extra="forbid")

    prompt_on_github_repo: bool = True
    non_github_default: InputMethodDefault = "manual"


class PrepareConstraints(BaseModel):
    """Allowed values for prepare configuration fields."""

    model_config = ConfigDict(extra="forbid")

    rigor: List[RigorLevel] = Field(default_factory=lambda: ["low", "medium", "high"])


class PrepareConfig(BaseModel):
    """Declarative ``cafe prepare`` prompt and default metadata."""

    model_config = ConfigDict(extra="forbid")

    prompt_for_spec_plan_config: bool = True
    setup_modes: PrepareSetupModes = Field(default_factory=PrepareSetupModes)
    quick_setup: PrepareQuickSetup = Field(default_factory=PrepareQuickSetup)
    non_interactive_defaults: PrepareNonInteractiveDefaults = Field(
        default_factory=PrepareNonInteractiveDefaults
    )
    input_method: PrepareInputMethod = Field(default_factory=PrepareInputMethod)
    constraints: PrepareConstraints = Field(default_factory=PrepareConstraints)
    fields: Optional[List[PrepareField]] = None
    fields_ref: Optional[str] = None

    @model_validator(mode="after")
    def _validate_fields_source(self) -> "PrepareConfig":
        if self.fields and self.fields_ref:
            raise ValueError(
                "commands.prepare.fields and commands.prepare.fields_ref are mutually exclusive"
            )
        return self


class CommandsConfig(BaseModel):
    """Command-level metadata blocks owned by a playbook."""

    model_config = ConfigDict(extra="forbid")

    prepare: Optional[PrepareConfig] = None


_PREPARE_FIELDS_ONLY_KEYS = frozenset({"fields", "fields_ref"})


def default_prepare_config() -> PrepareConfig:
    """Return backward-compatible prepare defaults matching the built-in default playbook."""
    return PrepareConfig()


def resolve_prepare_config(model: PlaybookDefinition) -> PrepareConfig:
    """Resolve effective prepare metadata, applying defaults when omitted."""
    if model.commands and model.commands.prepare is not None:
        return model.commands.prepare
    return default_prepare_config()


def confirmation_gate_steps(model: PlaybookDefinition) -> tuple[str, ...]:
    """Return ordered steps that declare a planned user confirmation gate.

    ``on.confirm_output`` is the playbook-level declaration that a completed
    step may hand its output to the user for approval. A binding that declares
    feedback delivery is a runtime local-review loop, not a kickoff scheduling
    choice. Other user-owned intents such as clarification, permission, and
    alignment checkpoints are reactive safety interruptions rather than
    kickoff confirmation choices.
    """
    return tuple(
        step_name
        for step_name, step in model.steps.items()
        if "confirm_output" in step.on
        and not any(binding.feedback_delivery is not None for binding in step.human_tasks)
    )


class PlaybookDefinition(BaseModel):
    """Top-level playbook definition."""

    model_config = ConfigDict(extra="forbid")

    playbook: PlaybookMeta
    roles: Dict[str, PlaybookRole] = Field(default_factory=dict)
    skills: Optional[PlaybookSkillEnvironments] = None
    behavior: StepBehaviorDeclaration = Field(default_factory=StepBehaviorDeclaration)
    steps: Dict[str, StepConfig]
    commands: Optional[CommandsConfig] = None
    entry_point: Optional[str] = None

    @model_validator(mode="after")
    def _default_entry_point(self) -> "PlaybookDefinition":
        if self.entry_point is None:
            self.entry_point = next(iter(self.steps.keys()))

        def declares_workflow_feedback(step: StepConfig) -> bool:
            return (
                "input_artifacts" in step.model_fields_set
                and "workflow_feedback" in (step.input_artifacts or [])
            )

        def github_pr_feedback_source_stages(step: StepConfig) -> list[str]:
            return [
                stage_name
                for stage_name, hooks in (
                    ("before_execute", step.hooks.before_execute),
                    ("prepare_input", step.hooks.prepare_input),
                    ("after_execute", step.hooks.after_execute),
                    ("publish_output", step.hooks.publish_output),
                )
                if any(
                    hook == "GitHubPRFeedbackSource"
                    or (
                        isinstance(hook, dict)
                        and hook.get("name") == "GitHubPRFeedbackSource"
                    )
                    for hook in hooks
                )
            ]

        for step_name, step in self.steps.items():
            behavior = resolve_step_behavior(self, step_name)
            target = behavior.feedback_target
            feedback_source_stages = github_pr_feedback_source_stages(step)
            if feedback_source_stages:
                if any(stage != "prepare_input" for stage in feedback_source_stages):
                    raise ValueError(
                        f"steps.{step_name}.hooks GitHubPRFeedbackSource only supports "
                        "hooks.prepare_input"
                    )
                direct_target = step.behavior.feedback_target
                if not isinstance(direct_target, str) or not direct_target.strip():
                    raise ValueError(
                        f"steps.{step_name}.hooks GitHubPRFeedbackSource requires "
                        "behavior.feedback_target"
                    )
            if target is not None and target not in self.steps:
                raise ValueError(
                    f"steps.{step_name}.behavior.feedback_target {target!r} is not a defined step"
                )
            if target is not None and not declares_workflow_feedback(self.steps[target]):
                raise ValueError(
                    f"steps.{step_name}.behavior.feedback_target {target!r} must declare "
                    "workflow_feedback in input_artifacts"
                )
            for binding in step.human_tasks:
                if binding.feedback_delivery is None:
                    continue
                for delivery_target in [
                    *binding.outcomes.values(),
                    *binding.allowed_targets,
                ]:
                    if (
                        delivery_target != DONE_TARGET
                        and delivery_target in self.steps
                        and not declares_workflow_feedback(self.steps[delivery_target])
                    ):
                        raise ValueError(
                            f"steps.{step_name}.human_tasks feedback_delivery target "
                            f"{delivery_target!r} must declare workflow_feedback in input_artifacts"
                        )
            if (
                behavior.publish_confirmation
                and "cafe.pr.publish" not in step.capability_requests
            ):
                raise ValueError(
                    f"steps.{step_name}.behavior.publish_confirmation requires "
                    "the cafe.pr.publish capability request"
                )
            _validate_ownership_contract(step_name, step, self.steps)
        return self


def resolve_step_behavior(
    playbook: PlaybookDefinition | Dict[str, Any], step_name: str
) -> EffectiveStepBehavior:
    """Resolve one step's declaration without considering its name.

    Omitted declarations use universal schema defaults.  This function is the
    only behavior merge point used by the runtime.
    """
    if isinstance(playbook, PlaybookDefinition):
        defaults = playbook.behavior
        step = playbook.steps[step_name]
        override = step.behavior
    else:
        defaults = StepBehaviorDeclaration.model_validate(playbook.get("behavior") or {})
        steps = playbook.get("steps") or {}
        if step_name not in steps:
            # Direct helper calls can supply a transient step definition that
            # is not part of a test fixture's complete playbook.  It receives
            # the same universal defaults as every omitted declaration.
            return EffectiveStepBehavior()
        override = StepBehaviorDeclaration.model_validate(steps[step_name].get("behavior") or {})
    return EffectiveStepBehavior(
        completion=_behavior_value(defaults, override, "completion", "status_code"),
        publish_confirmation=_behavior_value(defaults, override, "publish_confirmation", False),
        feedback_target=_behavior_value(defaults, override, "feedback_target", None),
        context_providers=_behavior_value(defaults, override, "context_providers", []),
        runtime_tool_grants=_behavior_value(defaults, override, "runtime_tool_grants", []),
    )


def resolve_playbook_skills(
    playbook: PlaybookDefinition | Dict[str, Any],
    *,
    channel: Literal["workflow", "chat"],
    role: Optional[str],
    step_name: Optional[str],
) -> List[str]:
    """Resolve one playbook-owned environment in shared → role → step order."""
    if isinstance(playbook, PlaybookDefinition):
        environments: Any = playbook.skills
    else:
        environments = playbook.get("skills")

    if environments is None:
        return []
    environment = (
        getattr(environments, channel, None)
        if isinstance(environments, PlaybookSkillEnvironments)
        else environments.get(channel)
    )
    if environment is None:
        return []

    if isinstance(environment, SkillEnvironmentChannel):
        shared = environment.shared
        role_overlay = environment.roles.get(role) if role else None
        step_overlay = environment.steps.get(step_name) if step_name else None
    else:
        shared = environment.get("shared", [])
        role_overlay = environment.get("roles", {}).get(role) if role else None
        step_overlay = environment.get("steps", {}).get(step_name) if step_name else None

    resolved = list(shared)
    for overlay in (role_overlay, step_overlay):
        if overlay is None:
            continue
        if isinstance(overlay, SkillEnvironmentOverlay):
            mode, names = overlay.mode, overlay.skills
        else:
            mode, names = overlay["mode"], overlay["skills"]
        resolved = list(names) if mode == "replace" else [*resolved, *names]
    return list(dict.fromkeys(resolved))


def iter_declared_playbook_skills(model: PlaybookDefinition) -> List[tuple[str, str]]:
    """Return every declared support skill with its actionable YAML field path."""
    if model.skills is None:
        return []

    references: List[tuple[str, str]] = []
    for channel_name in ("workflow", "chat"):
        environment = getattr(model.skills, channel_name)
        if environment is None:
            continue
        for index, skill_name in enumerate(environment.shared):
            references.append((f"skills.{channel_name}.shared[{index}]", skill_name))
        for scope_name, overlays in (("roles", environment.roles), ("steps", environment.steps)):
            for scope_key, overlay in overlays.items():
                for index, skill_name in enumerate(overlay.skills):
                    references.append(
                        (
                            f"skills.{channel_name}.{scope_name}.{scope_key}.skills[{index}]",
                            skill_name,
                        )
                    )
    return references


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
        hybrid = step.get("hybrid")
        if isinstance(hybrid, dict) and isinstance(hybrid.get("portions"), list):
            for portion in hybrid["portions"]:
                if isinstance(portion, dict) and "on" not in portion and True in portion:
                    value = portion.pop(True)
                    if isinstance(value, dict):
                        portion["on"] = value
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

    entry = model.entry_point
    if entry is not None and entry not in steps:
        raise ValueError(f"entry_point {entry!r} is not a defined step")

    _validate_initial_input_declarations(model, source=source)

    _report_structural_issue(
        playbook_id=model.playbook.id,
        filename=path.stem,
        source=source,
        strict=strict,
        warnings=warnings,
    )
    _validate_skill_environments(model, skill_loader=skill_loader, warnings=warnings)

    for step_name, step in steps.items():
        _validate_step_role(step_name, step, model.roles)
        _validate_step_chat_role(step_name, step, model.roles)
        _validate_step_skills(step_name, step, skill_loader)
        _validate_step_required_prompt_inputs(step_name, step, skill_loader)
        _validate_step_required_tools(step_name, step, skill_loader)
        _validate_step_human_tasks(step_name, step, steps, skill_loader)
        _validate_ownership_contract(step_name, step, steps)
        _validate_script_hook_stages(step_name, step.hooks)
        _validate_targets(step_name, step.allowed_goto, steps, "allowed_goto")
        _validate_transition_targets(step_name, step.on, steps)
        warnings.extend(_collect_tool_warnings(step_name, step.allowed_tools))
    _validate_feedback_target_prompt_inputs(model, skill_loader=skill_loader)
    _validate_prepare_metadata(
        model,
        skill_loader=skill_loader,
        playbook_path=path,
        source=source,
        warnings=warnings,
    )

    if warnings and strict:
        raise ValueError("\n".join(warnings))
    return warnings


def _validate_skill_environments(
    model: PlaybookDefinition,
    *,
    skill_loader: SkillLoader,
    warnings: List[str],
) -> None:
    """Validate playbook-owned support skills before workflow or chat starts."""
    environments = model.skills
    for channel_name in ("workflow", "chat"):
        environment = getattr(environments, channel_name) if environments is not None else None
        if environment is None:
            warnings.append(
                f"skills.{channel_name} is missing; declare it explicitly, for example "
                f"skills: {{{channel_name}: {{shared: []}}}}."
            )
            continue

        for role_name in environment.roles:
            if role_name not in model.roles:
                raise ValueError(
                    f"skills.{channel_name}.roles.{role_name} references an unknown playbook role; "
                    "add the role or remove the overlay"
                )
        for step_name in environment.steps:
            if step_name not in model.steps:
                raise ValueError(
                    f"skills.{channel_name}.steps.{step_name} references an unknown playbook step; "
                    "add the step or remove the overlay"
                )

    for field_path, skill_name in iter_declared_playbook_skills(model):
        try:
            skill_loader.get_skill_dir(skill_name)
        except (SkillDiscoveryError, FileNotFoundError) as exc:
            raise ValueError(
                f"{field_path} references unknown skill {skill_name!r}; "
                "install it or correct the declaration"
            ) from exc


def _validate_initial_input_declarations(model: PlaybookDefinition, *, source: str) -> None:
    """Fail closed on invalid initial-input declarations before execution."""
    registered = registered_initial_input_providers()
    for step_name, step in model.steps.items():
        declaration = step.initial_input
        if declaration is None:
            continue
        field_path = f"steps.{step_name}.initial_input"
        if step_name != model.entry_point:
            raise ValueError(f"{field_path} is only allowed on entry_point {model.entry_point!r}")
        if declaration.legacy_presentation and (
            source != "builtin" or model.playbook.id not in {"default", "simple", "tdd"}
        ):
            raise ValueError(
                f"{field_path}.legacy_presentation is reserved for bundled development playbooks"
            )
        missing = [provider for provider in declaration.providers if provider not in registered]
        if missing:
            raise ValueError(
                f"{field_path}.providers declares {missing[0]!r}, which has no trusted "
                "host implementation"
            )
        artifact = declaration.bind.artifact
        if artifact is not None and artifact != step.output_artifact:
            raise ValueError(
                f"{field_path}.bind.artifact {artifact!r} must match output_artifact "
                f"{step.output_artifact!r}"
            )
        if "InitialInputProviderResolver" not in step.hooks.prepare_input:
            raise ValueError(
                f"{field_path} requires hooks.prepare_input to include "
                "'InitialInputProviderResolver'"
            )


def _validate_prepare_metadata(
    model: PlaybookDefinition,
    *,
    skill_loader: SkillLoader,
    playbook_path: Path,
    source: str,
    warnings: List[str],
) -> None:
    """Validate prepare metadata templates, rigor constraints, and declarative fields."""
    prepare = resolve_prepare_config(model)
    declared_prepare = model.commands.prepare if model.commands else None
    spec_manager = TemplateManager(template_type="spec")
    plan_manager = TemplateManager(template_type="plan")
    template_managers = declared_template_managers(model, skill_loader)

    parsed_fields = resolve_prepare_fields(
        prepare,
        playbook_path=playbook_path,
        skill_loader=skill_loader,
    )

    if parsed_fields is None:
        if source == "builtin" and prepare.prompt_for_spec_plan_config:
            raise ValueError(
                "bundled interactive prepare requires commands.prepare.fields or "
                "commands.prepare.fields_ref"
            )
        if source in {"project", "global"} and (
            declared_prepare is None or prepare.prompt_for_spec_plan_config
        ):
            warnings.append(
                "Legacy interactive prepare is deprecated; migrate to "
                "commands.prepare.fields or commands.prepare.fields_ref."
            )

    if parsed_fields is None:
        _validate_prepare_template(
            spec_manager,
            prepare.quick_setup.spec.template,
            "commands.prepare.quick_setup.spec.template",
        )
        _validate_prepare_template(
            plan_manager,
            prepare.quick_setup.plan.template,
            "commands.prepare.quick_setup.plan.template",
        )
        _validate_prepare_template(
            spec_manager,
            prepare.non_interactive_defaults.spec_template,
            "commands.prepare.non_interactive_defaults.spec_template",
        )
        _validate_prepare_template(
            plan_manager,
            prepare.non_interactive_defaults.plan_template,
            "commands.prepare.non_interactive_defaults.plan_template",
        )

        allowed_rigor = set(prepare.constraints.rigor)
        if prepare.quick_setup.spec.rigor not in allowed_rigor:
            raise ValueError(
                "commands.prepare.quick_setup.spec.rigor "
                f"{prepare.quick_setup.spec.rigor!r} is not listed in "
                f"commands.prepare.constraints.rigor"
            )
        if prepare.non_interactive_defaults.rigor not in allowed_rigor:
            raise ValueError(
                "commands.prepare.non_interactive_defaults.rigor "
                f"{prepare.non_interactive_defaults.rigor!r} is not listed in "
                f"commands.prepare.constraints.rigor"
            )
        return

    has_explicit_legacy_prepare_metadata = bool(
        prepare.model_fields_set - _PREPARE_FIELDS_ONLY_KEYS
    )
    validate_field_semantics(
        parsed_fields.fields,
        prepare,
        spec_manager=spec_manager,
        plan_manager=plan_manager,
        template_managers=template_managers,
        step_names=set(model.steps),
        enforce_legacy_setup_modes=has_explicit_legacy_prepare_metadata,
    )

    if has_explicit_legacy_prepare_metadata:
        assert_prepare_semantics_match(model.commands.prepare, parsed_fields)


def declared_template_managers(
    model: PlaybookDefinition,
    skill_loader: SkillLoader,
) -> Dict[str, TemplateManager]:
    """Resolve template catalogs from each step's selected skill contract."""
    managers: Dict[str, TemplateManager] = {}
    for step_name, step in model.steps.items():
        selectors = [step.skill] if isinstance(step.skill, str) else list(step.skill.values())
        contracts = [skill_loader.get_workflow_contract(skill) for skill in selectors]
        catalogs = {
            contract.output_templates.catalog
            for contract in contracts
            if contract.output_templates is not None
        }
        if len(catalogs) > 1:
            raise ValueError(
                f"Step {step_name!r} selects skills with incompatible template catalogs"
            )
        if not catalogs:
            if step.template is not None:
                raise ValueError(
                    f"Step {step_name!r} declares a template without a skill template catalog"
                )
            continue
        skill_name = canonical_skill_name(str(selectors[0]))
        manager = TemplateManager(
            template_type=next(iter(catalogs)),
            skill_name=skill_name,
            skill_loader=skill_loader,
        )
        if step.template is not None:
            _validate_prepare_template(manager, step.template, f"steps.{step_name}.template")
        managers[step_name] = manager
    return managers


def _validate_prepare_template(
    manager: TemplateManager,
    template_name: str,
    field_path: str,
) -> None:
    if template_name == "auto":
        return
    if not manager.template_exists(template_name):
        raise ValueError(f"Unknown template {template_name!r} for {field_path}")


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


def _validate_step_chat_role(
    step_name: str, step: StepConfig, roles: Dict[str, PlaybookRole]
) -> None:
    if roles and step.chat_role and step.chat_role not in roles:
        raise ValueError(f"Step '{step_name}' references unknown chat_role '{step.chat_role}'")


def _validate_step_skills(step_name: str, step: StepConfig, skill_loader: SkillLoader) -> None:
    selectors = [step.skill] if isinstance(step.skill, str) else list(step.skill.values())
    for skill_name in selectors:
        try:
            skill_loader.get_skill_dir(skill_name)
        except (SkillDiscoveryError, FileNotFoundError) as exc:
            raise ValueError(f"Step '{step_name}' references unknown skill '{skill_name}'") from exc


def _validate_step_required_prompt_inputs(
    step_name: str,
    step: StepConfig,
    skill_loader: SkillLoader,
) -> None:
    """Reject a step whose artifact graph cannot satisfy a required mapping."""
    if "input_artifacts" not in step.model_fields_set:
        return
    selectors = [step.skill] if isinstance(step.skill, str) else list(step.skill.values())
    declared_artifacts = set(step.input_artifacts or [])
    for skill_name in selectors:
        contract = skill_loader.get_workflow_contract(skill_name)
        for mapping in contract.prompt_inputs:
            if mapping.required and not declared_artifacts.intersection(mapping.artifacts):
                candidates = ", ".join(mapping.artifacts)
                raise ValueError(
                    f"Step {step_name!r}, skill {canonical_skill_name(skill_name)!r}: "
                    f"required prompt input {mapping.placeholder!r} expects one of "
                    f"[{candidates}], but input_artifacts declares "
                    f"{sorted(declared_artifacts)}"
                )


def _validate_feedback_target_prompt_inputs(
    model: PlaybookDefinition,
    *,
    skill_loader: SkillLoader,
) -> None:
    """Ensure routed feedback is exposed to every possible target skill."""
    def receives_workflow_feedback(skill_name: str) -> bool:
        return any(
            mapping.artifacts[0] == "workflow_feedback"
            for mapping in skill_loader.get_workflow_contract(skill_name).prompt_inputs
        )

    for step_name, step in model.steps.items():
        behavior = resolve_step_behavior(model, step_name)
        targets: List[tuple[str, str]] = []
        if behavior.feedback_target is not None:
            targets.append(("behavior.feedback_target", behavior.feedback_target))
        for binding in step.human_tasks:
            if binding.feedback_delivery is None:
                continue
            targets.extend(
                ("human_tasks feedback_delivery", target)
                for target in [*binding.outcomes.values(), *binding.allowed_targets]
                if target != DONE_TARGET
            )

        for source, target_name in targets:
            target = model.steps[target_name]
            selectors = (
                [target.skill]
                if isinstance(target.skill, str)
                else list(target.skill.values())
            )
            missing = [
                canonical_skill_name(skill_name)
                for skill_name in selectors
                if not receives_workflow_feedback(skill_name)
            ]
            if missing:
                raise ValueError(
                    f"Step {step_name!r} {source} target {target_name!r} must declare "
                    "a prompt input for workflow_feedback; missing from "
                    f"{missing}"
                )


def _tool_requirement_satisfied(required: str, allowed_tools: List[str]) -> bool:
    if required in allowed_tools:
        return True
    tool_name = required.split("(", 1)[0]
    return tool_name in allowed_tools or f"{tool_name}(*)" in allowed_tools


def _validate_step_required_tools(
    step_name: str,
    step: StepConfig,
    skill_loader: SkillLoader,
) -> None:
    """Reject a step that cannot execute its selected skill's declared tools."""
    selectors = [step.skill] if isinstance(step.skill, str) else list(step.skill.values())
    for skill_name in selectors:
        contract = skill_loader.get_workflow_contract(skill_name)
        missing = [
            required
            for required in contract.required_tools
            if not _tool_requirement_satisfied(required, step.allowed_tools)
        ]
        if missing:
            raise ValueError(
                f"Step {step_name!r}, skill {canonical_skill_name(skill_name)!r}: "
                f"allowed_tools is missing required declarations {missing}"
            )


def _validate_step_human_tasks(
    step_name: str,
    step: StepConfig,
    steps: Dict[str, StepConfig],
    skill_loader: SkillLoader,
) -> None:
    """Ensure every policy binding names a skill task and declared destinations."""
    if not step.human_tasks:
        return
    hybrid_human_triggers = {
        portion.id
        for portion in (step.hybrid.portions if step.hybrid is not None else ())
        if portion.owner == "human"
    }
    selectors = [step.skill] if isinstance(step.skill, str) else list(step.skill.values())
    contracts = [skill_loader.get_workflow_contract(skill) for skill in selectors]
    for binding in step.human_tasks:
        if (
            binding.trigger != "initial"
            and binding.trigger not in step.on
            and binding.trigger not in hybrid_human_triggers
        ):
            raise ValueError(
                f"Step '{step_name}' human task trigger {binding.trigger!r} "
                "is not declared in its transitions"
            )
        for skill_name, contract in zip(selectors, contracts):
            matching_policies = [
                policy for policy in contract.human_tasks if policy.id == binding.task_id
            ]
            if not matching_policies:
                raise ValueError(
                    f"Step '{step_name}', skill {canonical_skill_name(str(skill_name))!r}: "
                    f"unknown human task {binding.task_id!r}"
                )
            policy = matching_policies[0]
            if binding.feedback_delivery is not None and not (
                policy.input_schema == "feedback"
                or any(decision.requires_feedback for decision in policy.decisions)
            ):
                raise ValueError(
                    f"Step '{step_name}', skill {canonical_skill_name(str(skill_name))!r}: "
                    f"human task {binding.task_id!r} cannot deliver feedback because its policy "
                    "does not collect feedback"
                )
            if any(decision.requires_target for decision in policy.decisions) and not (
                binding.allowed_targets
            ):
                raise ValueError(
                    f"Step '{step_name}', skill {canonical_skill_name(str(skill_name))!r}: "
                    f"human task {binding.task_id!r} requires allowed_targets on the binding"
                )
        for target in [*binding.outcomes.values(), *binding.allowed_targets]:
            if target != DONE_TARGET and target not in steps:
                raise ValueError(
                    f"Step '{step_name}' has invalid human task outcome target {target!r}"
                )


def _validate_ownership_contract(
    step_name: str,
    step: StepConfig,
    steps: Dict[str, StepConfig],
) -> None:
    """Validate explicit owner declarations before runtime dispatch can begin."""
    if step.assignee_type == "auto":
        if step.automatic is None or not step.on:
            raise ValueError(
                f"Step '{step_name}' automatic owner requires an executor and declared transitions"
            )
        return
    if step.assignee_type == "human":
        initial = [binding for binding in step.human_tasks if binding.trigger == "initial"]
        if len(step.human_tasks) != 1 or len(initial) != 1:
            raise ValueError(
                f"Step '{step_name}' human owner requires exactly one initial human task binding"
            )
        return
    if step.assignee_type != "hybrid":
        return

    hybrid = step.hybrid
    assert hybrid is not None
    portions = {portion.id: portion for portion in hybrid.portions}
    human_portions = {
        portion.id: portion for portion in hybrid.portions if portion.owner == "human"
    }
    bindings = {binding.trigger: binding for binding in step.human_tasks}
    if set(bindings) != set(human_portions):
        raise ValueError(
            f"Step '{step_name}' hybrid human portions require exactly one matching "
            "human task binding"
        )

    for portion in hybrid.portions:
        if portion.owner == "agent":
            invalid = set(portion.on) - PLAYBOOK_INTENT_KEYS
            if invalid:
                raise ValueError(
                    f"Step '{step_name}' hybrid agent portion {portion.id!r} has invalid "
                    f"completion key {sorted(invalid)[0]!r}"
                )
        for target in portion.on.values():
            if target.step is not None and target.step != DONE_TARGET and target.step not in steps:
                raise ValueError(
                    f"Step '{step_name}' hybrid portion {portion.id!r} targets unknown "
                    f"step {target.step!r}"
                )

    for portion_id, portion in human_portions.items():
        binding = bindings[portion_id]
        if set(binding.outcomes) != set(portion.on):
            raise ValueError(
                f"Step '{step_name}' hybrid human portion {portion_id!r} outcomes must "
                "match its declared continuations"
            )
        if any(target != step_name for target in binding.outcomes.values()):
            raise ValueError(
                f"Step '{step_name}' hybrid human task outcomes must return to the containing step"
            )

    seen: set[tuple[str, bool]] = set()

    def walk(portion_id: str, human_crossed: bool) -> None:
        state = (portion_id, human_crossed)
        if state in seen:
            return
        seen.add(state)
        portion = portions[portion_id]
        crossed = human_crossed or portion.owner == "human"
        for target in portion.on.values():
            if target.step is not None:
                if not crossed:
                    raise ValueError(
                        f"Step '{step_name}' hybrid exit from portion {portion_id!r} "
                        "bypasses its required human boundary"
                    )
            elif target.portion is not None:
                walk(target.portion, crossed)

    walk(hybrid.entry_portion, False)


def _validate_script_hook_stages(step_name: str, hooks: StepHooks) -> None:
    stage_entries = {
        "before_execute": hooks.before_execute,
        "prepare_input": hooks.prepare_input,
        "after_execute": hooks.after_execute,
        "publish_output": hooks.publish_output,
    }
    for stage_name, entries in stage_entries.items():
        if stage_name in SCRIPT_HOOK_STAGES:
            continue
        for entry in entries:
            if isinstance(entry, dict) and "script" in entry:
                raise ValueError(
                    f"Step '{step_name}' has script hook in unsupported stage '{stage_name}'"
                )


def _validate_targets(
    step_name: str,
    targets: List[str],
    steps: Dict[str, StepConfig],
    field_name: str,
) -> None:
    for target in targets:
        if target not in steps:
            raise ValueError(f"Step '{step_name}' has invalid {field_name} target '{target}'")


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
        tool.strip(): tool.strip() for tool in allowed_tools if "(" not in tool and tool.strip()
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
                f"Step '{step_name}': redundant allowed_tools entry '{normalized}' "
                f"because '{tool_name}' already allows it"
            )

    return warnings
