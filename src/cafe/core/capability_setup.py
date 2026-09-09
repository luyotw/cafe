"""Resolve capability-owned setup questions without executing or approving effects."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from cafe.core.capabilities import (
    CapabilityManifest,
    CapabilitySetupChoice,
    CapabilitySetupQuestion,
)
from cafe.core.playbook import PlaybookDefinition


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
