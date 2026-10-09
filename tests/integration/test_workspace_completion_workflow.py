"""Workspace completion journeys through the public workflow runtime (I1–I8)."""

import json
import subprocess
from pathlib import Path

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
@pytest.mark.parametrize("base_branch", [None, "main"])
def test_i1_custom_step_corrects_before_effects_in_exact_context(journey, mode, base_branch):
    j = journey([CORRECTED], mode=mode, workspace=True, workspace_action=change_then_commit,
                base_branch=base_branch)
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


@pytest.mark.parametrize("mode", ["baton", "legacy"])
@pytest.mark.parametrize(
    "locale,meanings",
    [
        ("zh-TW", ("來源", "權限")),
        ("zh-Hant", ("來源", "權限")),
        ("zh-HK", ("來源", "權限")),
        ("zh-CN", ("origin", "authorization")),
        ("zh-Hans-TW", ("origin", "authorization")),
        ("fr-FR", ("origin", "authorization")),
        (None, ("origin", "authorization")),
        ("zh//TW", ("origin", "authorization")),
    ],
)
def test_u5_i1_correction_prompt_uses_workflow_conversation_locale(journey, mode, locale, meanings):
    j = journey([CORRECTED], workspace=True, workspace_action=change_then_commit, mode=mode)
    j.runtime.blackboard.conversation_locale = locale
    j.runtime.blackboard_store.save(j.runtime.blackboard)
    j.runtime.run(start_step="inspect_custom")
    prompt = j.manager.calls[1][1]
    assert "owned.txt" in prompt and all(meaning in prompt for meaning in meanings)
    assert j.runtime.blackboard_store.load_or_create("inspect_custom").conversation_locale == locale
    assert j.manager.deliveries == 1
    assert len(j.manager.calls) == 2 and j.manager.calls[1][2].is_exact
    assert j.effects == ["prepare", "after"]


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
        j.executor.execute_step(
            "inspect_custom", j.playbook["steps"]["inspect_custom"], j.runtime.blackboard
        )
    assert j.effects.count("after") == before.count("after")
    assert j.effects.count("publish") == before.count("publish")
    assert len(j.manager.calls) == 1


