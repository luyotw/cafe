"""Contracts for the built-in development playbook choices."""

from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader


NEW_PLAYBOOKS = {"direct", "standard", "standard-qa", "tdd-qa"}


def test_new_development_playbooks_are_discoverable_and_strictly_valid() -> None:
    loader = PlaybookLoader()

    assert NEW_PLAYBOOKS <= set(loader.list_playbooks())
    for playbook_id in NEW_PLAYBOOKS:
        assert loader.load_model(playbook_id, strict=True).model.playbook.id == playbook_id


def test_direct_is_the_reviewed_no_spec_no_plan_path() -> None:
    playbook = PlaybookLoader().load_model("direct", strict=True).model

    assert playbook.entry_point == "develop"
    assert list(playbook.steps) == ["develop", "review", "pr"]
    assert playbook.steps["develop"].on["await_agent"] == "review"
    assert playbook.steps["review"].on["await_agent"] == "pr"
    assert playbook.steps["review"].on["manual_handoff"] == "develop"
    assert playbook.steps["pr"].on["manual_handoff"] == "develop"
    assert playbook.steps["review"].max_iterations == 5


def test_standard_is_an_explicit_peer_of_the_compatible_default_graph() -> None:
    loader = PlaybookLoader()
    default = loader.load_model("default", strict=True).model
    standard = loader.load_model("standard", strict=True).model

    assert default.entry_point == standard.entry_point == "spec"
    assert list(default.steps) == list(standard.steps) == [
        "spec",
        "plan",
        "develop",
        "review",
        "pr",
    ]
    for step_name in standard.steps:
        assert standard.steps[step_name].on == default.steps[step_name].on


def test_qa_variants_share_one_declarative_acceptance_phase() -> None:
    loader = PlaybookLoader()

    for playbook_id in ("standard-qa", "tdd-qa"):
        playbook = loader.load_model(playbook_id, strict=True).model
        assert list(playbook.steps) == ["spec", "plan", "develop", "review", "qa", "pr"]
        qa = playbook.steps["qa"]
        assert qa.skill == "cafe-qa"
        assert qa.role == "qa"
        assert qa.output_artifact == "qa_feedback"
        assert qa.on["await_agent"] == "pr"
        assert qa.on["manual_handoff"] == "develop"
        assert qa.on["need_clarification"] == "qa"
        assert qa.on["need_permission"] == "qa"
        assert qa.max_iterations == 5
        assert qa.hooks.after_execute == []
        assert playbook.steps["develop"].on["await_agent"] == "review"
        assert playbook.steps["review"].on["await_agent"] == "qa"


def test_qa_feedback_is_available_to_correction_and_pr_skills() -> None:
    loader = SkillLoader()
    develop_inputs = loader.get_workflow_contract("cafe-develop").prompt_inputs
    review_inputs = loader.get_workflow_contract("cafe-review").prompt_inputs
    pr_inputs = loader.get_workflow_contract("cafe-pr").prompt_inputs

    assert any("qa_feedback" in item.artifacts for item in develop_inputs)
    assert any("qa_feedback" in item.artifacts for item in review_inputs)
    assert any("qa_feedback" in item.artifacts for item in pr_inputs)
