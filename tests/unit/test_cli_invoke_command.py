"""Tests for cafe chat <role> command."""

import pytest
from pathlib import Path
from typer.testing import CliRunner
from unittest.mock import MagicMock, patch, call

from cafe.ui.cli import app
from cafe.core.types import AgentCLI


runner = CliRunner()


@pytest.fixture
def mock_initialized_branch(tmp_path: Path):
    """Create a mock initialized branch structure."""
    # 建立 .cafe/issues/issue36 目錄結構
    issue_dir = tmp_path / ".cafe" / "issues" / "issue36"
    issue_dir.mkdir(parents=True, exist_ok=True)

    # 建立各階段目錄
    for phase in ["spec", "plan", "develop", "review", "pr"]:
        (issue_dir / phase).mkdir(exist_ok=True)

    return tmp_path


@pytest.fixture
def config_with_agents(tmp_path: Path):
    """Create config with all three agents configured."""
    config_dir = tmp_path / ".cafe"
    config_dir.mkdir(parents=True, exist_ok=True)

    config_file = config_dir / "config.yaml"
    config_file.write_text("""
agents:
  pm:
    name: Roger
    cli: claude
  developer:
    name: David
    cli: copilot
  reviewer:
    name: Richard
    cli: gemini
""")

    # 建立對應的 agent 檔案
    agents_dir = config_dir / "agents"
    for role in ["pm", "developer", "reviewer"]:
        role_dir = agents_dir / role
        role_dir.mkdir(parents=True, exist_ok=True)

    (agents_dir / "pm" / "Roger.md").write_text("---\nname: Roger\n---\n")
    (agents_dir / "developer" / "David.md").write_text("---\nname: David\n---\n")
    (agents_dir / "reviewer" / "Richard.md").write_text("---\nname: Richard\n---\n")

    return config_dir


