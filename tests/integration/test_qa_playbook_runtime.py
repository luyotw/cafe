"""Focused runtime journeys for declarative QA playbooks."""

from __future__ import annotations

from pathlib import Path

import pytest

from cafe.core.blackboard import (
    ArtifactEntry,
    ArtifactKind,
    BlackboardStore,
    HandoffIntent,
    HandoffOwner,
)
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.playbooks.loader import PlaybookLoader
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor

pytestmark = pytest.mark.usefixtures("cached_builtin_playbook_models")


def _finish_pr(issue_dir: Path) -> None:
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("pr")
    store.update_handoff_contract(
        state,
        from_step="pr",
        to_owner=HandoffOwner.DONE,
        to_step="done",
        intent=HandoffIntent.WORKFLOW_COMPLETE,
        status_code="confirmed",
        source="test.executor",
    )


def _runtime_playbook(name: str) -> dict:
    playbook = PlaybookLoader().load(name, strict=True)
    playbook["steps"]["pr"]["capability_requests"] = []
    playbook["steps"]["pr"]["behavior"] = {"completion": "status_code"}
    playbook["steps"]["pr"]["on"].pop("confirm_output", None)
    playbook["steps"]["pr"]["on"]["workflow_complete"] = "_done"
    # These journeys stop at the PR stub, before automatic delivery.
    playbook["steps"].pop("deliver", None)
    return playbook


@pytest.mark.parametrize("name", ["direct-qa", "standard-qa", "tdd-qa"])
def test_qa_happy_path_reaches_pr(tmp_path: Path, name: str) -> None:
    issue_dir = tmp_path / name
    calls: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == "pr":
            _finish_pr(issue_dir)
        return StepExecutionResult(response="confirmed", artifacts={}, status_code="confirmed")

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_runtime_playbook(name),
        executor=executor,
    ).run(start_step="review")

    assert result.completed is True
    assert calls == ["review", "qa", "pr"]


