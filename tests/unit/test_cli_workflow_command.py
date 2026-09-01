"""Tests for workflow CLI command."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.git import BranchHealth
from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.core.workflow_models import PlaybookRunResult, StepExecutionResult
from cafe.playbooks.loader import PlaybookLoader
from cafe.ui.cli import (
    _execute_single_step_alias,
    _find_external_resume_step,
    _handle_user_phase,
    app,
)
from cafe.ui.cli_shared import (
    _alignment_checkpoint_menu_choices,
    _build_workflow_step_executor,
    _load_issue_step_names,
    _resolve_issue_playbook_name,
    apply_alignment_decision_from_payload,
)
from cafe.ui.human_tasks import resolve_step_human_task
from cafe.utils.config import ConfigManager

runner = CliRunner()


@pytest.fixture(autouse=True)
def _configured_cli_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """Workflow command tests isolate routing from phase-config preflight."""
    monkeypatch.setattr("cafe.ui.cli._check_agent_clis_available", lambda *args, **kwargs: [])


def test_issue_step_resolution_uses_issue_yaml_before_blackboard_exists(
    tmp_path: Path, monkeypatch
) -> None:
    """UT-009: configured custom steps are valid before the runtime creates state."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "custom-issue"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook: release-flow\n", encoding="utf-8")
    playbook_dir = tmp_path / ".cafe" / "playbooks"
    playbook_dir.mkdir(parents=True)
    (playbook_dir / "release-flow.yaml").write_text(
        """
playbook:
  id: release-flow
steps:
  prepare:
    skill: cafe-develop
    role: developer
    on: {await_agent: deploy}
  deploy:
    skill: cafe-develop
    role: developer
    on: {await_agent: _done}
""".strip(),
        encoding="utf-8",
    )

    assert _resolve_issue_playbook_name("custom-issue") == "release-flow"
    assert _load_issue_step_names("custom-issue") == ["prepare", "deploy"]

    (issue_dir / "blackboard.json").write_text(
        json.dumps({"current_step": "prepare"}), encoding="utf-8"
    )
    assert _resolve_issue_playbook_name("custom-issue") == "release-flow"
    assert _load_issue_step_names("custom-issue") == ["prepare", "deploy"]


@pytest.mark.parametrize(
    ("filename", "contents"),
    [
        ("blackboard.json", "{not json"),
        ("issue.yaml", "playbook: [not valid"),
    ],
)
def test_issue_playbook_resolution_rejects_unreadable_persisted_metadata(
    tmp_path: Path, monkeypatch, filename: str, contents: str
) -> None:
    """UT-009: resume never replaces present broken metadata with ``default``."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "broken-issue"
    issue_dir.mkdir(parents=True)
    (issue_dir / filename).write_text(contents, encoding="utf-8")

    with pytest.raises(ValueError, match="unreadable workflow metadata"):
        _resolve_issue_playbook_name("broken-issue")


def _result(
    *,
    status_code: str,
    step_name: str,
    step_def: dict,
    artifacts: dict[str, str] | None = None,
    events: list[dict[str, str]] | None = None,
) -> StepExecutionResult:
    return StepExecutionResult(
        response=status_code,
        artifacts=(
            artifacts
            if artifacts is not None
            else {str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"}
        ),
        status_code=status_code,
        events=events or [],
    )


def _handoff_to_step(
    *,
    issue_dir: Path,
    state: object,
    from_step: str,
    to_step: str,
    status_code: str,
    intent: HandoffIntent | None = None,
) -> None:
    store = BlackboardStore(issue_dir)
    to_owner = HandoffOwner.AGENT if to_step not in {"user", "done"} else HandoffOwner(to_step)
    if intent is None:
        intent = (
            HandoffIntent.WORKFLOW_COMPLETE
            if to_owner == HandoffOwner.DONE
            else HandoffIntent.MANUAL_HANDOFF
            if to_owner == HandoffOwner.USER
            else HandoffIntent.AWAIT_AGENT
        )
    store.update_handoff_contract(
        state,
        from_step=from_step,
        to_owner=to_owner,
        to_step=to_step,
        intent=intent,
        status_code=status_code,
        source="test.executor",
    )


def _pause_with_iteration_limit_task(issue_dir: Path):
    playbook = PlaybookLoader().load("standard")
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("review", playbook_id="standard")
    store.set_current_step(blackboard, "user")
    store.update_handoff_contract(
        blackboard,
        from_step="review",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.MANUAL_HANDOFF,
        status_code="ITERATION_LIMIT_REACHED",
        source="test",
    )
    policy, binding = resolve_step_human_task(
        playbook_data=playbook,
        step_name="review",
        trigger="manual_handoff",
    )
    contract = blackboard.handoff_contract
    assert contract is not None
    task = HumanTaskRecordStore(issue_dir).materialize(
        workflow_id=blackboard.workflow_id,
        step="review",
        iteration=1,
        trigger="manual_handoff",
        policy_id=policy.id,
        prompt=policy.prompt,
        expected_result=policy.model_dump(mode="json"),
        continuations=binding.outcomes,
        assignee_type="user",
        handoff_key=":".join(
            (
                "user-handoff",
                blackboard.workflow_id,
                contract.from_step,
                contract.intent.value,
                contract.created_at,
            )
        ),
    )
    return store, task


def test_alignment_checkpoint_menu_is_chat_first_and_concise() -> None:
    choices = _alignment_checkpoint_menu_choices(
        "Roger",
        [
            "approve",
            "narrow_scope",
            "revise_spec",
            "revise_plan",
            "update_strategic_documents_first",
            "strategic_documents_updated",
            "manual_pause",
            "reject_or_defer",
        ],
    )

    assert choices == [
        {"name": "Chat with Roger about alignment", "value": "chat_alignment"},
        {"name": "Approve and continue", "value": "approve"},
        {"name": "Strategic documents updated", "value": "strategic_documents_updated"},
        {"name": "Pause for manual decision", "value": "manual_pause"},
    ]


def test_alignment_checkpoint_menu_hides_disallowed_direct_decisions() -> None:
    choices = _alignment_checkpoint_menu_choices("Roger", ["approve"])

    assert choices == [
        {"name": "Chat with Roger about alignment", "value": "chat_alignment"},
        {"name": "Approve and continue", "value": "approve"},
    ]


def test_single_step_alias_updates_workflow_pointer_to_requested_step(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-210"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "standard",
                "current_step": "pr",
                "artifacts": {},
                "events": [],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )
    config_manager = ConfigManager(".cafe")
    config_manager._config = config_manager.get_default_config()

    class FakeExecutor:
        def __init__(self) -> None:
            self.agent_manager = MagicMock()

        def execute_step(
            self, step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            return _result(
                status_code="no_changes_needed",
                step_name=step_name,
                step_def=step_def,
                artifacts={},
            )

    with patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()):
        result = _execute_single_step_alias(
            issue_name="issue-210",
            step_name="develop",
            config_manager=config_manager,
        )

    assert result["status_code"] == "no_changes_needed"
    blackboard_data = json.loads((issue_dir / "blackboard.json").read_text(encoding="utf-8"))
    assert blackboard_data["current_step"] == "review"


def test_workflow_command_runs_dry_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".cafe" / "issues").mkdir(parents=True, exist_ok=True)

    with patch("cafe.ui.cli.GitOperations") as mock_git_cls:
        git = MagicMock()
        git.get_current_branch.return_value = "issue-100"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "standard", "--dry-run"])
        assert result.exit_code == 0
        assert "Workflow context" in result.stdout
        assert "playbook=standard step=spec" in result.stdout
        assert "Ownership plan (read-only)" in result.stdout
        blackboard_file = tmp_path / ".cafe" / "issues" / "issue-100" / "blackboard.json"
        assert not blackboard_file.exists()


def test_workflow_rejects_invalid_issue_playbook_override_before_execution(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-override"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text(
        "playbook: standard\n"
        "playbook_overrides:\n"
        "  steps:\n"
        "    review:\n"
        "      skill: cafe-develop\n",
        encoding="utf-8",
    )

    with patch("cafe.ui.cli.GitOperations") as mock_git_cls:
        git = MagicMock()
        git.get_current_branch.return_value = "issue-override"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "standard", "--dry-run"])

    assert result.exit_code == 1
    assert "playbook_overrides.steps.review supports only" in result.stdout
    assert "max_attempts_per_cycle; unsupported field(s): skill" in result.stdout
    assert not (issue_dir / "blackboard.json").exists()


def test_workflow_dry_mode_completes_declared_custom_publish_step(
    tmp_path: Path, monkeypatch
) -> None:
    """IT-001: dry execution recognizes publish behavior, not a step name."""
    monkeypatch.chdir(tmp_path)
    playbook_dir = tmp_path / ".cafe" / "playbooks"
    playbook_dir.mkdir(parents=True)
    (playbook_dir / "custom.yaml").write_text(
        """
playbook:
  id: custom
steps:
  release:
    skill: cafe-pr
    role: developer
    capability_requests: [cafe.pr.publish]
    behavior:
      completion: baton
      publish_confirmation: true
    on:
      workflow_complete: _done
""".strip(),
        encoding="utf-8",
    )

    with patch("cafe.ui.cli.GitOperations") as mock_git_cls:
        git = MagicMock()
        git.get_current_branch.return_value = "issue-custom-publish"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "custom", "--dry-run"])

    assert result.exit_code == 0, (result.stdout, result.exception)
    assert "Ownership plan (read-only)" in result.stdout
    assert not (tmp_path / ".cafe" / "issues" / "issue-custom-publish" / "blackboard.json").exists()


def test_workflow_command_runs_execute_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    executed_steps: list[str] = []

    class FakeExecutor:
        def execute_step(
            self, step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            executed_steps.append(step_name)
            if step_name == "pr":
                _handoff_to_step(
                    issue_dir=tmp_path / ".cafe" / "issues" / "issue-200",
                    state=blackboard_state,
                    from_step="pr",
                    to_step="user",
                    status_code="confirmed",
                    intent=HandoffIntent.MANUAL_HANDOFF,
                )
            return _result(status_code="confirmed", step_name=step_name, step_def=step_def)

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch(
            "cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()
        ) as mock_builder,
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-200"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "standard", "--execute"])
        assert result.exit_code == 0
        assert "Workflow context" in result.stdout
        assert "playbook=standard step=spec" in result.stdout
        assert "Executing step=spec iteration=001" in result.stdout
        assert "Executing step=plan iteration=001" in result.stdout
        assert "Executing step=develop iteration=001" in result.stdout
        assert "Executing step=review iteration=001" in result.stdout
        assert "Executing step=pr iteration=001" in result.stdout
        assert "Workflow is waiting for user input" in result.stdout
        assert mock_builder.called
        assert executed_steps == ["spec", "plan", "develop", "review", "pr"]


def test_workflow_command_passes_initial_user_input_to_spec_step(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)

    class FakeExecutor:
        def execute_step(
            self, step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            return _result(status_code="confirmed", step_name=step_name, step_def=step_def)

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch(
            "cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()
        ) as mock_builder,
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-201"
        mock_git_cls.return_value = git

        result = runner.invoke(
            app,
            [
                "workflow",
                "--playbook", "standard",
                "--execute",
                "--user-input",
                "As a user, I want a smoke-test workflow.",
            ],
        )

    assert result.exit_code == 0
    assert mock_builder.called
    assert mock_builder.call_args.kwargs["step_user_inputs"] == {
        "spec": "As a user, I want a smoke-test workflow."
    }


def test_workflow_command_passes_initial_user_input_to_question_step(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)

    class FakeExecutor:
        def execute_step(
            self, step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            return _result(status_code="confirmed", step_name=step_name, step_def=step_def)

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch(
            "cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()
        ) as mock_builder,
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-research-input"
        mock_git_cls.return_value = git

        result = runner.invoke(
            app,
            [
                "workflow",
                "--playbook",
                "research",
                "--execute",
                "--user-input",
                "What is the market size for EV batteries?",
            ],
        )

    assert result.exit_code == 0
    assert mock_builder.call_args.kwargs["step_user_inputs"] == {
        "question": "What is the market size for EV batteries?"
    }


def test_workflow_command_passes_initial_user_input_to_brief_step(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)

    class FakeExecutor:
        def execute_step(
            self, step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            return _result(status_code="confirmed", step_name=step_name, step_def=step_def)

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch(
            "cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()
        ) as mock_builder,
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-editorial-input"
        mock_git_cls.return_value = git

        result = runner.invoke(
            app,
            [
                "workflow",
                "--playbook",
                "editorial",
                "--execute",
                "--user-input",
                "Write a blog post about playbook-driven workflows.",
            ],
        )

    assert result.exit_code == 0
    assert mock_builder.call_args.kwargs["step_user_inputs"] == {
        "brief": "Write a blog post about playbook-driven workflows."
    }


def test_workflow_command_resume_user_input_targets_handoff_from_step(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-resume-plan"
    issue_dir.mkdir(parents=True, exist_ok=True)
    questions_dir = issue_dir / "plan" / "iteration_001"
    questions_dir.mkdir(parents=True)
    (questions_dir / "questions.xml").write_text(
        """<questions>
  <question id="scope"><title>Scope?</title><options><option>Include CSV export</option></options></question>
