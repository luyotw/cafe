"""Tests for workflow CLI command."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from typer.testing import CliRunner

from cafe.ui.cli import app, _execute_single_step_alias
from cafe.utils.config import ConfigManager


runner = CliRunner()


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

        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object) -> tuple[str, dict[str, str]]:
            return ("CAFE_NO_CHANGES_NEEDED", {})

    with patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()):
        result = _execute_single_step_alias(
            issue_name="issue-210",
            step_name="develop",
            config_manager=config_manager,
        )

    assert result["status_code"] == "CAFE_NO_CHANGES_NEEDED"
    blackboard_data = json.loads((issue_dir / "blackboard.json").read_text(encoding="utf-8"))
    assert blackboard_data["current_step"] == "develop"


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
        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object) -> tuple[str, dict[str, str]]:
            executed_steps.append(step_name)
            return ("CAFE_CONFIRMED", {str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"})

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
        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object) -> tuple[str, dict[str, str]]:
            executed_steps.append(step_name)
            return ("CAFE_CONFIRMED", {str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"})

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
        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object) -> tuple[str, dict[str, str]]:
            executed_steps.append(step_name)
            return ("CAFE_CONFIRMED", {})

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


def test_workflow_command_prints_paused_when_human_input_is_needed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    class FakeExecutor:
        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object) -> tuple[str, dict[str, str]]:
            return ("CAFE_NEED_CLARIFICATION", {})

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
        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object) -> tuple[str, dict[str, str]]:
            executed_steps.append(step_name)
            return ("CAFE_CONFIRMED", {})

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
        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object) -> tuple[str, dict[str, str]]:
            executed_steps.append(step_name)
            return ("CAFE_CONFIRMED", {})

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
        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object) -> tuple[str, dict[str, str]]:
            assert step_name == "pr"
            return ("CAFE_CONFIRMED", {})

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
        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object) -> tuple[str, dict[str, str]]:
            executed_steps.append(step_name)
            return ("CAFE_CONFIRMED", {})

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
        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object) -> tuple[str, dict[str, str]]:
            executed_steps.append(step_name)
            completed_iteration = issue_dir / "spec" / "iteration_003"
            completed_iteration.mkdir(parents=True, exist_ok=True)
            (completed_iteration / "context.json").write_text(
                json.dumps(
                    {
                        "iteration": 3,
                        "step_name": "spec",
                        "status_code": "CAFE_READY_FOR_REVIEW",
                        "end_time": "2026-04-14T10:05:00+08:00",
                    }
                ),
                encoding="utf-8",
            )
            return ("CAFE_READY_FOR_REVIEW", {})

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


def test_workflow_execute_uses_context_response_for_goto(tmp_path: Path, monkeypatch) -> None:
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
    valid_status_codes: [CAFE_CONFIRMED, CAFE_NEED_CLARIFICATION]
    on:
      CAFE_CONFIRMED: plan
      CAFE_NEED_CLARIFICATION: develop
  plan:
    skill: plan
    role: developer
    valid_status_codes: [CAFE_CONFIRMED]
    on:
      CAFE_CONFIRMED: _done
  develop:
    skill: develop
    role: developer
    valid_status_codes: [CAFE_CONFIRMED]
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
        executor.execute_step.side_effect = lambda step_name, step_def, blackboard_state: (
            executed_steps.append(step_name) or (
                "CAFE_CONFIRMED\nCAFE_GOTO:develop" if step_name == "spec" else "CAFE_CONFIRMED",
                {},
            )
        )
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
    valid_status_codes: [CAFE_CONFIRMED]
    on:
      CAFE_CONFIRMED: develop
  develop:
    skill: develop
    role: developer
    valid_status_codes: [CAFE_CONFIRMED]
    on:
      CAFE_CONFIRMED: _done
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
            executed_steps.append(step_name) or ("CAFE_CONFIRMED", {})
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
            executed_steps.append(step_name) or ("CAFE_CONFIRMED", {})
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
    valid_status_codes: [CAFE_CONFIRMED]
    on:
      CAFE_CONFIRMED: pr
  pr:
    skill: pr
    role: developer
    valid_status_codes: [CAFE_CONFIRMED]
    on:
      CAFE_CONFIRMED: _done
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
            executed_steps.append(step_name) or ("CAFE_CONFIRMED", {})
        )
        mock_builder.return_value = executor

        result = runner.invoke(app, ["workflow", "--execute"])
        assert result.exit_code == 0
        assert executed_steps == ["develop", "pr"]