@pytest.mark.parametrize(
    ("unfinished_gate", "intent"),
    [
        (None, "await_agent"),
        (0, "await_agent"),
        (1, "await_agent"),
        (2, "await_agent"),
        (None, "manual_handoff"),
    ],
)
def test_qa_evidence_gates_control_success_and_preserve_correction(
    tmp_path: Path, monkeypatch, unfinished_gate: int | None, intent: str
) -> None:
    """Real QA loading and completion enforce each projected evidence obligation."""
    import json

    from cafe.core.workspace_artifact import build_workspace_artifact

    from tests.integration.test_checklist_overlay_workflow import (
        JourneyAgent,
        executor_fixture,
        git_evidence,
    )

    executor, _, state, _ = executor_fixture(
        tmp_path, monkeypatch, primary="cafe-qa", injections=()
    )
    executor.playbook = PlaybookLoader().load("direct-qa", strict=True)
    step = executor.playbook["steps"]["qa"]
    executor.phase_name = "qa"
    executor.phase_dir = executor.issue_dir / "qa"
    directory = executor._get_iteration_dir(1)
    directory.mkdir(parents=True)
    state.current_step = "qa"
    state.playbook_id = "direct-qa"
    store = BlackboardStore(executor.issue_dir)
    summary = executor.issue_dir / "development.md"
    summary.write_text("# Implementation\nThe confirmed change is ready for acceptance.\n")
    store.set_artifact(state, "code", str(summary))
    store.save(state)
    commit = git_evidence(tmp_path)
    workspace = build_workspace_artifact(
        repo=tmp_path, name="workspace", version=1, base_sha=commit, head_sha=commit
    )
    workspace_path = executor.issue_dir / "workspace.json"
    workspace_path.write_text(json.dumps(workspace.to_dict()))
    state.artifacts["workspace"] = ArtifactEntry(
        name="workspace",
        kind=ArtifactKind.WORKSPACE,
        version=1,
        updated_by="develop",
        path=str(workspace_path),
        base_sha=commit,
        head_sha=commit,
    )
    store.save(state)
    (tmp_path / ".cafe/phases.yaml").write_text(
        "qa:\n  name: Quinn\n  clis: [{cli: codex, model: test}]\n"
    )
    executor.role_agent_map = {"qa": "Quinn"}

    executor._generate_checklist(
        step_name="qa",
        skill_name="cafe-qa",
        agent_name="Quinn",
        step_def=step,
        blackboard_state=state,
        checklist_file=directory / "checklist.md",
        output_file=directory / "output.md",
        questions_xml_file=directory / "questions.xml",
    )
    content = (directory / "checklist.md").read_text()
    gates = content.split("## Evidence interpretation")[-1].split("## Regression coverage")[0]
    evidence_rows = [line for line in gates.splitlines() if "[ ]" in line]
    assert len(evidence_rows) == 3

    def finish(iteration):
        path = iteration / "checklist.md"
        completed = path.read_text().replace("[ ]", "[x]")
        if unfinished_gate is not None:
            row = evidence_rows[unfinished_gate]
            completed = completed.replace(row.replace("[ ]", "[x]"), row)
        path.write_text(completed)
        report = "# QA\n\n## Todo List\n"
        report += (
            "- [ ] `QA-001` — Source: `qa` — Work: repair observed reorder failure "
            "— Closure: the required operation succeeds through its public entry "
            "— Evidence: connected App/emulator reproduction\n"
            if intent == "manual_handoff"
            else "No actionable work.\n"
        )
        (iteration / "output.md").write_text(report)
        baton = {"version": 1, "intent": intent}
        if intent == "manual_handoff":
            baton.update(to_owner="agent", to_step="develop")
        (executor.issue_dir / "next_step.txt").write_text(json.dumps(baton))
        return "needs_changes" if intent == "manual_handoff" else "confirmed"

    executor.agent_manager = JourneyAgent(executor, [finish])
    result = executor.execute_step("qa", step, state)
    handoff = store.load_or_create("qa").handoff_contract
    if unfinished_gate is None:
        assert result.artifact_ready
        assert handoff.to_step == ("develop" if intent == "manual_handoff" else "pr")
        assert "qa_feedback" in result.artifacts
    else:
        assert not result.artifact_ready
        assert handoff is None or handoff.to_step != "pr"


@pytest.mark.parametrize("origin", ["review", "qa", "pr"])
def test_every_correction_repeats_develop_review_and_qa(tmp_path: Path, origin: str) -> None:
    issue_dir = tmp_path / f"correction-{origin}"
    calls: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == origin and calls.count(origin) == 1:
            return StepExecutionResult(
                response="needs_changes",
                artifacts={},
                status_code="needs_changes",
            )
        if step_name == "pr":
            _finish_pr(issue_dir)
        return StepExecutionResult(response="confirmed", artifacts={}, status_code="confirmed")

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_runtime_playbook("standard-qa"),
        executor=executor,
    ).run(start_step=origin)

    assert result.completed is True
    correction = calls.index("develop")
    assert calls[correction : correction + 4] == ["develop", "review", "qa", "pr"]


def test_blocked_qa_resumes_in_qa_before_pr(tmp_path: Path) -> None:
    issue_dir = tmp_path / "blocked-qa"
    calls: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == "qa" and calls.count("qa") == 1:
            return StepExecutionResult(
                response="need_clarification",
                artifacts={},
                status_code="need_clarification",
                auto_continue=True,
            )
        if step_name == "pr":
            _finish_pr(issue_dir)
        return StepExecutionResult(response="confirmed", artifacts={}, status_code="confirmed")

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_runtime_playbook("standard-qa"),
        executor=executor,
    ).run(start_step="qa")

    assert result.completed is True
    assert calls == ["qa", "qa", "pr"]


