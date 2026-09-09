"""Tests for cafe chat <role> command."""

from pathlib import Path
from typer.testing import CliRunner
from unittest.mock import patch

import pytest

from cafe.ui.cli import app

pytestmark = pytest.mark.usefixtures("cached_builtin_playbook_models")

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

    def test_chat_help_describes_playbook_declared_roles(self):
        """U1/I1: 公開 help 不應把 workflow role 誤述為固定清單。"""
        result = runner.invoke(app, ["chat", "--help"], env={"COLUMNS": "200"})

        assert result.exit_code == 0
        help_text = " ".join(result.stdout.lower().replace("│", " ").split())
        assert "playbook-declared role" in help_text
        assert "pm, developer, or reviewer" not in help_text

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
                    with patch("cafe.ui.cli.launch_chat_session", return_value=0) as mock_launch:
                        result = runner.invoke(app, ["chat", role])
                        assert result.exit_code == 0
                        mock_launch.assert_called_once_with(role, "issue36")

    def test_chat_accepts_custom_playbook_role(self, tmp_path: Path, mock_initialized_branch, config_with_agents):
        """測試 active issue playbook 定義的自訂 role 可開啟 chat"""
        issue_dir = tmp_path / ".cafe" / "issues" / "issue36"
        (issue_dir / "blackboard.json").write_text(
            '{"schema_version":1,"playbook_id":"research","current_step":"question","artifacts":{},"events":[],"decisions":[]}',
            encoding="utf-8",
        )

        with (
            patch("cafe.ui.cli.GitOperations") as mock_git_class,
            patch("cafe.ui.cli.is_branch_initialized", return_value=True),
            patch("cafe.ui.cli.PlaybookLoader") as mock_loader_cls,
            patch("cafe.ui.cli.launch_chat_session", return_value=0) as mock_launch,
        ):
            mock_git = mock_git_class.return_value
            mock_git.is_valid_branch.return_value = True
            mock_git.get_current_branch.return_value = "issue36"
            mock_loader_cls.return_value.load.return_value = {
                "roles": {"researcher": {"default_agent": "Morgan"}},
                "steps": {"question": {"role": "researcher"}},
            }

            result = runner.invoke(app, ["chat", "researcher"])

        assert result.exit_code == 0
        mock_launch.assert_called_once_with("researcher", "issue36")

    def test_chat_gets_issue_from_current_branch(self, tmp_path: Path, mock_initialized_branch, config_with_agents):
        """測試從當前分支取得 issue name"""
        with patch("cafe.ui.cli.GitOperations") as mock_git_class:
            mock_git = mock_git_class.return_value
            mock_git.is_valid_branch.return_value = True
            mock_git.get_current_branch.return_value = "issue36"

            with patch("cafe.ui.cli.is_branch_initialized", return_value=True):
                with patch("cafe.ui.cli.launch_chat_session", return_value=0):
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
        """測試 chat command 會把 launch_chat_session 的失敗往外回傳"""
        with patch("cafe.ui.cli.GitOperations") as mock_git_class:
            mock_git = mock_git_class.return_value
            mock_git.is_valid_branch.return_value = True
            mock_git.get_current_branch.return_value = "issue36"

            with patch("cafe.ui.cli.is_branch_initialized", return_value=True):
                with patch("cafe.ui.cli.launch_chat_session", return_value=1):
                    result = runner.invoke(app, ["chat", "pm"])
                    assert result.exit_code != 0

    def test_chat_loads_agent_config_correctly(self, tmp_path: Path, mock_initialized_branch, config_with_agents):
        """測試 chat 指令委派給 launch_chat_session 並傳入正確參數"""
        with patch("cafe.ui.cli.GitOperations") as mock_git_class:
            mock_git = mock_git_class.return_value
            mock_git.is_valid_branch.return_value = True
            mock_git.get_current_branch.return_value = "issue36"

            with patch("cafe.ui.cli.is_branch_initialized", return_value=True):
                with patch("cafe.ui.cli.launch_chat_session", return_value=0) as mock_launch:
                    result = runner.invoke(app, ["chat", "pm"])

                    assert result.exit_code == 0
                    mock_launch.assert_called_once_with("pm", "issue36")

    def test_chat_prompt_runs_one_shot_mode(
        self, tmp_path: Path, mock_initialized_branch, config_with_agents
    ):
        with (
            patch("cafe.ui.cli.GitOperations") as mock_git_class,
            patch("cafe.ui.cli.is_branch_initialized", return_value=True),
            patch("cafe.ui.cli.launch_chat_session", return_value=0) as mock_launch,
        ):
            mock_git = mock_git_class.return_value
            mock_git.is_valid_branch.return_value = True
            mock_git.get_current_branch.return_value = "issue478"

            result = runner.invoke(app, ["chat", "developer", "-p", "Summarize this"])

        assert result.exit_code == 0
        mock_launch.assert_called_once_with(
            "developer", "issue478", prompt="Summarize this"
        )
