"""Contracts for the built-in software-development playbooks."""

from pathlib import Path

import pytest

from cafe.core.playbook import resolve_step_behavior
from cafe.playbooks.loader import PlaybookLoader
from cafe.playbooks.simulate import analyze_playbook
from cafe.skills.contracts import resolve_prompt_inputs
from cafe.skills.loader import SkillLoader

pytestmark = pytest.mark.usefixtures("cached_builtin_playbook_models")

DEVELOPMENT_PLAYBOOKS = {
    "direct",
    "direct-agent-review",
    "direct-qa",
    "simple",
    "standard",
    "standard-qa",
    "tdd",
    "tdd-qa",
    "hotfix",
}
BUNDLED_PLAYBOOKS = DEVELOPMENT_PLAYBOOKS | {"editorial", "incident", "research"}


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


def test_every_builtin_pr_requires_local_review_before_done() -> None:
    """A PR artifact cannot complete a built-in development workflow by itself."""
    loader = PlaybookLoader()

    for playbook_id in DEVELOPMENT_PLAYBOOKS:
        pr = loader.load_model(playbook_id, strict=True).model.steps["pr"]
        local_review = next(task for task in pr.human_tasks if task.task_id == "local-review")

        assert pr.on["confirm_output"] == "pr"
        assert "workflow_complete" not in pr.on
        assert local_review.trigger == "confirm_output"
        assert local_review.outcomes == {
            "fix_now": "pr",
            "create_follow_up": "_done",
            "continue_without_issue": "_done",
        }


def test_every_builtin_pr_curates_corrective_feedback_before_development() -> None:
    """Corrective sources re-enter their declared PR curator, not Develop."""
    loader = PlaybookLoader()

    for playbook_id in DEVELOPMENT_PLAYBOOKS:
        pr = loader.load_model(playbook_id, strict=True).model.steps["pr"]
        local_review = next(task for task in pr.human_tasks if task.task_id == "local-review")

        assert pr.behavior.feedback_target == "pr"
        assert pr.behavior.feedback_artifact == "workflow_feedback"
        assert "workflow_feedback" in pr.input_artifacts
        assert local_review.outcomes["fix_now"] == "pr"
        assert pr.on["manual_handoff"] == "develop"


