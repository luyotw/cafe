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
        observations.append(
            (
                git(repo, "rev-parse", "HEAD"),
                git(repo, "diff", "--cached"),
                git(repo, "status", "--porcelain=v1"),
            )
        )

    j = journey(
        [(CORRECTED, intent)] if initial else [CORRECTED, (CORRECTED, intent)],
        workspace=True,
        workspace_action=preexisting,
        human=intent,
    )
    j.runtime.run(start_step="inspect_custom")
    assert len(j.manager.calls) == (1 if initial else 2)
    assert j.manager.deliveries == 0
    assert j.runtime.blackboard.handoff_contract.to_owner.value == "user"
    assert j.runtime.blackboard.handoff_contract.intent.value == intent
    assert "custom_snapshot" not in j.runtime.blackboard.artifacts
    assert j.effects == ["prepare"]
    assert observations[-1] == observations[0]
    assert git(j.repo, "status", "--porcelain=v1") == observations[0][2]
    assert [(j.repo / f"template-{index}.txt").read_text() for index in range(6)] == [
        f"owner {index}" for index in range(6)
    ]


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
    assert (
        json.loads((j.iteration / "iteration.json").read_text())["workspace_completion"]["consumed"]
        == 3
    )
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

    submissions = (
        [CORRECTED, AgentExecutionError("provider unavailable", error_type="rate_limit")]
        if failure == "provider"
        else [CORRECTED]
    )
    j = journey(
        submissions,
        workspace=True,
        workspace_action=lambda repo, iteration, call: (repo / "owned.txt").write_text("dirty"),
        provider_mutation=mutation,
    )
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
                (iteration.parents[1] / "next_step.txt").write_text(
                    '{"version":1,"intent":"invalid"}'
                )

    j = journey(
        [CORRECTED, (CORRECTED, "invalid")] if invalid == "handoff" else [CORRECTED],
        workspace=True,
        workspace_action=change_then_commit,
        completed_checklist=invalid == "checklist",
        provider_mutation=mutation,
    )
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


@pytest.mark.parametrize("mutation", ["none", "source", "evidence"])
def test_i5_dirty_projected_evidence_enters_correction_and_revalidates(journey, mutation):
    def invalidate(iteration, call):
        if call >= 2 and mutation == "source":
            source = iteration.parents[1] / "blueprint.md"
            source.write_text(source.read_text().replace("Work: implement", "Work: changed"))
        if call >= 2 and mutation == "evidence":
            output = iteration / "output.md"
            head = git(j.repo, "rev-parse", "HEAD").decode().strip()
            output.write_text(output.read_text().replace(head, "f" * 40))

    j = journey(
        [CORRECTED],
        workspace=True,
        projected=True,
        workspace_action=change_then_commit,
        provider_mutation=invalidate,
    )
    result = j.runtime.run(start_step="inspect_custom")
    assert 2 <= len(j.manager.calls) <= 4
    if mutation == "none":
        assert result.completed and j.manager.deliveries == 1
        assert not HumanTaskRecordStore(j.issue).tasks()
    else:
        assert not result.completed and j.manager.deliveries == 0
        assert j.effects == ["prepare"]
        assert "custom_snapshot" not in j.runtime.blackboard.artifacts
        assert len(HumanTaskRecordStore(j.issue).tasks()) == 1


@pytest.mark.parametrize("decision", ["retry", "retry_fresh_session"])
def test_i2_interrupted_reservation_survives_runtime_reconstruction(journey, decision):
    from cafe.agents.executor import AgentExecutionError
    from cafe.core.blackboard import BlackboardStore
    from cafe.ui.human_tasks import apply_human_task_payload

    def dirty(repo, iteration, call):
        (repo / "owned.txt").write_text("dirty")

    j = journey(
        [
            CORRECTED,
            AgentExecutionError("interrupted provider", error_type="rate_limit"),
            CORRECTED,
        ],
        workspace=True,
        workspace_action=dirty,
    )
    j.runtime.run(start_step="inspect_custom")
    assert len(j.manager.calls) == 2
    before = json.loads((j.iteration / "iteration.json").read_text())["workspace_completion"]
    assert before["consumed"] == 1
    task = HumanTaskRecordStore(j.issue).tasks()[0]
    state = BlackboardStore(j.issue).load_or_create("inspect_custom")
    apply_human_task_payload(
        issue_dir=j.issue,
        playbook_data=j.playbook,
        blackboard=state,
        from_step="inspect_custom",
        trigger=task.trigger,
        raw_payload={"task": task.policy_id, "decision": decision, "human_task_id": task.id},
        source="test",
    )
    resumed = j.reconstruct()
    resumed.run()
    after = json.loads((j.iteration / "iteration.json").read_text())["workspace_completion"]
    assert after["consumed"] == 3
    assert len(j.manager.calls) == 4
    assert all(call[2].is_exact for call in j.manager.calls[2:])
    assert j.manager.deliveries == 0 and j.effects.count("after") == 0
    assert not (j.issue / "inspect_custom" / "iteration_002").exists()


