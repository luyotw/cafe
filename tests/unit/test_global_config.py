"""測試全域配置目錄管理功能."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from cafe.utils.config import get_global_cafe_dir, ensure_global_directories


class TestGlobalConfig:
    """測試全域配置目錄功能."""

    def test_get_global_cafe_dir_returns_home_cafe(self) -> None:
        """測試 get_global_cafe_dir() 回傳 ~/.cafe 路徑."""
        result = get_global_cafe_dir()
        expected = Path.home() / ".cafe"
        assert result == expected

    def test_get_global_cafe_dir_creates_directory_if_not_exists(self) -> None:
        """測試 get_global_cafe_dir() 會自動創建目錄."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_home = Path(tmpdir)
            fake_cafe_dir = fake_home / ".cafe"
            
            # Ensure directory doesn't exist yet
            assert not fake_cafe_dir.exists()
            
            with patch("cafe.utils.config.Path.home", return_value=fake_home):
                result = get_global_cafe_dir()
                
                # Should create the directory
                assert result.exists()
                assert result.is_dir()
                assert result == fake_cafe_dir

    def test_ensure_global_directories_creates_agent_subdirectories(self) -> None:
        """測試 ensure_global_directories() 創建 agents 子目錄."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_home = Path(tmpdir)
            
            with patch("cafe.utils.config.Path.home", return_value=fake_home):
                ensure_global_directories()
                
                # Check agents subdirectories
                assert (fake_home / ".cafe" / "agents" / "pm").exists()
                assert (fake_home / ".cafe" / "agents" / "developer").exists()
                assert (fake_home / ".cafe" / "agents" / "reviewer").exists()

    def test_ensure_global_directories_creates_template_subdirectories(self) -> None:
        """測試 ensure_global_directories() 創建 templates 子目錄."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_home = Path(tmpdir)
            
            with patch("cafe.utils.config.Path.home", return_value=fake_home):
                ensure_global_directories()
                
                # Check templates subdirectories
                assert (fake_home / ".cafe" / "templates" / "plan").exists()
                assert (fake_home / ".cafe" / "templates" / "spec").exists()

    def test_ensure_global_directories_idempotent(self) -> None:
        """測試 ensure_global_directories() 可以重複呼叫."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_home = Path(tmpdir)
            
            with patch("cafe.utils.config.Path.home", return_value=fake_home):
                # Call twice
                ensure_global_directories()
                ensure_global_directories()
                
                # Should still work and directories should exist
                assert (fake_home / ".cafe" / "agents" / "pm").exists()
                assert (fake_home / ".cafe" / "templates" / "plan").exists()
