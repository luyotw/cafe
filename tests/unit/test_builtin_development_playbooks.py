"""Contracts for the built-in software-development playbooks."""

import re
from pathlib import Path

import pytest

from cafe.core.playbook import (
    confirmation_gate_steps,
    mandatory_confirmation_gate_steps,
    resolve_playbook_skills,
    resolve_step_behavior,
)
from cafe.playbooks.loader import PlaybookLoader
from cafe.playbooks.simulate import analyze_playbook
from cafe.skills.checklist_composer import select_checklist_variant
from cafe.skills.contracts import resolve_prompt_inputs
from cafe.skills.loader import SkillLoader
from cafe.skills.workflow_composition import resolve_step_workflow_composition

pytestmark = pytest.mark.usefixtures("cached_builtin_playbook_models")

DEVELOPMENT_PLAYBOOKS = {
    "direct",
    "direct-subagent-review",
    "subagent-flow",
    "subagent-flow-qa",
    "direct-qa",
    "simple",
    "standard",
    "standard-qa",
    "tdd",
    "tdd-qa",
    "hotfix",
    "bug",
}
BUNDLED_PLAYBOOKS = DEVELOPMENT_PLAYBOOKS | {"editorial", "incident", "research", "streamlined"}


def test_adopting_graphs_have_separate_action_review_and_result_acceptance():
    """U6: delivery approval and final acceptance are independent declared tasks."""
    for name in DEVELOPMENT_PLAYBOOKS:
        model = PlaybookLoader().load_model(name, strict=True).model
        approval = model.steps["pr"]
        delivery = model.steps["deliver"]
        assert approval.delivery is None
        assert "DevelopmentActionContext" not in approval.hooks.publish_output
        assert "DevelopmentActionContext" in delivery.hooks.publish_output
        content = next(t for t in approval.human_tasks if t.trigger == "confirm_output")
        assert content.task_id == "pr-review"
        assert content.outcomes == {"fix_now": "pr", "confirm": "deliver"}
        review = next(t for t in delivery.human_tasks if t.trigger == "need_permission")
        assert review.task_id == delivery.delivery.approval_task == "delivery-review"
        assert delivery.delivery.approval_step == "deliver"
        assert set(review.outcomes.values()) == {"deliver"}
        outcome = next(t for t in delivery.human_tasks if t.trigger == "confirm_output")
        assert outcome.outcomes == {
            "confirm": "_done", "confirm_cleanup": "_done",
            "confirm_archive": "_done", "revise": "deliver",
        }
        assert delivery.delivery.publication_artifact in delivery.input_artifacts
        assert delivery.delivery.correction_step in delivery.allowed_goto



def test_all_bundled_playbooks_have_distinct_bounded_applicability() -> None:
    """U5/I3 — every packaged candidate is strictly valid and distinguishable."""
    playbook_root = Path(__file__).parents[2] / "src" / "cafe" / "data" / "playbooks"
    assert {path.stem for path in playbook_root.glob("*.yaml")} == BUNDLED_PLAYBOOKS

    summaries: set[str] = set()
    loader = PlaybookLoader()
    for playbook_id in sorted(BUNDLED_PLAYBOOKS):
        loaded = loader.load_model(playbook_id, strict=True)
        applicability = loaded.model.playbook.applicability

        assert applicability is not None
        assert 1 <= len(applicability.summary) <= 160
        assert 1 <= len(applicability.use_when) <= 6
        assert 1 <= len(applicability.avoid_when) <= 6
        assert all(len(condition) <= 200 for condition in applicability.use_when)
        assert all(len(condition) <= 200 for condition in applicability.avoid_when)
        summaries.add(applicability.summary.casefold())

    assert len(summaries) == len(BUNDLED_PLAYBOOKS)


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


def test_every_builtin_discretionary_destination_has_a_declared_label() -> None:
    loader = PlaybookLoader()

    for playbook_id in sorted(BUNDLED_PLAYBOOKS):
        playbook = loader.load_model(playbook_id, strict=True).model
        for source_name, source in playbook.steps.items():
            for target_name in source.allowed_goto:
                label = playbook.steps[target_name].handoff_label
                assert (
                    label and label.strip()
                ), f"{playbook_id}:{source_name} -> {target_name} needs handoff_label"


