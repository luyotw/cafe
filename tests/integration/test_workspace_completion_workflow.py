"""Workspace completion journeys through the public workflow runtime (I1–I8)."""
import json
import subprocess

import pytest

from cafe.core.human_task_records import HumanTaskRecordStore
from tests.integration.test_workflow_artifact_correction import CORRECTED, journey


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True).stdout


def change_then_commit(repo, iteration, call):
    if call == 1:
        (repo / "owned.txt").write_text("authorized change\n")
    else:
        git(repo, "add", "owned.txt")
        git(repo, "commit", "-m", "authorized change")


@pytest.mark.parametrize("mode", ["baton", "legacy"])
def test_i1_custom_step_corrects_before_effects_in_exact_context(journey, mode):
    j = journey([CORRECTED], mode=mode, workspace=True, workspace_action=change_then_commit)
    j.runtime.run(start_step="inspect_custom")
    assert len(j.manager.calls) == 2
    first, correction = j.manager.calls
    assert correction[2].is_exact
    assert correction[2].session_id == "exact-report-session"
    assert correction[3]["allowed_tools"] == first[3]["allowed_tools"]
    assert correction[3]["allowed_directories"] == first[3]["allowed_directories"]
    assert j.effects == ["prepare", "after"]
    assert not HumanTaskRecordStore(j.issue).tasks()
    assert "custom_snapshot" in j.runtime.blackboard.artifacts
    assert j.manager.deliveries == 1


@pytest.mark.parametrize("intent", ["need_clarification", "need_permission"])
@pytest.mark.parametrize("initial", [True, False])
def test_i4_preserves_preexisting_changes_and_human_handoff(journey, intent, initial):
    observations = []
    def preexisting(repo, iteration, call):
        if call == 1:
            for index in range(6):
                (repo / f"template-{index}.txt").write_text(f"owner {index}")
        observations.append((git(repo, "rev-parse", "HEAD"), git(repo, "diff", "--cached"),
                             git(repo, "status", "--porcelain=v1")))
    j = journey([(CORRECTED, intent)] if initial else [CORRECTED, (CORRECTED, intent)],
                workspace=True, workspace_action=preexisting, human=intent)
    j.runtime.run(start_step="inspect_custom")
    assert len(j.manager.calls) == (1 if initial else 2)
    assert j.manager.deliveries == 0
    assert j.runtime.blackboard.handoff_contract.to_owner.value == "user"
    assert j.runtime.blackboard.handoff_contract.intent.value == intent
    assert "custom_snapshot" not in j.runtime.blackboard.artifacts
    assert j.effects == ["prepare"]
    assert observations[-1] == observations[0]
    assert git(j.repo, "status", "--porcelain=v1") == observations[0][2]


def test_i7_undeclared_step_keeps_dirty_workspace_behavior(journey):
    def dirty(repo, iteration, call):
        (repo / "owned.txt").write_text("dirty")
    j = journey([CORRECTED], workspace=False, workspace_action=dirty)
    j.runtime.run(start_step="inspect_custom")
    assert len(j.manager.calls) == 1 and j.manager.deliveries == 1
    assert j.effects == ["prepare", "after"]
    assert "custom_snapshot" not in j.runtime.blackboard.artifacts


def test_i2_three_opportunities_exhaust_without_publication(journey):
    def dirty(repo, iteration, call):
        (repo / "owned.txt").write_text("dirty")
        if call > 1:
            metadata = json.loads((iteration / "iteration.json").read_text())
            assert metadata["workspace_completion"]["consumed"] == call - 1
    j = journey([CORRECTED], workspace=True, workspace_action=dirty)
    result = j.runtime.run(start_step="inspect_custom")
    assert len(j.manager.calls) == 4 and not result.completed
    assert j.effects == ["prepare"] and j.manager.deliveries == 0
    assert "custom_snapshot" not in j.runtime.blackboard.artifacts
    assert json.loads((j.iteration / "iteration.json").read_text())["workspace_completion"]["consumed"] == 3
    assert len(HumanTaskRecordStore(j.issue).tasks()) == 1


