"""Declarative prepare field schema, loading, and semantic validation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cafe.skills.exceptions import SkillDiscoveryError
from cafe.skills.loader import SkillLoader
from cafe.templates.manager import TemplateManager

ALLOWED_WRITE_TARGETS = frozenset(
    {
        "spec.rigor",
        "spec.template",
        "spec.sync_github",
        "spec.input_method",
        "spec.issue_id",
        "plan.template",
        "plan.sync_github",
        "pr.auto_create",
        "pr.post_todo_list",
    }
)

ALLOWED_FIELD_TYPES = frozenset({"enum", "boolean", "template", "text", "setup_mode"})
ALLOWED_SHOW_WHEN_KEYS = frozenset({"github_repo", "issue_id_present", "setup_mode"})
ALLOWED_SETUP_MODES = frozenset({"quick", "custom"})
ALLOWED_STATIC_SUFFIXES = frozenset({".yaml", ".yml", ".json"})
STEP_TEMPLATE_WRITE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*\.template$")

SKILL_FIELDS_REF_PATTERN = re.compile(r"^skill://([^/]+)/assets/(.+)$")


class FieldsRefKind(str, Enum):
    SKILL_ASSET = "skill_asset"
    PLAYBOOK_RELATIVE = "playbook_relative"


class PrepareFieldChoice(BaseModel):
    """One enum/setup_mode choice."""

    model_config = ConfigDict(extra="forbid")

    value: str
    label: str
    description: Optional[str] = None


class ShowWhen(BaseModel):
    """Simple visibility conditions for one prepare field."""

    model_config = ConfigDict(extra="forbid")

    github_repo: Optional[bool] = None
    issue_id_present: Optional[bool] = None
    setup_mode: Optional[Literal["quick", "custom"]] = None

    @model_validator(mode="after")
    def _validate_known_keys_only(self) -> "ShowWhen":
        return self


class PrepareField(BaseModel):
    """One declarative prepare question."""

    model_config = ConfigDict(extra="forbid")

    id: str
    type: Literal["enum", "boolean", "template", "text", "setup_mode"]
    label: str
    write: Optional[str] = None
    help: Optional[str] = None
    default: Optional[Any] = None
    choices: List[PrepareFieldChoice] = Field(default_factory=list)
    show_when: Optional[ShowWhen] = None
    group: Optional[str] = None

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field id must not be empty")
        return value

    @field_validator("write")
    @classmethod
    def _validate_write(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if value not in ALLOWED_WRITE_TARGETS and not STEP_TEMPLATE_WRITE_PATTERN.fullmatch(value):
            raise ValueError(
                f"unknown write target {value!r}; "
                f"must be one of {sorted(ALLOWED_WRITE_TARGETS)}"
            )
        return value

    @model_validator(mode="after")
    def _validate_type_constraints(self) -> "PrepareField":
        if self.type in {"enum", "setup_mode"} and not self.choices:
            raise ValueError(f"field {self.id!r} requires choices for type {self.type!r}")
        if self.type == "setup_mode" and self.write is not None:
            raise ValueError(f"setup_mode field {self.id!r} must not declare write target")
        if self.type != "setup_mode" and self.write is None:
            raise ValueError(f"field {self.id!r} requires write target")
        return self


class PrepareFieldsMeta(BaseModel):
    """Optional document-level prepare metadata mirrored from legacy blocks."""

    model_config = ConfigDict(extra="forbid")

    prompt_for_spec_plan_config: Optional[bool] = None


class PrepareFieldsDocument(BaseModel):
    """Top-level document loaded from inline fields or fields_ref assets."""

    model_config = ConfigDict(extra="forbid")

    meta: Optional[PrepareFieldsMeta] = None
    fields: List[PrepareField]


@dataclass(frozen=True)
class ParsedPrepareFields:
    """Parsed prepare field document."""

    fields: List[PrepareField]
    meta: Optional[PrepareFieldsMeta] = None


def parse_prepare_fields_document(raw: Any) -> ParsedPrepareFields:
    """Parse a fields document or bare list into typed prepare fields."""
    if isinstance(raw, list):
        return ParsedPrepareFields(fields=[PrepareField.model_validate(item) for item in raw])
    if isinstance(raw, dict):
        document = PrepareFieldsDocument.model_validate(raw)
        return ParsedPrepareFields(fields=document.fields, meta=document.meta)
    raise ValueError("prepare fields document must be a list or mapping with 'fields'")


def parse_prepare_fields(raw: Any) -> List[PrepareField]:
    """Parse a fields document or bare list into typed prepare fields."""
    return parse_prepare_fields_document(raw).fields


def parse_fields_ref(ref: str) -> FieldsRefKind:
    """Classify a fields_ref string."""
    token = str(ref or "").strip()
    if not token:
        raise ValueError("commands.prepare.fields_ref must not be empty")
    if "://" in token:
        if token.startswith("skill://"):
            return FieldsRefKind.SKILL_ASSET
        raise ValueError(f"unsupported fields_ref scheme in {token!r}")
    return FieldsRefKind.PLAYBOOK_RELATIVE


def _validate_relative_path(path: str, *, field_name: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        raise ValueError(f"{field_name} must be a relative path")
    if ".." in candidate.parts:
        raise ValueError(f"{field_name} must not contain '..'")
    if any(part == "scripts" for part in candidate.parts):
        raise ValueError(f"{field_name} must not reference scripts/")
    suffix = candidate.suffix.lower()
    if suffix not in ALLOWED_STATIC_SUFFIXES:
        raise ValueError(
            f"{field_name} must use one of {sorted(ALLOWED_STATIC_SUFFIXES)}"
        )
    return candidate


def _assert_contained(resolved: Path, sandbox: Path, *, field_name: str) -> Path:
    sandbox_resolved = sandbox.resolve()
    target = resolved.resolve()
    if not target.is_relative_to(sandbox_resolved):
        raise ValueError(f"{field_name} resolves outside allowed directory")
    if not target.is_file():
        raise FileNotFoundError(f"{field_name} not found: {target}")
    return target


def resolve_fields_ref_path(
    *,
    ref: str,
    playbook_path: Path,
    skill_loader: SkillLoader,
) -> Path:
    """Resolve fields_ref to a concrete static asset path."""
    kind = parse_fields_ref(ref)
    if kind is FieldsRefKind.SKILL_ASSET:
        match = SKILL_FIELDS_REF_PATTERN.match(ref.strip())
        if match is None:
            raise ValueError(f"invalid skill fields_ref {ref!r}")
        skill_name, asset_path = match.groups()
        relative = _validate_relative_path(asset_path, field_name="commands.prepare.fields_ref")
        try:
            skill_dir = skill_loader.get_skill_dir(skill_name)
        except (SkillDiscoveryError, FileNotFoundError) as exc:
            raise ValueError(
                f"commands.prepare.fields_ref references unknown skill {skill_name!r}"
            ) from exc
        assets_dir = (skill_dir / "assets").resolve()
        target = (assets_dir / relative).resolve()
        return _assert_contained(target, assets_dir, field_name="commands.prepare.fields_ref")

    relative = _validate_relative_path(ref.strip(), field_name="commands.prepare.fields_ref")
    playbook_dir = playbook_path.parent.resolve()
    target = (playbook_dir / relative).resolve()
    return _assert_contained(target, playbook_dir, field_name="commands.prepare.fields_ref")


def _load_static_document(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".json":
        return json.loads(text)
    return yaml.safe_load(text)


def load_fields_ref(
    *,
    ref: str,
    playbook_path: Path,
    skill_loader: SkillLoader,
) -> ParsedPrepareFields:
    """Load prepare fields from a static playbook or skill asset reference."""
    asset_path = resolve_fields_ref_path(
        ref=ref,
        playbook_path=playbook_path,
        skill_loader=skill_loader,
    )
    raw = _load_static_document(asset_path)
    if raw is None:
        raise ValueError(f"commands.prepare.fields_ref asset is empty: {asset_path}")
    return parse_prepare_fields_document(raw)


def resolve_prepare_fields(
    prepare: Any,
    *,
    playbook_path: Path,
    skill_loader: SkillLoader,
) -> Optional[ParsedPrepareFields]:
    """Return effective prepare fields declared on one playbook, if any."""
    if prepare.fields_ref:
        return load_fields_ref(
            ref=prepare.fields_ref,
            playbook_path=playbook_path,
            skill_loader=skill_loader,
        )
    if prepare.fields:
        return ParsedPrepareFields(fields=list(prepare.fields))
    return None


def validate_show_when(show_when: Optional[ShowWhen], *, field_id: str) -> None:
    """Reject unknown show_when keys (handled by pydantic) and invalid setup_mode."""
    if show_when is None:
        return
    if show_when.setup_mode is not None and show_when.setup_mode not in ALLOWED_SETUP_MODES:
        raise ValueError(
            f"field {field_id!r} has invalid show_when.setup_mode {show_when.setup_mode!r}"
        )


def validate_field_semantics(
    fields: List[PrepareField],
    prepare: Any,
    *,
    spec_manager: TemplateManager,
    plan_manager: TemplateManager,
    template_managers: Optional[Dict[str, TemplateManager]] = None,
    enforce_legacy_setup_modes: bool = True,
) -> None:
    """Apply semantic validation to loaded prepare fields."""
    allowed_rigor = set(prepare.constraints.rigor)
    seen_ids: set[str] = set()

    for field in fields:
        if field.id in seen_ids:
            raise ValueError(f"duplicate prepare field id {field.id!r}")
        seen_ids.add(field.id)
        validate_show_when(field.show_when, field_id=field.id)

        if field.type == "template" and field.default is not None:
            step_name = field.write.split(".", 1)[0] if field.write else "plan"
            manager = (template_managers or {}).get(step_name)
            if manager is None:
                manager = spec_manager if step_name == "spec" else plan_manager
            if step_name not in {"spec", "plan"} and step_name not in (template_managers or {}):
                raise ValueError(
                    f"field {field.id!r} targets step {step_name!r} without a declared template catalog"
                )
            _validate_template_default(manager, str(field.default), field)

        if field.write == "spec.rigor":
            _validate_rigor_value(field.default, allowed_rigor, field.id)
            for choice in field.choices:
                if choice.value not in allowed_rigor:
                    raise ValueError(
                        f"field {field.id!r} choice {choice.value!r} is not listed in "
                        "commands.prepare.constraints.rigor"
                    )

        if field.type == "setup_mode" and enforce_legacy_setup_modes:
            legacy = prepare.setup_modes
            expected = [
                (legacy.quick.label, legacy.quick.enabled),
                (legacy.custom.label, legacy.custom.enabled),
            ]
            actual = [(choice.label, True) for choice in field.choices]
            enabled_labels = [label for label, enabled in expected if enabled]
            actual_labels = [label for label, _ in actual]
            if actual_labels != enabled_labels:
                raise ValueError(
                    f"setup_mode field {field.id!r} choices do not match commands.prepare.setup_modes"
                )


def _validate_template_default(
    manager: TemplateManager,
    template_name: str,
    field: PrepareField,
) -> None:
    if template_name == "auto":
        return
    if not manager.template_exists(template_name):
        raise ValueError(
            f"field {field.id!r} references unknown template {template_name!r}"
        )


def _validate_rigor_value(
    value: Any,
    allowed: set[str],
    field_id: str,
) -> None:
    if value is None:
        return
    if value not in allowed:
        raise ValueError(
            f"field {field_id!r} default {value!r} is not listed in "
            "commands.prepare.constraints.rigor"
        )


def _show_when_tuple(show_when: Optional[ShowWhen]) -> tuple[tuple[str, Any], ...]:
    if show_when is None:
        return ()
    items: List[tuple[str, Any]] = []
    if show_when.github_repo is not None:
        items.append(("github_repo", show_when.github_repo))
    if show_when.issue_id_present is not None:
        items.append(("issue_id_present", show_when.issue_id_present))
    if show_when.setup_mode is not None:
        items.append(("setup_mode", show_when.setup_mode))
    return tuple(sorted(items, key=lambda item: item[0]))


def _legacy_field_defaults(prepare: Any) -> Dict[tuple[Optional[str], tuple[tuple[str, Any], ...]], Any]:
    quick = prepare.quick_setup
    defaults: Dict[tuple[Optional[str], tuple[tuple[str, Any], ...]], Any] = {
        ("spec.rigor", (("setup_mode", "quick"),)): quick.spec.rigor,
        ("spec.template", (("setup_mode", "quick"),)): quick.spec.template,
        ("plan.template", (("setup_mode", "quick"),)): quick.plan.template,
        (
            "spec.sync_github",
            (("issue_id_present", True), ("setup_mode", "quick")),
        ): quick.sync_github.when_issue_id_present,
        (
            "spec.sync_github",
            (("issue_id_present", False), ("setup_mode", "quick")),
        ): quick.sync_github.when_manual_input,
        (
            "plan.sync_github",
            (("issue_id_present", True), ("setup_mode", "quick")),
        ): quick.sync_github.when_issue_id_present,
        (
            "plan.sync_github",
            (("issue_id_present", False), ("setup_mode", "quick")),
        ): quick.sync_github.when_manual_input,
        (
            "pr.auto_create",
            (("github_repo", True), ("setup_mode", "quick")),
        ): quick.pr.auto_create_on_github_repo,
        (
            "pr.post_todo_list",
            (("github_repo", True), ("setup_mode", "quick")),
        ): quick.pr.post_todo_list_when_auto_create,
        ("spec.sync_github", (("issue_id_present", True), ("setup_mode", "custom"))): True,
        ("plan.sync_github", (("issue_id_present", True), ("setup_mode", "custom"))): True,
        (
            "pr.post_todo_list",
            (("github_repo", True), ("setup_mode", "custom")),
        ): True,
    }
    return defaults


def _fields_default_map(fields: List[PrepareField]) -> Dict[tuple[Optional[str], tuple[tuple[str, Any], ...]], Any]:
    mapping: Dict[tuple[Optional[str], tuple[tuple[str, Any], ...]], Any] = {}
    for field in fields:
        if field.type == "setup_mode" or field.write is None:
            continue
        mapping[(field.write, _show_when_tuple(field.show_when))] = field.default
    return mapping


def build_legacy_semantics(prepare: Any) -> Dict[str, Any]:
    """Build canonical prepare semantics from legacy commands.prepare metadata."""
    enabled_modes = []
    for name in ("quick", "custom"):
        entry = getattr(prepare.setup_modes, name)
        if entry.enabled:
            enabled_modes.append({"name": name, "label": entry.label})
    return {
        "prompt_for_spec_plan_config": prepare.prompt_for_spec_plan_config,
        "setup_modes": enabled_modes,
        "non_interactive_defaults": prepare.non_interactive_defaults.model_dump(),
        "input_method": prepare.input_method.model_dump(),
        "constraints": prepare.constraints.model_dump(),
        "defaults": _legacy_field_defaults(prepare),
    }


def build_fields_semantics(
    parsed: ParsedPrepareFields,
    *,
    prepare: Any,
) -> Dict[str, Any]:
    """Build canonical prepare semantics from declarative fields."""
    setup_field = next((field for field in parsed.fields if field.type == "setup_mode"), None)
    enabled_modes = []
    legacy_modes = [prepare.setup_modes.quick, prepare.setup_modes.custom]
    if setup_field is not None:
        for index, choice in enumerate(setup_field.choices):
            mode_name = "quick" if index == 0 else "custom"
            enabled = legacy_modes[index].enabled if index < len(legacy_modes) else True
            if enabled:
                enabled_modes.append({"name": mode_name, "label": choice.label})
    else:
        for name in ("quick", "custom"):
            entry = getattr(prepare.setup_modes, name)
            if entry.enabled:
                enabled_modes.append({"name": name, "label": entry.label})

    prompt_for_spec_plan_config = prepare.prompt_for_spec_plan_config
    if parsed.meta is not None and parsed.meta.prompt_for_spec_plan_config is not None:
        prompt_for_spec_plan_config = parsed.meta.prompt_for_spec_plan_config

    return {
        "prompt_for_spec_plan_config": prompt_for_spec_plan_config,
        "setup_modes": enabled_modes,
        "non_interactive_defaults": prepare.non_interactive_defaults.model_dump(),
        "input_method": prepare.input_method.model_dump(),
        "constraints": prepare.constraints.model_dump(),
        "defaults": _fields_default_map(parsed.fields),
    }


def assert_prepare_semantics_match(
    legacy: Any,
    parsed: ParsedPrepareFields,
) -> None:
    """Ensure declarative fields describe the same prepare semantics as legacy metadata."""
    legacy_semantics = build_legacy_semantics(legacy)
    fields_semantics = build_fields_semantics(parsed, prepare=legacy)

    for key in (
        "prompt_for_spec_plan_config",
        "setup_modes",
        "non_interactive_defaults",
        "input_method",
        "constraints",
    ):
        if legacy_semantics[key] != fields_semantics[key]:
            raise ValueError(
                f"commands.prepare.fields disagree with legacy metadata for {key!r}"
            )

    for key, expected in legacy_semantics["defaults"].items():
        actual = fields_semantics["defaults"].get(key)
        if actual != expected:
            raise ValueError(
                f"commands.prepare.fields default for {key!r} disagrees with legacy commands.prepare"
            )
