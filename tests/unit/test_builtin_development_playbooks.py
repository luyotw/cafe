"""Contracts for the built-in software-development playbooks."""

from pathlib import Path

import pytest

from cafe.playbooks.loader import PlaybookLoader
from cafe.playbooks.simulate import analyze_playbook
from cafe.skills.loader import SkillLoader

DEVELOPMENT_PLAYBOOKS = {
    "direct",
    "simple",
    "standard",
    "standard-qa",
    "tdd",
    "tdd-qa",
    "hotfix",
}


def test_development_playbooks_are_discoverable_and_strictly_valid() -> None:
    loader = PlaybookLoader()

    assert DEVELOPMENT_PLAYBOOKS <= set(loader.list_playbooks())
    for playbook_id in DEVELOPMENT_PLAYBOOKS:
        loaded = loader.load_model(playbook_id, strict=True)
        assert loaded.model.playbook.id == playbook_id
        simulation = analyze_playbook(loaded.model)
        assert simulation.unreachable_steps == ()
        assert simulation.dead_end_steps == ()
        assert simulation.missing_intent_handlers == ()


@pytest.mark.parametrize(
    ("playbook_id", "step_name"),
    [
        ("direct", "review"),
        ("simple", "qa"),
        ("standard", "review"),
        ("standard-qa", "review"),
        ("standard-qa", "qa"),
        ("tdd", "review"),
        ("tdd-qa", "review"),
        ("tdd-qa", "qa"),
        ("hotfix", "review"),
    ],
)
def test_bounded_builtin_steps_declare_a_resumable_iteration_limit_task(
    playbook_id: str, step_name: str
) -> None:
    step = PlaybookLoader().load_model(playbook_id, strict=True).model.steps[step_name]

    task = next(task for task in step.human_tasks if task.task_id == "iteration-limit")

    assert task.trigger == "manual_handoff"
    assert task.outcomes == {"resume": step_name}


def test_standard_replaces_default_without_alias_or_migration() -> None:
    loader = PlaybookLoader()

    assert "standard" in loader.list_playbooks()
    assert "default" not in loader.list_playbooks()
    with pytest.raises(FileNotFoundError, match="default"):
        loader.load_model("default")
    assert not (Path(__file__).parents[2] / "src/cafe/data/playbooks/default.yaml").exists()


def test_direct_is_the_reviewed_no_spec_no_plan_path() -> None:
    playbook = PlaybookLoader().load_model("direct", strict=True).model

    assert playbook.entry_point == "develop"
    assert list(playbook.steps) == ["develop", "review", "pr"]
    assert playbook.steps["develop"].on["await_agent"] == "review"
    assert playbook.steps["review"].on["await_agent"] == "pr"
    assert playbook.steps["review"].on["manual_handoff"] == "develop"
    assert playbook.steps["pr"].on["manual_handoff"] == "develop"
    assert playbook.steps["review"].max_iterations == 5


def test_standard_owns_the_established_full_development_graph() -> None:
    playbook = PlaybookLoader().load_model("standard", strict=True).model

    assert playbook.entry_point == "spec"
    assert list(playbook.steps) == ["spec", "plan", "develop", "review", "pr"]
    assert playbook.steps["spec"].on["await_agent"] == "plan"
    assert playbook.steps["plan"].on["await_agent"] == "develop"
    assert playbook.steps["develop"].on["await_agent"] == "review"
    assert playbook.steps["review"].on["await_agent"] == "pr"


def test_simple_owns_the_spec_develop_qa_pr_graph() -> None:
    loader = PlaybookLoader()

    simple = loader.load_model("simple", strict=True).model
    assert list(simple.steps) == ["spec", "develop", "qa", "pr"]
    assert simple.steps["spec"].on["await_agent"] == "develop"
    assert simple.steps["develop"].on["await_agent"] == "qa"
    assert simple.steps["qa"].on["await_agent"] == "pr"
    assert simple.steps["qa"].on["manual_handoff"] == "develop"
    assert "qa_feedback" in simple.steps["develop"].input_artifacts
    assert "qa_feedback" in simple.steps["pr"].input_artifacts
    develop = simple.steps["develop"]
    assert develop.on["no_changes_needed"] == "qa"
    assert "NoChangesNeededHandler" in develop.hooks.after_execute
    no_change_task = next(
        task for task in develop.human_tasks if task.trigger == "no_changes_needed"
    )
    assert no_change_task.outcomes == {"agree": "qa", "disagree": "develop"}


def test_existing_hotfix_and_tdd_paths_remain_unchanged() -> None:
    loader = PlaybookLoader()

    hotfix = loader.load_model("hotfix", strict=True).model
    assert hotfix.entry_point == "develop"
    assert list(hotfix.steps) == ["develop", "review", "pr"]
    assert hotfix.steps["develop"].on["await_agent"] == "review"
    assert hotfix.steps["review"].on["await_agent"] == "pr"

    tdd = loader.load_model("tdd", strict=True).model
    assert list(tdd.steps) == ["spec", "plan", "develop", "review", "pr"]
    assert tdd.roles["developer"].default_agent == "Nick"
    assert tdd.steps["develop"].on["await_agent"] == "review"
    assert tdd.steps["review"].on["await_agent"] == "pr"


@pytest.mark.parametrize("playbook_id", ["standard-qa", "tdd-qa"])
def test_qa_variants_share_one_bounded_acceptance_phase(playbook_id: str) -> None:
    playbook = PlaybookLoader().load_model(playbook_id, strict=True).model

    assert list(playbook.steps) == ["spec", "plan", "develop", "review", "qa", "pr"]
    qa = playbook.steps["qa"]
    assert qa.skill == "cafe-qa"
    assert qa.role == "qa"
    assert qa.output_artifact == "qa_feedback"
    assert qa.on == {
        "await_agent": "pr",
        "manual_handoff": "develop",
        "need_clarification": "qa",
        "need_permission": "qa",
    }
    assert qa.max_iterations == 5
    assert qa.allowed_goto == ["develop"]

    review = playbook.steps["review"]
    assert review.on["await_agent"] == "qa"
    assert review.on["manual_handoff"] == "develop"
    assert review.max_iterations == 5
    assert review.allowed_goto == ["develop"]

    pr = playbook.steps["pr"]
    assert pr.on["manual_handoff"] == "develop"
    assert pr.allowed_goto == ["develop"]

    develop = playbook.steps["develop"]
    assert develop.on["await_agent"] == "review"
    assert develop.on["no_changes_needed"] == "review"


def test_qa_feedback_is_exposed_by_every_correction_and_publication_skill() -> None:
    loader = SkillLoader()
    for skill_name in ("cafe-develop", "cafe-review", "cafe-pr"):
        prompt_inputs = loader.get_workflow_contract(skill_name).prompt_inputs
        assert any("qa_feedback" in item.artifacts for item in prompt_inputs)

    qa_contract = loader.get_workflow_contract("cafe-qa")
    required = {
        mapping.artifacts[0]
        for mapping in qa_contract.prompt_inputs
        if mapping.required
    }
    optional = {
        mapping.artifacts[0]
        for mapping in qa_contract.prompt_inputs
        if not mapping.required
    }
    assert required == {"spec", "code"}
    assert optional == {"plan", "review_feedback"}