def test_u3_u5_i2_large_dirty_workspace_preserves_usable_recovery(journey):
    def dirty(repo, iteration, call):
        if call == 1:
            for index in range(5000):
                (repo / (f"generated-{index:05d}-" + "x" * 180 + ".txt")).write_text("dirty")

    j = journey([CORRECTED], workspace=True, workspace_action=dirty)
    result = j.runtime.run(start_step="inspect_custom")
    assert not result.completed and j.manager.deliveries == 0
    assert len(j.manager.calls) == 4
    assert j.effects == ["prepare"]
    for _, prompt, _, _ in j.manager.calls[1:]:
        assert len(prompt.encode()) < 32768
        assert "5000" in prompt and "generated-00000" in prompt
    assert (j.iteration / "iteration.json").stat().st_size <= 1_048_576
    assert j.executor._load_workspace_completion(j.iteration)["consumed"] == 3
    assert len(HumanTaskRecordStore(j.issue).tasks()) == 1
    assert j.runtime.blackboard.handoff_contract.to_owner.value == "user"
    assert "custom_snapshot" not in j.runtime.blackboard.artifacts
    assert (
        len(git(j.repo, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")) - 1
        == 5000
    )


def test_i8_aggregate_hook_results_cannot_break_human_recovery(journey, monkeypatch):
    from cafe.phases.generic_phase import HookResult

    calls = []

    class LargeEffect:
        def run(self, **kwargs):
            calls.append(len(calls))
            if len(calls) == 70:
                raise RuntimeError("interrupted after bounded results")
            return HookResult(context_updates={str(len(calls)): "x" * 16000})

    j = journey([CORRECTED], workspace=True)
    j.executor.generic_phase.hook_registry["LargeEffect"] = LargeEffect
    step = j.playbook["steps"]["inspect_custom"]
    step["hooks"]["after_execute"] = ["LargeEffect"] * 70
    result = j.runtime.run(start_step="inspect_custom")
    assert not result.completed and j.manager.deliveries == 0
    assert len(HumanTaskRecordStore(j.issue).tasks()) == 1
    assert (j.iteration / "iteration.json").stat().st_size <= 1_048_576
    assert j.runtime.blackboard.handoff_contract.to_owner.value == "user"
    before = len(calls)
    monkeypatch.setattr(j.executor, "_get_next_iteration_number", lambda *args: 1)
    (j.issue / "next_step.txt").write_text(json.dumps({"version": 1, "intent": "await_agent"}))
    with pytest.raises(RuntimeError):
        j.executor.execute_step("inspect_custom", step, j.runtime.blackboard)
    assert len(calls) == before


@pytest.mark.parametrize("stage", ["after_execute", "publish_output"])
def test_u4_i4_stopped_hook_human_status_never_publishes_completion(journey, stage):
    from cafe.core.status_codes import PhaseStatusCode
    from cafe.phases.generic_phase import HookResult

    def status_only(repo, iteration, call):
        (iteration.parents[1] / "next_step.txt").unlink(missing_ok=True)

    class PermissionEffect:
        def run(self, **kwargs):
            j.effects.append("permission")
            return HookResult(
                continue_pipeline=False, override_status_code=PhaseStatusCode.NEED_PERMISSION
            )

    j = journey(
        [CORRECTED],
        workspace=True,
        mode="legacy",
        human="need_permission",
        post_submission=status_only,
    )
    j.executor.generic_phase.hook_registry["PermissionEffect"] = PermissionEffect
    step = j.playbook["steps"]["inspect_custom"]
    step["hooks"][stage] = ["PermissionEffect"]
    j.runtime.run(start_step="inspect_custom")
    assert j.manager.deliveries == 0
    assert j.runtime.blackboard.handoff_contract.to_owner.value == "user"
    assert j.runtime.blackboard.handoff_contract.intent.value == "need_permission"
    assert "custom_snapshot" not in j.runtime.blackboard.artifacts
    assert "evidence_bundle" not in j.runtime.blackboard.artifacts
    assert not (j.iteration / "artifact.json").exists()
    assert j.effects.count("permission") == 1 and len(j.manager.calls) == 1


@pytest.mark.parametrize("changed", ["human", "output"])
def test_i6_last_use_guard_refuses_changed_decision_before_effect(journey, monkeypatch, changed):
    from cafe.phases.generic_phase import HookResult
    from tests.integration.test_workflow_artifact_correction import REJECTED

    invocations = []

    class Consumer:
        def run(self, **kwargs):
            # The external consumer deliberately trusts the producer's gate.
            invocations.append(Path(kwargs["output_file"]).read_text())
            return HookResult()

    j = journey([CORRECTED], workspace=True, human="need_permission")
    j.executor.generic_phase.hook_registry["Consumer"] = Consumer
    step = j.playbook["steps"]["inspect_custom"]
    step["hooks"]["after_execute"] = ["Consumer"]
    save = j.executor._save_workspace_publication
    replaced = False

    def replace_after_started(iteration_dir, progress):
        nonlocal replaced
        save(iteration_dir, progress)
        if not replaced:
            replaced = True
            if changed == "human":
                (j.issue / "next_step.txt").write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "to_owner": "user",
                            "to_step": "user",
                            "intent": "need_permission",
                        }
                    )
                )
            else:
                (j.iteration / "output.md").write_text(REJECTED)

    monkeypatch.setattr(j.executor, "_save_workspace_publication", replace_after_started)
    j.runtime.run(start_step="inspect_custom")
    assert invocations == []
    assert j.effects == ["prepare"] and j.manager.deliveries == 0
    assert "custom_snapshot" not in j.runtime.blackboard.artifacts
    assert "evidence_bundle" not in j.runtime.blackboard.artifacts
    assert j.runtime.blackboard.handoff_contract.to_owner.value == "user"
    if changed == "human":
        assert j.runtime.blackboard.handoff_contract.intent.value == "need_permission"
    monkeypatch.setattr(j.executor, "_get_next_iteration_number", lambda *args: 1)
    (j.issue / "next_step.txt").write_text(json.dumps({"version": 1, "intent": "await_agent"}))
    (j.iteration / "output.md").write_text(CORRECTED)
    with pytest.raises(RuntimeError):
        j.executor.execute_step("inspect_custom", step, j.runtime.blackboard)
    assert invocations == [] and len(j.manager.calls) == 1


