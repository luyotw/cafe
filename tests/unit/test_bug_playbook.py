"""U1–U5: focused defect delivery through public declarative contracts."""

import pytest

from cafe.core.playbook import resolve_playbook_skills
from cafe.playbooks.loader import PlaybookLoader
from cafe.playbooks.simulate import analyze_playbook
from cafe.skills.checklist_composer import select_checklist_variant
from cafe.skills.contracts import DeclaredArtifactError, resolve_prompt_inputs
from cafe.skills.loader import SkillLoader
from cafe.skills.workflow_composition import resolve_step_workflow_composition
from cafe.ui.human_tasks import resolve_step_human_task

pytestmark = pytest.mark.usefixtures("cached_builtin_playbook_models")


def test_bug_is_additive_discoverable_and_bounded():
    """U1: explicit selection leaves existing candidates and defaults intact."""
    loader = PlaybookLoader()
    assert {"bug", "hotfix", "standard", "tdd"} <= set(loader.list_playbooks())
    model = loader.load_model("bug", strict=True).model
    assert model.entry_point == "diagnose"
    assert model.playbook.applicability.use_when
    assert model.playbook.applicability.avoid_when
    assert loader.load("standard")["entry_point"] == "spec"


def test_every_success_and_correction_preserves_independent_review():
    """U2: no repair success can reach PR without a distinct reviewer."""
    p = PlaybookLoader().load("bug", strict=True)
    steps = p["steps"]
    assert set(steps) == {"diagnose", "develop", "review", "pr"}
    assert steps["diagnose"]["on"]["await_agent"] == "develop"
    assert steps["develop"]["on"]["await_agent"] == "review"
    assert steps["review"]["on"]["await_agent"] == "pr"
    assert steps["develop"]["role"] != steps["review"]["role"]
    assert p["roles"]["developer"]["default_agent"] != p["roles"]["reviewer"]["default_agent"]
    assert "Edit" not in steps["review"]["allowed_tools"]
    assert steps["review"]["on"]["manual_handoff"] == "develop"
    assert "diagnose" in steps["review"]["allowed_goto"]
    assert steps["develop"]["on"]["manual_handoff"] == "diagnose"
    assert "pr" not in steps["develop"].get("allowed_goto", [])
    assert "no_changes_needed" not in steps["develop"]["on"]
    for phase in ("diagnose", "develop", "review"):
        assert steps[phase]["max_attempts_per_cycle"] == 3
        binding = next(t for t in steps[phase]["human_tasks"] if t["task_id"] == "iteration-limit")
        assert binding["outcomes"] == {"resume": phase}
    for source, target, artifact in (
        ("develop", "diagnose", "code"),
        ("review", "develop", "review_feedback"),
    ):
        behavior = steps[source]["behavior"]
        assert behavior["feedback_target"] == target
        assert behavior["feedback_artifact"] == artifact
        assert all(
            behavior[k]
            for k in (
                "feedback_source_kind",
                "feedback_todo_source",
                "feedback_todo_id_prefix",
            )
        )


@pytest.mark.parametrize("phase", ["diagnose", "develop", "review"])
def test_phase_inputs_and_checklists_carry_defect_evidence(phase):
    """U3: required proof/workspace and prior/correction inputs survive composition."""
    p = PlaybookLoader().load("bug", strict=True)
    step = p["steps"][phase]
    loader = SkillLoader()
    composition = resolve_step_workflow_composition(
        loader,
        primary_skill=step["skill"],
        step_name=phase,
        workflow_skills=resolve_playbook_skills(
            p,
            step_name=phase,
            role=step["role"],
            channel="workflow",
        ),
    )
    contract = loader.get_workflow_declaration(step["skill"])
    assert composition
    artifacts = {key: f"/{key}.md" for key in step["input_artifacts"]}
    artifacts["causal_todo"] = "/correction.md"
    inputs = resolve_prompt_inputs(contract, artifacts)
    assert inputs["prior_output_file"] == artifacts[step["output_artifact"]]
    assert inputs["causal_todo_file"] == "/correction.md"
    if phase != "diagnose":
        assert inputs["diagnosis_file"] == "/bug_diagnosis.md"
        assert inputs["workspace_file"] == "/workspace.md"
        for missing in ("bug_diagnosis", "workspace"):
            with pytest.raises(DeclaredArtifactError):
                resolve_prompt_inputs(
                    contract, {k: v for k, v in artifacts.items() if k != missing}
                )
    if phase == "review":
        assert inputs["code_file"] == "/code.md"
    for feedback in (False, True):
        variant = select_checklist_variant(
            contract,
            step=phase,
            iteration=2,
            artifacts=artifacts,
            feedback=feedback,
        )
        text = "\n".join(
            loader.get_reference(step["skill"], s.reference)
            for s in variant.sections
            if s.reference
        )
        assert "RED" in text and "regression" in text
        assert "scope" in text and "evidence" in text
        assert "[ ]" in text  # Evidence instructions must materialize as real gates.
        if phase != "diagnose":
            assert "GREEN" in text
        if feedback:
            assert any(s.todo_projection and s.todo_projection.causal for s in variant.sections)
    body = loader.activate(step["skill"])
    assert "prior_output_file" in body and "interruption" in body
    assert "three" in body if phase != "diagnose" else "unfixed" in body


@pytest.mark.parametrize("locale", ["en-US", "zh-TW"])
def test_human_and_pr_contracts_resolve_without_extra_authority(locale):
    """U4: localized decisions resume the affected phase, preserving PR gates."""
    p = PlaybookLoader().load("bug", strict=True)
    p["playbook"]["conversation_locale"] = locale
    for phase in ("diagnose", "develop", "review"):
        for trigger in ("need_clarification", "need_permission", "manual_handoff"):
            policy, binding = resolve_step_human_task(
                playbook_data=p,
                step_name=phase,
                trigger=trigger,
            )
            assert policy.prompt and "message_key" not in policy.prompt
            assert set(binding.outcomes.values()) == {phase}
            assert policy.prompt_locales.get("zh-TW")
            presented = policy.for_locale(locale)
            assert presented.prompt == (
                policy.prompt_locales["zh-TW"] if locale == "zh-TW" else policy.prompt
            )
            assert "message_key" not in presented.correction_guidance
    policy, binding = resolve_step_human_task(
        playbook_data=p,
        step_name="pr",
        trigger="confirm_output",
    )
    assert policy.id == "local-review"
    assert binding.outcomes == {
        "fix_now": "pr",
        "create_follow_up": "_done",
        "continue_without_issue": "_done",
    }
    pr = p["steps"]["pr"]
    assert pr["skill"] == "cafe-pr"
    assert pr["capability_requests"] == ["cafe.pr.publish"]
    assert pr["behavior"]["publish_confirmation"] is True
    assert pr["behavior"]["feedback_target"] == "pr"
    assert pr["on"]["manual_handoff"] == "develop"
    assert "workflow_complete" not in pr["on"]
    assert pr["hooks"]["publish_output"] == [
        "GitHubPRCreator",
        "LocalReviewContextProvider",
        "PRLinkOpener",
    ]


def test_strict_simulation_accounts_for_all_paths_and_bounds():
    """U5: unchanged loaded topology has no unexplained findings."""
    model = PlaybookLoader().load_model("bug", strict=True).model
    report = analyze_playbook(model)
    assert report.unreachable_steps == ()
    assert report.dead_end_steps == ()
    assert report.missing_intent_handlers == ()
    # Any automatic cycle crosses a bounded phase; PR always enters human review.
    assert all(
        model.steps[s].max_attempts_per_cycle == 3 for s in ("diagnose", "develop", "review")
    )
