"""Guardrails ensuring phases_legacy hidden CLI aliases stay removed (issue #315)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from cafe.ui.cli import app
from tests.conftest import create_minimal_config

runner = CliRunner()
pytestmark = pytest.mark.usefixtures("cached_builtin_playbook_models")


def test_phases_legacy_module_is_removed() -> None:
    assert not Path("src/cafe/ui/commands/phases_legacy.py").exists()


def test_cli_source_does_not_reference_phases_legacy() -> None:
    cli_source = Path("src/cafe/ui/cli.py").read_text(encoding="utf-8")
    assert "phases_legacy" not in cli_source


@pytest.mark.parametrize("legacy_command", ["spec", "plan", "develop", "review", "pr"])
def test_legacy_phase_commands_are_unknown(legacy_command: str) -> None:
    result = runner.invoke(app, [legacy_command])
    assert result.exit_code != 0
    assert "No such command" in result.output


def test_workflow_start_step_spec_is_supported(tmp_path: Path, monkeypatch) -> None:
    """Replacement entry: cafe workflow --start-step spec (not the retired cafe spec alias)."""
    monkeypatch.chdir(tmp_path)
    create_minimal_config(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-legacy-guard"
    issue_dir.mkdir(parents=True)

    with patch("cafe.ui.cli.GitOperations") as mock_git_cls:
        git = MagicMock()
        git.get_current_branch.return_value = "issue-legacy-guard"
        mock_git_cls.return_value = git

        result = runner.invoke(
            app,
            ["workflow", "--playbook", "standard", "--dry-run", "--start-step", "spec"],
        )

    assert result.exit_code == 0
    assert "playbook=standard step=spec" in result.stdout

    legacy = runner.invoke(app, ["spec"])
    assert legacy.exit_code != 0
    assert "No such command" in legacy.output