def test_simple_qa_failure_repeats_develop_and_qa_before_pr(tmp_path: Path) -> None:
    issue_dir = tmp_path / "simple-correction"
    calls: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == "qa" and calls.count("qa") == 1:
            return StepExecutionResult(
                response="needs_changes",
                artifacts={},
                status_code="needs_changes",
            )
        if step_name == "pr":
            _finish_pr(issue_dir)
        return StepExecutionResult(response="confirmed", artifacts={}, status_code="confirmed")

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_runtime_playbook("simple"),
        executor=executor,
    ).run(start_step="develop")

    assert result.completed is True
    assert calls == ["develop", "qa", "develop", "qa", "pr"]


def test_simple_no_change_correction_returns_to_qa_before_pr(tmp_path: Path) -> None:
    issue_dir = tmp_path / "simple-no-change-correction"
    calls: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == "qa" and calls.count("qa") == 1:
            return StepExecutionResult(
                response="needs_changes",
                artifacts={},
                status_code="needs_changes",
            )
        if step_name == "develop":
            return StepExecutionResult(
                response="no_changes_needed",
                artifacts={},
                status_code="no_changes_needed",
            )
        if step_name == "pr":
            _finish_pr(issue_dir)
        return StepExecutionResult(response="confirmed", artifacts={}, status_code="confirmed")

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_runtime_playbook("simple"),
        executor=executor,
    ).run(start_step="qa")

    assert result.completed is True
    assert calls == ["qa", "develop", "qa", "pr"]


@pytest.mark.parametrize("origin", ["qa", "pr"])
def test_subagent_qa_corrections_repeat_reviewed_development_and_acceptance(
    tmp_path: Path, origin: str
) -> None:
    issue_dir = tmp_path / f"subagent-correction-{origin}"
    calls: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == origin and calls.count(origin) == 1:
            return StepExecutionResult(
                response="needs_changes", artifacts={}, status_code="needs_changes"
            )
        if step_name == "pr":
            _finish_pr(issue_dir)
        return StepExecutionResult(response="confirmed", artifacts={}, status_code="confirmed")

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_runtime_playbook("subagent-flow-qa"),
        executor=executor,
    ).run(start_step=origin)

    assert result.completed is True
    correction = calls.index("develop")
    assert calls[correction : correction + 3] == ["develop", "qa", "pr"]


@pytest.mark.parametrize("sender", ["spec_plan", "qa"])
def test_subagent_qa_projects_only_the_current_correction_source(
    tmp_path: Path, sender: str
) -> None:
    store = BlackboardStore(tmp_path / "issue")
    state = store.load_or_create("develop")
    store.update_handoff_contract(
        state,
        from_step=sender,
        to_owner=HandoffOwner.AGENT,
        to_step="develop",
        intent=HandoffIntent.AWAIT_AGENT,
        source="test.current_sender",
    )
    artifacts = {}
    for name, owner, item_id, todo_source in (
        ("plan", "spec_plan", "PLAN-001", "plan"),
        ("qa_feedback", "qa", "QA-001", "qa"),
    ):
        report = tmp_path / f"{name}.md"
        report.write_text(
            "## Todo List\n"
            f"- [ ] `{item_id}` — Source: `{todo_source}` — Work: repair acceptance — "
            "Closure: requested journey passes — Evidence: targeted acceptance check\n",
            encoding="utf-8",
        )
        artifacts[name] = ArtifactEntry(
            name=name,
            kind=ArtifactKind.DOCUMENT,
            version=1,
            updated_by=owner,
            path=str(report),
        )

    resolved = GenericWorkflowStepExecutor._add_causal_todo_artifact(
        artifacts,
        state,
        playbook=PlaybookLoader().load("subagent-flow-qa", strict=True),
    )

    if sender == "qa":
        assert resolved["causal_todo"] is artifacts["qa_feedback"]
    else:
        assert "causal_todo" not in resolved
    assert resolved["plan"] is artifacts["plan"]
