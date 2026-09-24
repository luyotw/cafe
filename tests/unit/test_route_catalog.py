"""Contract tests for the topology-neutral route projection."""

from __future__ import annotations

from cafe.core.route_catalog import (
    RouteInputRequirement,
    RouteReadiness,
    authorize_route_target,
    build_route_projection,
    evaluate_route_readiness,
    route_choices,
    select_route_label,
)


def _playbook() -> dict:
    return {
        "entry_point": "compose",
        "steps": {
            "compose": {
                "on": {
                    "await_agent": "inspect",
                    "workflow_complete": "_done",
                },
                "allowed_goto": ["compose", "revise"],
            },
            "inspect": {
                "on": {"await_agent": "_done"},
                "allowed_goto": ["revise"],
            },
            "revise": {
                "on": {"need_clarification": "user"},
                "allowed_goto": [],
            },
        },
    }


def test_projection_is_deterministic_bounded_and_field_allowlisted() -> None:
    playbook = _playbook()
    readiness = {
        "inspect": RouteReadiness(ready=False, missing=("accepted_draft",)),
        "compose": RouteReadiness(ready=True),
        "revise": RouteReadiness(ready=True),
        "done": RouteReadiness(ready=True),
    }

    first = build_route_projection(
        playbook,
        current_step="compose",
        labels={
            "inspect": "Inspect the draft",
            "compose": "Continue composing",
            "revise": "Revise from findings",
            "done": "Complete workflow",
        },
        readiness=readiness,
        feedback_targets={"revise"},
    )
    second = build_route_projection(
        playbook,
        current_step="compose",
        labels={
            "inspect": "Inspect the draft",
            "compose": "Continue composing",
            "revise": "Revise from findings",
            "done": "Complete workflow",
        },
        readiness=readiness,
        feedback_targets={"revise"},
    )

    assert first == second
    assert first.graph == {
        "entry": "compose",
        "steps": [
            {
                "from": "compose",
                "defaults": [
                    {"intent": "await_agent", "to": "inspect"},
                    {"intent": "workflow_complete", "to": "done"},
                ],
                "goto": ["compose", "revise"],
            },
            {
                "from": "inspect",
                "defaults": [{"intent": "await_agent", "to": "done"}],
                "goto": ["revise"],
            },
            {
                "from": "revise",
                "defaults": [{"intent": "need_clarification", "to": "user"}],
                "goto": [],
            },
        ],
    }
    assert first.routes == {
        "defaults": {
            "await_agent": {
                "to": "inspect",
                "label": "Inspect the draft",
                "ready": False,
                "missing": ["accepted_draft"],
            },
            "workflow_complete": {
                "to": "done",
                "label": "Complete workflow",
                "ready": True,
            },
        },
        "goto": [
            {
                "to": "compose",
                "label": "Continue composing",
                "ready": True,
            },
            {
                "to": "revise",
                "label": "Revise from findings",
                "ready": True,
                "carries_feedback": True,
            },
        ],
    }
    allowed_fields = {"to", "label", "ready", "missing", "carries_feedback"}
    entries = [*first.routes["defaults"].values(), *first.routes["goto"]]
    assert all(set(entry) <= allowed_fields for entry in entries)
    assert "carries_feedback" not in first.routes["goto"][0]
    assert "missing" not in first.routes["goto"][0]


def test_label_fallback_prefers_handoff_then_composed_skill_role_and_step() -> None:
    assert (
        select_route_label(
            target="revise",
            handoff_label="Apply requested changes",
            skill_descriptions=("Primary authoring", "Correction overlay"),
            role_description="Writer",
        )
        == "Apply requested changes"
    )
    assert (
        select_route_label(
            target="revise",
            handoff_label=" ",
            skill_descriptions=("", "Correction overlay"),
            role_description="Writer",
        )
        == "Correction overlay"
    )
    assert (
        select_route_label(
            target="revise",
            handoff_label=None,
            skill_descriptions=(),
            role_description="Writer",
        )
        == "Writer"
    )
    assert (
        select_route_label(
            target="revise",
            handoff_label=None,
            skill_descriptions=("",),
            role_description=None,
        )
        == "revise"
    )


def test_readiness_requires_one_candidate_and_ignores_optional_inputs() -> None:
    requirements = (
        RouteInputRequirement(
            name="accepted_plan",
            candidates=("plan", "approved_brief"),
        ),
        RouteInputRequirement(
            name="workspace_snapshot",
            candidates=("develop_workspace",),
        ),
    )

    assert evaluate_route_readiness(
        requirements,
        available_artifacts={"approved_brief", "unrelated_optional"},
    ) == RouteReadiness(ready=False, missing=("workspace_snapshot",))
    assert evaluate_route_readiness(
        requirements,
        available_artifacts={"plan", "develop_workspace"},
    ) == RouteReadiness(ready=True)


def test_authorization_accepts_default_and_discretionary_but_not_merely_valid_step() -> None:
    playbook = _playbook()
    choices = route_choices(playbook, current_step="compose", intent="await_agent")

    assert choices.default == "inspect"
    assert choices.goto == ("compose", "revise")
    assert choices.authorized == ("inspect", "compose", "revise")
    assert authorize_route_target(
        playbook,
        current_step="compose",
        intent="await_agent",
        target="inspect",
    )
    assert authorize_route_target(
        playbook,
        current_step="compose",
        intent="await_agent",
        target="compose",
    )
    assert authorize_route_target(
        playbook,
        current_step="compose",
        intent="await_agent",
        target="revise",
    )
    assert not authorize_route_target(
        playbook,
        current_step="compose",
        intent="await_agent",
        target="unrelated",
    )


def test_route_choices_normalize_terminal_and_user_targets() -> None:
    playbook = _playbook()

    terminal = route_choices(
        playbook,
        current_step="compose",
        intent="workflow_complete",
    )
    user = route_choices(
        playbook,
        current_step="revise",
        intent="need_clarification",
    )

    assert terminal.default == "done"
    assert authorize_route_target(
        playbook,
        current_step="compose",
        intent="workflow_complete",
        target="done",
    )
    assert user.default == "user"
    assert authorize_route_target(
        playbook,
        current_step="revise",
        intent="need_clarification",
        target="user",
    )
