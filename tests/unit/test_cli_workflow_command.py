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
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-200"
    for phase in ["spec", "plan", "develop", "review", "pr"]:
        phase_dir = issue_dir / phase
        phase_dir.mkdir(parents=True, exist_ok=True)
        (phase_dir / "status.json").write_text('{"status_code":"CAFE_CONFIRMED"}', encoding="utf-8")
        iter_dir = phase_dir / "iteration_001"
        iter_dir.mkdir(parents=True, exist_ok=True)
        (iter_dir / "context.json").write_text(
            '{"response":"CAFE_CONFIRMED\\nstep done"}',
            encoding="utf-8",
        )

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli.subprocess.run") as mock_run,
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-200"
        mock_git_cls.return_value = git
        mock_run.return_value = MagicMock(returncode=0)

        result = runner.invoke(app, ["workflow", "--playbook", "default", "--execute"])
        assert result.exit_code == 0
        assert "Workflow completed" in result.stdout
        assert mock_run.call_count >= 5
        first_cmd = mock_run.call_args_list[0][0][0]
        assert "spec" in first_cmd
        assert "--auto" not in first_cmd
        assert "--no-interactive" in first_cmd


def test_workflow_execute_uses_context_response_for_goto(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-201"
    for phase in ["spec", "develop"]:
        phase_dir = issue_dir / phase
        phase_dir.mkdir(parents=True, exist_ok=True)
        (phase_dir / "status.json").write_text('{"status_code":"CAFE_CONFIRMED"}', encoding="utf-8")
        iter_dir = phase_dir / "iteration_001"
        iter_dir.mkdir(parents=True, exist_ok=True)
        response = "CAFE_CONFIRMED\\nstep done"
        if phase == "spec":
            response = "CAFE_CONFIRMED\\nCAFE_GOTO:develop"
        (iter_dir / "context.json").write_text(
            f'{{"response":"{response}"}}',
            encoding="utf-8",
        )

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
        patch("cafe.ui.cli.subprocess.run") as mock_run,
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-201"
        mock_git_cls.return_value = git
        mock_run.return_value = MagicMock(returncode=0)

        result = runner.invoke(app, ["workflow", "--playbook", "goto", "--execute"])
        assert result.exit_code == 0
        called = [call[0][0] for call in mock_run.call_args_list]
        assert any("develop" in cmd for cmd in called)
        assert not any("plan" in cmd for cmd in called)