def test_builtin_phase_routing_guidance_uses_injected_routes_not_step_names() -> None:
    skill_root = Path(__file__).parents[2] / "src" / "cafe" / "data" / "skills"
    phase_files = [
        path
        for directory in skill_root.glob("cafe-*")
        for path in directory.rglob("*.md")
        if "assets" not in path.parts
    ]
    concrete_route = re.compile(
        r"(?i)(?:route|handoff|hand off)[^\n]{0,100}`"
        r"(?:spec|plan|develop|review|qa|pr|brief|draft|publish|"
        r"research_[a-z_]+|incident_[a-z_]+)`"
    )

    findings = [
        f"{path.relative_to(skill_root)}: {match.group(0)}"
        for path in phase_files
        for match in concrete_route.finditer(path.read_text(encoding="utf-8"))
    ]

    assert findings == []
    assert all("{step_transitions}" not in path.read_text(encoding="utf-8") for path in phase_files)


def test_every_builtin_pr_requires_local_review_before_done() -> None:
    """A PR artifact cannot complete a built-in development workflow by itself."""
    loader = PlaybookLoader()

    for playbook_id in DEVELOPMENT_PLAYBOOKS:
        pr = loader.load_model(playbook_id, strict=True).model.steps["pr"]
        local_review = next(task for task in pr.human_tasks if task.task_id == "pr-review")

        assert pr.on["confirm_output"] == "pr"
        assert "workflow_complete" not in pr.on
        assert local_review.trigger == "confirm_output"
        assert local_review.outcomes == {"fix_now": "pr", "confirm": "deliver"}


def test_every_builtin_pr_curates_corrective_feedback_before_development() -> None:
    """Corrective sources re-enter their declared PR curator, not Develop."""
    loader = PlaybookLoader()

    for playbook_id in DEVELOPMENT_PLAYBOOKS:
        pr = loader.load_model(playbook_id, strict=True).model.steps["pr"]
        local_review = next(task for task in pr.human_tasks if task.task_id == "pr-review")

        assert pr.behavior.feedback_target == "pr"
        assert pr.behavior.feedback_artifact == "workflow_feedback"
        assert "workflow_feedback" in pr.input_artifacts
        assert local_review.outcomes["fix_now"] == "pr"
        assert pr.on["manual_handoff"] == "develop"