@pytest.mark.parametrize("failure", ["missing", "drift", "provider", "metadata"])
def test_i3_exact_session_failure_never_substitutes_provider(journey, failure):
    def mutation(iteration, call):
        if call == 1 and failure == "missing":
            j.manager.get_last_session_id = lambda: None
        if call == 2 and failure == "drift":
            j.manager.get_last_session_id = lambda: "replacement-session"
        if call == 2 and failure == "metadata":
            path = iteration / "iteration.json"
            data = json.loads(path.read_text())
            data["allowed_tools"] = ["expanded"]
            path.write_text(json.dumps(data))
    from cafe.agents.executor import AgentExecutionError
    submissions = [CORRECTED, AgentExecutionError("provider unavailable", error_type="rate_limit")] if failure == "provider" else [CORRECTED]
    j = journey(submissions, workspace=True, workspace_action=lambda repo, iteration, call: (repo / "owned.txt").write_text("dirty"), provider_mutation=mutation)
    j.runtime.run(start_step="inspect_custom")
    assert len(j.manager.calls) == (1 if failure == "missing" else 2)
    assert j.manager.deliveries == 0 and j.effects == ["prepare"]
    assert "custom_snapshot" not in j.runtime.blackboard.artifacts
    assert len(HumanTaskRecordStore(j.issue).tasks()) == 1


@pytest.mark.parametrize("invalid", ["checklist", "report", "handoff"])
def test_i5_correction_revalidates_current_conditions_with_one_budget(journey, invalid):
    from tests.integration.test_workflow_artifact_correction import REJECTED
    def mutation(iteration, call):
        if call >= 2:
            if invalid == "checklist":
                p = iteration / "checklist.md"
                p.write_text(p.read_text().replace("[x]", "[ ]"))
            elif invalid == "report":
                (iteration / "output.md").write_text(REJECTED)
            else:
                (iteration.parents[1] / "next_step.txt").write_text('{"version":1,"intent":"invalid"}')
    j = journey([CORRECTED, (CORRECTED, "invalid")] if invalid == "handoff" else [CORRECTED],
                workspace=True, workspace_action=change_then_commit,
                completed_checklist=invalid == "checklist", provider_mutation=mutation)
    j.runtime.run(start_step="inspect_custom")
    assert 2 <= len(j.manager.calls) <= 4
    assert j.effects == ["prepare"] and j.manager.deliveries == 0
    assert "custom_snapshot" not in j.runtime.blackboard.artifacts
    assert len(HumanTaskRecordStore(j.issue).tasks()) == 1


@pytest.mark.parametrize("stage", ["after", "publish", "final"])
def test_i6_late_dirty_state_blocks_later_effects_and_registration(journey, monkeypatch, stage):
    def effect(current, repo, count):
        if current == stage:
            (repo / "late.txt").write_text("late dirty")
    j = journey([CORRECTED], workspace=True, extra_publication=True, effect_action=effect)
    if stage == "final":
        original = j.executor._publish_workspace_artifact_under_lock
        def late(**kwargs):
            (j.repo / "late.txt").write_text("late dirty")
            return original(**kwargs)
        monkeypatch.setattr(j.executor, "_publish_workspace_artifact_under_lock", late)
    j.runtime.run(start_step="inspect_custom")
    assert j.manager.deliveries == 0
    assert "custom_snapshot" not in j.runtime.blackboard.artifacts
    assert "evidence_bundle" not in j.runtime.blackboard.artifacts
    assert not (j.iteration / "artifact.json").exists()
    assert j.effects.count("publish") == (0 if stage == "after" else 1 if stage == "publish" else 2)
    assert len(j.manager.calls) == 1


@pytest.mark.parametrize("ambiguous", [False, True])
def test_i8_same_iteration_reentry_never_replays_effects(journey, monkeypatch, ambiguous):
    def effect(stage, repo, count):
        if ambiguous and stage == "after":
            raise RuntimeError("interrupted after effect dispatch")
    j = journey([CORRECTED], workspace=True, extra_publication=True, effect_action=effect)
    j.runtime.run(start_step="inspect_custom")
    before = j.effects.count("after"), j.effects.count("publish")
    monkeypatch.setattr(j.executor, "_get_next_iteration_number", lambda *args: 1)
    step = j.playbook["steps"]["inspect_custom"]
    # Simulate retrying the producing delivery, before a new runtime transition.
    (j.issue / "next_step.txt").write_text(json.dumps({"version": 1, "intent": "await_agent"}))
    if ambiguous:
        with pytest.raises(RuntimeError):
            j.executor.execute_step("inspect_custom", step, j.runtime.blackboard)
    else:
        result = j.executor.execute_step("inspect_custom", step, j.runtime.blackboard)
        assert result.artifact_ready
        assert {"type": "effect", "stage": "after"} in result.events
    assert (j.effects.count("after"), j.effects.count("publish")) == before
    assert len(j.manager.calls) == 1
