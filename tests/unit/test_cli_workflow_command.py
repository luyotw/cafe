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
    for phase in ["spec", "plan", "develop", "review"]:
        phase_dir = issue_dir / phase
        phase_dir.mkdir(parents=True, exist_ok=True)
        (phase_dir / "status.json").write_text('{"status_code":"CAFE_CONFIRMED"}', encoding="utf-8")

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
        assert mock_run.call_count >= 4