</questions>""",
        encoding="utf-8",
    )
    (questions_dir / "iteration.json").write_text(
        json.dumps({"iteration": 1, "end_time": "done"}), encoding="utf-8"
    )
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("user", playbook_id="standard")
    store.set_current_step(blackboard, "user")
    store.update_handoff_contract(
        blackboard,
        from_step="plan",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.NEED_CLARIFICATION,
        status_code="need_clarification",
        source="test",
    )

    class FakeExecutor:
        def execute_step(
            self, step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            return _result(status_code="confirmed", step_name=step_name, step_def=step_def)

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch(
            "cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()
        ) as mock_builder,
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-resume-plan"
        mock_git_cls.return_value = git

        result = runner.invoke(
            app,
            [
                "workflow",
                "--playbook", "standard",
                "--execute",
                "--single-step",
                "--user-input",
                '{"task":"clarification-answers","answers":{"scope":"include CSV export in scope"}}',
            ],
        )

    assert result.exit_code == 0
    assert mock_builder.call_args.kwargs["step_user_inputs"] is None
    resume_input = issue_dir / "plan" / "iteration_002" / "user_input.md"
    assert resume_input.read_text(encoding="utf-8") == "scope: include CSV export in scope"
    reloaded = store.load_or_create("spec", playbook_id="standard")
    assert (
        "completed human task clarification-answers for plan"
        in (reloaded.handoff_summary or "").lower()
    )
    assert reloaded.current_step == "develop"


def test_workflow_command_routes_manual_handoff_payload_through_durable_task(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-durable-manual"
    store, task = _pause_with_iteration_limit_task(issue_dir)
    executed_steps: list[str] = []

    class FakeExecutor:
        def execute_step(
            self, step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            executed_steps.append(step_name)
            return _result(status_code="confirmed", step_name=step_name, step_def=step_def)

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-durable-manual"
        mock_git_cls.return_value = git

        result = runner.invoke(
            app,
            [
                "workflow",
                "--playbook",
                "standard",
                "--execute",
                "--single-step",
                "--user-input",
                json.dumps(
                    {
                        "task": "iteration-limit",
                        "decision": "resume",
                        "human_task_id": task.id,
                    }
                ),
            ],
        )

    records = HumanTaskRecordStore(issue_dir)
    assert result.exit_code == 0, (result.stdout, result.exception)
    assert executed_steps == ["review"]
    assert records.get_task(task.id).status is HumanTaskStatus.COMPLETED
    assert records.get_wait_state(task.id).released_at is not None
    assert len(records.results()) == 1
    assert not (issue_dir / "review" / "iteration_001" / "user_input.md").exists()
    assert any(
        event.event_type == "human_task_completed"
        for event in store.load_or_create("review", playbook_id="standard").events
    )


def test_workflow_command_rejects_completed_durable_task_from_later_handoff(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-durable-replay"
    store, task = _pause_with_iteration_limit_task(issue_dir)
    executed_steps: list[str] = []

    class FakeExecutor:
        def execute_step(
            self, step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            executed_steps.append(step_name)
            return _result(status_code="confirmed", step_name=step_name, step_def=step_def)

    payload = json.dumps(
        {
            "task": "iteration-limit",
            "decision": "resume",
            "human_task_id": task.id,
        }
    )
    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-durable-replay"
        mock_git_cls.return_value = git
        completed = runner.invoke(
            app,
            [
                "workflow",
                "--playbook",
                "standard",
                "--execute",
                "--single-step",
                "--user-input",
                payload,
            ],
        )
        assert completed.exit_code == 0, (completed.stdout, completed.exception)
        assert executed_steps == ["review"]

        blackboard = store.load_or_create("plan", playbook_id="standard")
        store.set_current_step(blackboard, "user")
        store.update_handoff_contract(
            blackboard,
            from_step="plan",
            to_owner=HandoffOwner.USER,
            to_step="user",
            intent=HandoffIntent.ALIGNMENT_CHECKPOINT,
            status_code="alignment_checkpoint",
            source="test",
        )
        executed_steps.clear()
        replayed = runner.invoke(
            app,
            [
                "workflow",
                "--playbook",
                "standard",
                "--execute",
                "--single-step",
                "--user-input",
                payload,
            ],
        )

    reloaded = store.load_or_create("plan", playbook_id="standard")
    assert replayed.exit_code == 0, (replayed.stdout, replayed.exception)
    assert executed_steps == []
    assert reloaded.current_step == "user"
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.intent is HandoffIntent.ALIGNMENT_CHECKPOINT
    assert reloaded.handoff_contract.from_step == "plan"
    assert any(
        event.event_type == "human_task_rejected"
        and event.data.get("task_id") == task.id
        for event in reloaded.events
    )


def test_workflow_command_rejects_unknown_durable_task_without_generic_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-unknown-durable"
    store, task = _pause_with_iteration_limit_task(issue_dir)

    with patch("cafe.ui.cli.GitOperations") as mock_git_cls:
        git = MagicMock()
        git.get_current_branch.return_value = "issue-unknown-durable"
        mock_git_cls.return_value = git
        result = runner.invoke(
            app,
            [
                "workflow",
                "--playbook",
                "standard",
                "--execute",
                "--single-step",
                "--user-input",
                json.dumps(
                    {
                        "task": "iteration-limit",
                        "decision": "resume",
                        "human_task_id": "unknown-task-id",
                    }
                ),
            ],
        )

    records = HumanTaskRecordStore(issue_dir)
    assert result.exit_code == 0
    assert "Unknown durable human task" in result.stdout
    assert records.get_task(task.id).status is HumanTaskStatus.PENDING
    assert records.get_wait_state(task.id).released_at is None
    assert store.load_or_create("review", playbook_id="standard").current_step == "user"
    assert not (issue_dir / "review" / "iteration_001" / "user_input.md").exists()


def test_workflow_command_requires_interrupt_task_before_retrying_agent_once(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-durable-agent-retry"
    _store, task = _pause_with_iteration_limit_task(issue_dir)

    class FlakyExecutor:
        calls = 0

        def execute_step(
            self, step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("agent failed after task completion")
            return _result(status_code="confirmed", step_name=step_name, step_def=step_def)

    executor = FlakyExecutor()
    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=executor),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-durable-agent-retry"
        mock_git_cls.return_value = git
        first = runner.invoke(
            app,
            [
                "workflow",
                "--playbook",
                "standard",
                "--execute",
                "--single-step",
                "--user-input",
                json.dumps(
                    {
                        "task": "iteration-limit",
                        "decision": "resume",
                        "human_task_id": task.id,
                    }
                ),
            ],
        )
        records = HumanTaskRecordStore(issue_dir)
        interrupted_task = next(
            record
            for record in records.tasks()
            if record.status is HumanTaskStatus.PENDING
        )
        retry = runner.invoke(
            app,
            [
                "workflow",
                "--playbook",
                "standard",
                "--execute",
                "--single-step",
                "--user-input",
                json.dumps(
                    {
                        "task": interrupted_task.policy_id,
                        "decision": "retry",
                        "human_task_id": interrupted_task.id,
                    }
                ),
            ],
        )

    records = HumanTaskRecordStore(issue_dir)
    assert first.exit_code == 0
    assert "Workflow interrupted" in first.stdout
    assert retry.exit_code == 0, (retry.stdout, retry.exception)
    assert executor.calls == 2
    assert records.get_task(task.id).status is HumanTaskStatus.COMPLETED
    assert records.get_wait_state(task.id).released_at is not None
    assert records.get_task(interrupted_task.id).status is HumanTaskStatus.COMPLETED
    assert records.get_wait_state(interrupted_task.id).released_at is not None
    assert len(records.results()) == 2


def test_user_phase_alignment_checkpoint_approve_resumes_step(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-align-user"
    request_dir = issue_dir / "develop" / "iteration_001"
    request_dir.mkdir(parents=True, exist_ok=True)
    (request_dir / "alignment_request.json").write_text(
        json.dumps(
            {
                "fingerprint": "fp-1",
                "from_step": "develop",
                "recommended_resume_target": "develop",
                "strategic_document_update_requirements": [],
                "allowed_decisions": ["approve"],
            }
        ),
        encoding="utf-8",
    )
    playbook_data = {
        "playbook": {"id": "default"},
        "steps": {
            "develop": {"role": "developer", "on": {"await_agent": "review"}},
            "review": {"role": "reviewer", "on": {}},
        },
    }
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("user", playbook_id="standard")
    store.set_current_step(blackboard, "user")
    store.update_handoff_contract(
        blackboard,
        from_step="develop",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.ALIGNMENT_CHECKPOINT,
        status_code="alignment_checkpoint",
        source="test",
    )

    def fake_prompt_list(message: str, choices: list[dict[str, str]], default: str | None = None):
        assert message == "How should this alignment checkpoint continue?"
        assert choices[0]["value"] == "chat_alignment"
        assert default == "chat_alignment"
        return "approve"

    with patch("cafe.ui.inquirer_prompts.prompt_list", side_effect=fake_prompt_list):
        result = _handle_user_phase(
            issue_name="issue-align-user",
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            blackboard=blackboard,
        )

    assert result == "develop"
    reloaded = store.load_or_create("develop", playbook_id="standard")
    assert reloaded.current_step == "develop"
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.intent == HandoffIntent.AWAIT_AGENT


def test_user_phase_alignment_checkpoint_chat_decision_uses_host_apply(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-align-chat"
    request_dir = issue_dir / "spec" / "iteration_001"
    request_dir.mkdir(parents=True, exist_ok=True)
    request_file = request_dir / "alignment_request.json"
    request_file.write_text(
        json.dumps(
            {
                "fingerprint": "fp-chat",
                "from_step": "spec",
                "recommended_resume_target": "spec",
                "strategic_document_update_requirements": [],
                "allowed_decisions": ["narrow_scope"],
                "affected_documents": [
                    {
                        "category": "roadmap",
                        "path": "docs/roadmap.md",
                        "status": "exists",
                        "exists": True,
                    },
                    {
                        "category": "positioning",
                        "path": "docs/positioning.md",
                        "status": "missing",
                        "exists": False,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    playbook_data = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {"role": "pm", "chat_role": "pm", "on": {"await_agent": "plan"}},
            "plan": {"role": "developer", "on": {}},
        },
    }
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("user", playbook_id="standard")
    store.set_current_step(blackboard, "user")
    store.update_handoff_contract(
        blackboard,
        from_step="spec",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.ALIGNMENT_CHECKPOINT,
        status_code="alignment_checkpoint",
        source="test",
    )

    def fake_chat(role: str, issue_name: str, **kwargs):
        assert role == "pm"
        assert issue_name == "issue-align-chat"
        assert kwargs["chat_mode"] == "alignment"
        initial_prompt = kwargs["initial_prompt"]
        assert "alignment checkpoint chat" in initial_prompt
        assert str(request_file) in initial_prompt
        assert "Decision output file:" in initial_prompt
        assert "narrow_scope" in initial_prompt
        assert "Existing strategic docs:" in initial_prompt
        assert "roadmap:exists (docs/roadmap.md)" in initial_prompt
        assert "Missing/unconfigured strategic categories:" in initial_prompt
        assert "positioning:missing (docs/positioning.md)" in initial_prompt
        assert "Chat-mode decision mapping:" in initial_prompt
        assert "Option 2 starts strategic document alignment" in initial_prompt
        assert "does not by itself approve document content" in initial_prompt
        assert "strategic_documents_updated" in initial_prompt
        assert "user_confirmed" in initial_prompt
        assert "user_confirmation" in initial_prompt
        assert "Write update_strategic_documents_first only" in initial_prompt
        assert "Do not edit the blackboard" in initial_prompt
        extra_env = kwargs["extra_env"]
        assert extra_env["CAFE_ALIGNMENT_REQUEST_FILE"] == str(request_file)
        decision_file = Path(extra_env["CAFE_ALIGNMENT_DECISION_FILE"])
        decision_file.write_text(
            json.dumps(
                {
                    "decision": "narrow_scope",
                    "correction": "Keep this issue limited to capability request UX.",
                }
            ),
            encoding="utf-8",
        )
        return 0

    with (
        patch("cafe.ui.inquirer_prompts.prompt_list", return_value="chat_alignment"),
        patch("cafe.ui.cli.launch_chat_session", side_effect=fake_chat),
    ):
        result = _handle_user_phase(
            issue_name="issue-align-chat",
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            blackboard=blackboard,
        )

    assert result == "spec"
    assert (request_dir / "user_input.md").read_text(encoding="utf-8") == (
        "Keep this issue limited to capability request UX."
    )
    reloaded = store.load_or_create("spec", playbook_id="standard")
    assert reloaded.current_step == "spec"
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.intent == HandoffIntent.AWAIT_AGENT


def test_user_phase_alignment_checkpoint_rejects_chat_decision_outside_allowed_choices(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-align-chat-blocked"
    request_dir = issue_dir / "spec" / "iteration_001"
    request_dir.mkdir(parents=True, exist_ok=True)
    (request_dir / "alignment_request.json").write_text(
        json.dumps(
            {
                "fingerprint": "fp-chat-blocked",
                "from_step": "spec",
                "recommended_resume_target": "spec",
                "strategic_document_update_requirements": [],
                "allowed_decisions": ["approve"],
            }
        ),
        encoding="utf-8",
    )
    playbook_data = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {"role": "pm", "chat_role": "pm", "on": {"await_agent": "plan"}},
            "plan": {"role": "developer", "on": {}},
        },
    }
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("user", playbook_id="standard")
    store.set_current_step(blackboard, "user")
    store.update_handoff_contract(
        blackboard,
        from_step="spec",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.ALIGNMENT_CHECKPOINT,
        status_code="alignment_checkpoint",
        source="test",
    )

    def fake_chat(_role: str, _issue_name: str, **kwargs):
        Path(kwargs["extra_env"]["CAFE_ALIGNMENT_DECISION_FILE"]).write_text(
            json.dumps({"decision": "narrow_scope", "correction": "Try to narrow anyway."}),
            encoding="utf-8",
        )
        return 0

    with (
        patch("cafe.ui.inquirer_prompts.prompt_list", return_value="chat_alignment"),
        patch("cafe.ui.cli.launch_chat_session", side_effect=fake_chat),
    ):
        result = _handle_user_phase(
            issue_name="issue-align-chat-blocked",
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            blackboard=blackboard,
        )

    assert result is None
    reloaded = store.load_or_create("user", playbook_id="standard")
    assert reloaded.current_step == "user"
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.intent == HandoffIntent.ALIGNMENT_CHECKPOINT
    assert not (request_dir / "user_input.md").exists()


def test_user_phase_alignment_checkpoint_rejects_unconfirmed_chat_strategic_docs(
    tmp_path: Path,
) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True)
    (docs_dir / "positioning.md").write_text(
        "# Positioning\n\nDraft positioning.\n", encoding="utf-8"
    )
    cafe_dir = tmp_path / ".cafe"
    cafe_dir.mkdir(parents=True, exist_ok=True)
    (cafe_dir / "strategic_context.yaml").write_text(
        "version: 1\n"
        "documents:\n"
        "  positioning:\n"
        "    path: docs/positioning.md\n"
        "    status: exists\n",
        encoding="utf-8",
    )
    issue_dir = cafe_dir / "issues" / "issue-align-unconfirmed-docs"
    request_dir = issue_dir / "spec" / "iteration_001"
    request_dir.mkdir(parents=True, exist_ok=True)
    (request_dir / "alignment_request.json").write_text(
        json.dumps(
            {
                "fingerprint": "fp-unconfirmed-docs",
                "from_step": "spec",
                "recommended_resume_target": "spec",
                "strategic_document_update_requirements": [],
                "affected_documents": [
                    {
                        "category": "positioning",
                        "path": "docs/positioning.md",
                        "status": "missing",
                        "sha256": None,
                        "exists": False,
                    }
                ],
                "allowed_decisions": ["strategic_documents_updated"],
            }
        ),
        encoding="utf-8",
    )
    playbook_data = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {"role": "pm", "chat_role": "pm", "on": {"await_agent": "plan"}},
            "plan": {"role": "developer", "on": {}},
        },
    }
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("user", playbook_id="standard")
    store.set_current_step(blackboard, "user")
    store.update_handoff_contract(
        blackboard,
        from_step="spec",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.ALIGNMENT_CHECKPOINT,
        status_code="alignment_checkpoint",
        source="test",
    )

    def fake_chat(_role: str, _issue_name: str, **kwargs):
        Path(kwargs["extra_env"]["CAFE_ALIGNMENT_DECISION_FILE"]).write_text(
            json.dumps(
                {
                    "decision": "strategic_documents_updated",
                    "reason": "Created positioning from existing roadmap context.",
                }
            ),
            encoding="utf-8",
        )
        return 0

    with (
        patch("cafe.ui.inquirer_prompts.prompt_list", return_value="chat_alignment"),
        patch("cafe.ui.cli.launch_chat_session", side_effect=fake_chat),
    ):
        result = _handle_user_phase(
            issue_name="issue-align-unconfirmed-docs",
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            blackboard=blackboard,
        )

    assert result is None
    reloaded = store.load_or_create("user", playbook_id="standard")
    assert reloaded.current_step == "user"
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.intent == HandoffIntent.ALIGNMENT_CHECKPOINT
    assert any(
        event.event_type == "alignment_decision_blocked"
        and event.data.get("reason") == "missing_user_confirmation"
        for event in reloaded.events
    )


def test_user_phase_alignment_checkpoint_accepts_confirmed_chat_strategic_docs(
    tmp_path: Path,
) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True)
    (docs_dir / "positioning.md").write_text(
        "# Positioning\n\nConfirmed positioning.\n",
        encoding="utf-8",
    )
    cafe_dir = tmp_path / ".cafe"
    cafe_dir.mkdir(parents=True, exist_ok=True)
    (cafe_dir / "strategic_context.yaml").write_text(
        "version: 1\n"
        "documents:\n"
        "  positioning:\n"
        "    path: docs/positioning.md\n"
        "    status: exists\n",
        encoding="utf-8",
    )
    issue_dir = cafe_dir / "issues" / "issue-align-confirmed-docs"
    request_dir = issue_dir / "spec" / "iteration_001"
    request_dir.mkdir(parents=True, exist_ok=True)
    (request_dir / "alignment_request.json").write_text(
        json.dumps(
            {
                "fingerprint": "fp-confirmed-docs",
                "from_step": "spec",
                "recommended_resume_target": "spec",
                "strategic_document_update_requirements": [],
                "affected_documents": [
                    {
                        "category": "positioning",
                        "path": "docs/positioning.md",
                        "status": "missing",
                        "sha256": None,
                        "exists": False,
                    }
                ],
                "allowed_decisions": ["strategic_documents_updated"],
            }
        ),
        encoding="utf-8",
    )
    playbook_data = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {"role": "pm", "chat_role": "pm", "on": {"await_agent": "plan"}},
            "plan": {"role": "developer", "on": {}},
        },
    }
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("user", playbook_id="standard")
    store.set_current_step(blackboard, "user")
    store.update_handoff_contract(
        blackboard,
        from_step="spec",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.ALIGNMENT_CHECKPOINT,
        status_code="alignment_checkpoint",
        source="test",
    )

    def fake_chat(_role: str, _issue_name: str, **kwargs):
        Path(kwargs["extra_env"]["CAFE_ALIGNMENT_DECISION_FILE"]).write_text(
            json.dumps(
                {
                    "decision": "strategic_documents_updated",
                    "reason": "User approved the final positioning document.",
                    "user_confirmed": True,
                    "user_confirmation": "User confirmed the final positioning draft after review.",
                }
            ),
            encoding="utf-8",
        )
        return 0

    with (
        patch("cafe.ui.inquirer_prompts.prompt_list", return_value="chat_alignment"),
        patch("cafe.ui.cli.launch_chat_session", side_effect=fake_chat),
    ):
        result = _handle_user_phase(
            issue_name="issue-align-confirmed-docs",
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            blackboard=blackboard,
        )

    assert result == "spec"
    reloaded = store.load_or_create("spec", playbook_id="standard")
    assert reloaded.current_step == "spec"
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.intent == HandoffIntent.AWAIT_AGENT


def test_alignment_payload_rejects_unconfirmed_strategic_documents_updated(
    tmp_path: Path,
) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True)
    (docs_dir / "positioning.md").write_text(
        "# Positioning\n\nDraft positioning.\n",
        encoding="utf-8",
    )
    cafe_dir = tmp_path / ".cafe"
    cafe_dir.mkdir(parents=True, exist_ok=True)
    (cafe_dir / "strategic_context.yaml").write_text(
        "version: 1\n"
        "documents:\n"
        "  positioning:\n"
        "    path: docs/positioning.md\n"
        "    status: exists\n",
        encoding="utf-8",
    )
    issue_dir = cafe_dir / "issues" / "issue-align-payload-unconfirmed"
    request_dir = issue_dir / "spec" / "iteration_001"
    request_dir.mkdir(parents=True, exist_ok=True)
    (request_dir / "alignment_request.json").write_text(
        json.dumps(
            {
                "fingerprint": "fp-payload-unconfirmed",
                "from_step": "spec",
                "recommended_resume_target": "spec",
                "strategic_document_update_requirements": [],
                "affected_documents": [
                    {
                        "category": "positioning",
                        "path": "docs/positioning.md",
                        "status": "missing",
                        "sha256": None,
                        "exists": False,
                    }
                ],
                "allowed_decisions": ["strategic_documents_updated"],
            }
        ),
        encoding="utf-8",
    )
    playbook_data = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {"role": "pm", "on": {"await_agent": "plan"}},
            "plan": {"role": "developer", "on": {}},
        },
    }
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("user", playbook_id="standard")
    store.set_current_step(blackboard, "user")
    store.update_handoff_contract(
        blackboard,
        from_step="spec",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.ALIGNMENT_CHECKPOINT,
        status_code="alignment_checkpoint",
        source="test",
    )

    result = apply_alignment_decision_from_payload(
        issue_dir=issue_dir,
        playbook_data=playbook_data,
        blackboard=blackboard,
        payload={"decision": "docs_updated"},
    )

    assert result is None
    reloaded = store.load_or_create("user", playbook_id="standard")
    assert reloaded.current_step == "user"
    assert any(
        event.event_type == "alignment_decision_blocked"
        and event.data.get("reason") == "missing_user_confirmation"
        for event in reloaded.events
    )


def test_alignment_payload_accepts_confirmed_strategic_documents_updated(
    tmp_path: Path,
) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True)
    (docs_dir / "positioning.md").write_text(
        "# Positioning\n\nConfirmed positioning.\n",
        encoding="utf-8",
    )
    cafe_dir = tmp_path / ".cafe"
    cafe_dir.mkdir(parents=True, exist_ok=True)
    (cafe_dir / "strategic_context.yaml").write_text(
        "version: 1\n"
        "documents:\n"
        "  positioning:\n"
        "    path: docs/positioning.md\n"
        "    status: exists\n",
        encoding="utf-8",
    )
    issue_dir = cafe_dir / "issues" / "issue-align-payload-confirmed"
    request_dir = issue_dir / "spec" / "iteration_001"
    request_dir.mkdir(parents=True, exist_ok=True)
    (request_dir / "alignment_request.json").write_text(
        json.dumps(
            {
                "fingerprint": "fp-payload-confirmed",
                "from_step": "spec",
                "recommended_resume_target": "spec",
                "strategic_document_update_requirements": [],
                "affected_documents": [
                    {
                        "category": "positioning",
                        "path": "docs/positioning.md",
                        "status": "missing",
                        "sha256": None,
                        "exists": False,
                    }
                ],
                "allowed_decisions": ["strategic_documents_updated"],
            }
        ),
        encoding="utf-8",
    )
    playbook_data = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {"role": "pm", "on": {"await_agent": "plan"}},
            "plan": {"role": "developer", "on": {}},
        },
    }
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("user", playbook_id="standard")
    store.set_current_step(blackboard, "user")
    store.update_handoff_contract(
        blackboard,
        from_step="spec",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.ALIGNMENT_CHECKPOINT,
        status_code="alignment_checkpoint",
        source="test",
    )

    result = apply_alignment_decision_from_payload(
        issue_dir=issue_dir,
        playbook_data=playbook_data,
        blackboard=blackboard,
        payload={
            "decision": "strategic_documents_updated",
            "user_confirmed": True,
            "user_confirmation": "User confirmed the final strategic document.",
        },
    )

    assert result == "spec"
    reloaded = store.load_or_create("spec", playbook_id="standard")
    assert reloaded.current_step == "spec"
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.intent == HandoffIntent.AWAIT_AGENT


def test_user_phase_alignment_checkpoint_accepts_updated_strategic_document(tmp_path: Path) -> None:
    roadmap = tmp_path / "ROADMAP.md"
    roadmap.write_text("Updated roadmap direction\n", encoding="utf-8")
    cafe_dir = tmp_path / ".cafe"
    cafe_dir.mkdir(parents=True, exist_ok=True)
    (cafe_dir / "strategic_context.yaml").write_text(
        "version: 1\n" "documents:\n" "  roadmap:\n" "    path: ROADMAP.md\n",
        encoding="utf-8",
    )
    issue_dir = cafe_dir / "issues" / "codex" / "issue-align-docs"
    request_dir = issue_dir / "develop" / "iteration_001"
    request_dir.mkdir(parents=True, exist_ok=True)
    (request_dir / "alignment_request.json").write_text(
        json.dumps(
            {
                "fingerprint": "fp-2",
                "from_step": "develop",
                "recommended_resume_target": "develop",
                "strategic_document_update_requirements": [
                    {"category": "roadmap", "current_sha256": "previous-roadmap-sha"}
                ],
                "allowed_decisions": ["strategic_documents_updated"],
            }
        ),
        encoding="utf-8",
    )
    playbook_data = {
        "playbook": {"id": "default"},
        "steps": {
            "develop": {"role": "developer", "on": {"await_agent": "review"}},
            "review": {"role": "reviewer", "on": {}},
        },
    }
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("user", playbook_id="standard")
    store.set_current_step(blackboard, "user")
    store.update_handoff_contract(
        blackboard,
        from_step="develop",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.ALIGNMENT_CHECKPOINT,
        status_code="alignment_checkpoint",
        source="test",
    )

    with patch("cafe.ui.inquirer_prompts.prompt_list", return_value="strategic_documents_updated"):
        result = _handle_user_phase(
            issue_name="codex/issue-align-docs",
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            blackboard=blackboard,
        )

    assert result == "develop"
    reloaded = store.load_or_create("develop", playbook_id="standard")
    assert reloaded.current_step == "develop"
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.intent == HandoffIntent.AWAIT_AGENT


def test_user_phase_alignment_checkpoint_accepts_newly_created_missing_affected_document(
    tmp_path: Path,
) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True)
    (docs_dir / "positioning.md").write_text(
        "# Positioning\n\nCAFE serves workflow builders who need human-agent handoffs.\n",
        encoding="utf-8",
    )
    cafe_dir = tmp_path / ".cafe"
    cafe_dir.mkdir(parents=True, exist_ok=True)
    (cafe_dir / "strategic_context.yaml").write_text(
        "version: 1\n"
        "documents:\n"
        "  positioning:\n"
        "    path: docs/positioning.md\n"
        "    status: exists\n",
        encoding="utf-8",
    )
    issue_dir = cafe_dir / "issues" / "issue-align-positioning"
    request_dir = issue_dir / "spec" / "iteration_001"
    request_dir.mkdir(parents=True, exist_ok=True)
    (request_dir / "alignment_request.json").write_text(
        json.dumps(
            {
                "fingerprint": "fp-positioning",
                "from_step": "spec",
                "recommended_resume_target": "spec",
                "strategic_document_update_requirements": [],
                "affected_documents": [
                    {
                        "category": "positioning",
                        "path": "docs/positioning.md",
                        "status": "missing",
                        "sha256": None,
                        "exists": False,
                    }
                ],
                "allowed_decisions": ["strategic_documents_updated"],
            }
        ),
        encoding="utf-8",
    )
    playbook_data = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {"role": "pm", "on": {"await_agent": "plan"}},
            "plan": {"role": "developer", "on": {}},
        },
    }
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("user", playbook_id="standard")
    store.set_current_step(blackboard, "user")
    store.update_handoff_contract(
        blackboard,
        from_step="spec",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.ALIGNMENT_CHECKPOINT,
        status_code="alignment_checkpoint",
        source="test",
    )

    with patch("cafe.ui.inquirer_prompts.prompt_list", return_value="strategic_documents_updated"):
        result = _handle_user_phase(
            issue_name="issue-align-positioning",
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            blackboard=blackboard,
        )

    assert result == "spec"
    reloaded = store.load_or_create("spec", playbook_id="standard")
    assert reloaded.current_step == "spec"
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.intent == HandoffIntent.AWAIT_AGENT


def test_workflow_command_does_not_treat_generic_user_input_as_alignment_approval(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-align-resume"
    issue_dir.mkdir(parents=True, exist_ok=True)
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("user", playbook_id="standard")
    store.set_current_step(blackboard, "user")
    store.update_handoff_contract(
        blackboard,
        from_step="develop",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.ALIGNMENT_CHECKPOINT,
        status_code="alignment_checkpoint",
        source="test",
    )

    with patch("cafe.ui.cli.GitOperations") as mock_git_cls:
        git = MagicMock()
        git.get_current_branch.return_value = "issue-align-resume"
        mock_git_cls.return_value = git

        result = runner.invoke(
            app,
            [
                "workflow",
                "--playbook", "standard",
                "--execute",
                "--user-input",
                "looks good",
            ],
        )

    assert result.exit_code == 0, (result.stdout, result.exception)
    assert "alignment decision payload" in result.stdout
    assert not (issue_dir / "develop" / "iteration_001" / "user_input.md").exists()
    reloaded = store.load_or_create("spec", playbook_id="standard")
    assert reloaded.current_step == "user"


def test_workflow_command_resume_confirm_output_keeps_await_agent_intent(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-resume-confirm"
    issue_dir.mkdir(parents=True, exist_ok=True)
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("user", playbook_id="standard")
    store.set_current_step(blackboard, "user")
    store.update_handoff_contract(
        blackboard,
        from_step="spec",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.CONFIRM_OUTPUT,
        status_code="ready_for_review",
        source="test",
    )

    class FakeExecutor:
        def execute_step(
            self, step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            return _result(status_code="confirmed", step_name=step_name, step_def=step_def)

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-resume-confirm"
        mock_git_cls.return_value = git

        result = runner.invoke(
            app,
            [
                "workflow",
                "--playbook", "standard",
                "--execute",
                "--user-input",
                '{"task":"output-review","decision":"confirm"}',
            ],
        )

    assert result.exit_code == 0
    reloaded = store.load_or_create("spec", playbook_id="standard")
    assert reloaded.handoff_contract.intent == HandoffIntent.AWAIT_AGENT


def test_workflow_help_describes_user_input_without_spec_only_wording() -> None:
    result = runner.invoke(app, ["workflow", "--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    help_text = result.stdout.lower()
    normalized_help = " ".join(help_text.replace("│", " ").split())
    assert "spec step" not in help_text
    assert (
        "initial workflow input, or answer to write when resuming from a user handoff"
        in normalized_help
    )
    assert "--mute-agent-output" in help_text


def test_make_help_describes_user_input_without_spec_only_wording() -> None:
    result = runner.invoke(app, ["make", "--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    help_text = result.stdout.lower()
    normalized_help = " ".join(help_text.replace("│", " ").split())
    assert "spec step" not in help_text
    assert (
        "initial workflow input, or answer to write when resuming from a user handoff"
        in normalized_help
    )


def test_build_workflow_step_executor_passes_allowed_directories(
    tmp_path: Path, monkeypatch
) -> None:
    """Builder should read config dirs and preserve CLI-provided dirs on the executor."""
    monkeypatch.chdir(tmp_path)
    cafe_dir = tmp_path / ".cafe"
    cafe_dir.mkdir()
    config_manager = ConfigManager(str(cafe_dir))
    config_manager._config = {
        "agents": {
            "pm": {"name": "Roger", "cli": "claude"},
            "developer": {"name": "David", "cli": "claude"},
            "reviewer": {"name": "Richard", "cli": "claude"},
        },
        "allowed_directories": ["src"],
    }

    with (
        patch("cafe.ui.cli_shared.setup_agents", return_value=MagicMock()),
        patch("cafe.ui.cli_shared._get_git_operations_cls", return_value=MagicMock),
    ):
        executor = _build_workflow_step_executor(
            config_manager=config_manager,
            issue_dir=tmp_path / ".cafe" / "issues" / "issue-dirs",
            issue_name="issue-dirs",
            playbook_data={"playbook": {"id": "default"}, "roles": {}, "steps": {}},
            generic_phase=MagicMock(),
            open_pr=True,
            extra_allowed_directories=["docs"],
        )

    assert executor._config_allowed_directories == ["src"]
    assert executor._extra_allowed_directories == ["docs"]
    assert executor.open_pr is True


def test_workflow_accepts_add_dir_and_passes_through(tmp_path: Path, monkeypatch) -> None:
    """workflow --add-dir should validate the directory and pass it to the builder."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()

    class FakeExecutor:
        def execute_step(
            self, step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            return _result(status_code="confirmed", step_name=step_name, step_def=step_def)

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch(
            "cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()
        ) as mock_builder,
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-add-dir"
        mock_git_cls.return_value = git

        result = runner.invoke(
            app,
            ["workflow", "--playbook", "standard", "--execute", "--single-step", "--add-dir", "src"],
        )

    assert result.exit_code == 0, result.output
    assert mock_builder.call_args.kwargs["extra_allowed_directories"] == ["src"]


def test_workflow_command_prints_generic_event_display(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    executed_steps: list[str] = []

    class FakeExecutor:
        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object, **kwargs):
            executed_steps.append(step_name)
            events = []
            if step_name == "pr":
                events = [
                    {
                        "type": "custom_display_event",
                        "display": {
                            "style": "green",
                            "lines": [
                                "PR synced",
                                "  URL: https://github.com/test/repo/pull/238",
                            ],
                        },
                    }
                ]
            return StepExecutionResult(
                response="confirmed",
                artifacts={
                    str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"
                },
                status_code="confirmed",
                events=events,
            )

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-238"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "standard", "--execute"])

    assert result.exit_code == 0
    assert "PR synced" in result.stdout
    assert "https://github.com/test/repo/pull/238" in result.stdout
    assert executed_steps == ["spec", "plan", "develop", "review", "pr"]


def test_workflow_command_does_not_duplicate_pr_url_without_display(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)

    class FakeExecutor:
        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object, **kwargs):
            events = []
            if step_name == "pr":
                events = [
                    {
                        "type": "pr_synced",
                        "url": "https://github.com/test/repo/pull/277",
                        "display": {
                            "style": "green",
                            "lines": [
                                "PR synced",
                                "  URL: https://github.com/test/repo/pull/277",
                            ],
                        },
                    },
                    {
                        "type": "pr_link_opened",
                        "url": "https://github.com/test/repo/pull/277",
                    },
                ]
            return StepExecutionResult(
                response="confirmed",
                artifacts={
                    str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"
                },
                status_code="confirmed",
                events=events,
            )

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-277"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "standard", "--execute"])

    assert result.exit_code == 0
    assert result.stdout.count("PR synced") == 1
    assert result.stdout.count("https://github.com/test/repo/pull/277") == 1


