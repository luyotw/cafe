"""Tests for automatic sync after cafe template edit."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from cafe.ui.cli import app


@pytest.fixture
def runner():
    """Create a CLI test runner."""
    return CliRunner()


class TestTemplateEditAutoSync:
    """Tests for automatic sync after template edit."""

    @patch("cafe.ui.cli.subprocess.run")
    @patch("cafe.ui.init_helpers.sync_templates")
    @patch("cafe.ui.cli.prompt_list")
    @patch("cafe.templates.manager.get_global_cafe_dir")
    def test_template_edit_triggers_sync_after_successful_edit(
        self,
        mock_get_global_cafe_dir: MagicMock,
        mock_prompt_list: MagicMock,
        mock_sync_templates: MagicMock,
        mock_subprocess_run: MagicMock,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Test that template edit triggers sync after successful edit."""
        # Setup global cafe directory
        global_cafe_dir = tmp_path / ".cafe_global"
        global_cafe_dir.mkdir()
        templates_dir = global_cafe_dir / "templates" / "plan"
        templates_dir.mkdir(parents=True)
        template_file = templates_dir / "my-template.md"
        template_file.write_text("test content")

        mock_get_global_cafe_dir.return_value = global_cafe_dir
        mock_prompt_list.side_effect = ["plan", "my-template"]
        mock_subprocess_run.return_value = MagicMock(returncode=0)
        mock_sync_templates.return_value = (3, 0)

        # Setup local .cafe directory
        with patch("cafe.ui.cli.Path") as mock_path:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path.return_value = mock_path_instance

            result = runner.invoke(app, ["template", "edit"])

        # Verify sync was called after edit
        assert mock_sync_templates.called

    @patch("cafe.ui.cli.subprocess.run")
    @patch("cafe.ui.init_helpers.sync_templates")
    @patch("cafe.ui.cli.prompt_list")
    @patch("cafe.templates.manager.get_global_cafe_dir")
    def test_template_edit_does_not_sync_if_edit_fails(
        self,
        mock_get_global_cafe_dir: MagicMock,
        mock_prompt_list: MagicMock,
        mock_sync_templates: MagicMock,
        mock_subprocess_run: MagicMock,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Test that template edit does not trigger sync if edit fails."""
        # Setup global cafe directory
        global_cafe_dir = tmp_path / ".cafe_global"
        global_cafe_dir.mkdir()
        templates_dir = global_cafe_dir / "templates" / "plan"
        templates_dir.mkdir(parents=True)
        template_file = templates_dir / "my-template.md"
        template_file.write_text("test content")

        mock_get_global_cafe_dir.return_value = global_cafe_dir
        mock_prompt_list.side_effect = ["plan", "my-template"]
        # Simulate subprocess failure
        mock_subprocess_run.side_effect = Exception("Editor failed")

        result = runner.invoke(app, ["template", "edit"])

        # Verify sync was NOT called when edit fails
        assert not mock_sync_templates.called