def test_cafe_pr_routes_completed_artifacts_to_local_review() -> None:
    skill = (
        Path(__file__).parents[2] / "src" / "cafe" / "data" / "skills" / "cafe-pr" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "injected `{step_transitions}`" in skill
    assert "Route `confirm_output` to `user`" in skill
    assert "complete directly only when `workflow_complete→done` is declared" in skill
    assert "select an undeclared route" in skill
    assert "workflow_feedback_file" in skill
    assert "current corrective cycle" in skill
    assert "`manual_handoff`" in skill
    assert "Follow-up Proposals" in skill
    assert "does not create a GitHub issue automatically" in skill
    assert "decision applies to every open FUP" in skill
    assert "per-proposal mixed disposition is not supported" in skill

    policy = next(
        task
        for task in SkillLoader().get_workflow_contract("cafe-pr").human_tasks
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
    risk = (root / "references" / "execution_risk_assessment.md").read_text(
        encoding="utf-8"
    )
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
    assert list(playbook.steps) == ["develop", "review", "pr"]
    assert playbook.steps["develop"].on["await_agent"] == "review"
    assert playbook.steps["review"].on["await_agent"] == "pr"
    assert playbook.steps["review"].on["manual_handoff"] == "develop"
    assert playbook.steps["pr"].on["manual_handoff"] == "develop"
    assert playbook.steps["review"].max_attempts_per_cycle == 5


def test_direct_agent_review_uses_two_in_phase_reviewers_before_pr() -> None:
    playbook = PlaybookLoader().load_model("direct-agent-review", strict=True).model

    assert playbook.entry_point == "develop"
    assert list(playbook.steps) == ["develop", "pr"]
    develop = playbook.steps["develop"]
    assert develop.skill == "cafe-develop_agent_review"
    assert "Agent" in develop.allowed_tools
    assert develop.on["await_agent"] == "pr"
    assert develop.on["no_changes_needed"] == "develop"
    no_change = next(task for task in develop.human_tasks if task.trigger == "no_changes_needed")
    assert no_change.outcomes == {"agree": "develop", "disagree": "develop"}

    skill = (
        Path(__file__).parents[2]
        / "src/cafe/data/skills/cafe-develop_agent_review/SKILL.md"
    ).read_text(encoding="utf-8")
    assert "剛好兩個原生 subagent" in skill
    assert "`detail`" in skill
    assert "`scope`" in skill
    assert "重新並行啟動" in skill
    assert "都明確回報 no blocking issues" in skill

    references = (
        Path(__file__).parents[2]
        / "src/cafe/data/skills/cafe-develop_agent_review/references"
    )
    for name in ("execution_steps_normal.md", "execution_steps_correction.md"):
        checklist = (references / name).read_text(encoding="utf-8")
        assert "Launch exactly two native subagents" in checklist
        assert "reports no blocking issues from both `detail` and `scope`" in checklist
    correction = (references / "execution_steps_correction.md").read_text(encoding="utf-8")
    assert "`review`:" not in correction


def test_standard_owns_the_established_full_development_graph() -> None:
    playbook = PlaybookLoader().load_model("standard", strict=True).model

    assert playbook.entry_point == "spec"
    assert list(playbook.steps) == ["spec", "plan", "develop", "review", "pr"]
    assert playbook.steps["spec"].on["await_agent"] == "plan"
    assert playbook.steps["plan"].on["await_agent"] == "develop"
    assert playbook.steps["develop"].on["await_agent"] == "review"
    assert playbook.steps["review"].on["await_agent"] == "pr"


@pytest.mark.parametrize("playbook_id", ["standard", "standard-qa", "tdd", "tdd-qa"])
def test_solution_alignment_stays_inside_the_plan_step(playbook_id: str) -> None:
    playbook = PlaybookLoader().load_model(playbook_id, strict=True).model

    assert "approach" not in playbook.steps
    plan = playbook.steps["plan"]
    assert plan.on["need_clarification"] == "plan"
    assert plan.on["confirm_output"] == "plan"
    assert plan.on["await_agent"] == "develop"
    clarification = next(
        task for task in plan.human_tasks if task.trigger == "need_clarification"
    )
    assert clarification.task_id == "clarification-answers"
    assert clarification.outcomes == {"submit": "plan"}


def test_plan_steps_declare_the_prior_identity_authority_and_skill_input() -> None:
    loader = PlaybookLoader()
    contract = SkillLoader().get_workflow_contract("cafe-plan")
    prior_input = next(item for item in contract.prompt_inputs if item.placeholder == "prior_plan_file")
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
        for task in SkillLoader().get_workflow_contract("cafe-develop").human_tasks
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


def test_direct_qa_owns_the_planless_reviewed_qa_graph() -> None:
    playbook = PlaybookLoader().load_model("direct-qa", strict=True).model

    assert playbook.entry_point == "develop"
    assert list(playbook.steps) == ["develop", "review", "qa", "pr"]
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
    assert list(hotfix.steps) == ["develop", "review", "pr"]
    assert hotfix.steps["develop"].on["await_agent"] == "review"
    assert hotfix.steps["review"].on["await_agent"] == "pr"

    tdd = loader.load_model("tdd", strict=True).model
    assert list(tdd.steps) == ["spec", "plan", "develop", "review", "pr"]
    assert tdd.roles["developer"].default_agent == "Nick"
    assert tdd.steps["develop"].on["await_agent"] == "review"
    assert tdd.steps["review"].on["await_agent"] == "pr"


@pytest.mark.parametrize(
    "playbook_id",
    [
        "standard",
        "standard-qa",
        "direct",
        "direct-agent-review",
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
        "direct-agent-review",
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
    assert required == {"code"}
    assert optional == {"spec", "plan", "review_feedback", "workspace"}

    pr_contract = loader.get_workflow_contract("cafe-pr")
    resolved = resolve_prompt_inputs(
        pr_contract,
        {"qa_feedback": "qa.md", "review_feedback": "review.md"},
    )
    assert resolved["feedback_file"] == "qa.md"
    assert resolved["review_feedback_file"] == "review.md"
