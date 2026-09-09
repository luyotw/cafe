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
    (tmp_path / ".cafe" / "phases.yaml").write_text(
        "qa:\n  name: Richard\n  clis:\n    - cli: codex\n      model: test-model\n",
        encoding="utf-8",
    )
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-205"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text(
        "playbook: custom\ncontract_version: 2\ndriver:\n  mode: unattended\n",
        encoding="utf-8",
    )

    playbook_dir = tmp_path / ".cafe" / "playbooks"
    playbook_dir.mkdir(parents=True, exist_ok=True)
    (playbook_dir / "custom.yaml").write_text(
        """
playbook:
  id: custom
steps:
  qa:
    skill: cafe-review
    role: reviewer
    allowed_tools: ["Bash(cafe verification check:*)"]
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
        git.get_current_branch.return_value = "issue-205"
        mock_git_cls.return_value = git
        executor = MagicMock()
        executor.execute_step.return_value = ("confirmed", {})
        mock_builder.return_value = executor

        result = runner.invoke(app, ["qa"])

        assert result.exit_code == 0, (result.stdout, result.exception)
        executor.execute_step.assert_called_once()
        assert executor.execute_step.call_args[0][0] == "qa"
