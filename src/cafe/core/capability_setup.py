"""Resolve capability-owned setup questions without executing or approving effects."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from cafe.core.capabilities import (
    default_capability_definition_dirs,
    load_capability_registry,
    CapabilityManifest,
    CapabilitySetupChoice,
    CapabilitySetupQuestion,
)
from cafe.core.playbook import PlaybookDefinition, normalize_playbook_yaml
from cafe.utils.issue_config import (
    issue_config_lock,
    read_issue_config_strict,
    resolve_issue_config_path,
    write_issue_config_atomic,
)


def resolve_setup_choices(
    model: PlaybookDefinition,
    registry: Mapping[str, CapabilityManifest],
    answers: Sequence[str],
) -> list[tuple[CapabilitySetupQuestion, CapabilitySetupChoice]]:
    """Require one typed answer per declared question, with no inferred defaults."""
    questions: dict[str, CapabilitySetupQuestion] = {}
    requested = dict.fromkeys(
        capability for step in model.steps.values() for capability in step.capability_requests
    )
    for capability in requested:
        if capability not in registry:
            raise ValueError(f"unknown declared capability: {capability}")
        for question in registry[capability].setup_questions:
            if question.setting in questions:
                raise ValueError(f"duplicate capability setup setting: {question.setting}")
            questions[question.setting] = question

    supplied: dict[str, object] = {}
    for answer in answers:
        setting, separator, encoded = answer.partition("=")
        if not separator:
            raise ValueError("--capability-choice uses SETTING=JSON")
        if setting not in questions:
            raise ValueError(f"--capability-choice is not applicable: {setting}")
        if setting in supplied:
            raise ValueError(f"duplicate --capability-choice: {setting}")
        try:
            supplied[setting] = json.loads(encoded)
        except ValueError as exc:
            raise ValueError(f"--capability-choice requires a JSON value: {setting}") from exc

    resolved = []
    for setting, question in questions.items():
        if setting not in supplied:
            raise ValueError(f"--capability-choice is required for {setting}")
        value = supplied[setting]
        selected = next(
            (
                choice
                for choice in question.choices
                if type(value) is type(choice.value) and value == choice.value
            ),
            None,
        )
        if selected is None:
            raise ValueError(f"invalid --capability-choice for {setting}")
        resolved.append((question, selected))
    return resolved


def resolve_setup_choice(
    model: PlaybookDefinition,
    registry: Mapping[str, CapabilityManifest],
    setting: str,
    value: object,
) -> tuple[CapabilitySetupQuestion, CapabilitySetupChoice]:
    """Validate one capability-owned setting without requiring unrelated answers."""
    requested = dict.fromkeys(
        capability for step in model.steps.values() for capability in step.capability_requests
    )
    matches: list[CapabilitySetupQuestion] = []
    for capability in requested:
        if capability not in registry:
            raise ValueError(f"unknown declared capability: {capability}")
        matches.extend(
            question
            for question in registry[capability].setup_questions
            if question.setting == setting
        )
    if len(matches) != 1:
        raise ValueError(f"capability setting is not applicable: {setting}")
    question = matches[0]
    selected = next(
        (
            choice
            for choice in question.choices
            if type(value) is type(choice.value) and value == choice.value
        ),
        None,
    )
    if selected is None:
        raise ValueError(f"invalid capability setting value: {setting}")
    return question, selected


@dataclass(frozen=True)
class PrAutoCreateUpdateResult:
    status: str
    changes: Mapping[str, Any]
    config_path: Path


def _load_effective_playbook(config: Mapping[str, Any], config_path: Path) -> PlaybookDefinition:
    name = config.get("playbook_id")
    if not isinstance(name, str) or not name or Path(name).name != name:
        raise ValueError("issue.yaml has an invalid playbook_id")
    repository_root = config_path.parents[3]
    roots = (
        repository_root / ".cafe" / "playbooks",
        Path.home() / ".cafe" / "playbooks",
        Path(__file__).resolve().parents[1] / "data" / "playbooks",
    )
    path = next(
        (
            candidate
            for root in roots
            for candidate in (root / f"{name}.yaml", root / f"{name}.yml")
            if candidate.is_file()
        ),
        None,
    )
    if path is None:
        raise ValueError(f"playbook is unavailable: {name}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return PlaybookDefinition.model_validate(normalize_playbook_yaml(raw))
    except (OSError, yaml.YAMLError, ValueError) as exc:
        raise ValueError(f"playbook is invalid: {name}") from exc


def update_pr_auto_create(
    *, config_path: Path, value: bool, preview: bool = False
) -> PrAutoCreateUpdateResult:
    """Preview or save the capability-owned ``pr.auto_create`` choice."""
    if type(value) is not bool:
        raise ValueError("pr.auto_create must be an exact Boolean")
    authority = resolve_issue_config_path(config_path, require_registered_worktree=True)

    def build() -> tuple[dict[str, Any], bool | None]:
        config = read_issue_config_strict(authority)
        pr = config.get("pr", {})
        if not isinstance(pr, dict):
            raise ValueError("issue.yaml pr must be a mapping")
        model = _load_effective_playbook(config, authority)
        registry = load_capability_registry(
            default_capability_definition_dirs(authority.parents[3])
        )
        resolve_setup_choice(model, registry, "pr.auto_create", value)
        old = pr.get("auto_create")
        if old is not None and type(old) is not bool:
            raise ValueError("existing pr.auto_create must be a Boolean")
        updated_pr = dict(pr)
        updated_pr["auto_create"] = value
        config["pr"] = updated_pr
        return config, old

    if preview:
        _config, old = build()
        status = "unchanged" if old is value else "proposed"
        changes = {"pr.auto_create": {"before": old, "after": value}}
        return PrAutoCreateUpdateResult(status, changes, authority)
    with issue_config_lock(authority):
        config, old = build()
        changes = {"pr.auto_create": {"before": old, "after": value}}
        if old is value:
            return PrAutoCreateUpdateResult("unchanged", changes, authority)
        write_issue_config_atomic(authority, config)
        return PrAutoCreateUpdateResult("saved", changes, authority)