def parsed_turn_usage(count):
    """Use the actual CLI parser and supported typed provider usage contract."""
    from cafe.agents.cli.codex import CodexCLI
    from cafe.core.types import TokenUsage

    event = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 2}})
    return TokenUsage(
        input_tokens=3 * count,
        output_tokens=2 * count,
        turn_usages=CodexCLI.extract_turn_usages([event] * count),
    )


@pytest.mark.parametrize("turns", [0, 1, 20, 6000])
def test_u3_i1_i2_correction_telemetry_preserves_usable_recovery(journey, turns):
    def authorized_work(repo, iteration, call):
        if call <= 2:
            change_then_commit(repo, iteration, call)
        else:
            assert j.executor._load_workspace_completion(iteration)["consumed"] == 2

    j = journey(
        [CORRECTED],
        workspace=True,
        workspace_action=authorized_work,
        provider_usage=lambda call: parsed_turn_usage(turns if call == 2 else 0),
    )
    result = j.runtime.run(start_step="inspect_custom")
    assert len(j.manager.calls) == 2
    assert j.executor._load_workspace_completion(j.iteration)["consumed"] == 1
    assert (j.iteration / "iteration.json").stat().st_size <= 1_048_576
    metadata = json.loads((j.iteration / "iteration.json").read_text())
    if turns < 6000:
        assert result.completed and j.manager.deliveries == 1
        assert len(metadata["stats"]["turn_usages"]) == turns
        assert metadata["stats"]["input_tokens"] == 3 * turns
        assert j.effects == ["prepare", "after"]
    else:
        assert not result.completed and j.manager.deliveries == 0
        assert j.runtime.blackboard.current_step == "user"
        assert len(HumanTaskRecordStore(j.issue).tasks()) == 1
        assert j.runtime.blackboard.handoff_contract.to_owner.value == "user"
        assert j.effects == ["prepare"]
        assert "custom_snapshot" not in j.runtime.blackboard.artifacts
        assert "evidence_bundle" not in j.runtime.blackboard.artifacts
        assert metadata["stats"]["turn_usages"] == []
        assert any(
            "capacity" in reason
            for reason in j.executor._load_workspace_completion(j.iteration)["rejections"]
        )
        from cafe.core.blackboard import BlackboardStore
        from cafe.ui.human_tasks import apply_human_task_payload

        task = HumanTaskRecordStore(j.issue).tasks()[0]
        apply_human_task_payload(
            issue_dir=j.issue,
            playbook_data=j.playbook,
            blackboard=BlackboardStore(j.issue).load_or_create("inspect_custom"),
            from_step="inspect_custom",
            trigger=task.trigger,
            raw_payload={"task": task.policy_id, "decision": "retry", "human_task_id": task.id},
            source="test",
        )
        resumed = j.reconstruct()
        assert resumed.run().completed
        assert j.executor._load_workspace_completion(j.iteration)["consumed"] == 2
        assert len(j.manager.calls) == 3 and j.manager.deliveries == 1
        assert j.manager.calls[-1][2].is_exact
        assert j.effects.count("after") == 1
        assert not (j.issue / "inspect_custom" / "iteration_002").exists()