def test_workflow_command_rejects_plain_text_chat_baton_before_execution(
    tmp_path: Path, monkeypatch
) -> None:
    """Issue #386: a plain-text chat-authored baton is never consumed as a step name."""
    monkeypatch.chdir(tmp_path)
    executed_steps: list[str] = []

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-205"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "standard",
                "current_step": "pr",
                "artifacts": {},
                "events": [],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )
    next_step_file = issue_dir / "next_step.txt"
    next_step_file.write_text("plan\n", encoding="utf-8")

    class FakeExecutor:
        def execute_step(
            self, step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            executed_steps.append(step_name)
            if step_name == "pr":
                _handoff_to_step(
                    issue_dir=issue_dir,
                    state=blackboard_state,
                    from_step="pr",
                    to_step="user",
                    status_code="confirmed",
                    intent=HandoffIntent.MANUAL_HANDOFF,
                )
            return _result(status_code="confirmed", step_name=step_name, step_def=step_def)

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-205"
        git.has_uncommitted_changes.return_value = False
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "standard", "--execute"])

    # The plain-text baton is rejected as an invalid structured contract, not
    # silently normalized into a step-name handoff.
    assert result.exit_code == 1
    assert not executed_steps
    assert next_step_file.exists()
    blackboard_data = json.loads((issue_dir / "blackboard.json").read_text(encoding="utf-8"))
    assert blackboard_data["current_step"] == "pr"


