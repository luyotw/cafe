"""Tests for workflow CLI command."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from typer.testing import CliRunner

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.workflow_models import StepExecutionResult
from cafe.ui.cli import app, _execute_single_step_alias, _find_external_resume_step
from cafe.utils.config import ConfigManager


runner = CliRunner()


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
        artifacts=artifacts if artifacts is not None else {str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"},
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
    intent: HandoffIntent = HandoffIntent.AWAIT_AGENT,
) -> None:
    store = BlackboardStore(issue_dir)
    to_owner = HandoffOwner.AGENT if to_step not in {"user", "done"} else HandoffOwner(to_step)
    store.update_handoff_contract(
        state,
        from_step=from_step,
        to_owner=to_owner,
        to_step=to_step,
        intent=intent,
        status_code=status_code,
        source="test.executor",
    )


def test_single_step_alias_updates_workflow_pointer_to_requested_step(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-210"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "default",
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

        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object) -> StepExecutionResult:
            return _result(status_code="no_changes_needed", step_name=step_name, step_def=step_def, artifacts={})

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

        result = runner.invoke(app, ["workflow", "--playbook", "default", "--dry-run"])
        assert result.exit_code == 0
        assert "Workflow context" in result.stdout
        assert "playbook=default step=spec" in result.stdout
        assert "Workflow completed" in result.stdout
        blackboard_file = tmp_path / ".cafe" / "issues" / "issue-100" / "blackboard.json"
        assert blackboard_file.exists()


def test_workflow_command_runs_execute_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    executed_steps: list[str] = []

    class FakeExecutor:
        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object) -> StepExecutionResult:
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
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()) as mock_builder,
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-200"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "default", "--execute"])
        assert result.exit_code == 0
        assert "Workflow context" in result.stdout
        assert "playbook=default step=spec" in result.stdout
        assert "Executing step=spec iteration=001" in result.stdout
        assert "Executing step=plan iteration=001" in result.stdout
        assert "Executing step=develop iteration=001" in result.stdout
        assert "Executing step=review iteration=001" in result.stdout
        assert "Executing step=pr iteration=001" in result.stdout
        assert "Workflow is waiting for user input" in result.stdout
        assert mock_builder.called
        assert executed_steps == ["spec", "plan", "develop", "review", "pr"]


def test_workflow_command_passes_initial_user_input_to_spec_step(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    class FakeExecutor:
        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object) -> StepExecutionResult:
            return _result(status_code="confirmed", step_name=step_name, step_def=step_def)

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()) as mock_builder,
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-201"
        mock_git_cls.return_value = git

        result = runner.invoke(
            app,
            [
                "workflow",
                "--playbook",
                "default",
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


def test_workflow_command_prints_pr_url_when_pr_step_reports_sync_event(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    executed_steps: list[str] = []

    class FakeExecutor:
        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object):
            executed_steps.append(step_name)
            events = []
            if step_name == "pr":
                events = [{"type": "pr_synced", "url": "https://github.com/test/repo/pull/238"}]
            return StepExecutionResult(
                response="confirmed",
                artifacts={str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"},
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

        result = runner.invoke(app, ["workflow", "--playbook", "default", "--execute"])

    assert result.exit_code == 0
    assert "PR synced" in result.stdout
    assert "https://github.com/test/repo/pull/238" in result.stdout
    assert executed_steps == ["spec", "plan", "develop", "review", "pr"]


def test_workflow_command_consumes_chat_baton_before_execution(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    executed_steps: list[str] = []

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-205"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "default",
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
        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object) -> StepExecutionResult:
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

        result = runner.invoke(app, ["workflow", "--playbook", "default", "--execute"])
        assert result.exit_code == 0

    assert "Workflow context" in result.stdout
    assert "playbook=default step=plan" in result.stdout
    assert "Executing step=plan iteration=001" in result.stdout
    assert executed_steps == ["plan", "develop", "review", "pr"]
    assert next_step_file.exists()
    blackboard_data = json.loads((issue_dir / "blackboard.json").read_text(encoding="utf-8"))
    assert blackboard_data["current_step"] == "user"


def test_workflow_command_does_not_consume_chat_baton_with_uncommitted_changes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    executed_steps: list[str] = []

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-205b"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "default",
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
        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object) -> StepExecutionResult:
            executed_steps.append(step_name)
            return _result(status_code="confirmed", step_name=step_name, step_def=step_def, artifacts={})

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-205b"
        git.has_uncommitted_changes.return_value = True
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "default", "--execute"])

    assert result.exit_code == 0
    assert "uncommitted \nchanges" in result.stdout
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

        result = runner.invoke(app, ["workflow", "--playbook", "default", "--execute"])

    assert result.exit_code == 1
    assert "Baton contract step 'qa' is not valid" in result.stdout


def test_workflow_command_rejects_malformed_baton_json(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-206b"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "next_step.txt").write_text("{not-json", encoding="utf-8")

    with patch("cafe.ui.cli.GitOperations") as mock_git_cls:
        git = MagicMock()
        git.get_current_branch.return_value = "issue-206b"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "default", "--execute"])

    assert result.exit_code == 1
    assert "Baton contract step '{not-json' is not valid" in result.stdout


def test_workflow_command_start_step_rebuilds_stale_text_baton(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-206c"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "default",
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
        ) -> StepExecutionResult:
            return _result(
                status_code="need_clarification",
                step_name=step_name,
                step_def=step_def,
                artifacts={},
            )

    with patch("cafe.ui.cli.GitOperations") as mock_git_cls, patch(
        "cafe.ui.cli._build_workflow_step_executor",
        return_value=FakeExecutor(),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-206c"
        mock_git_cls.return_value = git

        result = runner.invoke(
            app,
            [
                "workflow",
                "--playbook",
                "default",
                "--execute",
                "--start-step",
                "spec",
                "--single-step",
            ],
        )

    assert result.exit_code == 0
    assert "Invalid baton contract payload" not in result.stdout

    baton = json.loads((issue_dir / "next_step.txt").read_text(encoding="utf-8"))
    assert baton["from_step"] == "spec"
    assert baton["to_step"] in {"spec", "user"}


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

    with patch("cafe.ui.cli.GitOperations") as mock_git_cls, patch(
        "cafe.ui.cli._build_workflow_step_executor",
        return_value=FakeExecutor(),
    ), patch("cafe.ui.commands.workflow.BlackboardWorkflowRuntime", FailingRuntime):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-206d"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "default", "--execute"])

    assert result.exit_code == 1
    assert "Workflow baton file is not a valid handoff contract" in result.stdout
    assert "cafe workflow" in result.stdout
    assert "--playbook default" in result.stdout
    assert "--execute" in result.stdout
    assert "--start-step <step>" in result.stdout
    assert "Error: workflow run failed: Invalid baton contract payload" in result.stdout


def test_workflow_command_prints_paused_when_human_input_is_needed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    class FakeExecutor:
        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object) -> StepExecutionResult:
            return _result(status_code="need_clarification", step_name=step_name, step_def=step_def, artifacts={})

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-201"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "default", "--execute"])
        assert result.exit_code == 0
        assert "Workflow is waiting for user input" in result.stdout


def test_workflow_command_prints_recovery_guidance_for_pr_baton_pause(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-233"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "default",
                "current_step": "pr",
                "artifacts": {},
                "events": [],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )

    class FakeExecutor:
        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object) -> StepExecutionResult:
            return StepExecutionResult(response="no baton", artifacts={}, status_code=None)

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-233"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "default", "--execute"])

    assert result.exit_code == 0
    assert "Workflow paused" in result.stdout
    assert "status=NO_BATON_TRANSITION" in result.stdout
    assert "Open chat with role `developer`" in result.stdout
    assert "Do not wait for remote PR existence." in result.stdout


def test_workflow_command_offers_recovery_menu_for_baton_pause_in_interactive_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAFE_FORCE_INTERACTIVE", "1")
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-233"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "default",
                "current_step": "pr",
                "artifacts": {},
                "events": [],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )

    class FakeExecutor:
        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object) -> StepExecutionResult:
            return StepExecutionResult(response="no baton", artifacts={}, status_code=None)

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
        patch("cafe.ui.cli.prompt_list", return_value="Leave it for now") as mock_prompt_list,
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-233"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "default", "--execute"])

    assert result.exit_code == 0
    assert mock_prompt_list.called
    assert "Workflow paused" in result.stdout


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
                "playbook_id": "default",
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
        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object) -> StepExecutionResult:
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
            return _result(status_code="confirmed", step_name=step_name, step_def=step_def, artifacts={})

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
        patch("cafe.ui.cli.prompt_multiline", return_value="Please continue implementation with the new handoff context."),
        patch("cafe.ui.cli.prompt_confirm", return_value=True),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-207"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "default", "--execute"])

    assert result.exit_code == 0
    assert "Workflow is waiting for user input" in result.stdout
    assert "Executing step=develop iteration=001" in result.stdout
    assert "Workflow completed by user" in result.stdout
    assert executed_steps == ["develop", "review", "pr"]
    blackboard_data = json.loads((issue_dir / "blackboard.json").read_text(encoding="utf-8"))
    assert blackboard_data["handoff_summary"] == "workflow completed by user"
    handoff_event = next(event for event in blackboard_data["events"] if event["event_type"] == "user_handoff")
    assert handoff_event["data"]["note"] == "Please continue implementation with the new handoff context."


def test_workflow_command_user_owner_can_complete_workflow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAFE_FORCE_INTERACTIVE", "1")

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-208"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "default",
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

        result = runner.invoke(app, ["workflow", "--playbook", "default", "--execute"])

    assert result.exit_code == 0
    assert "Workflow completed by user" in result.stdout
    blackboard_data = json.loads((issue_dir / "blackboard.json").read_text(encoding="utf-8"))
    assert blackboard_data["current_step"] == "done"


def test_workflow_command_user_owner_can_chat_and_resume_from_baton(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAFE_FORCE_INTERACTIVE", "1")
    executed_steps: list[str] = []

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-209"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "default",
                "current_step": "user",
                "artifacts": {},
                "events": [],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )

    class FakeExecutor:
        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object) -> StepExecutionResult:
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
            return _result(status_code="confirmed", step_name=step_name, step_def=step_def, artifacts={})

    def fake_launch_chat(role: str, issue_name: str) -> int:
        assert role == "developer"
        assert issue_name == "issue-209"
        (issue_dir / "next_step.txt").write_text("develop\n", encoding="utf-8")
        return 0

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
        patch("cafe.ui.cli.prompt_list", side_effect=["Open chat with a role", "developer", "Mark the workflow complete"]),
        patch("cafe.ui.cli.launch_chat_session", side_effect=fake_launch_chat),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-209"
        git.has_uncommitted_changes.return_value = False
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "default", "--execute"])

    assert result.exit_code == 0
    assert executed_steps == ["develop", "review", "pr"]
    assert "Workflow completed by user" in result.stdout
    assert (issue_dir / "next_step.txt").exists()


def test_workflow_command_enters_user_phase_immediately_after_agent_handoff(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAFE_FORCE_INTERACTIVE", "1")

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-211"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "default",
                "current_step": "pr",
                "artifacts": {},
                "events": [],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )

    class FakeExecutor:
        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object) -> StepExecutionResult:
            assert step_name == "pr"
            _handoff_to_step(
                issue_dir=issue_dir,
                state=blackboard_state,
                from_step="pr",
                to_step="user",
                status_code="confirmed",
                intent=HandoffIntent.MANUAL_HANDOFF,
            )
            return _result(status_code="confirmed", step_name=step_name, step_def=step_def, artifacts={})

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
        patch("cafe.ui.cli.prompt_list", return_value="Mark the workflow complete"),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-211"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "default", "--execute"])

    assert result.exit_code == 0
    assert "Executing step=pr iteration=001" in result.stdout
    assert "Workflow is waiting for user input" in result.stdout
    assert "Workflow completed by user" in result.stdout
    assert "Workflow completed step=pr" not in result.stdout
    blackboard_data = json.loads((issue_dir / "blackboard.json").read_text(encoding="utf-8"))
    assert blackboard_data["current_step"] == "done"


def test_workflow_command_noninteractive_stops_after_agent_handoff_to_user(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-211b"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "default",
                "current_step": "pr",
                "artifacts": {},
                "events": [],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )

    class FakeExecutor:
        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object) -> StepExecutionResult:
            assert step_name == "pr"
            _handoff_to_step(
                issue_dir=issue_dir,
                state=blackboard_state,
                from_step="pr",
                to_step="user",
                status_code="confirmed",
                intent=HandoffIntent.MANUAL_HANDOFF,
            )
            return _result(status_code="confirmed", step_name=step_name, step_def=step_def, artifacts={})

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
        patch("cafe.ui.cli._find_external_resume_step") as mock_external_resume,
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-211b"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "default", "--execute"])

    assert result.exit_code == 0
    assert "Executing step=pr iteration=001" in result.stdout
    assert "Workflow is waiting for user input" in result.stdout
    assert mock_external_resume.call_count == 0


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
                "playbook_id": "default",
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
        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object) -> StepExecutionResult:
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
            return _result(status_code="confirmed", step_name=step_name, step_def=step_def, artifacts={})

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

        result = runner.invoke(app, ["workflow", "--playbook", "default", "--execute"])

    assert result.exit_code == 0
    assert "Workflow already completed" in result.stdout
    assert "Workflow is waiting for user input" in result.stdout
    assert "Executing step=develop iteration=001" in result.stdout
    assert executed_steps == ["develop", "review", "pr"]


def test_workflow_command_resumes_incomplete_iteration_before_user_phase(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    executed_steps: list[str] = []

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-224"
    spec_iteration = issue_dir / "spec" / "iteration_002"
    spec_iteration.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "default",
                "current_step": "user",
                "handoff_summary": "clarification answers confirmed",
                "artifacts": {},
                "events": [],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )
    (spec_iteration / "context.json").write_text(
        json.dumps(
            {
                "iteration": 2,
                "step_name": "spec",
                "skill_name": "spec_revise",
                "user_input": "confirmed clarification answers",
                "timestamp": "2026-04-14T10:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )

    class FakeExecutor:
        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object) -> StepExecutionResult:
            executed_steps.append(step_name)
            completed_iteration = issue_dir / "spec" / "iteration_003"
            completed_iteration.mkdir(parents=True, exist_ok=True)
            (completed_iteration / "context.json").write_text(
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
            return _result(status_code="ready_for_review", step_name=step_name, step_def=step_def, artifacts={})

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
        patch("cafe.ui.cli.prompt_list", return_value="Leave it for now"),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-224"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "default", "--execute"])

    assert result.exit_code == 0
    assert "Resuming unfinished iteration" in result.stdout
    assert "step=spec" in result.stdout
    assert "Executing step=spec iteration=002" in result.stdout
    assert "Workflow is waiting for user input" in result.stdout
    assert executed_steps == ["spec"]


def test_find_external_resume_step_returns_pr_when_new_pr_comments_exist(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-238"
    (issue_dir / "pr").mkdir(parents=True, exist_ok=True)
    playbook_data = {
        "steps": {
            "pr": {
                "hooks": {
                    "prepare_input": ["GitHubPRCreator", "UserInputCollector"],
                }
            }
        }
    }
    git_ops = MagicMock()
    git_ops.get_current_branch.return_value = "issue-238"
    git_ops.has_unpushed_commits.return_value = False

    with (
        patch("cafe.ui.cli.GitHubOps") as mock_github_ops,
        patch("cafe.utils.github.get_all_pr_comments", return_value=["comment-1"]),
        patch("cafe.utils.github.filter_unresolved_comments", return_value=["comment-1"]),
    ):
        mock_github_ops.return_value.get_pr_for_branch.return_value = {
            "number": 238,
            "url": "https://github.com/test/repo/pull/238",
        }

        result = _find_external_resume_step(
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            git_ops=git_ops,
        )

    assert result == "pr"


def test_find_external_resume_step_returns_none_when_last_seen_covers_all_comments(
    tmp_path: Path,
) -> None:
    """P2: stale context.json processed fields must not be the only source; last-seen artifact excludes known IDs."""
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-241"
    pr_dir = issue_dir / "pr"
    artifact = pr_dir / "artifacts" / "pr_last_seen_comments.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps({"last_seen_comment_ids": ["c1", "c2"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    playbook_data = {
        "steps": {
            "pr": {
                "hooks": {
                    "prepare_input": ["GitHubPRCreator", "UserInputCollector"],
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
    mock_fetch.assert_called_once()
    call_kw = mock_fetch.call_args[1]
    assert call_kw["exclude_ids"] == {"c1", "c2"}


def test_find_external_resume_step_returns_pr_when_unpushed_commits_but_unresolved_exist(
    tmp_path: Path,
) -> None:
    """Unresolved feedback must still wake the PR step even with local commits."""
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-239"
    (issue_dir / "pr").mkdir(parents=True, exist_ok=True)
    playbook_data = {
        "steps": {
            "pr": {
                "hooks": {
                    "prepare_input": ["GitHubPRCreator", "UserInputCollector"],
                },
            },
        },
    }
    git_ops = MagicMock()
    git_ops.get_current_branch.return_value = "issue-239"
    git_ops.has_unpushed_commits.return_value = True

    with (
        patch("cafe.ui.cli.GitHubOps") as mock_github_ops,
        patch("cafe.utils.github.get_all_pr_comments", return_value=["comment-1"]),
        patch("cafe.utils.github.filter_unresolved_comments", return_value=["comment-1"]),
    ):
        mock_github_ops.return_value.get_pr_for_branch.return_value = {
            "number": 239,
            "url": "https://github.com/test/repo/pull/239",
        }

        result = _find_external_resume_step(
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            git_ops=git_ops,
        )

    assert result == "pr"


def test_find_external_resume_step_returns_none_when_no_github_pr(tmp_path: Path) -> None:
    """Control case: missing remote PR means no resume (plan Test 1.3 branch)."""
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-240"
    (issue_dir / "pr").mkdir(parents=True, exist_ok=True)
    playbook_data = {
        "steps": {
            "pr": {
                "hooks": {
                    "prepare_input": ["GitHubPRCreator", "UserInputCollector"],
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


def test_workflow_command_resumes_pr_when_external_feedback_arrives_while_done(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    executed_steps: list[str] = []

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-238"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "default",
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
        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object) -> StepExecutionResult:
            executed_steps.append(step_name)
            return _result(status_code="confirmed", step_name=step_name, step_def=step_def, artifacts={})

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
        patch("cafe.ui.cli._find_external_resume_step", side_effect=["pr", None]),
        patch("cafe.ui.cli._find_incomplete_workflow_step", return_value=None),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-238"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "default", "--execute"])

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
    skill: plan
    role: developer
    valid_intents: [confirmed]
    on:
      await_agent: _done
  develop:
    skill: develop
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
        def _execute(step_name: str, step_def: dict, blackboard_state: object) -> StepExecutionResult:
            executed_steps.append(step_name)
            if step_name == "spec":
                _handoff_to_step(
                    issue_dir=tmp_path / ".cafe" / "issues" / "issue-201",
                    state=blackboard_state,
                    from_step="spec",
                    to_step="develop",
                    status_code="confirmed",
                )
            return _result(status_code="confirmed", step_name=step_name, step_def=step_def, artifacts={})
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
    skill: plan
    role: developer
    valid_intents: [confirmed]
    on:
      await_agent: develop
  develop:
    skill: develop
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
        executor.execute_step.side_effect = lambda step_name, step_def, blackboard_state: (
            executed_steps.append(step_name) or _result(status_code="confirmed", step_name=step_name, step_def=step_def, artifacts={})
        )
        mock_builder.return_value = executor

        result = runner.invoke(
            app,
            ["workflow", "--playbook", "single", "--execute", "--start-step", "plan", "--single-step"],
        )
        assert result.exit_code == 0
        assert executed_steps == ["plan"]


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
        executor.execute_step.side_effect = lambda step_name, step_def, blackboard_state: (
            executed_steps.append(step_name) or _result(status_code="confirmed", step_name=step_name, step_def=step_def, artifacts={})
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
    skill: develop
    role: developer
    valid_intents: [confirmed]
    on:
      await_agent: pr
  pr:
    skill: pr
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
        executor.execute_step.side_effect = lambda step_name, step_def, blackboard_state: (
            executed_steps.append(step_name) or _result(status_code="confirmed", step_name=step_name, step_def=step_def, artifacts={})
        )
        mock_builder.return_value = executor

        result = runner.invoke(app, ["workflow", "--execute"])
        assert result.exit_code == 0
        assert executed_steps == ["develop", "pr"]