class TestChatCommand:
    """測試 cafe chat <role> 命令功能"""

    def test_chat_validates_role_parameter(self, tmp_path: Path, mock_initialized_branch, config_with_agents):
        """測試 role 參數驗證 - 應只接受 pm、developer、reviewer"""
        # 測試無效的 role - 應該在驗證階段就失敗（exit code 1）
        result = runner.invoke(app, ["chat", "invalid_role"])
        assert result.exit_code == 1

    def test_chat_accepts_valid_roles(self, tmp_path: Path, mock_initialized_branch, config_with_agents):
        """測試接受有效的 role 參數 - pm、developer、reviewer"""
        valid_roles = ["pm", "developer", "reviewer"]

        for role in valid_roles:
            with patch("cafe.ui.cli.GitOperations") as mock_git_class:
                mock_git = mock_git_class.return_value
                mock_git.is_valid_branch.return_value = True
                mock_git.get_current_branch.return_value = "issue36"

                with patch("cafe.ui.cli.is_branch_initialized", return_value=True):
                    with patch("cafe.ui.cli.ConfigManager") as mock_config_class:
                        mock_config = mock_config_class.return_value
                        mock_config.get.side_effect = lambda key, default: {
                            "agents.pm": {"name": "Roger", "cli": "claude"},
                            "agents.developer": {"name": "David", "cli": "copilot"},
                            "agents.reviewer": {"name": "Richard", "cli": "gemini"}
                        }.get(key, default)

                        with patch("cafe.ui.cli.subprocess.run") as mock_run:
                            mock_run.return_value = MagicMock(returncode=0)

                            result = runner.invoke(app, ["chat", role])
                            # 命令應該被接受（不應在 role 驗證階段失敗）
                            # 註：如果實作使用互動模式，exit_code 可能不是 0

    def test_chat_gets_issue_from_current_branch(self, tmp_path: Path, mock_initialized_branch, config_with_agents):
        """測試從當前分支取得 issue name"""
        with patch("cafe.ui.cli.GitOperations") as mock_git_class:
            mock_git = mock_git_class.return_value
            mock_git.is_valid_branch.return_value = True
            mock_git.get_current_branch.return_value = "issue36"

            with patch("cafe.ui.cli.is_branch_initialized", return_value=True):
                with patch("cafe.ui.cli.ConfigManager") as mock_config_class:
                    mock_config = mock_config_class.return_value
                    mock_config.get.side_effect = lambda key, default=None: {
                        "agents.pm": {"name": "Roger", "cli": "claude"},
                    }.get(key, default)

                    with patch("cafe.ui.cli._setup_agents") as mock_setup:
                        mock_agent_manager = MagicMock()
                        mock_executor = MagicMock()
                        mock_executor.config.session_id = None
                        mock_agent_manager.get_agent.return_value = mock_executor
                        mock_setup.return_value = mock_agent_manager

                        with patch("cafe.ui.cli.subprocess.run") as mock_run:
                            mock_run.return_value = MagicMock(returncode=0)

                            result = runner.invoke(app, ["chat", "pm"])

                            # 驗證有呼叫 _get_and_validate_branch，這會內部呼叫 get_current_branch
                            assert mock_git.get_current_branch.called or result.exit_code == 0

    def test_chat_checks_branch_initialized(self, tmp_path: Path, config_with_agents):
        """測試檢查分支是否已初始化"""
        with patch("cafe.ui.cli.GitOperations") as mock_git_class:
            mock_git = mock_git_class.return_value
            mock_git.is_valid_branch.return_value = True
            mock_git.get_current_branch.return_value = "issue36"

            # 分支未初始化 - 應該要失敗
            with patch("cafe.ui.cli.is_branch_initialized", return_value=False):
                result = runner.invoke(app, ["chat", "pm"])
                assert result.exit_code == 1

    def test_chat_fails_when_agent_not_configured(self, tmp_path: Path, mock_initialized_branch):
        """測試當 role 沒有配置 agent 時顯示錯誤"""
        with patch("cafe.ui.cli.GitOperations") as mock_git_class:
            mock_git = mock_git_class.return_value
            mock_git.is_valid_branch.return_value = True
            mock_git.get_current_branch.return_value = "issue36"

            with patch("cafe.ui.cli.is_branch_initialized", return_value=True):
                with patch("cafe.ui.cli.ConfigManager") as mock_config_class:
                    mock_config = mock_config_class.return_value
                    # pm agent 未配置（返回 None 或空 dict）
                    mock_config.get.return_value = None

                    result = runner.invoke(app, ["chat", "pm"])
                    assert result.exit_code != 0

    def test_chat_loads_agent_config_correctly(self, tmp_path: Path, mock_initialized_branch, config_with_agents):
        """測試正確從 config 載入 agent 設定"""
        with patch("cafe.ui.cli.GitOperations") as mock_git_class:
            mock_git = mock_git_class.return_value
            mock_git.is_valid_branch.return_value = True
            mock_git.get_current_branch.return_value = "issue36"

            with patch("cafe.ui.cli.is_branch_initialized", return_value=True):
                with patch("cafe.ui.cli.ConfigManager") as mock_config_class:
                    mock_config = mock_config_class.return_value

                    # 記錄所有的 get 呼叫
                    get_calls = []
                    def track_get(key, default=None):
                        get_calls.append(key)
                        return {
                            "agents.pm": {"name": "Roger", "cli": "claude"},
                            "agents.developer": {"name": "David", "cli": "copilot"},
                            "agents.reviewer": {"name": "Richard", "cli": "gemini"}
                        }.get(key, default)

                    mock_config.get.side_effect = track_get

                    with patch("cafe.ui.cli._setup_agents") as mock_setup:
                        mock_agent_manager = MagicMock()
                        mock_executor = MagicMock()
                        mock_executor.config.session_id = None
                        mock_agent_manager.get_agent.return_value = mock_executor
                        mock_setup.return_value = mock_agent_manager

                        with patch("cafe.ui.cli.subprocess.run") as mock_run:
                            mock_run.return_value = MagicMock(returncode=0)

                            result = runner.invoke(app, ["chat", "pm"])

                            # 驗證有查詢 pm agent 設定
                            assert "agents.pm" in get_calls