def test_workflow_command_does_not_consume_chat_baton_with_uncommitted_changes(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    executed_steps: list[str] = []

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-205b"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "standard",
                "current_step": "user",
                "artifacts": {},
                "events": [],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )
    next_step_file = issue_dir / "next_step.txt"
    next_step_file.write_text("develop\n", encoding="utf-8")

    class FakeExecutor:
        def execute_step(
            self, step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            executed_steps.append(step_name)
            return _result(
                status_code="confirmed", step_name=step_name, step_def=step_def, artifacts={}
            )

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-205b"
        git.has_uncommitted_changes.return_value = True
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "standard", "--execute"])

    # A plain-text baton is rejected outright (never consumed), independent of
    # the uncommitted-changes guard that only applied to the legacy path. The
    # workflow stays paused at "user" rather than crashing or advancing.
    assert result.exit_code == 0
    assert not executed_steps
    assert next_step_file.exists()
    blackboard_data = json.loads((issue_dir / "blackboard.json").read_text(encoding="utf-8"))
    assert blackboard_data["current_step"] == "user"


def test_workflow_command_rejects_invalid_chat_baton_step(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-206"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "next_step.txt").write_text("qa\n", encoding="utf-8")

    with patch("cafe.ui.cli.GitOperations") as mock_git_cls:
        git = MagicMock()
        git.get_current_branch.return_value = "issue-206"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "standard", "--execute"])

    assert result.exit_code == 1
    assert "Workflow baton file is not a valid handoff contract" in result.stdout
    assert "Invalid baton contract payload" in result.stdout