@pytest.mark.parametrize("defect", ["missing", "malformed", "wrong-owner"])
def test_u4_i5_current_missing_or_rejected_handoff_cannot_publish(journey, defect):
    def reject(repo, iteration, call):
        if call >= 2:
            path = iteration.parents[1] / "next_step.txt"
            if defect == "missing":
                path.unlink()
            elif defect == "malformed":
                path.write_text("{bad")
            else:
                path.write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "to_owner": "agent",
                            "to_step": "user",
                            "intent": "need_permission",
                        }
                    )
                )

    j = journey(
        [CORRECTED], workspace=True, workspace_action=change_then_commit, post_submission=reject
    )
    j.runtime.run(start_step="inspect_custom")
    assert 2 <= len(j.manager.calls) <= 4
    assert j.manager.deliveries == 0 and j.effects == ["prepare"]
    assert "custom_snapshot" not in j.runtime.blackboard.artifacts
    assert len(HumanTaskRecordStore(j.issue).tasks()) == 1


def test_i8_changed_delivery_refuses_effect_replay(journey, monkeypatch):
    j = journey([CORRECTED], workspace=True, extra_publication=True)
    j.runtime.run(start_step="inspect_custom")
    before = j.effects.count("after"), j.effects.count("publish")
    (j.iteration / "output.md").write_text(CORRECTED.replace("#", "# Changed", 1))
    (j.issue / "next_step.txt").write_text(json.dumps({"version": 1, "intent": "await_agent"}))
    monkeypatch.setattr(j.executor, "_get_next_iteration_number", lambda *args: 1)
    with pytest.raises(RuntimeError):
        j.executor.execute_step(
            "inspect_custom", j.playbook["steps"]["inspect_custom"], j.runtime.blackboard
        )
    assert (j.effects.count("after"), j.effects.count("publish")) == before
    assert len(j.manager.calls) == 1


@pytest.mark.parametrize("drift", ["cli", "model", "directories"])
def test_i3_observed_execution_drift_blocks_publication(journey, drift, monkeypatch):
    from cafe.core.types import AgentCLI

    def mutation(iteration, call):
        if call == 2:
            if drift == "cli":
                j.manager.get_last_cli = lambda: AgentCLI.CLAUDE
            elif drift == "model":
                j.manager.agent.config.model = "different-model"
            else:
                monkeypatch.setattr(j.executor, "_get_allowed_directories", lambda: ["expanded"])

    j = journey(
        [CORRECTED], workspace=True, workspace_action=change_then_commit, provider_mutation=mutation
    )
    j.runtime.run(start_step="inspect_custom")
    assert len(j.manager.calls) == 2 and j.manager.deliveries == 0
    assert j.effects == ["prepare"]
    assert "custom_snapshot" not in j.runtime.blackboard.artifacts
    assert len(HumanTaskRecordStore(j.issue).tasks()) == 1


def test_u5_i1_correction_prompt_uses_workflow_conversation_locale(journey):
    j = journey([CORRECTED], workspace=True, workspace_action=change_then_commit)
    j.runtime.blackboard.conversation_locale = "zh-TW"
    j.runtime.blackboard_store.save(j.runtime.blackboard)
    j.runtime.run(start_step="inspect_custom")
    prompt = j.manager.calls[1][1]
    assert "owned.txt" in prompt and "來源" in prompt and "權限" in prompt
    assert j.manager.deliveries == 1


@pytest.mark.parametrize("progress", [False, True])
def test_i2_i8_malformed_diagnostics_block_before_provider_dispatch(journey, progress):
    j = journey([CORRECTED], workspace=True)
    j.iteration.mkdir(parents=True)
    key = "workspace_publication" if progress else "workspace_completion"
    (j.iteration / "iteration.json").write_text(json.dumps({key: None}))
    j.runtime.run(start_step="inspect_custom")
    assert not j.manager.calls and j.manager.deliveries == 0
    assert "custom_snapshot" not in j.runtime.blackboard.artifacts
    assert len(HumanTaskRecordStore(j.issue).tasks()) == 1


