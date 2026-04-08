"""Tests for dynamic playbook step commands."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from cafe.ui.cli import app


runner = CliRunner()


def test_custom_step_command_routes_to_workflow_runtime(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".cafe").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".cafe" / "config.yaml").write_text("playbook: custom\n", encoding="utf-8")

    issue_dir = tmp_path / ".cafe" / "issues" / "issue-205" / "qa"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "status.json").write_text('{"status_code":"CAFE_CONFIRMED"}', encoding="utf-8")
    iter_dir = issue_dir / "iteration_001"
    iter_dir.mkdir(parents=True, exist_ok=True)
    (iter_dir / "context.json").write_text('{"response":"CAFE_CONFIRMED\\nstep done"}', encoding="utf-8")

    playbook_dir = tmp_path / ".cafe" / "playbooks"
    playbook_dir.mkdir(parents=True, exist_ok=True)
    (playbook_dir / "custom.yaml").write_text(
        """
playbook:
  id: custom
steps:
  qa:
    skill: review
    role: reviewer
    valid_status_codes: [CAFE_CONFIRMED]
    on:
      CAFE_CONFIRMED: _done
""".strip(),
        encoding="utf-8",
    )

    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.ui.cli.subprocess.run") as mock_run,
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "issue-205"
        mock_git_cls.return_value = git
        mock_run.return_value = MagicMock(returncode=0)

        result = runner.invoke(app, ["qa"])

        assert result.exit_code == 0
        called = [call[0][0] for call in mock_run.call_args_list if "cafe.ui.cli" in " ".join(call[0][0])]
        assert len(called) == 1
        assert "review" in called[0]
