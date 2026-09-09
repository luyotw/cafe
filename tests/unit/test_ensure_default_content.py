"""Tests for project default-content preparation."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.ui import cli
from cafe.ui.cli import _ensure_default_content


@pytest.fixture
def temp_cafe_dir(tmp_path: Path) -> Path:
    cafe_dir = tmp_path / ".cafe"
    cafe_dir.mkdir(parents=True)
    return cafe_dir


class TestEnsureDefaultContent:
    """Fallback agents remain dynamic while project templates are prepared."""

    @patch("cafe.ui.cli.copy_templates_to_local", return_value=[])
    def test_ensure_default_content_copies_templates_without_agent_snapshots(
        self,
        mock_copy_templates: MagicMock,
        temp_cafe_dir: Path,
    ) -> None:
        _ensure_default_content(temp_cafe_dir)

        mock_copy_templates.assert_called_once_with(temp_cafe_dir)
        assert not hasattr(cli, "copy_agents_to_local")
        assert not (temp_cafe_dir / "agents").exists()

    @patch("cafe.ui.cli.console")
    @patch("cafe.ui.cli.copy_templates_to_local")
    def test_ensure_default_content_reports_template_success(
        self,
        mock_copy_templates: MagicMock,
        mock_console: MagicMock,
        temp_cafe_dir: Path,
    ) -> None:
        mock_copy_templates.return_value = [
            ("templates/plan/default.md", "system default", True),
        ]

        _ensure_default_content(temp_cafe_dir)

        message = mock_console.print.call_args[0][0]
        assert "1 template(s)" in message
        assert "agent" not in message.lower()

    @patch("cafe.ui.cli.console")
    @patch("cafe.ui.cli.copy_templates_to_local")
    def test_ensure_default_content_reports_template_failures(
        self,
        mock_copy_templates: MagicMock,
        mock_console: MagicMock,
        temp_cafe_dir: Path,
    ) -> None:
        mock_copy_templates.return_value = [
            ("templates/plan/default.md", "system default", False),
        ]

        _ensure_default_content(temp_cafe_dir)

        assert mock_console.print.call_count == 1
        assert "Failed to copy 1 file(s)" in mock_console.print.call_args[0][0]