@pytest.mark.parametrize("reason", ["invalid-base", "wrong-repository"])
def test_i7_non_dirty_validation_failures_do_not_enter_correction(journey, reason, monkeypatch):
    j = journey([CORRECTED], workspace=True)
    if reason == "invalid-base":
        original = j.executor._get_issue_config_value
        monkeypatch.setattr(
            j.executor,
            "_get_issue_config_value",
            lambda file, field: "missing-base" if field == "base_branch" else original(file, field),
        )
    else:
        # The Git boundary reports a subdirectory instead of the active worktree root.
        folder = j.repo / "subdir"
        folder.mkdir()
        j.executor.git_ops.repo_path = folder
    j.runtime.run(start_step="inspect_custom")
    assert len(j.manager.calls) <= 1 and j.manager.deliveries == 0
    assert j.effects.count("after") == 0
    assert "custom_snapshot" not in j.runtime.blackboard.artifacts
    assert len(HumanTaskRecordStore(j.issue).tasks()) == 1


def test_i8_dirty_reentry_never_dispatches_another_correction(journey, monkeypatch):
    j = journey([CORRECTED], workspace=True, extra_publication=True)
    j.runtime.run(start_step="inspect_custom")
    before = j.effects.count("after"), j.effects.count("publish")
    (j.repo / "late.txt").write_text("dirty after effects")
    (j.issue / "next_step.txt").write_text(json.dumps({"version": 1, "intent": "await_agent"}))
    monkeypatch.setattr(j.executor, "_get_next_iteration_number", lambda *args: 1)
    with pytest.raises((RuntimeError, ValueError)):
        j.executor.execute_step(
            "inspect_custom", j.playbook["steps"]["inspect_custom"], j.runtime.blackboard
        )
    assert (j.effects.count("after"), j.effects.count("publish")) == before
    assert len(j.manager.calls) == 1


def test_i3_unsupported_exact_continuation_uses_human_recovery(journey):
    j = journey([CORRECTED], workspace=True, workspace_action=change_then_commit)
    original = j.manager.execute

    def unsupported(
        name,
        prompt,
        allowed_tools=None,
        allowed_directories=None,
        streaming_output_file=None,
        phase_name=None,
    ):
        return original(
            name,
            prompt,
            allowed_tools=allowed_tools,
            allowed_directories=allowed_directories,
            streaming_output_file=streaming_output_file,
            phase_name=phase_name,
        )

    j.manager.execute = unsupported
    j.runtime.run(start_step="inspect_custom")
    assert len(j.manager.calls) == 1 and j.manager.deliveries == 0
    assert j.effects == ["prepare"]
    assert len(HumanTaskRecordStore(j.issue).tasks()) == 1


def test_i4_reentry_human_decision_precedes_another_reserved_dispatch(journey):
    from cafe.agents.executor import AgentExecutionError

    j = journey(
        [CORRECTED, AgentExecutionError("provider stopped", error_type="rate_limit")],
        workspace=True,
        workspace_action=lambda repo, iteration, call: (repo / "owned.txt").write_text("dirty"),
        human="need_permission",
    )
    j.runtime.run(start_step="inspect_custom")
    before = json.loads((j.iteration / "iteration.json").read_text())["workspace_completion"][
        "consumed"
    ]
    (j.issue / "next_step.txt").write_text(
        json.dumps(
            {"version": 1, "to_owner": "user", "to_step": "user", "intent": "need_permission"}
        )
    )
    result = j.executor.execute_step(
        "inspect_custom", j.playbook["steps"]["inspect_custom"], j.runtime.blackboard
    )
    assert not result.artifact_ready and not result.artifacts
    assert len(j.manager.calls) == 2
    assert (
        json.loads((j.iteration / "iteration.json").read_text())["workspace_completion"]["consumed"]
        == before
    )


def test_i7_clean_workspace_preserves_existing_checklist_retry(journey):
    def complete(iteration, call):
        if call >= 2:
            path = iteration / "checklist.md"
            path.write_text(path.read_text().replace("[ ]", "[x]"))

    j = journey([CORRECTED], workspace=True, unchecked=True, provider_mutation=complete)
    result = j.runtime.run(start_step="inspect_custom")
    assert len(j.manager.calls) == 2 and j.manager.deliveries == 1
    assert result.completed and j.effects == ["prepare", "after"]
    budget = json.loads((j.iteration / "iteration.json").read_text()).get(
        "workspace_completion", {}
    )
    assert budget.get("consumed", 0) == 0


