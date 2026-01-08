"""測試 _ensure_default_content 函數"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import shutil

import pytest

from cafe.ui.cli import _ensure_default_content


@pytest.fixture
def temp_cafe_dir(tmp_path):
    """創建臨時 .cafe 目錄"""
    cafe_dir = tmp_path / ".cafe"
    cafe_dir.mkdir(parents=True)
    return cafe_dir


@pytest.fixture
def package_data_dir(tmp_path):
    """創建模擬 package data 目錄"""
    # Create mock package data directory
    package_dir = tmp_path / "mock_package" / "data"
    package_dir.mkdir(parents=True)

    # Create agents directory
    agents_dir = package_dir / "agents"
    (agents_dir / "pm").mkdir(parents=True)
    (agents_dir / "pm" / "Roger.md").write_text("# Roger PM Agent")
    (agents_dir / "developer").mkdir(parents=True)
    (agents_dir / "developer" / "David.md").write_text("# David Developer Agent")
    (agents_dir / "reviewer").mkdir(parents=True)
    (agents_dir / "reviewer" / "Richard.md").write_text("# Richard Reviewer Agent")

    # Create templates directory
    templates_dir = package_dir / "templates"
    (templates_dir / "plan").mkdir(parents=True)
    (templates_dir / "plan" / "default.md").write_text("# Default Plan Template")

    return package_dir


class TestEnsureDefaultContent:
    """測試 _ensure_default_content 函數 - 現在是 no-op"""

    def test_ensure_default_content_is_noop(self, temp_cafe_dir):
        """測試 _ensure_default_content 現在是 no-op (不複製任何內容)"""
        # Call the function
        _ensure_default_content(temp_cafe_dir)

        # Verify no directories were created
        # (agents and templates are now managed globally at ~/.cafe/)
        assert not (temp_cafe_dir / "agents").exists()
        assert not (temp_cafe_dir / "templates").exists()
