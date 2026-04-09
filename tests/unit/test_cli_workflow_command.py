"""Tests for workflow CLI command."""

from pathlib import Path
from unittest.mock import patch, MagicMock

from typer.testing import CliRunner

from cafe.ui.cli import app


runner = CliRunner()


def test_workflow_command_runs_dry_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".cafe" / "issues").mkdir(parents=True, exist_ok=True)

    with patch("cafe.ui.cli.GitOperations") as mock_git_cls:
        git = MagicMock()
        git.get_current_branch.return_value = "issue-100"
        mock_git_cls.return_value = git

        result = runner.invoke(app, ["workflow", "--playbook", "default", "--dry-run"])
        assert result.exit_code == 0
        assert "Workflow completed" in result.stdout
        workflow_file = tmp_path / ".cafe" / "issues" / "issue-100" / "workflow_instance.json"
        blackboard_file = tmp_path / ".cafe" / "issues" / "issue-100" / "blackboard.json"
        assert workflow_file.exists()
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
        assert "Workflow completed" in result.stdout
        assert mock_builder.called
        assert executed_steps == ["spec", "plan", "develop", "review", "pr"]


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
        assert "Workflow paused" in result.stdout
        assert "CAFE_NEED_CLARIFICATION" in result.stdout


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