@pytest.mark.parametrize("intent", ["await_agent", "need_permission"])
def test_u4_i1_legacy_status_without_baton_retains_completion_and_human_routes(journey, intent):
    def status_only(repo, iteration, call):
        (iteration.parents[1] / "next_step.txt").unlink(missing_ok=True)

    j = journey(
        [CORRECTED, (CORRECTED, intent)],
        mode="legacy",
        workspace=True,
        workspace_action=change_then_commit,
        post_submission=status_only,
        human="need_permission" if intent == "need_permission" else None,
    )
    j.runtime.run(start_step="inspect_custom")
    assert len(j.manager.calls) == 2
    if intent == "await_agent":
        assert j.manager.deliveries == 1 and "custom_snapshot" in j.runtime.blackboard.artifacts
    else:
        assert j.manager.deliveries == 0 and j.effects == ["prepare"]
        assert j.runtime.blackboard.handoff_contract.to_owner.value == "user"
        assert "custom_snapshot" not in j.runtime.blackboard.artifacts


def test_i3_original_directories_are_pinned_before_initial_dispatch(journey, monkeypatch):
    def mutate(iteration, call):
        if call == 1:
            monkeypatch.setattr(j.executor, "_get_allowed_directories", lambda: ["expanded"])

    j = journey(
        [CORRECTED],
        workspace=True,
        workspace_action=lambda repo, iteration, call: (repo / "owned.txt").write_text("dirty"),
        provider_mutation=mutate,
    )
    j.runtime.run(start_step="inspect_custom")
    assert len(j.manager.calls) == 1 and j.manager.deliveries == 0
    assert j.effects == ["prepare"]


def test_i8_agent_cannot_author_host_publication_results(journey):
    def mutate(iteration, call):
        data = json.loads((iteration / "iteration.json").read_text())
        data["workspace_publication"] = {"delivery": "f" * 64, "response": "", "hooks": {}}
        (iteration / "iteration.json").write_text(json.dumps(data))

    j = journey([CORRECTED], workspace=True, provider_mutation=mutate)
    j.runtime.run(start_step="inspect_custom")
    assert len(j.manager.calls) == 1 and j.manager.deliveries == 0
    assert j.effects == ["prepare"]
    assert "custom_snapshot" not in j.runtime.blackboard.artifacts


def test_i6_changed_comparison_base_cannot_certify_consumed_delivery(journey):
    def commit(repo, iteration, call):
        git(repo, "checkout", "-b", "feature")
        change_then_commit(repo, iteration, 1)
        change_then_commit(repo, iteration, 2)

    def change_base(stage, repo, count):
        git(repo, "branch", "new-base", "HEAD")
        (j.issue / "issue.yaml").write_text("base_branch: new-base\n")

    j = journey([CORRECTED], workspace=True, workspace_action=commit, effect_action=change_base)
    j.runtime.run(start_step="inspect_custom")
    assert j.manager.deliveries == 0
    assert "custom_snapshot" not in j.runtime.blackboard.artifacts
    assert "evidence_bundle" not in j.runtime.blackboard.artifacts
    assert j.effects == ["prepare", "after"]


@pytest.mark.parametrize("changed", ["base", "input"])
def test_i8_recovery_refuses_changed_authoritative_delivery(journey, monkeypatch, changed):
    def commit(repo, iteration, call):
        git(repo, "checkout", "-b", "feature")
        change_then_commit(repo, iteration, 1)
        change_then_commit(repo, iteration, 2)

    j = journey([CORRECTED], workspace=True, workspace_action=commit, declared_input=True)
    j.runtime.run(start_step="inspect_custom")
    assert j.manager.deliveries == 1
    if changed == "base":
        git(j.repo, "branch", "new-base", "HEAD")
        (j.issue / "issue.yaml").write_text("base_branch: new-base\n")
    else:
        (j.issue / "brief.md").write_text("replacement brief")
    monkeypatch.setattr(j.executor, "_get_next_iteration_number", lambda *args: 1)
    (j.issue / "next_step.txt").write_text(json.dumps({"version": 1, "intent": "await_agent"}))
    before = list(j.effects)
    with pytest.raises(RuntimeError):
        j.executor.execute_step("inspect_custom", j.playbook["steps"]["inspect_custom"], j.runtime.blackboard)
    assert j.effects.count("after") == before.count("after")
    assert j.effects.count("publish") == before.count("publish")
    assert len(j.manager.calls) == 1