def test_cafe_pr_routes_completed_artifacts_to_local_review() -> None:
    skill = (
        Path(__file__).parents[2] / "src" / "cafe" / "data" / "skills" / "cafe-pr" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "from the injected route catalog" in skill
    assert "Route `confirm_output` to `user`" in skill
    assert "catalog declares a `workflow_complete` default to `done`" in skill
    assert "select an undeclared route" in skill
    assert "workflow_feedback_file" in skill
    assert "current corrective batch" in skill
    assert "Canonical Todo fields for this batch" in skill
    assert "do not derive or substitute a generic PR-comment prefix or source" in skill
    assert "`manual_handoff`" in skill
    assert "Follow-up Proposals" in skill
    assert "does not create a GitHub issue automatically" in skill
    assert "one PR HumanTask choice applies to all open" in skill

    policy = next(
        task
        for task in SkillLoader().get_workflow_declaration("cafe-pr").human_tasks
        if task.id == "local-review"
    )
    decisions = {decision.id: decision for decision in policy.decisions}
    assert set(decisions) == {"fix_now", "create_follow_up", "continue_without_issue"}
    assert decisions["fix_now"].requires_feedback is True
    assert decisions["create_follow_up"].requires_feedback is False
    assert decisions["continue_without_issue"].requires_feedback is False


def test_cafe_review_convergence_contract_preserves_critical_blockers() -> None:
    """Late non-critical discovery converges without weakening Critical review."""
    root = Path(__file__).parents[2] / "src" / "cafe" / "data" / "skills" / "cafe-review"
    skill = (root / "SKILL.md").read_text(encoding="utf-8")
    convergence = (root / "references" / "execution_convergence.md").read_text(encoding="utf-8")
    finalize = (root / "references" / "execution_finalize.md").read_text(encoding="utf-8")
    risk = (root / "references" / "execution_risk_assessment.md").read_text(encoding="utf-8")
    acceptance = (root / "references" / "execution_acceptance_closure.md").read_text(
        encoding="utf-8"
    )

    assert "Rounds 1–3 are discovery mode" in skill
    assert "From round 4" in skill
    assert "confidence bucket" in skill
    assert "`Impact: Critical`" in skill
    assert "remain blocking" in skill
    assert "unresolved existing blocker lineage" in convergence
    assert "regression causally introduced by the current correction" in convergence
    assert "newly evidenced `Impact: Critical`" in convergence
    assert "Convert every other newly discovered Important or Minor" in convergence
    assert "`BLK-NNN`" in finalize
    assert "`FUP-NNN`" in finalize
    assert "draft issue title and body" in finalize
    assert "## Finding Registry" in finalize
    assert "every historical and current BLK/FUP lineage" in finalize
    assert "mark it `promoted` with a linked existing-or-new `BLK-NNN`" in convergence
    assert "late non-Critical observation cannot become blocking" in convergence
    assert "pre-existing late non-Critical observation" in risk
    assert "non-blocking status `follow_up`" in risk
    assert "every allowed blocking row is `closed_fresh`" in acceptance
    assert "every non-blocking late gap is `follow_up`" in acceptance
    assert "correction-impacted entries classified `follow_up`" in finalize


@pytest.mark.parametrize(
    ("playbook_id", "step_name"),
    [
        ("direct", "review"),
        ("simple", "qa"),
        ("standard", "review"),
        ("standard-qa", "review"),
        ("standard-qa", "qa"),
        ("direct-qa", "review"),
        ("direct-qa", "qa"),
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
    assert list(playbook.steps) == ["develop", "review", "pr", "deliver"]
    assert playbook.steps["develop"].on["await_agent"] == "review"
    assert playbook.steps["review"].on["await_agent"] == "pr"
    assert playbook.steps["review"].on["manual_handoff"] == "develop"
    assert playbook.steps["pr"].on["manual_handoff"] == "develop"
    assert playbook.steps["review"].max_attempts_per_cycle == 5

    contract = SkillLoader().get_workflow_declaration("cafe-develop")
    planless = select_checklist_variant(
        contract,
        step="develop",
        iteration=1,
        artifacts={},
        feedback=False,
    )
    assert all(section.todo_projection is None for section in planless.sections)

    planned = select_checklist_variant(
        contract,
        step="develop",
        iteration=1,
        artifacts={"plan": object()},
        feedback=False,
    )
    projections = [section.todo_projection for section in planned.sections]
    assert any(item is not None and item.artifact == "plan" for item in projections)


def test_direct_subagent_review_composes_develop_with_one_review_overlay() -> None:
    loader = PlaybookLoader()
    playbook = loader.load_model("direct-subagent-review", strict=True).model
    raw_playbook = loader.load("direct-subagent-review")

    assert playbook.entry_point == "develop"
    assert list(playbook.steps) == ["develop", "pr", "deliver"]
    develop = playbook.steps["develop"]
    assert develop.skill == "cafe-develop"
    assert "Agent" in develop.allowed_tools
    assert develop.on["await_agent"] == "pr"
    assert develop.on["no_changes_needed"] == "develop"
    no_change = next(task for task in develop.human_tasks if task.trigger == "no_changes_needed")
    assert no_change.outcomes == {"agree": "develop", "disagree": "develop"}

    composition = resolve_step_workflow_composition(
        SkillLoader(),
        primary_skill=develop.skill,
        step_name="develop",
        workflow_skills=resolve_playbook_skills(
            raw_playbook, channel="workflow", role="developer", step_name="develop"
        ),
    )
    assert composition.skill_names == (
        "cafe-develop",
        "cafe-workflow-common",
        "cafe-github_sync",
        "cafe-develop_subagent_review",
    )
    assert composition.required_tools == ("Agent",)

    overlay = composition.contributors[-1].declaration
    assert overlay.checklist is None
    assert overlay.checklist_overlay is not None
    assert len(overlay.checklist_overlay.variants) == 1
    assert [section.reference for section in overlay.checklist_overlay.variants[0].sections] == [
        "dual_review_gate.md"
    ]

    skill_path = (
        Path(__file__).parents[2] / "src/cafe/data/skills/cafe-develop_subagent_review/SKILL.md"
    )
    skill = skill_path.read_text(encoding="utf-8")
    assert "剛好兩個原生 subagent" in skill
    assert "`detail`" in skill
    assert "`scope`" in skill
    assert "重新並行啟動" in skill
    assert "都明確回報 no blocking issues" in skill
    assert "prompt_inputs:" not in skill
    assert "human_tasks:" not in skill

    root = skill_path.parent
    assert sorted(
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    ) == ["SKILL.md", "references/dual_review_gate.md"]


def test_standard_owns_the_established_full_development_graph() -> None:
    playbook = PlaybookLoader().load_model("standard", strict=True).model

    assert playbook.entry_point == "spec"
    assert list(playbook.steps) == ["spec", "plan", "develop", "review", "pr", "deliver"]
    assert playbook.steps["spec"].on["await_agent"] == "plan"
    assert playbook.steps["plan"].on["await_agent"] == "develop"
    assert playbook.steps["develop"].on["await_agent"] == "review"
    assert playbook.steps["review"].on["await_agent"] == "pr"


@pytest.mark.parametrize("playbook_id", ["subagent-flow", "subagent-flow-qa"])
def test_joint_spec_plan_has_one_planning_gate_and_same_phase_revisions(playbook_id: str) -> None:
    playbook = PlaybookLoader().load_model(playbook_id, strict=True).model
    planning = playbook.steps["spec_plan"]

    assert playbook.entry_point == "spec_plan"
    expected_steps = ["spec_plan", "develop", "pr", "deliver"]
    if playbook_id == "subagent-flow-qa":
        expected_steps.insert(2, "qa")
    assert list(playbook.steps) == expected_steps
    if playbook_id == "subagent-flow":
        assert confirmation_gate_steps(playbook) == ("spec_plan", "pr", "deliver")
        assert mandatory_confirmation_gate_steps(playbook) == ()
    else:
        assert confirmation_gate_steps(playbook) == ("spec_plan", "pr")
        assert mandatory_confirmation_gate_steps(playbook) == ("deliver",)
    assert planning.output_artifact == "plan"
    assert planning.input_artifacts == ["plan"]
    assert planning.todo_identity_input_artifact == "plan"
    assert planning.initial_input.bind.prompt_context == "user_input"
    assert planning.initial_input.bind.artifact == "plan"
    review = next(task for task in planning.human_tasks if task.trigger == "confirm_output")
    assert review.outcomes == {"confirm": "develop", "revise": "spec_plan"}
    for trigger in ("need_clarification", "need_permission", "manual_handoff"):
        task = next(task for task in planning.human_tasks if task.trigger == trigger)
        assert task.outcomes == {"submit": "spec_plan"}
    assert planning.on["confirm_output"] == "spec_plan"
    assert planning.on["await_agent"] == "develop"
    assert playbook.steps["develop"].allowed_goto == ["spec_plan"]
    assert all("spec" not in step.input_artifacts for step in playbook.steps.values())


@pytest.mark.parametrize("playbook_id", ["subagent-flow", "subagent-flow-qa"])
def test_joint_plan_reuses_direct_review_and_does_not_skip_no_change_review(
    playbook_id: str,
) -> None:
    loader = PlaybookLoader()
    raw = loader.load(playbook_id)
    playbook = loader.load_model(playbook_id, strict=True).model
    direct = loader.load_model("direct-subagent-review", strict=True).model
    develop = playbook.steps["develop"]

    assert develop.skill == direct.steps["develop"].skill
    expected_routes = dict(direct.steps["develop"].on)
    if playbook_id == "subagent-flow-qa":
        expected_routes["await_agent"] = "qa"
    assert develop.on == expected_routes
    assert develop.human_tasks == direct.steps["develop"].human_tasks
    assert develop.workspace_artifact == "workspace"
    assert "plan" in develop.input_artifacts
    assert "plan" in playbook.steps["pr"].input_artifacts
    assert "InitialInputProviderResolver" not in develop.hooks.prepare_input
    assert playbook.steps["pr"].hooks == direct.steps["pr"].hooks

    composition = resolve_step_workflow_composition(
        SkillLoader(),
        primary_skill=develop.skill,
        step_name="develop",
        workflow_skills=resolve_playbook_skills(
            raw, channel="workflow", role="developer", step_name="develop"
        ),
    )
    assert "cafe-develop_subagent_review" in composition.skill_names
    assert composition.required_tools == ("Agent",)
    planned = select_checklist_variant(
        composition.contributors[0].declaration,
        step="develop",
        iteration=1,
        artifacts={"plan": object()},
        feedback=False,
    )
    assert planned is not None
    assert any(
        section.todo_projection is not None
        and section.todo_projection.artifact == "plan"
        and section.todo_projection.source == "plan"
        for section in planned.sections
    )


@pytest.mark.parametrize("playbook_id", ["subagent-flow", "subagent-flow-qa"])
def test_joint_planning_composition_requires_real_subagents_and_prior_plan_authority(
    playbook_id: str,
) -> None:
    playbook = PlaybookLoader().load(playbook_id)
    composition = resolve_step_workflow_composition(
        SkillLoader(),
        primary_skill="cafe-spec_plan",
        step_name="spec_plan",
        workflow_skills=resolve_playbook_skills(
            playbook, channel="workflow", role="developer", step_name="spec_plan"
        ),
    )
    assert set(composition.required_tools) == {"Agent", "Bash"}
    assert "cafe-plan" not in composition.skill_names
    prior = next(
        item for item in composition.prompt_inputs if item.placeholder == "prior_plan_file"
    )
    assert prior.artifacts == ("plan",)
    assert prior.required is False
    review = next(task for task in composition.human_tasks if task.id == "output-review")
    decisions = {decision.id: decision for decision in review.decisions}
    assert set(decisions) == {"confirm", "revise"}
    assert decisions["revise"].requires_feedback is True


def test_subagent_qa_keeps_acceptance_and_correction_feedback_in_the_graph() -> None:
    loader = PlaybookLoader()
    raw = loader.load("subagent-flow-qa")
    playbook = loader.load_model("subagent-flow-qa", strict=True).model
    qa = playbook.steps["qa"]

    assert "review" not in playbook.steps
    assert playbook.steps["develop"].on["await_agent"] == "qa"
    assert qa.skill == "cafe-qa"
    assert qa.role == "qa"
    assert qa.input_artifacts == ["plan", "code", "workspace"]
    assert qa.workspace_input_artifact == "workspace"
    assert qa.output_artifact == "qa_feedback"
    assert qa.max_attempts_per_cycle == 5
    assert qa.allowed_goto == ["develop"]
    assert qa.on == {
        "await_agent": "pr",
        "manual_handoff": "develop",
        "need_clarification": "qa",
        "need_permission": "qa",
    }
    limit = next(task for task in qa.human_tasks if task.task_id == "iteration-limit")
    assert limit.outcomes == {"resume": "qa"}
    feedback = resolve_step_behavior(playbook, "qa")
    assert feedback.feedback_target == "develop"
    assert feedback.feedback_artifact == "qa_feedback"
    assert feedback.feedback_source_kind == "qa"
    assert feedback.feedback_todo_source == "qa"
    assert feedback.feedback_todo_id_prefix == "QA"
    for step_name in ("develop", "pr"):
        assert "qa_feedback" in playbook.steps[step_name].input_artifacts

    composition = resolve_step_workflow_composition(
        SkillLoader(),
        primary_skill="cafe-develop",
        step_name="develop",
        workflow_skills=resolve_playbook_skills(
            raw, channel="workflow", role="developer", step_name="develop"
        ),
    )
    assert any(item.artifacts == ("qa_feedback",) for item in composition.prompt_inputs)


@pytest.mark.parametrize("playbook_id", ["standard", "standard-qa", "tdd", "tdd-qa"])
def test_solution_alignment_stays_inside_the_plan_step(playbook_id: str) -> None:
    playbook = PlaybookLoader().load_model(playbook_id, strict=True).model

    assert "approach" not in playbook.steps
    plan = playbook.steps["plan"]
    assert plan.on["need_clarification"] == "plan"
    assert plan.on["confirm_output"] == "plan"
    assert plan.on["await_agent"] == "develop"
    clarification = next(task for task in plan.human_tasks if task.trigger == "need_clarification")
    assert clarification.task_id == "clarification-answers"
    assert clarification.outcomes == {"submit": "plan"}


def test_plan_steps_declare_the_prior_identity_authority_and_skill_input() -> None:
    loader = PlaybookLoader()
    contract = SkillLoader().get_workflow_declaration("cafe-plan")
    prior_input = next(
        item for item in contract.prompt_inputs if item.placeholder == "prior_plan_file"
    )
    assert prior_input.artifacts == ("plan",)
    assert prior_input.required is False
    for playbook_id in ("standard", "standard-qa", "tdd", "tdd-qa"):
        plan = loader.load_model(playbook_id, strict=True).model.steps["plan"]
        assert plan.todo_identity_input_artifact == "plan"
        assert plan.input_artifacts == ["spec", "plan"]


def test_every_builtin_develop_step_binds_the_generic_permission_task() -> None:
    """Test List 5: permission requests reuse one policy and always resume develop."""
    policy = next(
        task
        for task in SkillLoader().get_workflow_declaration("cafe-develop").human_tasks
        if task.id == "permission-answers"
    )
    assert policy.pattern == "revision_feedback"
    assert policy.input_schema == "feedback"

    loader = PlaybookLoader()
    for playbook_id in DEVELOPMENT_PLAYBOOKS:
        develop = loader.load_model(playbook_id, strict=True).model.steps["develop"]
        binding = next(task for task in develop.human_tasks if task.trigger == "need_permission")
        assert binding.task_id == "permission-answers"
        assert binding.outcomes == {"submit": "develop"}


def test_simple_owns_the_spec_develop_qa_pr_graph() -> None:
    loader = PlaybookLoader()

    simple = loader.load_model("simple", strict=True).model
    assert list(simple.steps) == ["spec", "develop", "qa", "pr", "deliver"]
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


def test_direct_qa_owns_the_planless_reviewed_qa_graph() -> None:
    playbook = PlaybookLoader().load_model("direct-qa", strict=True).model

    assert playbook.entry_point == "develop"
    assert list(playbook.steps) == ["develop", "review", "qa", "pr", "deliver"]
    assert playbook.steps["develop"].on["await_agent"] == "review"
    assert playbook.steps["review"].on["await_agent"] == "qa"
    assert playbook.steps["qa"].on["await_agent"] == "pr"
    assert playbook.steps["review"].on["manual_handoff"] == "develop"
    assert playbook.steps["qa"].on["manual_handoff"] == "develop"
    assert "qa_feedback" in playbook.steps["develop"].input_artifacts
    assert "qa_feedback" in playbook.steps["pr"].input_artifacts
    assert "pm" not in playbook.roles
    assert all("spec" not in step.input_artifacts for step in playbook.steps.values())


def test_existing_hotfix_and_tdd_paths_remain_unchanged() -> None:
    loader = PlaybookLoader()

    hotfix = loader.load_model("hotfix", strict=True).model
    assert hotfix.entry_point == "develop"
    assert list(hotfix.steps) == ["develop", "review", "pr", "deliver"]
    assert hotfix.steps["develop"].on["await_agent"] == "review"
    assert hotfix.steps["review"].on["await_agent"] == "pr"

    tdd = loader.load_model("tdd", strict=True).model
    assert list(tdd.steps) == ["spec", "plan", "develop", "review", "pr", "deliver"]
    assert tdd.roles["developer"].default_agent == "Nick"
    assert tdd.steps["develop"].on["await_agent"] == "review"
    assert tdd.steps["review"].on["await_agent"] == "pr"


@pytest.mark.parametrize(
    "playbook_id",
    [
        "standard",
        "standard-qa",
        "direct",
        "direct-subagent-review",
        "subagent-flow",
        "subagent-flow-qa",
        "direct-qa",
        "hotfix",
        "simple",
        "tdd",
        "tdd-qa",
    ],
)
def test_builtin_pr_feedback_routes_declare_portable_todo_metadata(
    playbook_id: str,
) -> None:
    playbook = PlaybookLoader().load_model(playbook_id, strict=True).model
    behavior = resolve_step_behavior(playbook, "pr")

    assert behavior.feedback_target == "pr"
    assert behavior.feedback_artifact == "workflow_feedback"
    assert behavior.feedback_source_kind == "github_pr"
    assert behavior.feedback_todo_source == "pr_comment"
    assert behavior.feedback_todo_id_prefix == "PRC"
    binding = next(task for task in playbook.steps["pr"].human_tasks if task.feedback_delivery)
    assert binding.feedback_delivery is not None
    assert binding.feedback_delivery.todo_source == "workflow_feedback"
    assert binding.feedback_delivery.todo_id_prefix == "WF"


@pytest.mark.parametrize(
    "playbook_id",
    [
        "standard",
        "standard-qa",
        "direct",
        "direct-subagent-review",
        "subagent-flow",
        "subagent-flow-qa",
        "direct-qa",
        "hotfix",
        "simple",
        "tdd",
        "tdd-qa",
    ],
)
def test_builtin_develop_publishes_workspace_and_consumers_declare_it(
    playbook_id: str,
) -> None:
    playbook = PlaybookLoader().load_model(playbook_id, strict=True).model

    develop = playbook.steps["develop"]
    assert develop.workspace_artifact == "workspace"
    for step_name in ("review", "qa", "pr"):
        if step_name in playbook.steps:
            assert "workspace" in playbook.steps[step_name].input_artifacts


@pytest.mark.parametrize("playbook_id", ["standard-qa", "tdd-qa"])
def test_qa_variants_share_one_bounded_acceptance_phase(playbook_id: str) -> None:
    playbook = PlaybookLoader().load_model(playbook_id, strict=True).model

    assert list(playbook.steps) == ["spec", "plan", "develop", "review", "qa", "pr", "deliver"]
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
    assert qa.max_attempts_per_cycle == 5
    assert qa.allowed_goto == ["develop"]

    review = playbook.steps["review"]
    assert review.on["await_agent"] == "qa"
    assert review.on["manual_handoff"] == "develop"
    assert review.max_attempts_per_cycle == 5
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
        prompt_inputs = loader.get_workflow_declaration(skill_name).prompt_inputs
        assert any("qa_feedback" in item.artifacts for item in prompt_inputs)

    qa_contract = loader.get_workflow_declaration("cafe-qa")
    required = {mapping.artifacts[0] for mapping in qa_contract.prompt_inputs if mapping.required}
    optional = {
        mapping.artifacts[0] for mapping in qa_contract.prompt_inputs if not mapping.required
    }
    assert required == {"code"}
    assert optional == {"spec", "plan", "review_feedback", "workspace"}

    pr_contract = loader.get_workflow_declaration("cafe-pr")
    resolved = resolve_prompt_inputs(
        pr_contract,
        {"qa_feedback": "qa.md", "review_feedback": "review.md"},
    )
    assert resolved["feedback_file"] == "qa.md"
    assert resolved["review_feedback_file"] == "review.md"


@pytest.mark.parametrize("mandatory", [None, True, False])
def test_feedback_gate_assignment_preserves_legacy_default(mandatory):
    from cafe.core.human_tasks import HumanTaskBinding

    model = PlaybookLoader().load_model("standard", strict=True).model.model_copy(deep=True)
    binding = next(t for t in model.steps["pr"].human_tasks if t.trigger == "confirm_output")
    payload = binding.model_dump(exclude={"mandatory_confirmation"})
    if mandatory is not None:
        payload["mandatory_confirmation"] = mandatory
    replacement = HumanTaskBinding.model_validate(payload)
    model.steps["pr"].human_tasks = [replacement if t is binding else t for t in model.steps["pr"].human_tasks]
    assert ("pr" in confirmation_gate_steps(model)) is (mandatory is False)
    assert ("pr" in mandatory_confirmation_gate_steps(model)) is (mandatory is not False)
    assert "deliver" in mandatory_confirmation_gate_steps(model)
    permission = next(t for t in model.steps["deliver"].human_tasks if t.trigger == "need_permission")
    assert permission.task_id == model.steps["deliver"].delivery.approval_task


def test_feedback_gate_mandatory_flag_rejects_ambiguous_strings():
    from cafe.core.human_tasks import HumanTaskBinding
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        HumanTaskBinding(trigger="confirm_output", task_id="content", mandatory_confirmation="false")