def test_workflow_command_rejects_malformed_baton_json(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-206b"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "next_step.txt").write_text("{not-json", encoding="utf-8")

    with patch("cafe.ui.cli.GitOperations") as mock_git_cls:
        git = MagicMock()
        git.get_current_branch.return_value = "issue-206b"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "standard", "--execute"])

    assert result.exit_code == 1
    assert "Workflow baton file is not a valid handoff contract" in result.stdout
    assert "Invalid baton contract payload" in result.stdout


def test_workflow_command_start_step_rebuilds_stale_text_baton(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-206c"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "standard",
                "current_step": "spec",
                "artifacts": {},
                "events": [],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )
    (issue_dir / "next_step.txt").write_text("done\n", encoding="utf-8")

    class FakeExecutor:
        def execute_step(
            self,
            step_name: str,
            step_def: dict,
            blackboard_state: object,
            **kwargs,
        ) -> StepExecutionResult:
            return _result(
                status_code="need_clarification",
                step_name=step_name,
                step_def=step_def,
                artifacts={},
            )

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch(
            "cafe.ui.cli._build_workflow_step_executor",
            return_value=FakeExecutor(),
        ),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-206c"
        mock_git_cls.return_value = git

        result = runner.invoke(
            app,
            [
                "workflow",
                "--playbook", "standard",
                "--execute",
                "--start-step",
                "spec",
                "--single-step",
            ],
        )

    assert result.exit_code == 0
    assert "Invalid baton contract payload" not in result.stdout

    baton = json.loads((issue_dir / "next_step.txt").read_text(encoding="utf-8"))
    assert set(baton) == {"version", "to_owner", "to_step", "intent"}
    assert baton["to_step"] in {"spec", "user"}
    blackboard = BlackboardStore(issue_dir).load_or_create("spec")
    assert blackboard.handoff_contract is not None
    assert blackboard.handoff_contract.from_step == "spec"


