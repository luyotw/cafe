"""Tests for sync helper functions."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.ui.init_helpers import sync_agents, sync_templates


@pytest.fixture
def temp_cafe_dir(tmp_path):
    """Create a temporary .cafe directory."""
    cafe_dir = tmp_path / ".cafe"
    cafe_dir.mkdir(parents=True)
    return cafe_dir


class TestSyncAgents:
    """Tests for sync_agents helper function."""

    @patch("cafe.ui.init_helpers.copy_agents_to_local")
    def test_sync_agents_returns_counts(
        self,
        mock_copy_agents: MagicMock,
        temp_cafe_dir: Path,
    ) -> None:
        """Test that sync_agents returns correct success and failure counts."""
        mock_copy_agents.return_value = [
            ("agents/pm/Roger.md", "system default", True),
            ("agents/developer/Nick.md", "custom", True),
            ("agents/reviewer/Richard.md", "system default", False),
        ]

        success_count, fail_count = sync_agents(temp_cafe_dir)

        assert success_count == 2
        assert fail_count == 1
        mock_copy_agents.assert_called_once_with(temp_cafe_dir)

    @patch("cafe.ui.init_helpers.copy_agents_to_local")
    def test_sync_agents_with_all_success(
        self,
        mock_copy_agents: MagicMock,
        temp_cafe_dir: Path,
    ) -> None:
        """Test sync_agents with all successful copies."""
        mock_copy_agents.return_value = [
            ("agents/pm/Roger.md", "system default", True),
            ("agents/developer/Nick.md", "custom", True),
        ]

        success_count, fail_count = sync_agents(temp_cafe_dir)

        assert success_count == 2
        assert fail_count == 0

    @patch("cafe.ui.init_helpers.copy_agents_to_local")
    def test_sync_agents_with_no_agents(
        self,
        mock_copy_agents: MagicMock,
        temp_cafe_dir: Path,
    ) -> None:
        """Test sync_agents with no agents to sync."""
        mock_copy_agents.return_value = []

        success_count, fail_count = sync_agents(temp_cafe_dir)

        assert success_count == 0
        assert fail_count == 0


class TestSyncTemplates:
    """Tests for sync_templates helper function."""

    @patch("cafe.ui.init_helpers.copy_templates_to_local")
    def test_sync_templates_returns_counts(
        self,
        mock_copy_templates: MagicMock,
        temp_cafe_dir: Path,
    ) -> None:
        """Test that sync_templates returns correct success and failure counts."""
        mock_copy_templates.return_value = [
            ("templates/plan/default.md", "system default", True),
            ("templates/spec/detailed.md", "custom", True),
            ("templates/plan/custom.md", "custom", False),
        ]

        success_count, fail_count = sync_templates(temp_cafe_dir)

        assert success_count == 2
        assert fail_count == 1
        mock_copy_templates.assert_called_once_with(temp_cafe_dir)

    @patch("cafe.ui.init_helpers.copy_templates_to_local")
    def test_sync_templates_with_all_success(
        self,
        mock_copy_templates: MagicMock,
        temp_cafe_dir: Path,
    ) -> None:
        """Test sync_templates with all successful copies."""
        mock_copy_templates.return_value = [
            ("templates/plan/default.md", "system default", True),
        ]

        success_count, fail_count = sync_templates(temp_cafe_dir)

        assert success_count == 1
        assert fail_count == 0

    @patch("cafe.ui.init_helpers.copy_templates_to_local")
    def test_sync_templates_with_no_templates(
        self,
        mock_copy_templates: MagicMock,
        temp_cafe_dir: Path,
    ) -> None:
        """Test sync_templates with no templates to sync."""
        mock_copy_templates.return_value = []

        success_count, fail_count = sync_templates(temp_cafe_dir)

        assert success_count == 0
        assert fail_count == 0
