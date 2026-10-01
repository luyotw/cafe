"""I1–I8: producer correction through the public runtime and real phase pipeline."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from cafe.agents.executor import AgentExecutionError
from cafe.core.blackboard import ArtifactEntry, ArtifactKind, BlackboardStore
from cafe.core.git import GitOperations
from cafe.core.hooks import HookResult
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.todo import parse_todo_list
from cafe.core.types import AgentCLI, TokenUsage
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.phases.generic_phase import GenericPhase
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader
from cafe.skills.native_bridge import NativeSkillBridge

FIXTURES = Path(__file__).parents[1] / "fixtures" / "artifact_correction"
REJECTED = (FIXTURES / "rejected.md").read_text()
CORRECTED = (FIXTURES / "corrected.md").read_text()
MISSING_PLAN_TODO = "<!-- plan-stage: detailed-plan -->\n# Missing tasks\n"


def _assert_correction_context_preserved(manager):
    first = manager.calls[0]
    assert all(value == manager.checklists[0] for value in manager.checklists)
    for name, prompt, continuation, kwargs in manager.calls[1:]:
        assert name == first[0]
        assert "evidence_bundle" in prompt
        assert continuation.is_exact and continuation.session_id == "exact-report-session"
        assert kwargs["allowed_tools"] == first[3]["allowed_tools"]
        assert kwargs["allowed_directories"] == first[3]["allowed_directories"]
    for metadata in manager.metadata:
        assert metadata["effective_checklist"] == manager.metadata[0]["effective_checklist"]
        assert metadata["model"] == "test-model"


@pytest.fixture
def journey(tmp_path, monkeypatch):
    def build(submissions, *, mode="baton", completed_checklist=False, human=None,
              reverse=False, unchecked=False, mutate=None, provider_mutation=None, capability=None, publication_mutation=False, workspace=None, workspace_action=None, effect_action=None, extra_publication=False, projected=False, post_submission=None):
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
        monkeypatch.chdir(repo)
        config = repo / ".cafe"
        config.mkdir()
        (config / "strategic_context.yaml").write_text("version: 1\n")
        (config / "phases.yaml").write_text(
            "inspect_custom:\n  name: Author\n  clis:\n    - cli: codex\n      model: test-model\n"
            "deliver_custom:\n  name: Author\n  clis:\n    - cli: codex\n      model: test-model\n"
        )
        agent = config / "agents" / "author_custom" / "Author.md"
        agent.parent.mkdir(parents=True)
        agent.write_text("---\nname: Author\ndescription: Write evidence\n---\n\nWrite evidence.\n")
        skill = config / "skills" / "custom-report"
        skill.mkdir(parents=True)
        workflow = {"execution_profile": {"workload": "implementation", "reasoning": "standard",
                    "risk_domains": ["integration"], "fallback_strength": "equivalent_or_stronger"}}
        if human:
            workflow["human_tasks"] = [{"id": "decision", "pattern": "confirm_output" if human == "confirm_output" else "revision_feedback",
                "prompt": "Review report", "input_schema": "feedback" if human != "confirm_output" else "decision",
                **({"decisions": [{"id": "accept", "label": "Accept"}]} if human == "confirm_output" else {})}]
        if completed_checklist or unchecked or projected:
            # Existing checklist guidance uses the developer directory for
            # custom roles; this journey does not change that separate policy.
            guidance = config / "agents" / "developer" / "Author.md"
            guidance.parent.mkdir(parents=True)
            guidance.write_text(agent.read_text())
            (skill / "references").mkdir()
            (skill / "references" / "gates.md").write_text("[ ] Preserve evidence\n")
            workflow["checklist"] = {"include_role_guidance": False, "variants": [{"when": {}, "sections": [{"reference": "gates.md"}]}]}
        if projected:
            workflow["checklist"] = {"include_role_guidance": False, "variants": [{"when": {}, "sections": [{"todo_projection": {"artifact": "blueprint", "source": "bespoke"}}]}]}
        (skill / "SKILL.md").write_text("---\n" + yaml.safe_dump({
            "name": "custom-report", "description": "Write evidence", "workflow": workflow,
        }) + "---\n\nWrite {output_file} and submit {next_step_file}.\n")
        issue = config / "issues" / "correction"
        iteration = issue / "inspect_custom" / "iteration_001"
        effects = []

        class Prepare:
            def run(self, **kwargs):
                effects.append("prepare")
                return HookResult()

        class After:
            def run(self, **kwargs):
                # An external effect must never consume a rejected report.
                content = Path(kwargs["output_file"]).read_text()
                parse_todo_list(content)
                effects.append("after")
                if effect_action:
                    effect_action("after", repo, len(effects))
                if mutate and not publication_mutation:
                    mutate(Path(kwargs["output_file"]))
                return HookResult(context_updates={"after_effect": "retained"}, events=[{"type": "effect", "stage": "after"}])

        class MutatePublication:
            def run(self, **kwargs):
                effects.append("publish-mutation")
                mutate(Path(kwargs["output_file"]))
                return HookResult()

        class Publish:
            def run(self, **kwargs):
                assert kwargs["context"]["after_effect"] == "retained"
                effects.append("publish")
                if effect_action:
                    effect_action("publish", repo, len(effects))
                return HookResult(context_updates={"completed_effect": "retained"})

        producer = {"skill": "custom-report", "role": "author_custom",
            "output_artifact": "evidence_bundle", "valid_intents": ["await_agent", "confirm_output", "need_permission", "need_clarification"],
            "allowed_tools": ["Read", "Write"],
            "hooks": {"prepare_input": ["Prepare"], "after_execute": ["After"]},
            "on": {"await_agent": "deliver_custom", "confirm_output": "inspect_custom",
                   "need_permission": "inspect_custom", "need_clarification": "inspect_custom"}}
        if projected:
            producer["input_artifacts"] = ["blueprint"]
        if extra_publication:
            producer["hooks"]["publish_output"] = ["Publish", "Publish"]
        if workspace:
            producer["workspace_artifact"] = "custom_snapshot"
        if capability:
            producer["capability_requests"] = ["cafe.browser.open"]
            producer["hooks"]["publish_output"] = (
                ["MutatePublication", "GitHubPRCreator"] if publication_mutation else ["GitHubPRCreator"]
            )
        if human != "confirm_output":
            producer["on"].pop("confirm_output")
        if mode == "baton":
            producer["behavior"] = {"completion": "baton"}
        successor = {"skill": "custom-report", "role": "author_custom",
            "input_artifacts": ["evidence_bundle"], "output_artifact": "delivery_receipt",
            "valid_intents": ["await_agent"], "on": {"await_agent": "_done"}}
        if projected:
            receipt = config / "skills" / "receipt-skill"
            receipt.mkdir()
            (receipt / "SKILL.md").write_text("---\nname: receipt-skill\ndescription: Record delivery\n---\nWrite delivery.\n")
            successor["skill"] = "receipt-skill"
        steps = {"inspect_custom": producer, "deliver_custom": successor}
        if reverse:
            steps = dict(reversed(list(steps.items())))
        definition = {"playbook": {"id": "correction", "applicability": {
            "summary": "Test artifact correction", "use_when": ["testing"], "avoid_when": ["production"]}},
            "commands": {"prepare": {"prompt_for_spec_plan_config": False}},
            "skills": {"workflow": {"shared": []}, "chat": {"shared": []}},
            "roles": {"author_custom": {"default_agent": "Author"}}, "steps": steps}
        playbooks = config / "playbooks"
        playbooks.mkdir()
        (playbooks / "correction.yaml").write_text(yaml.safe_dump(definition, sort_keys=False))
        playbook = PlaybookLoader(project_root=repo, global_root=tmp_path / "global").load("correction", strict=True)

        class Provider:
            def __init__(self):
                self.agent = SimpleNamespace(config=SimpleNamespace(
                    cli=AgentCLI.CODEX, session_id="exact-report-session", model="test-model"))
                self.calls = []
                self.checklists = []
                self.metadata = []
                self.deliveries = 0

            def get_agent(self, name):
                return self.agent

            def get_last_cli(self):
                return AgentCLI.CODEX

            def get_last_session_id(self):
                return "exact-report-session"

            def execute(self, name, prompt, *, continuation=None, phase_name=None, **kwargs):
                if phase_name == "deliver_custom":
                    self.deliveries += 1
                    output = issue / phase_name / "iteration_001" / "output.md"
                    assert BlackboardStore(issue).load_or_create("inspect_custom").artifacts["evidence_bundle"]
                    output.write_text(CORRECTED)
                    checklist = output.parent / "checklist.md"
                    checklist.write_text(checklist.read_text().replace("[ ]", "[x]"))
                    (issue / "next_step.txt").write_text(json.dumps({"version": 1, "intent": "await_agent"}))
                    return "await_agent", TokenUsage(), [], [], [], None
                self.calls.append((name, prompt, continuation, kwargs))
                if workspace_action:
                    workspace_action(repo, iteration, len(self.calls))
                assert not any(t.status.value == "pending" for t in HumanTaskRecordStore(issue).tasks())
                assert not any(e.event_type == "step_completed" for e in BlackboardStore(issue).load_or_create("inspect_custom").events)
                if len(self.calls) == 1 and completed_checklist:
                    checklist = iteration / "checklist.md"
                    checklist.write_text(checklist.read_text().replace("[ ]", "[x]"))
                self.checklists.append((iteration / "checklist.md").read_bytes())
                self.metadata.append(json.loads((iteration / "iteration.json").read_text()))
                submission = submissions[min(len(self.calls) - 1, len(submissions) - 1)]
                if isinstance(submission, Exception):
                    raise submission
                content, intent = submission if isinstance(submission, tuple) else (submission, "await_agent")
                (iteration / "output.md").write_text(content)
                if projected:
                    from tests.integration.test_checklist_overlay_workflow import complete_ledger
                    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
                    complete_ledger(iteration, commit)
                    output = iteration / "output.md"
                    output.write_text(content + "\n" + output.read_text().replace("`work.txt`", "`owned.txt`"))
                if provider_mutation:
                    provider_mutation(iteration, len(self.calls))
                baton = {"version": 1, "intent": intent}
                if intent in ("confirm_output", "need_permission", "need_clarification"):
                    baton.update(to_owner="user", to_step="user")
                    (iteration / "questions.xml").write_text('<questions><question id="1"><title>Decision?</title><options><option>Continue</option></options></question></questions>')
                if capability:
                    baton = {"version": 1, "to_owner": "agent", "to_step": "deliver_custom", "intent": "await_agent"}
                    (iteration / "capability_request.json").write_text(json.dumps({
                        "capability": "cafe.browser.open", "args": {"target_ref": "current_pr"},
                        "effects": {"browser_open": ["current_pr"], "writes": [], "network_destinations": []},
                        "credentials": [], "permissions": {},
                    }))
                (issue / "next_step.txt").write_text(json.dumps(baton))
                if post_submission:
                    post_submission(repo, iteration, len(self.calls))
                return "" if mode == "baton" else intent, TokenUsage(), [], [], [], None

        if workspace is not None:
            (repo / ".gitignore").write_text(".cafe/\n")
            (repo / "owned.txt").write_text("baseline\n")
            (repo / "work.txt").write_text("evidence\n")
            for index in range(6):
                (repo / f"template-{index}.txt").write_text("baseline template\n")
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True, capture_output=True)
        manager = Provider()
        loader = SkillLoader(project_root=repo, global_root=tmp_path / "global")
        phase = GenericPhase(loader, hook_registry={"Prepare": Prepare, "After": After, "MutatePublication": MutatePublication, "Publish": Publish},
            skill_bridge=NativeSkillBridge(loader, project_root=repo, home_dir=tmp_path / "home"))
        executor = GenericWorkflowStepExecutor(issue_dir=issue, issue_name="correction", playbook=playbook,
            generic_phase=phase, agent_manager=manager, git_ops=GitOperations(repo),
            role_agent_map={"author_custom": "Author"})
        runtime = BlackboardWorkflowRuntime(issue_dir=issue, playbook=playbook, executor=executor.execute_step)
        for name in (["plan", "qa_feedback", "review_feedback"] if reverse else ["qa_feedback", "review_feedback", "plan"]):
            decoy = issue / f"{name}.md"
            decoy.write_text(REJECTED)
            runtime.blackboard.artifacts[name] = ArtifactEntry(name=name, kind=ArtifactKind.DOCUMENT,
                path=str(decoy), version=1, updated_by="decoy", updated_at="2026-01-01T00:00:00+00:00")
        if projected:
            source = issue / "blueprint.md"
            source.write_text("## Todo List\n- [ ] `TASK-001` — Source: `bespoke` — Work: implement — Closure: correct — Evidence: tests\n")
            runtime.blackboard_store.set_artifact(runtime.blackboard, "blueprint", str(source))
        runtime.blackboard_store.save(runtime.blackboard)
        def reconstruct():
            renewed = GenericWorkflowStepExecutor(issue_dir=issue, issue_name="correction", playbook=playbook,
                generic_phase=phase, agent_manager=manager, git_ops=GitOperations(repo),
                role_agent_map={"author_custom": "Author"})
            return BlackboardWorkflowRuntime(issue_dir=issue, playbook=playbook, executor=renewed.execute_step)
        return SimpleNamespace(runtime=runtime, manager=manager, effects=effects, issue=issue,
            iteration=iteration, executor=executor, playbook=playbook, repo=repo, reconstruct=reconstruct)
    return build


@pytest.mark.parametrize("mode", ["baton", "legacy"])
@pytest.mark.parametrize("corrections", [1, 2])
@pytest.mark.parametrize("completed_checklist", [False, True])
def test_i1_i3_i5_i7_correction_preserves_evidence_and_context(journey, mode, corrections, completed_checklist):
    j = journey([REJECTED] * corrections + [CORRECTED], mode=mode,
                completed_checklist=completed_checklist, reverse=corrections == 2)
    result = j.runtime.run(start_step="inspect_custom")
    assert len(j.manager.calls) == 1 + corrections
    assert HumanTaskRecordStore(j.issue).tasks() == ()
    assert j.effects == ["prepare", "after"]
    assert (j.iteration / "output.md").read_text() == CORRECTED
    _assert_correction_context_preserved(j.manager)
    assert not (j.issue / "inspect_custom" / "iteration_002").exists()
    for consumed, metadata in enumerate(j.manager.metadata[1:], start=1):
        budget = metadata["artifact_correction"]
        assert budget["consumed"] == consumed
        assert len(budget["rejections"]) == consumed
    state = BlackboardStore(j.issue).load_or_create("inspect_custom")
    assert "evidence_bundle" in state.artifacts
    assert state.current_step in ("deliver_custom", "done", "_done")
    assert result.final_step in ("inspect_custom", "deliver_custom", "_done")


@pytest.mark.parametrize("mode", ["baton", "legacy"])
@pytest.mark.parametrize("changed", [False, True])
def test_i2_exhaustion_is_durable_and_does_not_publish(journey, mode, changed):
    j = journey([REJECTED, MISSING_PLAN_TODO if changed else REJECTED, REJECTED], mode=mode)
    result = j.runtime.run(start_step="inspect_custom")
    assert len(j.manager.calls) == 3
    assert not result.completed
    assert "artifact_format" in result.final_status_code
    assert j.effects == ["prepare"]
    assert not (j.iteration / "artifact.json").exists()
    state = BlackboardStore(j.issue).load_or_create("inspect_custom")
    assert "evidence_bundle" not in state.artifacts
    tasks = HumanTaskRecordStore(j.issue).tasks()
    assert len(tasks) == 1
    assert tasks[0].continuations == {"retry": "inspect_custom", "retry_fresh_session": "inspect_custom"}
    assert HumanTaskRecordStore(j.issue).get_assignment(tasks[0].id).assignee_type == "user"
    evidence = "\n".join(str(e.data) for e in state.events if e.event_type == "step_interrupted")
    assert "evidence_bundle" in evidence and "2" in evidence
    assert "evidence_bundle" in tasks[0].prompt
    metadata = json.loads((j.iteration / "iteration.json").read_text())
    assert metadata["artifact_correction"]["consumed"] == 2
    assert len(metadata["artifact_correction"]["rejections"]) == 3
    assert not metadata.get("workflow_completion_trusted")


@pytest.mark.parametrize("intent", ["confirm_output", "need_permission", "need_clarification"])
def test_i4_report_repair_preserves_human_decisions(journey, intent):
    j = journey([REJECTED, (CORRECTED, intent)], human=intent)
    result = j.runtime.run(start_step="inspect_custom")
    assert not result.completed
    assert len(j.manager.calls) == 2
    assert j.manager.deliveries == 0
    assert j.runtime.blackboard.handoff_contract.to_owner.value == "user"
    assert j.runtime.blackboard.handoff_contract.intent.value == intent


@pytest.mark.parametrize("during_correction", [False, True])
def test_i6_provider_failure_retains_existing_recovery(journey, during_correction):
    error = AgentExecutionError("provider unavailable", error_type="rate_limit")
    j = journey(([REJECTED] if during_correction else []) + [error])
    result = j.runtime.run(start_step="inspect_custom")
    assert len(j.manager.calls) == (2 if during_correction else 1)
    assert "agent_rate_limit" in result.final_status_code
    assert j.effects == ["prepare"]
    assert len(HumanTaskRecordStore(j.issue).tasks()) == 1
    assert not (j.iteration / "artifact.json").exists()
    if during_correction:
        budget = json.loads((j.iteration / "iteration.json").read_text())["artifact_correction"]
        assert budget["consumed"] == 1
        assert len(budget["rejections"]) == 1
        assert budget == j.manager.metadata[-1]["artifact_correction"]


def test_i8_valid_report_needs_no_repair(journey):
    j = journey([CORRECTED])
    j.runtime.run(start_step="inspect_custom")
    assert len(j.manager.calls) == 1
    assert j.effects == ["prepare", "after"]


def test_i8_plan_syntax_is_checked_before_effects(journey):
    j = journey([MISSING_PLAN_TODO, CORRECTED])
    j.runtime.run(start_step="inspect_custom")
    assert len(j.manager.calls) == 2
    assert j.effects == ["prepare", "after"]


def test_i8_checklist_failure_keeps_existing_recovery(journey):
    j = journey([CORRECTED], unchecked=True, mode="legacy")
    result = j.runtime.run(start_step="inspect_custom")
    assert "CHECKLIST_VALIDATION_FAILED" in result.final_status_code
    assert len(j.manager.calls) == 4
    assert (j.iteration / "checklist.md").read_bytes() == j.manager.checklists[0]


def test_i4_late_format_mutation_fails_closed_without_pipeline_replay(journey):
    j = journey([CORRECTED], mutate=lambda output: output.write_text(REJECTED))
    result = j.runtime.run(start_step="inspect_custom")
    assert not result.completed
    assert len(j.manager.calls) == 1
    assert j.effects == ["prepare", "after"]
    assert not (j.iteration / "artifact.json").exists()


@pytest.mark.parametrize("mode", ["baton", "legacy"])
def test_i4_invalid_baton_cannot_reset_format_budget(journey, mode):
    j = journey([REJECTED, (CORRECTED, "unknown_intent"), REJECTED], mode=mode)
    result = j.runtime.run(start_step="inspect_custom")
    assert not result.completed
    assert len(j.manager.calls) == 3
    assert j.manager.deliveries == 0
    assert "artifact_format" in result.final_status_code
    assert len(HumanTaskRecordStore(j.issue).tasks()) == 1
    state = BlackboardStore(j.issue).load_or_create("inspect_custom")
    assert not any(e.event_type == "step_completed" for e in state.events)


@pytest.mark.parametrize("failure", [OSError("unreadable output"), ValueError("authority conflict")])
def test_i6_non_format_failure_is_not_repaired(journey, failure):
    def fail_read(output):
        raise failure
    j = journey([CORRECTED], mutate=fail_read)
    result = j.runtime.run(start_step="inspect_custom")
    assert result.final_status_code == "INTERRUPTED:agent_error"
    assert len(j.manager.calls) == 1
    assert len(HumanTaskRecordStore(j.issue).tasks()) == 1


@pytest.mark.parametrize("change", ["checklist", "metadata", "session", "model"])
def test_i3_context_mutation_fails_closed(journey, change):
    def mutate(iteration, call):
        if call != 2:
            return
        if change == "checklist":
            (iteration / "checklist.md").write_text("[x] altered\n")
        elif change == "metadata":
            path = iteration / "iteration.json"
            data = json.loads(path.read_text())
            data["effective_checklist"] = {}
            path.write_text(json.dumps(data))
        elif change == "model":
            path = iteration / "iteration.json"
            data = json.loads(path.read_text())
            data["model"] = "different-model"
            path.write_text(json.dumps(data))
        else:
            j.manager.get_last_session_id = lambda: "another-session"
    j = journey([REJECTED, CORRECTED], provider_mutation=mutate)
    result = j.runtime.run(start_step="inspect_custom")
    assert not result.completed
    assert len(j.manager.calls) == 2
    assert j.effects == ["prepare"]
    assert not (j.iteration / "artifact.json").exists()


def test_i3_missing_exact_session_uses_existing_recovery(journey):
    j = journey([REJECTED, CORRECTED])
    j.manager.get_last_session_id = lambda: None
    result = j.runtime.run(start_step="inspect_custom")
    assert result.final_status_code == "INTERRUPTED:agent_error"
    assert len(j.manager.calls) == 1
    assert j.effects == ["prepare"]
    budget = json.loads((j.iteration / "iteration.json").read_text())["artifact_correction"]
    assert budget["consumed"] == 0
    assert len(budget["rejections"]) == 1
    assert budget["rejections"][0]["artifact"] == "evidence_bundle"
    assert not (j.iteration / "artifact.json").exists()
    assert len(HumanTaskRecordStore(j.issue).tasks()) == 1


@pytest.mark.parametrize("approval", ["not_required", "required"])
def test_i5_real_capability_dispatch_runs_once_or_waits_for_approval(journey, monkeypatch, tmp_path, approval):
    import cafe.core.capabilities as capabilities

    # Substitute only the immutable manifest I/O and the external adapter.
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    packaged = Path(capabilities.__file__).parents[1] / "data" / "capabilities" / "cafe.browser.open.yaml"
    manifest = yaml.safe_load(packaged.read_text())
    manifest["approval"] = approval
    (manifests / "browser.yaml").write_text(yaml.safe_dump(manifest))
    monkeypatch.setattr(capabilities, "default_capability_definition_dirs", lambda repo: [manifests])
    dispatches = []
    monkeypatch.setitem(capabilities.HOST_CAPABILITY_ADAPTERS, "open_current_pr",
        lambda **kwargs: (dispatches.append("open") or {"opened": True}, None))
    j = journey([REJECTED, REJECTED, CORRECTED], capability=True)
    result = j.runtime.run(start_step="inspect_custom")
    assert len(j.manager.calls) == 3
    assert j.effects == ["prepare", "after"]
    assert dispatches == (["open"] if approval == "not_required" else [])
    if approval == "required":
        assert not result.completed
        tasks = HumanTaskRecordStore(j.issue).tasks()
        assert len(tasks) == 1
        assert tasks[0].capability_approval is not None
        assert tasks[0].status.value == "pending"
        assert j.manager.deliveries == 0


@pytest.mark.parametrize("decision", ["retry", "retry_fresh_session"])
def test_i2_explicit_user_recovery_permits_one_submission_without_replenishing_budget(journey, decision):
    from cafe.ui.human_tasks import apply_human_task_payload

    submissions = [REJECTED, REJECTED, REJECTED, CORRECTED]
    j = journey(submissions)
    first = j.runtime.run(start_step="inspect_custom")
    assert not first.completed and len(j.manager.calls) == 3
    task = HumanTaskRecordStore(j.issue).tasks()[0]
    state = BlackboardStore(j.issue).load_or_create("inspect_custom")
    apply_human_task_payload(issue_dir=j.issue, playbook_data=j.playbook, blackboard=state,
        from_step="inspect_custom", trigger=task.trigger,
        raw_payload={"task": task.policy_id, "decision": decision, "human_task_id": task.id}, source="test")
    resumed = BlackboardWorkflowRuntime(issue_dir=j.issue, playbook=j.playbook, executor=j.executor.execute_step)
    resumed.run()
    assert len(j.manager.calls) == 4
    assert "evidence_bundle" in resumed.blackboard.artifacts
    data = json.loads((j.iteration / "iteration.json").read_text())
    assert data["artifact_correction"]["consumed"] == 2
    assert not any(t.status.value == "pending" for t in HumanTaskRecordStore(j.issue).tasks())



def test_i2_prior_provider_recovery_cannot_grant_a_later_automatic_submission(journey):
    from cafe.core.artifact_validation import ArtifactCorrectionExhausted
    from cafe.ui.human_tasks import apply_human_task_payload

    j = journey([AgentExecutionError("provider unavailable", error_type="rate_limit"), REJECTED])
    j.runtime.run(start_step="inspect_custom")
    task = HumanTaskRecordStore(j.issue).tasks()[0]
    state = BlackboardStore(j.issue).load_or_create("inspect_custom")
    apply_human_task_payload(issue_dir=j.issue, playbook_data=j.playbook, blackboard=state,
        from_step="inspect_custom", trigger=task.trigger,
        raw_payload={"task": task.policy_id, "decision": "retry", "human_task_id": task.id}, source="test")
    resumed = BlackboardWorkflowRuntime(issue_dir=j.issue, playbook=j.playbook, executor=j.executor.execute_step)
    resumed.run()
    assert len(j.manager.calls) == 4  # Failed execution, then initial report plus two corrections.
    with pytest.raises(ArtifactCorrectionExhausted):
        j.executor.execute_step("inspect_custom", j.playbook["steps"]["inspect_custom"], resumed.blackboard)
    assert len(j.manager.calls) == 4


@pytest.mark.parametrize("mode", ["baton", "legacy"])
@pytest.mark.parametrize("completed_checklist", [False, True])
def test_i4_i5_repaired_report_handoff_retry_does_not_replay_phase(journey, mode, completed_checklist):
    """BLK-001: handoff repair remains in the producer's bounded continuation."""
    j = journey([REJECTED, (CORRECTED, "unknown_intent"), CORRECTED], mode=mode,
                completed_checklist=completed_checklist)
    result = j.runtime.run(start_step="inspect_custom")
    assert result.completed
    assert len(j.manager.calls) == 3
    assert j.manager.deliveries == 1
    assert j.effects == ["prepare", "after"]
    assert not (j.issue / "inspect_custom" / "iteration_002").exists()
    _assert_correction_context_preserved(j.manager)
    for _, prompt, _, _ in j.manager.calls[1:]:
        assert "Do not repeat unrelated work" in prompt
    assert "unknown_intent" in j.manager.calls[-1][1]


@pytest.mark.parametrize("mode", ["baton", "legacy"])
@pytest.mark.parametrize("publication_mutation", [False, True])
def test_i5_late_invalid_report_never_reaches_publication_adapter(journey, monkeypatch, mode, publication_mutation):
    """BLK-002: a real publication capability cannot consume a late invalid report."""
    import cafe.core.capabilities as capabilities

    dispatches = []
    monkeypatch.setitem(capabilities.HOST_CAPABILITY_ADAPTERS, "open_current_pr",
        lambda **kwargs: (dispatches.append("open") or {"opened": True}, None))
    j = journey([REJECTED, CORRECTED], mode=mode, capability=True,
                mutate=lambda output: output.write_text(REJECTED), publication_mutation=publication_mutation)
    result = j.runtime.run(start_step="inspect_custom")
    assert not result.completed
    assert len(j.manager.calls) == 2
    assert j.effects == ["prepare", "after"] + (["publish-mutation"] if publication_mutation else [])
    assert dispatches == []
    assert j.manager.deliveries == 0
    assert not (j.iteration / "artifact.json").exists()
    assert len(HumanTaskRecordStore(j.issue).tasks()) == 1