@pytest.mark.parametrize("candidate_bytes", [983039, 983040, 983041, 1048576, 1048577])
def test_u3_i2_telemetry_admission_and_reader_boundaries(journey, candidate_bytes):
    from cafe.core.phase import Phase

    snapshots = []
    usage = parsed_turn_usage(350)

    def seed_admitted_record(repo, iteration, call):
        change_then_commit(repo, iteration, call)
        if call == 2:
            path = iteration / "iteration.json"
            metadata = json.loads(path.read_text())
            candidate = dict(metadata)
            candidate["stats"] = Phase._merge_token_usage_stats(metadata.get("stats"), usage)
            overhead = len((json.dumps(candidate, ensure_ascii=False, indent=2) + "\n").encode())
            metadata["prompt"] += "p" * (candidate_bytes - overhead)
            j.executor._persist_workspace_metadata(path, metadata)
            snapshots.append(metadata)

    j = journey(
        [CORRECTED],
        workspace=True,
        workspace_action=seed_admitted_record,
        provider_usage=lambda call: usage if call == 2 else parsed_turn_usage(0),
    )
    result = j.runtime.run(start_step="inspect_custom")
    assert not result.completed and j.manager.deliveries == 0
    assert j.runtime.blackboard.current_step == "user"
    assert len(HumanTaskRecordStore(j.issue).tasks()) == 1
    assert j.effects == ["prepare"]
    assert j.executor._load_workspace_completion(j.iteration)["consumed"] == 1

    path = j.iteration / "iteration.json"
    assert path.stat().st_size <= 1_048_576
    actual = json.loads(path.read_text())
    assert actual["prompt"] == snapshots[0]["prompt"]
    if candidate_bytes > 983040:
        assert actual["stats"] == snapshots[0]["stats"]
    else:
        assert len(actual["stats"]["turn_usages"]) == 350
    # The actual reader can mark this record again without destroying its budget.
    j.runtime._mark_latest_iteration_completion_untrusted("inspect_custom")
    assert path.stat().st_size <= 1_048_576
    assert j.executor._load_workspace_completion(j.iteration)["consumed"] == 1