def test_explicit_start_step_preserves_user_handoff_for_runtime_supersession(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The runtime must see the original task before it replaces the baton."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-stale-task"
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("user", playbook_id="standard")
    store.set_current_step(blackboard, "user")
    store.update_handoff_contract(
        blackboard,
        from_step="develop",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.NEED_CLARIFICATION,
        source="test",
    )
    observed: dict[str, object] = {}

    class CapturingRuntime:
        def __init__(self, *, issue_dir: Path, **_kwargs: object) -> None:
            self.issue_dir = issue_dir

        def run(self, *, start_step: str | None = None, single_step: bool = False):
            state = BlackboardStore(self.issue_dir).load_or_create("spec", playbook_id="standard")
            observed.update(
                {
                    "current_step": state.current_step,
                    "handoff": state.handoff_contract,
                    "start_step": start_step,
                    "single_step": single_step,
                }
            )
            return PlaybookRunResult(
                final_step="spec", final_status_code="await_agent", completed=True
            )

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.commands.workflow.BlackboardWorkflowRuntime", CapturingRuntime),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-stale-task"
        mock_git_cls.return_value = git
        result = runner.invoke(
            app,
            ["workflow", "--playbook", "standard", "--execute", "--start-step", "spec"],
        )

    assert result.exit_code == 0
    assert observed["current_step"] == "user"
    handoff = observed["handoff"]
    assert handoff is not None
    assert handoff.from_step == "develop"
    assert handoff.to_owner is HandoffOwner.USER
    assert observed["start_step"] == "spec"


def test_workflow_command_prints_guidance_for_invalid_runtime_baton(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-206d"
    issue_dir.mkdir(parents=True, exist_ok=True)

    class FakeExecutor:
        def execute_step(
            self,
            step_name: str,
            step_def: dict,
            blackboard_state: object,
            **kwargs,
        ) -> StepExecutionResult:
            return _result(
                status_code="need_clarification",
                step_name=step_name,
                step_def=step_def,
                artifacts={},
            )

    class FailingRuntime:
        def __init__(self, *args, **kwargs) -> None:
            raise ValueError(
                "Invalid baton contract payload: Expecting value: line 1 column 1 (char 0)"
            )

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch(
            "cafe.ui.cli._build_workflow_step_executor",
            return_value=FakeExecutor(),
        ),
        patch("cafe.ui.commands.workflow.BlackboardWorkflowRuntime", FailingRuntime),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-206d"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "standard", "--execute"])

    assert result.exit_code == 1
    assert "Workflow baton file is not a valid handoff contract" in result.stdout
    assert "cafe workflow" in result.stdout
    assert "--playbook standard" in result.stdout
    assert "--execute" in result.stdout
    assert "--start-step <step>" in result.stdout
    assert "Error: workflow run failed: Invalid baton contract payload" in result.stdout


def test_workflow_command_prints_paused_when_human_input_is_needed(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)

    class FakeExecutor:
        def execute_step(
            self, step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            return _result(
                status_code="need_clarification",
                step_name=step_name,
                step_def=step_def,
                artifacts={},
            )

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-201"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "standard", "--execute"])
        assert result.exit_code == 0
        assert "Workflow is waiting for user input" in result.stdout


def test_workflow_command_prints_owner_task_id_for_noninteractive_wait(
    tmp_path: Path, monkeypatch
) -> None:
    """The non-interactive owner handoff exposes the durable task identifier."""
    monkeypatch.chdir(tmp_path)

    class WaitingRuntime:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def run(self, **_kwargs) -> PlaybookRunResult:
            return PlaybookRunResult(
                final_step="approval",
                final_status_code="HUMAN_TASK_PENDING",
                completed=False,
                detail="task-owner-123",
            )

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.commands.workflow.BlackboardWorkflowRuntime", WaitingRuntime),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-201"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "standard", "--execute"])

    assert result.exit_code == 0
    assert "task-owner-123" in result.stdout


def test_workflow_command_prints_recovery_guidance_for_pr_baton_pause(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-233"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "standard",
                "current_step": "pr",
                "artifacts": {},
                "events": [],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )

    class FakeExecutor:
        def execute_step(
            self, step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            return StepExecutionResult(response="no baton", artifacts={}, status_code=None)

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-233"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "standard", "--execute"])

    assert result.exit_code == 1
    assert "wrote invalid baton 3 times" in result.stdout
    assert "field 'to_step' got 'pr'" in result.stdout


def test_workflow_command_offers_recovery_menu_for_baton_pause_in_interactive_mode(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAFE_FORCE_INTERACTIVE", "1")
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-233"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "standard",
                "current_step": "pr",
                "artifacts": {},
                "events": [],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )

    class FakeExecutor:
        def execute_step(
            self, step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            return StepExecutionResult(response="no baton", artifacts={}, status_code=None)

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
        patch("cafe.ui.cli.prompt_list", return_value="Leave it for now") as mock_prompt_list,
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-233"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "standard", "--execute"])

    assert result.exit_code == 1
    assert not mock_prompt_list.called
    assert "wrote invalid baton 3 times" in result.stdout
    assert "field 'to_step' got 'pr'" in result.stdout


def test_workflow_command_user_owner_can_set_next_phase(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAFE_FORCE_INTERACTIVE", "1")
    executed_steps: list[str] = []

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-207"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "standard",
                "current_step": "user",
                "handoff_summary": "waiting for user decision",
                "artifacts": {},
                "events": [],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )

    class FakeExecutor:
        def execute_step(
            self, step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            executed_steps.append(step_name)
            if step_name == "pr":
                _handoff_to_step(
                    issue_dir=issue_dir,
                    state=blackboard_state,
                    from_step="pr",
                    to_step="user",
                    status_code="confirmed",
                    intent=HandoffIntent.MANUAL_HANDOFF,
                )
            return _result(
                status_code="confirmed", step_name=step_name, step_def=step_def, artifacts={}
            )

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
        patch(
            "cafe.ui.cli.prompt_list",
            side_effect=[
                "Leave a handoff note and continue the workflow",
                "Continue implementation (develop)",
                "Mark the workflow complete",
            ],
        ),
        patch(
            "cafe.ui.cli.prompt_multiline",
            return_value="Please continue implementation with the new handoff context.",
        ),
        patch("cafe.ui.cli.prompt_confirm", return_value=True),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-207"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "standard", "--execute"])

    assert result.exit_code == 0
    assert "Workflow is waiting for user input" in result.stdout
    assert "Executing step=develop iteration=001" in result.stdout
    assert "Workflow completed by user" in result.stdout
    assert executed_steps == ["develop", "review", "pr"]
    blackboard_data = json.loads((issue_dir / "blackboard.json").read_text(encoding="utf-8"))
    assert blackboard_data["handoff_summary"] == "workflow completed by user"
    handoff_event = next(
        event for event in blackboard_data["events"] if event["event_type"] == "user_handoff"
    )
    assert (
        handoff_event["data"]["note"]
        == "Please continue implementation with the new handoff context."
    )


def test_user_phase_uses_playbook_handoff_labels_and_generic_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "research-1"
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("user", playbook_id="research")
    store.set_current_step(blackboard, "user")
    playbook_data = {
        "playbook": {"id": "research"},
        "entry_point": "question",
        "roles": {"researcher": {"default_agent": "Morgan"}},
        "steps": {
            "question": {
                "role": "researcher",
                "handoff_label": "Refine research question",
                "on": {"await_agent": "collect"},
            },
            "collect": {"role": "researcher", "on": {"await_agent": "done"}},
        },
    }
    prompts: list[tuple[str, list[str]]] = []

    def prompt_side_effect(message, choices, **kwargs):
        prompts.append((message, choices))
        if message == "Select next action":
            return "Leave a handoff note and continue the workflow"
        if message == "Which phase should continue next?":
            return "Continue collect (collect)"
        return choices[0]

    with (
        patch("cafe.ui.cli.prompt_list", side_effect=prompt_side_effect),
        patch("cafe.ui.cli.prompt_multiline", return_value="Continue custom workflow."),
        patch("cafe.ui.cli.prompt_confirm", return_value=True),
    ):
        result = _handle_user_phase(
            issue_name="research-1",
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            blackboard=blackboard,
        )

    assert result == "collect"
    step_prompt = next(
        choices for message, choices in prompts if message == "Which phase should continue next?"
    )
    assert "Refine research question (question)" in step_prompt
    assert "Continue collect (collect)" in step_prompt
    assert all("implementation" not in choice.lower() for choice in step_prompt)


def test_confirm_output_chat_uses_playbook_chat_role(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "editorial-1"
    (issue_dir / "brief" / "iteration_001").mkdir(parents=True, exist_ok=True)
    (issue_dir / "brief" / "iteration_001" / "output.md").write_text("# Brief\n", encoding="utf-8")
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("user", playbook_id="editorial")
    store.set_current_step(blackboard, "user")
    store.update_handoff_contract(
        blackboard,
        from_step="brief",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.CONFIRM_OUTPUT,
        status_code="ready_for_review",
        source="test",
    )
    playbook_data = {
        "playbook": {"id": "editorial"},
        "entry_point": "brief",
        "roles": {
            "editor": {"default_agent": "Roger"},
            "writer": {"default_agent": "David"},
        },
        "steps": {
            "brief": {
                "role": "editor",
                "chat_role": "writer",
                "skill": "cafe-brief_first",
                "human_tasks": [
                    {
                        "trigger": "confirm_output",
                        "task_id": "editorial-output-review",
                        "outcomes": {"approve": "draft", "revise": "brief"},
                    }
                ],
                "on": {"await_agent": "draft", "confirm_output": "brief"},
            },
            "draft": {"role": "writer", "on": {"await_agent": "_done"}},
        },
    }

    with (
        patch("cafe.ui.cli.launch_chat_session") as mock_chat,
        patch("cafe.ui.cli._consume_pending_chat_handoff", return_value="draft"),
        patch("cafe.ui.cli_shared._print_output_file"),
        patch("cafe.ui.inquirer_prompts.prompt_list", return_value="chat"),
    ):
        result = _handle_user_phase(
            issue_name="editorial-1",
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            blackboard=blackboard,
        )

    assert result == "draft"
    mock_chat.assert_called_once_with("writer", "editorial-1")


def test_brief_confirm_output_routes_to_review_confirmation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "editorial-confirm"
    (issue_dir / "brief" / "iteration_001").mkdir(parents=True, exist_ok=True)
    (issue_dir / "brief" / "iteration_001" / "output.md").write_text("# Brief\n", encoding="utf-8")
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("user", playbook_id="editorial")
    store.set_current_step(blackboard, "user")
    store.update_handoff_contract(
        blackboard,
        from_step="brief",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.CONFIRM_OUTPUT,
        status_code="ready_for_review",
        source="test",
    )
    playbook_data = {
        "playbook": {"id": "editorial"},
        "entry_point": "brief",
        "roles": {"editor": {"default_agent": "Roger"}},
        "steps": {
            "brief": {
                "role": "editor",
                "skill": "cafe-brief_first",
                "human_tasks": [
                    {
                        "trigger": "confirm_output",
                        "task_id": "editorial-output-review",
                        "outcomes": {"approve": "draft", "revise": "brief"},
                    }
                ],
                "on": {"confirm_output": "brief", "await_agent": "draft"},
            },
            "draft": {"role": "writer", "on": {"await_agent": "_done"}},
        },
    }

    with patch(
        "cafe.ui.human_tasks.collect_human_task_payload",
        return_value={"task": "editorial-output-review", "decision": "approve"},
    ):
        result = _handle_user_phase(
            issue_name="editorial-confirm",
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            blackboard=blackboard,
        )

    assert result == "draft"


def test_user_phase_no_changes_needed_resumes_develop_without_generic_menu(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-no-changes"
    (issue_dir / "develop" / "iteration_001").mkdir(parents=True, exist_ok=True)
    (issue_dir / "develop" / "iteration_001" / "output.md").write_text(
        "No implementation changes are needed because the current code already "
        "satisfies the review.",
        encoding="utf-8",
    )
    playbook_data = {
        "playbook": {"id": "default"},
        "roles": {"developer": {"default_agent": "David"}},
        "steps": {
            "develop": {
                "role": "developer",
                "skill": "cafe-develop",
                "human_tasks": [
                    {
                        "trigger": "no_changes_needed",
                        "task_id": "no-change-decision",
                        "outcomes": {"agree": "review", "disagree": "develop"},
                    }
                ],
                "on": {
                    "await_agent": "review",
                    "manual_handoff": "pr",
                    "no_changes_needed": "develop",
                },
            },
            "review": {"role": "reviewer", "on": {}},
            "pr": {"role": "developer", "on": {}},
        },
    }
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("user", playbook_id="standard")
    store.set_current_step(blackboard, "user")
    store.update_handoff_contract(
        blackboard,
        from_step="develop",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.NO_CHANGES_NEEDED,
        status_code="no_changes_needed",
        source="test",
    )

    with (
        patch("cafe.ui.cli_shared._handle_user_phase_generic") as mock_generic,
        patch(
            "cafe.ui.human_tasks.collect_human_task_payload",
            return_value={"task": "no-change-decision", "decision": "agree"},
        ),
    ):
        result = _handle_user_phase(
            issue_name="issue-no-changes",
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            blackboard=blackboard,
        )

    assert result == "review"
    mock_generic.assert_not_called()
    reloaded = store.load_or_create("develop", playbook_id="standard")
    assert reloaded.current_step == "review"
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.to_owner == HandoffOwner.AGENT
    assert reloaded.handoff_contract.to_step == "review"
    assert reloaded.handoff_contract.intent == HandoffIntent.AWAIT_AGENT
    assert any(event.event_type == "human_task_completed" for event in reloaded.events)


def test_workflow_command_user_owner_can_complete_workflow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAFE_FORCE_INTERACTIVE", "1")

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-208"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "standard",
                "current_step": "user",
                "artifacts": {},
                "events": [],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli.prompt_list", return_value="Mark the workflow complete"),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-208"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "standard", "--execute"])

    assert result.exit_code == 0
    assert "Workflow completed by user" in result.stdout
    blackboard_data = json.loads((issue_dir / "blackboard.json").read_text(encoding="utf-8"))
    assert blackboard_data["current_step"] == "done"


def test_workflow_command_user_owner_can_chat_and_resume_from_baton(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAFE_FORCE_INTERACTIVE", "1")
    executed_steps: list[str] = []

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-209"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "standard",
                "current_step": "user",
                "artifacts": {},
                "events": [],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )

    class FakeExecutor:
        def execute_step(
            self, step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            executed_steps.append(step_name)
            if step_name == "pr":
                _handoff_to_step(
                    issue_dir=issue_dir,
                    state=blackboard_state,
                    from_step="pr",
                    to_step="user",
                    status_code="confirmed",
                    intent=HandoffIntent.MANUAL_HANDOFF,
                )
            return _result(
                status_code="confirmed", step_name=step_name, step_def=step_def, artifacts={}
            )

    def fake_launch_chat(role: str, issue_name: str) -> int:
        assert role == "developer"
        assert issue_name == "issue-209"
        # Structured JSON baton only (issue #386): plain-text batons are rejected.
        (issue_dir / "next_step.txt").write_text(
            json.dumps(
                {
                    "version": 1,
                    "to_owner": "agent",
                    "to_step": "develop",
                    "intent": "await_agent",
                }
            ),
            encoding="utf-8",
        )
        return 0

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
        patch(
            "cafe.ui.cli.prompt_list",
            side_effect=["Open chat with a role", "developer", "Mark the workflow complete"],
        ),
        patch("cafe.ui.cli.launch_chat_session", side_effect=fake_launch_chat),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-209"
        git.has_uncommitted_changes.return_value = False
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "standard", "--execute"])

    assert result.exit_code == 0
    assert executed_steps == ["develop", "review", "pr"]
    assert "Workflow completed by user" in result.stdout
    assert (issue_dir / "next_step.txt").exists()


def test_workflow_command_enters_user_phase_immediately_after_agent_handoff(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAFE_FORCE_INTERACTIVE", "1")

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-211"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "standard",
                "current_step": "pr",
                "artifacts": {},
                "events": [],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )

    class FakeExecutor:
        def execute_step(
            self, step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            assert step_name == "pr"
            _handoff_to_step(
                issue_dir=issue_dir,
                state=blackboard_state,
                from_step="pr",
                to_step="user",
                status_code="confirmed",
                intent=HandoffIntent.MANUAL_HANDOFF,
            )
            return _result(
                status_code="confirmed", step_name=step_name, step_def=step_def, artifacts={}
            )

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
        patch("cafe.ui.cli.prompt_list", return_value="Mark the workflow complete"),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-211"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "standard", "--execute"])

    assert result.exit_code == 0
    assert "Executing step=pr iteration=001" in result.stdout
    assert "Workflow is waiting for user input" in result.stdout
    assert "Workflow completed by user" in result.stdout
    assert "Workflow completed step=pr" not in result.stdout
    blackboard_data = json.loads((issue_dir / "blackboard.json").read_text(encoding="utf-8"))
    assert blackboard_data["current_step"] == "done"


def test_workflow_command_noninteractive_stops_after_agent_handoff_to_user(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-211b"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "standard",
                "current_step": "pr",
                "artifacts": {},
                "events": [],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )

    class FakeExecutor:
        def execute_step(
            self, step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            assert step_name == "pr"
            _handoff_to_step(
                issue_dir=issue_dir,
                state=blackboard_state,
                from_step="pr",
                to_step="user",
                status_code="confirmed",
                intent=HandoffIntent.MANUAL_HANDOFF,
            )
            return _result(
                status_code="confirmed", step_name=step_name, step_def=step_def, artifacts={}
            )

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
        patch("cafe.ui.cli._find_external_resume_step") as mock_external_resume,
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-211b"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "standard", "--execute"])

    assert result.exit_code == 0
    assert "Executing step=pr iteration=001" in result.stdout
    assert "Workflow is waiting for user input" in result.stdout
    assert mock_external_resume.call_count == 0


def test_user_phase_need_clarification_collects_questions_and_resumes_step(
    tmp_path: Path, capsys
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-clarification"
    issue_dir.mkdir(parents=True, exist_ok=True)
    spec_iter_dir = issue_dir / "spec" / "iteration_001"
    spec_iter_dir.mkdir(parents=True)
    (spec_iter_dir / "output.md").write_text(
        "# Spec Draft\n\nNeeds confirmation.", encoding="utf-8"
    )
    (spec_iter_dir / "questions.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<questions>
  <question id="1">
    <title>Confirm scope?</title>
    <options>
      <option>All roles</option>
      <option>Developer only</option>
    </options>
  </question>
</questions>
""",
        encoding="utf-8",
    )
    (spec_iter_dir / "iteration.json").write_text(
        json.dumps({"iteration": 1, "end_time": "done"}), encoding="utf-8"
    )
    playbook_data = {
        "playbook": {"id": "default"},
        "roles": {"pm": {"default_agent": "Roger"}},
        "steps": {
            "spec": {
                "role": "pm",
                "skill": "cafe-spec",
                "human_tasks": [
                    {
                        "trigger": "need_clarification",
                        "task_id": "clarification-answers",
                        "outcomes": {"submit": "spec"},
                    }
                ],
                "on": {"need_clarification": "spec", "await_agent": "plan"},
            },
            "plan": {"role": "developer", "on": {}},
        },
    }
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("user", playbook_id="standard")
    store.set_current_step(blackboard, "user")
    store.update_handoff_contract(
        blackboard,
        from_step="spec",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.NEED_CLARIFICATION,
        status_code="need_clarification",
        source="test",
    )

    with patch(
        "cafe.ui.human_tasks.collect_human_task_payload",
        return_value={"task": "clarification-answers", "answers": {"1": "All roles"}},
    ):
        result = _handle_user_phase(
            issue_name="issue-clarification",
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            blackboard=blackboard,
        )

    assert result == "spec"
    output = capsys.readouterr().out
    assert "Completed human task clarification-answers -> spec" in output
    next_input = issue_dir / "spec" / "iteration_002" / "user_input.md"
    assert next_input.read_text(encoding="utf-8") == "1: All roles"
    reloaded = store.load_or_create("spec", playbook_id="standard")
    assert reloaded.current_step == "spec"
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.intent == HandoffIntent.AWAIT_AGENT
    assert reloaded.handoff_contract.to_step == "spec"


def test_user_phase_question_need_clarification_requires_declared_policy(
    tmp_path: Path, capsys
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-research-clarify"
    issue_dir.mkdir(parents=True, exist_ok=True)
    question_iter_dir = issue_dir / "question" / "iteration_001"
    question_iter_dir.mkdir(parents=True)
    (question_iter_dir / "output.md").write_text("# Research question\n", encoding="utf-8")
    (question_iter_dir / "questions.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<questions>
  <question id="1">
    <title>Primary source?</title>
    <options>
      <option>Academic papers</option>
      <option>Industry reports</option>
    </options>
  </question>
</questions>
""",
        encoding="utf-8",
    )
    playbook_data = {
        "playbook": {"id": "research"},
        "roles": {"researcher": {"default_agent": "Morgan"}},
        "steps": {
            "question": {
                "role": "researcher",
                "on": {"need_clarification": "question", "await_agent": "collect"},
            },
            "collect": {"role": "researcher", "on": {}},
        },
    }
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("user", playbook_id="research")
    store.set_current_step(blackboard, "user")
    store.update_handoff_contract(
        blackboard,
        from_step="question",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.NEED_CLARIFICATION,
        status_code="need_clarification",
        source="test",
    )

    result = _handle_user_phase(
        issue_name="issue-research-clarify",
        issue_dir=issue_dir,
        playbook_data=playbook_data,
        blackboard=blackboard,
    )

    assert result is None
    assert not (issue_dir / "question" / "iteration_002" / "user_input.md").exists()
    reloaded = store.load_or_create("question", playbook_id="research")
    assert reloaded.current_step == "user"
    assert reloaded.handoff_contract.intent == HandoffIntent.NEED_CLARIFICATION
    assert reloaded.events[-1].event_type == "human_task_configuration_error"


def test_workflow_command_done_phase_can_restart_workflow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAFE_FORCE_INTERACTIVE", "1")
    executed_steps: list[str] = []

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-222"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "standard",
                "current_step": "done",
                "handoff_summary": "workflow completed",
                "artifacts": {},
                "events": [],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )

    class FakeExecutor:
        def execute_step(
            self, step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            executed_steps.append(step_name)
            if step_name == "pr":
                _handoff_to_step(
                    issue_dir=issue_dir,
                    state=blackboard_state,
                    from_step="pr",
                    to_step="user",
                    status_code="confirmed",
                    intent=HandoffIntent.MANUAL_HANDOFF,
                )
            return _result(
                status_code="confirmed", step_name=step_name, step_def=step_def, artifacts={}
            )

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
        patch(
            "cafe.ui.cli.prompt_list",
            side_effect=[
                "Leave a handoff note and continue the workflow",
                "Continue implementation (develop)",
                "Mark the workflow complete",
            ],
        ),
        patch("cafe.ui.cli.prompt_multiline", return_value="Implement the new follow-up request."),
        patch("cafe.ui.cli.prompt_confirm", return_value=True),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-222"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "standard", "--execute"])

    assert result.exit_code == 0
    assert "Workflow already completed" in result.stdout
    assert "Workflow is waiting for user input" in result.stdout
    assert "Executing step=develop iteration=001" in result.stdout
    assert executed_steps == ["develop", "review", "pr"]


def test_workflow_command_resumes_incomplete_iteration_when_user_handoff_is_legacy(
    tmp_path: Path, monkeypatch
) -> None:
    """Legacy user pointers without a handoff contract still recover unfinished work."""
    monkeypatch.chdir(tmp_path)
    executed_steps: list[str] = []

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-224"
    spec_iteration = issue_dir / "spec" / "iteration_002"
    spec_iteration.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "standard",
                "current_step": "user",
                "handoff_summary": "clarification answers confirmed",
                "artifacts": {},
                "events": [],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )
    (spec_iteration / "iteration.json").write_text(
        json.dumps(
            {
                "iteration": 2,
                "step_name": "spec",
                "skill_name": "spec_revise",
                "status_code": "ready_for_review",
                "user_input": "confirmed clarification answers",
                "timestamp": "2026-04-14T10:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )

    class FakeExecutor:
        def execute_step(
            self, step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            executed_steps.append(step_name)
            completed_iteration = issue_dir / "spec" / "iteration_003"
            completed_iteration.mkdir(parents=True, exist_ok=True)
            (completed_iteration / "iteration.json").write_text(
                json.dumps(
                    {
                        "iteration": 3,
                        "step_name": "spec",
                        "status_code": "ready_for_review",
                        "end_time": "2026-04-14T10:05:00+08:00",
                    }
                ),
                encoding="utf-8",
            )
            return _result(
                status_code="ready_for_review", step_name=step_name, step_def=step_def, artifacts={}
            )

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
        patch("cafe.ui.cli.prompt_list", return_value="Leave it for now"),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-224"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "standard", "--execute"])

    assert result.exit_code == 0
    assert "Resuming unfinished iteration" in result.stdout
    assert "step=spec" in result.stdout
    assert "Executing step=spec iteration=002" in result.stdout
    assert "Workflow is waiting for user input" in result.stdout
    assert executed_steps == ["spec"]


def test_workflow_user_handoff_precedes_incomplete_iteration_resume(
    tmp_path: Path, monkeypatch
) -> None:
    """A valid user-owned baton cannot be overwritten by stale unfinished work."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAFE_FORCE_INTERACTIVE", "1")
    executed_steps: list[str] = []

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-user-incomplete"
    develop_iteration = issue_dir / "develop" / "iteration_002"
    develop_iteration.mkdir(parents=True, exist_ok=True)
    (develop_iteration / "iteration.json").write_text(
        json.dumps(
            {
                "iteration": 2,
                "step_name": "develop",
                "timestamp": "2026-08-11T11:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("spec", playbook_id="standard")
    store.set_current_step(blackboard, "user")
    store.update_handoff_contract(
        blackboard,
        from_step="spec",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.CONFIRM_OUTPUT,
        status_code="ready_for_review",
        source="test",
    )

    class FakeExecutor:
        def execute_step(
            self, step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            executed_steps.append(step_name)
            return _result(
                status_code="need_clarification",
                step_name=step_name,
                step_def=step_def,
            )

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
        patch("cafe.ui.cli._find_incomplete_workflow_step") as mock_find_incomplete,
        patch(
            "cafe.ui.cli._handle_user_phase",
            return_value=None,
        ) as mock_handle_user_phase,
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-user-incomplete"
        mock_git_cls.return_value = git

        result = runner.invoke(
            app,
            [
                "workflow",
                "--playbook", "standard",
                "--execute",
                "--user-input",
                '{"task":"output-review","decision":"confirm"}',
            ],
        )

    assert result.exit_code == 0
    assert "Resuming unfinished iteration" not in result.stdout
    assert "Executing step=plan iteration=001" in result.stdout
    assert executed_steps == ["plan"]
    mock_find_incomplete.assert_not_called()
    mock_handle_user_phase.assert_called_once()


@pytest.mark.parametrize("source", ["chat.bootstrap", "unknown"])
def test_nonmeaningful_user_handoff_does_not_hide_incomplete_iteration(
    tmp_path: Path, monkeypatch, source: str
) -> None:
    """Bootstrap/default baton metadata cannot outrank runnable phase state."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / f"issue-{source.replace('.', '-')}"
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("spec", playbook_id="standard")
    store.set_current_step(blackboard, "user")
    store.update_handoff_contract(
        blackboard,
        from_step="spec",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.MANUAL_HANDOFF,
        source=source,
    )
    executed_steps: list[str] = []

    class FakeExecutor:
        def execute_step(
            self, step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            executed_steps.append(step_name)
            return _result(
                status_code="need_clarification",
                step_name=step_name,
                step_def=step_def,
            )

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
        patch(
            "cafe.ui.cli._find_incomplete_workflow_step", return_value="develop"
        ) as mock_find_incomplete,
    ):
        git = MagicMock()
        git.get_current_branch.return_value = issue_dir.name
        mock_git_cls.return_value = git

        result = runner.invoke(
            app,
            ["workflow", "--playbook", "standard", "--execute", "--single-step"],
        )

    assert result.exit_code == 0
    assert "Resuming unfinished iteration" in result.stdout
    assert executed_steps == ["develop"]
    mock_find_incomplete.assert_called_once()


@pytest.mark.parametrize("source", ["test", "unknown"])
def test_workflow_alignment_decision_precedes_incomplete_iteration_resume(
    tmp_path: Path, monkeypatch, source: str
) -> None:
    monkeypatch.chdir(tmp_path)
    executed_steps: list[str] = []

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-align-incomplete"
    develop_iteration = issue_dir / "develop" / "iteration_002"
    develop_iteration.mkdir(parents=True, exist_ok=True)
    (develop_iteration / "iteration.json").write_text(
        json.dumps(
            {
                "iteration": 2,
                "step_name": "develop",
                "timestamp": "2026-07-15T11:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )
    (develop_iteration / "alignment_request.json").write_text(
        json.dumps(
            {
                "fingerprint": "fp-incomplete",
                "from_step": "develop",
                "recommended_resume_target": "develop",
                "strategic_document_update_requirements": [],
                "allowed_decisions": ["approve"],
            }
        ),
        encoding="utf-8",
    )
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("user", playbook_id="standard")
    store.set_current_step(blackboard, "user")
    store.update_handoff_contract(
        blackboard,
        from_step="develop",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.ALIGNMENT_CHECKPOINT,
        status_code="alignment_checkpoint",
        source=source,
    )

    class FakeExecutor:
        def execute_step(
            self, step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            executed_steps.append(step_name)
            return _result(
                status_code="ready_for_review",
                step_name=step_name,
                step_def=step_def,
            )

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
        patch("cafe.ui.cli._find_incomplete_workflow_step") as mock_find_incomplete,
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-align-incomplete"
        mock_git_cls.return_value = git

        result = runner.invoke(
            app,
            [
                "workflow",
                "--playbook", "standard",
                "--execute",
                "--single-step",
                "--user-input",
                '{"decision":"approve"}',
            ],
        )

    assert result.exit_code == 0
    assert "Alignment decision recorded approve -> develop" in result.stdout
    assert "Resuming unfinished iteration" not in result.stdout
    assert executed_steps == ["develop"]
    mock_find_incomplete.assert_not_called()


def test_find_external_resume_step_preserves_pending_ledger_feedback_for_its_target(
    tmp_path: Path,
) -> None:
    """UT-003: durable feedback selects its declared target without early delivery."""
    from cafe.core.workflow_feedback import WorkflowFeedbackLedger

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-238"
    WorkflowFeedbackLedger(issue_dir).record(
        source_identity="github-pr:238:comment-1",
        source_kind="github_pr",
        target_step="develop",
        content="Handle the unresolved comment.",
    )
    playbook_data = {
        "steps": {
            "pr": {
                "hooks": {
                    "prepare_input": ["GitHubPRCreator", "GitHubPRFeedbackSource", "UserInputCollector"],
                }
            },
            "develop": {},
        }
    }
    git_ops = MagicMock()

    result = _find_external_resume_step(
        issue_dir=issue_dir,
        playbook_data=playbook_data,
        git_ops=git_ops,
    )

    assert result == "develop"
    assert len(WorkflowFeedbackLedger(issue_dir).pending(target_step="develop")) == 1
    git_ops.get_current_branch.assert_not_called()


def test_find_external_resume_step_returns_none_for_consumed_ledger_feedback(
    tmp_path: Path,
) -> None:
    """UT-002: delivered but unresolved feedback must not reopen the PR step."""
    from cafe.core.workflow_feedback import WorkflowFeedbackLedger

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-241"
    ledger = WorkflowFeedbackLedger(issue_dir)
    identity = "github-pr:241:comment-1"
    ledger.record(
        source_identity=identity,
        source_kind="github_pr",
        target_step="pr",
        content="Already delivered unresolved feedback.",
    )
    assert ledger.consume(identity) is True
    playbook_data = {
        "steps": {
            "pr": {
                "hooks": {
                    "prepare_input": [
                        "GitHubPRCreator",
                        "GitHubPRFeedbackSource",
                        "UserInputCollector",
                    ],
                },
            },
        },
    }
    git_ops = MagicMock()
    git_ops.get_current_branch.return_value = "issue-241"

    with (
        patch("cafe.ui.cli.GitHubOps") as mock_github_ops,
        patch("cafe.utils.github.get_all_pr_comments") as mock_fetch,
        patch("cafe.utils.github.filter_unresolved_comments", return_value=[]),
    ):
        mock_github_ops.return_value.get_pr_for_branch.return_value = {
            "number": 241,
            "url": "https://github.com/test/repo/pull/241",
        }
        mock_fetch.return_value = []
        result = _find_external_resume_step(
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            git_ops=git_ops,
        )

    assert result is None
    mock_fetch.assert_called_once_with(241, exclude_ids={"comment-1"})


def test_find_external_resume_step_returns_none_without_pending_ledger_feedback(
    tmp_path: Path,
) -> None:
    """UT-003: external resume does not perform a second GitHub feedback read."""
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-240"
    (issue_dir / "pr").mkdir(parents=True, exist_ok=True)
    playbook_data = {
        "steps": {
            "pr": {
                "hooks": {
                    "prepare_input": ["GitHubPRCreator", "GitHubPRFeedbackSource", "UserInputCollector"],
                },
            },
        },
    }
    git_ops = MagicMock()
    git_ops.get_current_branch.return_value = "issue-240"

    with (
        patch("cafe.ui.cli.GitHubOps") as mock_github_ops,
        patch("cafe.utils.github.get_all_pr_comments") as mock_fetch,
    ):
        mock_github_ops.return_value.get_pr_for_branch.return_value = None
        result = _find_external_resume_step(
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            git_ops=git_ops,
        )

    assert result is None
    mock_fetch.assert_not_called()


def test_find_external_resume_step_returns_pr_for_new_unresolved_github_feedback(
    tmp_path: Path,
) -> None:
    """UT-003: unknown unresolved comments still start their declared source step."""
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-242"
    playbook_data = {
        "steps": {
            "pr": {
                "hooks": {
                    "prepare_input": ["GitHubPRCreator", "GitHubPRFeedbackSource", "UserInputCollector"],
                },
            },
        },
    }
    git_ops = MagicMock()
    git_ops.get_current_branch.return_value = "issue-242"

    with (
        patch("cafe.ui.cli.GitHubOps") as mock_github_ops,
        patch("cafe.utils.github.get_all_pr_comments") as mock_fetch,
        patch("cafe.utils.github.filter_unresolved_comments", return_value=["comment-1"]),
    ):
        mock_github_ops.return_value.get_pr_for_branch.return_value = {
            "number": 242,
            "url": "https://github.com/test/repo/pull/242",
        }
        mock_fetch.return_value = ["comment-1"]
        result = _find_external_resume_step(
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            git_ops=git_ops,
        )

    assert result == "pr"
    mock_fetch.assert_called_once_with(242, exclude_ids=set())


def test_workflow_command_resumes_pr_when_external_feedback_arrives_while_done(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    executed_steps: list[str] = []

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-238"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "standard",
                "current_step": "done",
                "handoff_summary": "workflow completed",
                "artifacts": {},
                "events": [],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )

    class FakeExecutor:
        def execute_step(
            self, step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            executed_steps.append(step_name)
            return _result(
                status_code="confirmed", step_name=step_name, step_def=step_def, artifacts={}
            )

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
        patch("cafe.ui.cli._find_external_resume_step", side_effect=["pr", None]),
        patch("cafe.ui.cli._find_incomplete_workflow_step", return_value=None),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-238"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "standard", "--execute"])

    assert result.exit_code == 0
    assert "Detected external workflow feedback" in result.stdout
    assert "Executing step=pr iteration=001" in result.stdout
    assert executed_steps == ["pr"]


def test_workflow_execute_uses_baton_for_cross_step_handoff(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    executed_steps: list[str] = []

    playbook_dir = tmp_path / ".cafe" / "playbooks"
    playbook_dir.mkdir(parents=True, exist_ok=True)
    (playbook_dir / "goto.yaml").write_text(
        """
playbook:
  id: goto
steps:
  spec:
    skill: spec_first
    role: pm
    allowed_goto: [develop]
    valid_intents: [confirmed, need_clarification]
    on:
      await_agent: plan
      need_clarification: develop
  plan:
    skill: cafe-plan
    role: developer
    valid_intents: [confirmed]
    on:
      await_agent: _done
  develop:
    skill: cafe-develop
    role: developer
    valid_intents: [confirmed]
    on: {}
""".strip(),
        encoding="utf-8",
    )

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor") as mock_builder,
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-201"
        mock_git_cls.return_value = git

        executor = MagicMock()

        def _execute(
            step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            executed_steps.append(step_name)
            if step_name == "spec":
                _handoff_to_step(
                    issue_dir=tmp_path / ".cafe" / "issues" / "issue-201",
                    state=blackboard_state,
                    from_step="spec",
                    to_step="develop",
                    status_code="confirmed",
                )
            return _result(
                status_code="confirmed", step_name=step_name, step_def=step_def, artifacts={}
            )

        executor.execute_step.side_effect = _execute
        mock_builder.return_value = executor

        result = runner.invoke(app, ["workflow", "--playbook", "goto", "--execute"])
        assert result.exit_code == 0
        assert executed_steps == ["spec", "develop"]


def test_workflow_command_supports_start_step_single_step(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    executed_steps: list[str] = []

    playbook_dir = tmp_path / ".cafe" / "playbooks"
    playbook_dir.mkdir(parents=True, exist_ok=True)
    (playbook_dir / "single.yaml").write_text(
        """
playbook:
  id: single
steps:
  plan:
    skill: cafe-plan
    role: developer
    valid_intents: [confirmed]
    on:
      await_agent: develop
  develop:
    skill: cafe-develop
    role: developer
    valid_intents: [confirmed]
    on:
      await_agent: _done
""".strip(),
        encoding="utf-8",
    )

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor") as mock_builder,
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-202"
        mock_git_cls.return_value = git
        executor = MagicMock()
        executor.execute_step.side_effect = lambda step_name, step_def, blackboard_state, **kw: (
            executed_steps.append(step_name)
            or _result(
                status_code="confirmed", step_name=step_name, step_def=step_def, artifacts={}
            )
        )
        mock_builder.return_value = executor

        result = runner.invoke(
            app,
            [
                "workflow",
                "--playbook",
                "single",
                "--execute",
                "--start-step",
                "plan",
                "--single-step",
                "--mute-agent-output",
                "--open-pr",
            ],
        )
        assert result.exit_code == 0
        assert executed_steps == ["plan"]
        assert mock_builder.call_args.kwargs["phase_name"] == "plan"
        assert mock_builder.call_args.kwargs["stream_agent_output"] is False
        assert mock_builder.call_args.kwargs["open_pr"] is True


def test_workflow_command_rebuilds_executor_for_each_active_phase(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    built_phases: list[str | None] = []
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-phase-chain"
    playbook_dir = tmp_path / ".cafe" / "playbooks"
    playbook_dir.mkdir(parents=True)
    (playbook_dir / "phase-chain.yaml").write_text(
        """
playbook:
  id: phase-chain
steps:
  spec:
    skill: cafe-spec
    role: pm
    on: {await_agent: plan}
  plan:
    skill: cafe-plan
    role: developer
    on: {await_agent: develop}
  develop:
    skill: cafe-develop
    role: developer
    on: {await_agent: _done}
""".strip(),
        encoding="utf-8",
    )

    class FakeExecutor:
        def execute_step(
            self, step_name: str, step_def: dict, blackboard_state: object, **kwargs
        ) -> StepExecutionResult:
            next_step = {"spec": "plan", "plan": "develop", "develop": "done"}[step_name]
            _handoff_to_step(
                issue_dir=issue_dir,
                state=blackboard_state,
                from_step=step_name,
                to_step=next_step,
                status_code="confirmed",
            )
            return _result(
                status_code="confirmed",
                step_name=step_name,
                step_def=step_def,
                artifacts={},
            )

    def fake_builder(**kwargs):
        built_phases.append(kwargs.get("phase_name"))
        return FakeExecutor()

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", side_effect=fake_builder),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-phase-chain"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "phase-chain", "--execute"])

    assert result.exit_code == 0, result.output
    assert built_phases[:3] == ["spec", "plan", "develop"]


def test_workflow_command_runs_hotfix_playbook(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    executed_steps: list[str] = []

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor") as mock_builder,
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-203"
        mock_git_cls.return_value = git
        executor = MagicMock()
        executor.execute_step.side_effect = lambda step_name, step_def, blackboard_state, **kw: (
            executed_steps.append(step_name)
            or _result(
                status_code="confirmed", step_name=step_name, step_def=step_def, artifacts={}
            )
        )
        mock_builder.return_value = executor

        result = runner.invoke(app, ["workflow", "--playbook", "hotfix", "--execute"])
        assert result.exit_code == 0
        assert executed_steps == ["develop", "review", "pr"]


def test_workflow_command_uses_config_selected_custom_playbook(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".cafe").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".cafe" / "config.yaml").write_text("playbook: custom\n", encoding="utf-8")
    executed_steps: list[str] = []

    playbook_dir = tmp_path / ".cafe" / "playbooks"
    playbook_dir.mkdir(parents=True, exist_ok=True)
    (playbook_dir / "custom.yaml").write_text(
        """
playbook:
  id: custom
steps:
  develop:
    skill: cafe-develop
    role: developer
    valid_intents: [confirmed]
    on:
      await_agent: pr
  pr:
    skill: cafe-pr
    role: developer
    valid_intents: [confirmed]
    on:
      await_agent: _done
""".strip(),
        encoding="utf-8",
    )

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor") as mock_builder,
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-204"
        mock_git_cls.return_value = git
        executor = MagicMock()
        executor.execute_step.side_effect = (
            lambda step_name, step_def, blackboard_state, **kwargs: (
                executed_steps.append(step_name)
                or _result(
                    status_code="confirmed", step_name=step_name, step_def=step_def, artifacts={}
                )
            )
        )
        mock_builder.return_value = executor

        result = runner.invoke(app, ["workflow", "--execute"])
        assert result.exit_code == 0
        assert executed_steps == ["develop", "pr"]


def test_workflow_resume_uses_the_issue_owned_playbook_before_global_config(
    tmp_path: Path, monkeypatch
) -> None:
    """UT-009: an existing issue resumes its recorded workflow, not global config."""
    monkeypatch.chdir(tmp_path)
    cafe_dir = tmp_path / ".cafe"
    issue_dir = cafe_dir / "issues" / "issue-owned-flow"
    issue_dir.mkdir(parents=True)
    (cafe_dir / "config.yaml").write_text("playbook: standard\n", encoding="utf-8")
    (issue_dir / "issue.yaml").write_text("playbook: release-flow\n", encoding="utf-8")
    playbook_dir = cafe_dir / "playbooks"
    playbook_dir.mkdir()
    (playbook_dir / "release-flow.yaml").write_text(
        """
playbook:
  id: release-flow
steps:
  ship:
    skill: cafe-develop
    role: developer
    on: {await_agent: _done}
""".strip(),
        encoding="utf-8",
    )
    executed_steps: list[str] = []

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor") as mock_builder,
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-owned-flow"
        mock_git_cls.return_value = git
        executor = MagicMock()
        executor.execute_step.side_effect = lambda step_name, step_def, state, **kwargs: (
            executed_steps.append(step_name)
            or _result(
                status_code="confirmed", step_name=step_name, step_def=step_def, artifacts={}
            )
        )
        mock_builder.return_value = executor

        result = runner.invoke(app, ["workflow", "--issue", "issue-owned-flow", "--execute"])

    assert result.exit_code == 0, result.output
    assert executed_steps == ["ship"]


def test_workflow_execute_syncs_active_issue_on_healthy_branch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cafe_dir = tmp_path / ".cafe"
    issue_dir = cafe_dir / "issues" / "issue-sync"
    issue_dir.mkdir(parents=True)
    (cafe_dir / "active_issue").write_text("stale\n", encoding="utf-8")

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor") as mock_builder,
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-sync"
        mock_git_cls.return_value = git
        executor = MagicMock()
        executor.execute_step.return_value = _result(
            status_code="confirmed",
            step_name="spec",
            step_def={"output_artifact": "spec"},
        )
        mock_builder.return_value = executor

        result = runner.invoke(
            app, ["workflow", "--playbook", "standard", "--execute", "--single-step"]
        )

    assert result.exit_code == 0
    assert (cafe_dir / "active_issue").read_text(encoding="utf-8").strip() == "issue-sync"


def test_workflow_execute_recovers_from_unhealthy_git_via_marker(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    cafe_dir = tmp_path / ".cafe"
    issue_dir = cafe_dir / "issues" / "saved-issue"
    issue_dir.mkdir(parents=True)
    (cafe_dir / "active_issue").write_text("saved-issue\n", encoding="utf-8")

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor") as mock_builder,
    ):
        git = MagicMock()
        git.get_branch_health.return_value = BranchHealth(is_healthy=False, reason="detached_head")
        mock_git_cls.return_value = git
        executor = MagicMock()
        executor.execute_step.return_value = _result(
            status_code="confirmed",
            step_name="spec",
            step_def={"output_artifact": "spec"},
        )
        mock_builder.return_value = executor

        result = runner.invoke(
            app, ["workflow", "--playbook", "standard", "--execute", "--single-step"]
        )

    assert result.exit_code == 0
    assert (cafe_dir / "active_issue").read_text(encoding="utf-8").strip() == "saved-issue"


def test_workflow_execute_invalid_marker_exits_with_guidance(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cafe_dir = tmp_path / ".cafe"
    (cafe_dir / "issues").mkdir(parents=True)
    (cafe_dir / "active_issue").write_text("missing-issue\n", encoding="utf-8")

    with patch("cafe.ui.cli.GitOperations") as mock_git_cls:
        git = MagicMock()
        git.get_branch_health.return_value = BranchHealth(is_healthy=False, reason="git_error")
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "standard", "--execute"])

    assert result.exit_code == 1
    assert "missing-issue" in result.stdout


def test_resolve_initial_step_user_inputs_explicit_start_step_wins_over_user_park() -> None:
    from cafe.ui.commands.workflow import _resolve_initial_step_user_inputs

    playbook_data = {"entry_point": "build", "steps": {"build": {}, "review": {}}}
    inputs, remaining = _resolve_initial_step_user_inputs(
        playbook_data, "fix the geo mapping", "review", "user"
    )
    assert inputs == {"review": "fix the geo mapping"}
    assert remaining is None


def test_resolve_initial_step_user_inputs_user_park_defers_to_handoff_branch() -> None:
    from cafe.ui.commands.workflow import _resolve_initial_step_user_inputs

    playbook_data = {"entry_point": "build", "steps": {"build": {}, "review": {}}}
    inputs, remaining = _resolve_initial_step_user_inputs(
        playbook_data, "answer for the asking step", None, "user"
    )
    assert inputs is None
    assert remaining == "answer for the asking step"


def test_resolve_initial_step_user_inputs_cold_start_maps_entry_point() -> None:
    from cafe.ui.commands.workflow import _resolve_initial_step_user_inputs

    playbook_data = {"entry_point": "build", "steps": {"build": {}, "review": {}}}
    inputs, remaining = _resolve_initial_step_user_inputs(
        playbook_data, "initial requirement", None, "build"
    )
    assert inputs == {"build": "initial requirement"}
    assert remaining is None
