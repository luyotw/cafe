"""測試 _ensure_default_content 函數"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.ui.cli import _ensure_default_content


@pytest.fixture
def temp_cafe_dir(tmp_path):
    """創建臨時 .cafe 目錄"""
    cafe_dir = tmp_path / ".cafe"
    cafe_dir.mkdir(parents=True)
    return cafe_dir


class TestEnsureDefaultContent:
    """測試 _ensure_default_content 函數 - 複製 agent 和 template 到本地"""

    @patch("cafe.ui.cli.copy_templates_to_local")
    @patch("cafe.ui.cli.copy_agents_to_local")
    def test_ensure_default_content_calls_copy_functions(
        self,
        mock_copy_agents: MagicMock,
        mock_copy_templates: MagicMock,
        temp_cafe_dir: Path,
    ) -> None:
        """測試 _ensure_default_content 會呼叫複製函式"""
        mock_copy_agents.return_value = []
        mock_copy_templates.return_value = []

        _ensure_default_content(temp_cafe_dir)

        mock_copy_agents.assert_called_once_with(temp_cafe_dir)
        mock_copy_templates.assert_called_once_with(temp_cafe_dir)

    @patch("cafe.ui.cli.copy_templates_to_local")
    @patch("cafe.ui.cli.copy_agents_to_local")
    def test_ensure_default_content_creates_agents_and_templates(
        self,
        mock_copy_agents: MagicMock,
        mock_copy_templates: MagicMock,
        temp_cafe_dir: Path,
    ) -> None:
        """測試複製成功時建立 agents 和 templates 目錄"""
        # 模擬複製結果
        mock_copy_agents.return_value = [
            ("agents/pm/Roger.md", "system default", True),
            ("agents/developer/David.md", "custom", True),
        ]
        mock_copy_templates.return_value = [
            ("templates/plan/default.md", "system default", True),
        ]

        _ensure_default_content(temp_cafe_dir)

        # 函式應該正常完成（無例外）
        mock_copy_agents.assert_called_once()
        mock_copy_templates.assert_called_once()

    @patch("cafe.ui.cli.copy_templates_to_local")
    @patch("cafe.ui.cli.copy_agents_to_local")
    def test_ensure_default_content_handles_partial_failures(
        self,
        mock_copy_agents: MagicMock,
        mock_copy_templates: MagicMock,
        temp_cafe_dir: Path,
    ) -> None:
        """測試部分複製失敗時不影響後續操作"""
        # 模擬部分失敗
        mock_copy_agents.return_value = [
            ("agents/pm/Roger.md", "system default", True),
            ("agents/pm/Custom.md", "custom", False),  # 複製失敗
        ]
        mock_copy_templates.return_value = [
            ("templates/plan/default.md", "system default", True),
        ]

        # 應該不拋出例外
        _ensure_default_content(temp_cafe_dir)

        mock_copy_agents.assert_called_once()
        mock_copy_templates.assert_called_once()