@pytest.mark.parametrize("intent", ["need_permission", "need_clarification"])
@pytest.mark.parametrize("mode", ["baton", "legacy"])
@pytest.mark.parametrize(
    "diagnostic,turns",
    [("ordinary", 20), ("usage", 6000), ("stream", 20), ("full_record", 6000)],
)
def test_u4_i4_human_correction_survives_auxiliary_telemetry_limit(
    journey, intent, mode, diagnostic, turns
):
    observations = []

    def preserve_owner_work(repo, iteration, call):
        if call == 1:
            for index in range(6):
                (repo / f"template-{index}.txt").write_text(f"owner {index}")
        observations.append((git(repo, "rev-parse", "HEAD"), git(repo, "diff", "--cached")))

    def status_only(repo, iteration, call):
        if mode == "legacy":
            (iteration.parents[1] / "next_step.txt").unlink(missing_ok=True)
        if call == 2 and diagnostic == "full_record":
            path = iteration / "iteration.json"
            metadata = json.loads(path.read_text())
            size = len((json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode())
            metadata["prompt"] += "p" * (983040 - size)
            j.executor._persist_workspace_metadata(path, metadata)

    j = journey(
        [CORRECTED, (CORRECTED, intent)],
        workspace=True,
        human=intent,
        mode=mode,
        workspace_action=preserve_owner_work,
        post_submission=status_only,
        provider_usage=lambda call: parsed_turn_usage(turns if call == 2 else 0),
    )
    if diagnostic == "stream":
        execute = j.manager.execute

        def oversized_stream(agent_name, prompt, *, continuation=None, **kwargs):
            result = list(execute(agent_name, prompt, continuation=continuation, **kwargs))
            if len(j.manager.calls) == 2:
                result[4] = ["s" * 1_048_576]
            return tuple(result)

        j.manager.execute = oversized_stream
    result = j.runtime.run(start_step="inspect_custom")
    assert not result.completed and j.manager.deliveries == 0
    assert j.runtime.blackboard.handoff_contract.intent.value == intent
    assert j.runtime.blackboard.handoff_contract.to_owner.value == "user"
    tasks = HumanTaskRecordStore(j.issue).tasks()
    assert all(task.policy_id != "agent-execution-interrupted" for task in tasks)
    assert (j.iteration / "questions.xml").exists()
    assert len(j.manager.calls) == 2 and j.manager.calls[-1][2].is_exact
    assert j.manager.calls[-1][2].session_id == "exact-report-session"
    assert j.effects == ["prepare"]
    assert "custom_snapshot" not in j.runtime.blackboard.artifacts
    assert "evidence_bundle" not in j.runtime.blackboard.artifacts
    assert observations[0] == observations[1]
    assert git(j.repo, "rev-parse", "HEAD") == observations[0][0]
    assert git(j.repo, "diff", "--cached") == observations[0][1]
    assert [(j.repo / f"template-{index}.txt").read_text() for index in range(6)] == [
        f"owner {index}" for index in range(6)
    ]
    path = j.iteration / "iteration.json"
    assert path.stat().st_size <= 1_048_576
    metadata = json.loads(path.read_text())
    assert len(metadata["stats"]["turn_usages"]) == (turns if turns == 20 else 0)
    budget = j.executor._load_workspace_completion(j.iteration)
    assert budget["consumed"] == 1
    if diagnostic in ("usage", "stream"):
        assert any("capacity" in reason for reason in budget["rejections"])


@pytest.mark.parametrize("defect", ["malformed", "wrong-owner", "undeclared"])
def test_u4_i5_auxiliary_limit_cannot_authorize_invalid_human_baton(journey, defect):
    def invalid_control(repo, iteration, call):
        if call != 2:
            return
        path = iteration.parents[1] / "next_step.txt"
        if defect == "malformed":
            path.write_text("{")
        else:
            payload = json.loads(path.read_text())
            if defect == "wrong-owner":
                payload.update(to_owner="agent", to_step="deliver_custom")
            else:
                payload["intent"] = "confirm_output"
            path.write_text(json.dumps(payload))

    j = journey(
        [CORRECTED, (CORRECTED, "need_permission")],
        workspace=True,
        human="need_permission",
        workspace_action=lambda repo, iteration, call: (repo / "owned.txt").write_text("dirty"),
        post_submission=invalid_control,
        provider_usage=lambda call: parsed_turn_usage(6000 if call == 2 else 0),
    )
    if defect == "undeclared":
        j.playbook["steps"]["inspect_custom"]["valid_intents"].remove("confirm_output")
    assert not j.runtime.run(start_step="inspect_custom").completed
    assert len(j.manager.calls) == 2 and j.manager.deliveries == 0
    assert j.effects == ["prepare"]
    assert j.runtime.blackboard.handoff_contract.intent.value == "manual_handoff"
    tasks = HumanTaskRecordStore(j.issue).tasks()
    assert len(tasks) == 1 and tasks[0].policy_id == "agent-execution-interrupted"
    assert j.executor._load_workspace_completion(j.iteration)["consumed"] == 1
    assert (j.iteration / "iteration.json").stat().st_size <= 1_048_576
    assert "custom_snapshot" not in j.runtime.blackboard.artifacts


@pytest.mark.parametrize("mode", ["baton", "legacy"])
@pytest.mark.parametrize("defect", ["wrong-directory", "missing", "malformed", "undeclared-intent", "wrong-producer"])
def test_early_handoff_rejection_retries_original_session_before_publication(journey, mode, defect):
    def damage(repo, iteration, call):
        if call != 1:
            return
        path = iteration.parents[1] / "next_step.txt"
        if defect == "wrong-directory":
            # The agent writes ../next_step.txt from iteration_001, leaving the
            # previous phase's valid but inappropriate baton at the real path.
            (iteration.parent / "next_step.txt").write_bytes(path.read_bytes())
            path.write_text(json.dumps({"version": 1, "intent": "manual_handoff",
                                        "to_owner": "agent", "to_step": "inspect_custom"}))
        elif defect == "missing":
            path.unlink()
        elif defect == "malformed":
            path.write_text("{broken")
        elif defect == "undeclared-intent":
            path.write_text(json.dumps({"version": 1, "intent": "manual_handoff"}))
        else:
            path.write_text(json.dumps({"version": 1, "intent": "await_agent",
                                        "from_step": "deliver_custom", "to_owner": "agent",
                                        "to_step": "deliver_custom"}))

    j = journey([CORRECTED], mode=mode, workspace=True, extra_publication=True,
                post_submission=damage)
    if mode == "legacy" and defect == "missing":
        original_execute = j.manager.execute

        def without_status(*args, **kwargs):
            response = original_execute(*args, **kwargs)
            if len(j.manager.calls) == 1:
                return ("", *response[1:])
            return response

        j.manager.execute = without_status
    result = j.runtime.run(start_step="inspect_custom")
    assert result.completed
    assert len(j.manager.calls) == 2
    first, correction = j.manager.calls
    assert correction[2].is_exact and correction[2].session_id == "exact-report-session"
    # Existing baton-only retries deliberately narrow write access.
    writes = [tool for tool in correction[3]["allowed_tools"] if tool.startswith(("edit", "write"))]
    assert writes and all(tool.endswith("/next_step.txt)") for tool in writes)
    assert correction[3]["allowed_directories"] == first[3]["allowed_directories"]
    assert str((j.issue / "next_step.txt").resolve()) in correction[1]
    assert "BATON ERROR" in correction[1]
    assert j.effects.count("after") == 1 and j.effects.count("publish") == 2
    assert j.manager.deliveries == 1
    assert not HumanTaskRecordStore(j.issue).tasks()
    assert not (j.issue / "inspect_custom" / "iteration_002").exists()
    events = j.runtime.blackboard.events
    assert any(e.event_type == "baton_rejected" for e in events)
    assert not any(e.event_type == "step_interrupted" for e in events)


@pytest.mark.parametrize("mode", ["baton", "legacy"])
@pytest.mark.parametrize("intent", ["confirm_output", "need_permission", "need_clarification"])
def test_handoff_retry_preserves_agent_request_for_human(journey, mode, intent):
    def damage(repo, iteration, call):
        if call == 1:
            (iteration.parents[1] / "next_step.txt").write_text("{broken")

    j = journey([CORRECTED, (CORRECTED, intent)], mode=mode, human=intent,
                workspace=True, extra_publication=True, post_submission=damage)
    result = j.runtime.run(start_step="inspect_custom")
    assert not result.completed
    assert len(j.manager.calls) == 2
    assert j.manager.calls[1][2].is_exact
    assert j.manager.deliveries == 0
    assert "publish" not in j.effects
    assert j.runtime.blackboard.handoff_contract.to_owner.value == "user"
    assert j.runtime.blackboard.handoff_contract.intent.value == intent
    tasks = HumanTaskRecordStore(j.issue).tasks()
    assert not any(task.trigger == "agent_execution_interrupted" for task in tasks)


@pytest.mark.parametrize("mode", ["baton", "legacy"])
def test_early_handoff_rejection_exhausts_existing_bounded_retry(journey, mode):
    def damage(repo, iteration, call):
        (iteration.parents[1] / "next_step.txt").write_text("{broken")

    j = journey([CORRECTED], mode=mode, workspace=True, extra_publication=True,
                post_submission=damage)
    with pytest.raises(RuntimeError, match="invalid baton 3 times"):
        j.runtime.run(start_step="inspect_custom")
    assert len(j.manager.calls) == 3
    assert all(call[2].is_exact for call in j.manager.calls[1:])
    assert j.effects.count("after") == 0 and j.effects.count("publish") == 0
    assert j.manager.deliveries == 0
    assert not HumanTaskRecordStore(j.issue).tasks()
    rejected = [e for e in j.runtime.blackboard.events if e.event_type == "baton_rejected"]
    assert [e.data["retry"] for e in rejected if e.data["retry"]] == [1, 2, 3]


@pytest.mark.parametrize("mode", ["baton", "legacy"])
def test_handoff_rejected_after_publication_does_not_repeat_effects(journey, mode):
    def damage(stage, repo, count):
        if stage == "publish":
            (repo / ".cafe/issues/correction/next_step.txt").write_text("{broken")

    j = journey([CORRECTED], mode=mode, workspace=True, extra_publication=True,
                effect_action=damage)
    with pytest.raises(RuntimeError, match="invalid baton 3 times"):
        j.runtime.run(start_step="inspect_custom")
    assert len(j.manager.calls) == 1
    assert j.effects.count("after") == 1 and j.effects.count("publish") == 1
    assert j.manager.deliveries == 0
    assert "custom_snapshot" not in j.runtime.blackboard.artifacts


@pytest.mark.parametrize("mode", ["baton", "legacy"])
def test_handoff_retry_never_substitutes_a_missing_original_session(journey, mode):
    def lose_session(iteration, call):
        if call == 1:
            j.manager.get_last_session_id = lambda: None

    def damage(repo, iteration, call):
        (iteration.parents[1] / "next_step.txt").write_text("{broken")

    j = journey([CORRECTED], mode=mode, workspace=True, extra_publication=True,
                provider_mutation=lose_session, post_submission=damage)
    result = j.runtime.run(start_step="inspect_custom")
    assert not result.completed
    assert len(j.manager.calls) == 1
    assert j.effects.count("after") == 0 and j.effects.count("publish") == 0
    assert j.manager.deliveries == 0
    assert any(
        "exact producing session" in e.data.get("detail", "")
        for e in j.runtime.blackboard.events if e.event_type == "step_interrupted"
    )


@pytest.mark.parametrize("mode", ["baton", "legacy"])
def test_preparation_recovery_runs_new_session_then_corrects_handoff_before_publication(
    journey, monkeypatch, mode
):
    """Exercise real persisted recovery through invocation, re-entry and publication."""
    from cafe.core.blackboard import BlackboardStore
    from cafe.core.session_continuation import SessionContinuationPolicy
    from cafe.ui.human_tasks import apply_human_task_payload

    j = journey([(CORRECTED, "invalid_intent"), CORRECTED], mode=mode, workspace=True)
    generate = j.executor._generate_checklist

    def unavailable(**kwargs):
        raise ValueError("preparation input unavailable")

    monkeypatch.setattr(j.executor, "_generate_checklist", unavailable)
    interrupted = j.runtime.run(start_step="inspect_custom")
    assert not interrupted.completed and not j.manager.calls
    records = HumanTaskRecordStore(j.issue)
    task = records.tasks()[0]
    state = BlackboardStore(j.issue).load_or_create("inspect_custom")
    application = apply_human_task_payload(
        issue_dir=j.issue, playbook_data=j.playbook, blackboard=state,
        from_step="inspect_custom", trigger=task.trigger,
        raw_payload={"task": task.policy_id, "decision": "retry_fresh_session",
                     "human_task_id": task.id}, source="test",
    )
    assert application.rejection is None
    recovery = records.get_result(task.id).payload["session_continuation"]
    assert "preparation" in recovery and "previous" not in recovery
    monkeypatch.setattr(j.executor, "_generate_checklist", generate)
    resumed = j.reconstruct()
    result = resumed.run()
    assert result.completed
    assert len(j.manager.calls) == 2
    assert j.manager.calls[0][2].policy is SessionContinuationPolicy.NEW
    assert j.manager.calls[1][2].is_exact
    assert j.manager.calls[1][2].session_id == "exact-report-session"
    saved = json.loads((j.iteration / "iteration.json").read_text())
    assert saved["session_recovery"] == recovery
    assert saved["session_id"] == "exact-report-session"
    assert j.effects.count("after") == 1 and j.manager.deliveries == 1
    assert not any(t.status.value == "pending" for t in records.tasks())
    # Another runtime reconstruction must not repeat the external effect.
    assert j.reconstruct().run().completed
    assert j.effects.count("after") == 1 and j.manager.deliveries == 1
