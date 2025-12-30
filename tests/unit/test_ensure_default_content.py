"""測試 _ensure_default_content 函數"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import shutil

import pytest

from cafe.ui.commands.init_commands import _ensure_default_content


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
    """測試 _ensure_default_content 函數"""

    def test_copies_agents_from_package_data(self, temp_cafe_dir, package_data_dir, monkeypatch):
        """測試從 package data 目錄複製 agents"""
        # Mock __file__ to point to our test package
        mock_cli_file = package_data_dir.parent / "ui" / "cli.py"
        mock_cli_file.parent.mkdir(parents=True)
        mock_cli_file.touch()

        with patch('cafe.ui.cli.Path') as MockPath:
            # Make Path() calls work normally
            MockPath.side_effect = lambda x: Path(x)

            # But make Path(__file__) return our mock location
            mock_file_path = MagicMock()
            mock_file_path.parent.parent = package_data_dir.parent
            MockPath.__file__ = str(mock_cli_file)

            # Patch __file__ directly in the module scope
            with patch('cafe.ui.cli.__file__', str(mock_cli_file)):
                _ensure_default_content(temp_cafe_dir)

        # Verify agents directory was created
        agents_dir = temp_cafe_dir / "agents"
        assert agents_dir.exists()
        assert (agents_dir / "pm" / "Roger.md").exists()
        assert (agents_dir / "developer" / "David.md").exists()
        assert (agents_dir / "reviewer" / "Richard.md").exists()

    def test_copies_templates_from_package_data(self, temp_cafe_dir, package_data_dir):
        """測試從 package data 目錄複製 templates"""
        mock_cli_file = package_data_dir.parent / "ui" / "cli.py"
        mock_cli_file.parent.mkdir(parents=True)
        mock_cli_file.touch()

        with patch('cafe.ui.cli.__file__', str(mock_cli_file)):
            _ensure_default_content(temp_cafe_dir)

        # Verify templates directory was created
        templates_dir = temp_cafe_dir / "templates"
        assert templates_dir.exists()
        assert (templates_dir / "plan" / "default.md").exists()

    def test_falls_back_to_repo_root_if_package_data_missing(self, temp_cafe_dir, tmp_path, monkeypatch):
        """測試當 package data 不存在時, 從 repo root 複製"""
        # Change to temp directory
        monkeypatch.chdir(tmp_path)

        # Create repo root agents directory (no package data)
        repo_agents = tmp_path / "agents"
        (repo_agents / "pm").mkdir(parents=True)
        (repo_agents / "pm" / "Roger.md").write_text("# Repo Roger")

        # Mock __file__ to point to a location without data directory
        mock_cli_file = tmp_path / "no_data" / "ui" / "cli.py"
        mock_cli_file.parent.mkdir(parents=True)
        mock_cli_file.touch()

        with patch('cafe.ui.cli.__file__', str(mock_cli_file)):
            _ensure_default_content(temp_cafe_dir)

        # Verify agents were copied from repo root
        agents_dir = temp_cafe_dir / "agents"
        assert agents_dir.exists()
        assert (agents_dir / "pm" / "Roger.md").exists()
        assert (agents_dir / "pm" / "Roger.md").read_text() == "# Repo Roger"

    def test_skips_if_agents_already_exist(self, temp_cafe_dir, package_data_dir):
        """測試當 agents 目錄已存在時不會覆蓋"""
        # Pre-create agents directory with custom content
        agents_dir = temp_cafe_dir / "agents"
        agents_dir.mkdir()
        custom_file = agents_dir / "custom.md"
        custom_file.write_text("# Custom Agent")

        mock_cli_file = package_data_dir.parent / "ui" / "cli.py"
        mock_cli_file.parent.mkdir(parents=True)
        mock_cli_file.touch()

        with patch('cafe.ui.cli.__file__', str(mock_cli_file)):
            _ensure_default_content(temp_cafe_dir)

        # Verify existing content not overwritten
        assert custom_file.exists()
        assert custom_file.read_text() == "# Custom Agent"

        # Verify package data not copied
        assert not (agents_dir / "pm" / "Roger.md").exists()

    def test_skips_if_templates_already_exist(self, temp_cafe_dir, package_data_dir):
        """測試當 templates 目錄已存在時不會覆蓋"""
        # Pre-create templates directory with custom content
        templates_dir = temp_cafe_dir / "templates"
        templates_dir.mkdir()
        custom_file = templates_dir / "custom.md"
        custom_file.write_text("# Custom Template")

        mock_cli_file = package_data_dir.parent / "ui" / "cli.py"
        mock_cli_file.parent.mkdir(parents=True)
        mock_cli_file.touch()

        with patch('cafe.ui.cli.__file__', str(mock_cli_file)):
            _ensure_default_content(temp_cafe_dir)

        # Verify existing content not overwritten
        assert custom_file.exists()
        assert custom_file.read_text() == "# Custom Template"

        # Verify package data not copied
        assert not (templates_dir / "plan" / "default.md").exists()

    def test_handles_missing_both_package_and_repo_data(self, temp_cafe_dir, tmp_path, monkeypatch):
        """測試當 package data and repo root 都沒有資料時, 不會報錯"""
        # Change to temp directory with no agents/templates
        monkeypatch.chdir(tmp_path)

        # Mock __file__ to point to a location without data directory
        mock_cli_file = tmp_path / "no_data" / "ui" / "cli.py"
        mock_cli_file.parent.mkdir(parents=True)
        mock_cli_file.touch()

        # Should not raise any exception
        with patch('cafe.ui.cli.__file__', str(mock_cli_file)):
            _ensure_default_content(temp_cafe_dir)

        # Verify no directories were created
        assert not (temp_cafe_dir / "agents").exists()
        assert not (temp_cafe_dir / "templates").exists()
