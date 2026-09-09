"""Contracts for the reviewed direct-development playbook."""

import pytest

from cafe.playbooks.loader import PlaybookLoader

pytestmark = pytest.mark.usefixtures("cached_builtin_playbook_models")


def test_direct_is_discoverable_and_strictly_valid() -> None:
    loader = PlaybookLoader()

    assert "direct" in loader.list_playbooks()
    assert loader.load_model("direct", strict=True).model.playbook.id == "direct"


def test_direct_is_the_reviewed_no_spec_no_plan_path() -> None:
    playbook = PlaybookLoader().load_model("direct", strict=True).model
    develop = playbook.steps["develop"]
    review = playbook.steps["review"]
    pr = playbook.steps["pr"]

    assert playbook.entry_point == "develop"
    assert list(playbook.steps) == ["develop", "review", "pr"]
    assert develop.initial_input.providers == ["manual_text", "github_issue"]
    assert develop.initial_input.bind.artifact is None
    assert develop.initial_input.bind.prompt_context == "user_input"
    assert "InitialInputProviderResolver" in develop.hooks.prepare_input
    assert develop.on["await_agent"] == "review"
    assert develop.on["manual_handoff"] == "develop"
    assert develop.on["no_changes_needed"] == "review"
    assert any(task.trigger == "no_changes_needed" for task in develop.human_tasks)
    assert "NoChangesNeededHandler" in develop.hooks.after_execute
    assert review.on["await_agent"] == "pr"
    assert review.on["manual_handoff"] == "develop"
    assert review.on["need_permission"] == "review"
    assert review.max_attempts_per_cycle == 5
    assert pr.on["manual_handoff"] == "develop"
    assert pr.allowed_goto == ["develop"]
