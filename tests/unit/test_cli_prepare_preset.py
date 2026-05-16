"""Tests for cafe prepare --preset flag."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from typer.testing import CliRunner

import yaml

from cafe.ui.cli import app


runner = CliRunner()


def _make_cafe_dir(tmp_path: Path) -> Path:
    """Create a minimal .cafe environment for prepare tests."""
    cafe_dir = tmp_path / ".cafe"
    cafe_dir.mkdir()
    # Minimal config.yaml so prepare doesn't bail early
    (cafe_dir / "config.yaml").write_text(
        "agents:\n"
        "  pm:\n    name: Roger\n    cli: claude\n"
        "  developer:\n    name: David\n    cli: claude\n"
        "  reviewer:\n    name: Richard\n    cli: claude\n"
    )
    return cafe_dir


class TestPreparePreset:
    """Tests for cafe prepare --preset flag."""

    def test_prepare_preset_applies_preset_to_crew_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        _make_cafe_dir(tmp_path)

        with (
            patch("cafe.ui.commands.lifecycle.GitOperations") as mock_git_cls,
            patch("cafe.ui.commands.lifecycle._ensure_default_content"),
            patch("cafe.ui.init_helpers.sync_agents", return_value=(0, 0)),
            patch("cafe.ui.init_helpers.sync_templates", return_value=(0, 0)),
        ):
            mock_git = MagicMock()
            mock_git.get_current_branch.return_value = "main"
            mock_git.branch_exists.return_value = False
            mock_git_cls.return_value = mock_git

            result = runner.invoke(
                app,
                [
                    "prepare",
                    "test-issue",
                    "--no-interactive",
                    "--input-method", "manual",
                    "--preset", "default",
                ],
            )

        crew_yaml = tmp_path / ".cafe" / "crew.yaml"
        assert crew_yaml.exists(), f"crew.yaml not created. Output: {result.output}"
        data = yaml.safe_load(crew_yaml.read_text())
        assert "pm" in data
        assert "developer" in data
        assert "reviewer" in data

    def test_prepare_unknown_preset_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        _make_cafe_dir(tmp_path)

        with (
            patch("cafe.ui.commands.lifecycle.GitOperations") as mock_git_cls,
            patch("cafe.ui.commands.lifecycle._ensure_default_content"),
            patch("cafe.ui.init_helpers.sync_agents", return_value=(0, 0)),
            patch("cafe.ui.init_helpers.sync_templates", return_value=(0, 0)),
        ):
            mock_git = MagicMock()
            mock_git.get_current_branch.return_value = "main"
            mock_git.branch_exists.return_value = False
            mock_git_cls.return_value = mock_git

            result = runner.invoke(
                app,
                [
                    "prepare",
                    "test-issue",
                    "--no-interactive",
                    "--input-method", "manual",
                    "--preset", "totally-nonexistent-preset-xyz",
                ],
            )

        assert result.exit_code != 0
        assert "totally-nonexistent-preset-xyz" in result.output or "not found" in result.output.lower()
