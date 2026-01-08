"""測試 cafe agent 指令集 CLI 介面."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from typer.testing import CliRunner

from cafe.ui.cli import app


@pytest.fixture
def runner():
    """建立 CLI runner."""
    return CliRunner()


@pytest.fixture
def temp_global_dir(tmp_path):
    """建立暫時全域 .cafe 目錄結構."""
    global_cafe_dir = tmp_path / ".cafe"
    agents_dir = global_cafe_dir / "agents"

    # 建立角色目錄
    for role in ["pm", "developer", "reviewer"]:
        (agents_dir / role).mkdir(parents=True)

    return global_cafe_dir


class TestAgentLsCommand:
    """測試 cafe agent ls 指令."""

    def test_agent_ls_with_no_custom_agents(self, runner, temp_global_dir):
        """測試沒有自訂 agent 時顯示系統預設 agents."""
        # Mock Path.home() 返回 temp 目錄 (沒有自訂 agents)
        with patch("cafe.utils.config.Path.home", return_value=temp_global_dir.parent):
            result = runner.invoke(app, ["agent", "ls"])

        assert result.exit_code == 0
        # 應該會列出系統預設的 agents，不會顯示 "No agents found"
        # 至少會有一些系統預設的 agents
        assert "Available Agents" in result.stdout or "pm" in result.stdout or "developer" in result.stdout or "reviewer" in result.stdout

    def test_agent_ls_with_multiple_agents(self, runner, temp_global_dir):
        """測試有多個 agents 時按角色分類列出."""
        # 建立測試用 agent 檔案
        agents_dir = temp_global_dir / "agents"

        # PM agents
        (agents_dir / "pm" / "Roger.md").write_text(
            "---\nname: Roger\ndescription: PM agent\n---\n\nRules here"
        )

        # Developer agents
        (agents_dir / "developer" / "David.md").write_text(
            "---\nname: David\ndescription: Developer agent\n---\n\nRules here"
        )
        (agents_dir / "developer" / "John.md").write_text(
            "---\nname: John\ndescription: Another developer\n---\n\nRules here"
        )

        # Reviewer agents
        (agents_dir / "reviewer" / "Richard.md").write_text(
            "---\nname: Richard\ndescription: Reviewer agent\n---\n\nRules here"
        )

        # Mock Path.home() 返回 temp 目錄
        with patch("cafe.utils.config.Path.home", return_value=temp_global_dir.parent):
            result = runner.invoke(app, ["agent", "ls"])

        assert result.exit_code == 0
        # 驗證輸出包含角色分類
        assert "pm" in result.stdout.lower()
        assert "developer" in result.stdout.lower()
        assert "reviewer" in result.stdout.lower()
        # 驗證輸出包含 agent 名稱，且自訂 agents 有 (custom) 標記
        assert "Roger (custom)" in result.stdout
        assert "David (custom)" in result.stdout
        assert "John (custom)" in result.stdout
        assert "Richard (custom)" in result.stdout


class TestAgentRmCommand:
    """測試 cafe agent rm 指令."""

    def test_agent_rm_success(self, runner, temp_global_dir):
        """測試成功刪除 agent 檔案."""
        # 建立測試用 agent 檔案
        agents_dir = temp_global_dir / "agents"
        agent_file = agents_dir / "developer" / "John.md"
        agent_file.write_text("---\nname: John\n---\nRules")

        # Mock Path.home() and prompt_list and prompt_confirm
        with patch("cafe.utils.config.Path.home", return_value=temp_global_dir.parent), \
             patch("cafe.ui.cli.prompt_list") as mock_prompt_list, \
             patch("cafe.ui.cli.prompt_confirm", return_value=True):
            # 模擬使用者選擇角色and agent
            mock_prompt_list.side_effect = ["developer", "John.md"]

            result = runner.invoke(app, ["agent", "rm"])

        assert result.exit_code == 0
        assert "deleted successfully" in result.stdout
        assert not agent_file.exists()

    def test_agent_rm_file_not_found(self, runner, temp_global_dir):
        """測試刪除不存在 agent 檔案（選擇 role 沒有任何 agent）."""
        # Mock Path.home() and prompt_list 選擇一個沒有 agents  role
        with patch("cafe.utils.config.Path.home", return_value=temp_global_dir.parent), \
             patch("cafe.ui.cli.prompt_list") as mock_prompt_list:
            mock_prompt_list.return_value = "developer"

            result = runner.invoke(app, ["agent", "rm"])

        assert result.exit_code == 1
        assert "no agents found" in result.stdout.lower()

    def test_agent_rm_user_cancels(self, runner, temp_global_dir):
        """測試使用者取消刪除操作."""
        # 建立測試用 agent 檔案
        agents_dir = temp_global_dir / "agents"
        agent_file = agents_dir / "developer" / "John.md"
        agent_file.write_text("---\nname: John\n---\nRules")

        # Mock Path.home() and prompt_list and typer.confirm (回傳 False)
        with patch("cafe.utils.config.Path.home", return_value=temp_global_dir.parent), \
             patch("cafe.ui.cli.prompt_list") as mock_prompt_list, \
             patch("typer.confirm", return_value=False):
            # 模擬使用者選擇角色and agent
            mock_prompt_list.side_effect = ["developer", "John.md"]

            result = runner.invoke(app, ["agent", "rm"])

        assert result.exit_code == 0
        assert "cancelled" in result.stdout.lower()
        assert agent_file.exists()  # 檔案仍然存在


class TestAgentCreateCommand:
    """測試 cafe agent create 指令."""

    def test_agent_create_success(self, runner, temp_global_dir):
        """測試成功建立 agent 檔案."""
        # Mock Path.home() and prompt_list and prompt_text
        with patch("cafe.utils.config.Path.home", return_value=temp_global_dir.parent), \
             patch("cafe.ui.cli.prompt_list") as mock_prompt_list, \
             patch("cafe.ui.cli.prompt_text") as mock_prompt_text:
            # 模擬使用者選擇角色and輸入 name/description
            mock_prompt_list.return_value = "developer"
            mock_prompt_text.side_effect = ["Michael", "A senior Rust developer"]

            # Mock subprocess.run for editor
            with patch("subprocess.run") as mock_run:

                def side_effect(cmd, **kwargs):
                    # Write to the temp file when "editor" is called
                    if len(cmd) >= 2:
                        # Read existing content and append custom code
                        with open(cmd[1], "r") as f:
                            existing_content = f.read()
                        with open(cmd[1], "w") as f:
                            f.write(existing_content + "\nAlways write safe Rust code\n")
                    return Mock(returncode=0)

                mock_run.side_effect = side_effect

                result = runner.invoke(app, ["agent", "create"])

        assert result.exit_code == 0
        assert "created successfully" in result.stdout

        # 驗證檔案內容
        agent_file = temp_global_dir / "agents" / "developer" / "Michael.md"
        assert agent_file.exists()

        content = agent_file.read_text()
        assert "name: Michael" in content
        assert "description: A senior Rust developer" in content
        assert "Always write safe Rust code" in content

    def test_agent_create_file_already_exists(self, runner, temp_global_dir):
        """測試建立已存在 agent 檔案."""
        # 建立已存在 agent 檔案
        agents_dir = temp_global_dir / "agents"
        agent_file = agents_dir / "developer" / "Michael.md"
        agent_file.write_text("---\nname: Michael\n---\nExisting")

        # Mock Path.home() and prompt_list and prompt_text
        with patch("cafe.utils.config.Path.home", return_value=temp_global_dir.parent), \
             patch("cafe.ui.cli.prompt_list") as mock_prompt_list, \
             patch("cafe.ui.cli.prompt_text") as mock_prompt_text:
            mock_prompt_list.return_value = "developer"
            mock_prompt_text.side_effect = ["Michael", "A developer"]

            result = runner.invoke(app, ["agent", "create"])

        assert result.exit_code == 1
        assert "already exists" in result.stdout
        assert "cafe agent edit" in result.stdout


class TestAgentEditCommand:
    """測試 cafe agent edit 指令."""

    def test_agent_edit_success(self, runner, temp_global_dir):
        """測試成功編輯 agent 檔案."""
        # 建立測試用 agent 檔案
        agents_dir = temp_global_dir / "agents"
        pm_dir = agents_dir / "pm"
        pm_dir.mkdir(parents=True, exist_ok=True)
        (pm_dir / "Roger.md").write_text("---\nname: Roger\n---\nPM rules")

        dev_dir = agents_dir / "developer"
        dev_dir.mkdir(parents=True, exist_ok=True)
        (dev_dir / "David.md").write_text("---\nname: David\n---\nDev rules")

        # Mock Path.home() and prompt_list
        with patch("cafe.utils.config.Path.home", return_value=temp_global_dir.parent), \
             patch("cafe.ui.cli.prompt_list") as mock_prompt_list:
            # 模擬使用者選擇角色and agent
            mock_prompt_list.side_effect = ["developer", "David.md"]

            # Mock subprocess.run for editor
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = Mock(returncode=0)

                result = runner.invoke(app, ["agent", "edit"])

        assert result.exit_code == 0
        assert "updated successfully" in result.stdout or "Updated" in result.stdout


class TestAgentCatCommand:
    """測試 cafe agent cat 指令."""

    def test_agent_cat_with_flags(self, runner, temp_global_dir):
        """測試使用 --role 和 --name 參數查看 agent."""
        # 建立測試用 agent 檔案
        agents_dir = temp_global_dir / "agents"
        agent_content = "---\nname: TestAgent\ndescription: Test agent\n---\n\nAgent rules here"
        (agents_dir / "developer" / "TestAgent.md").write_text(agent_content)

        # Mock Path.home() and subprocess.run
        with patch("cafe.utils.config.Path.home", return_value=temp_global_dir.parent), \
             patch("subprocess.run") as mock_run:
            # Mock less command failure to trigger fallback
            mock_run.side_effect = FileNotFoundError()

            result = runner.invoke(app, ["agent", "cat", "--role", "developer", "--name", "TestAgent"])

        assert result.exit_code == 0
        assert "Agent rules here" in result.stdout

    def test_agent_cat_interactive_mode(self, runner, temp_global_dir):
        """測試互動式模式查看 agent."""
        # 建立測試用 agent 檔案
        agents_dir = temp_global_dir / "agents"
        agent_content = "---\nname: InteractiveAgent\ndescription: Interactive test\n---\n\nContent"
        (agents_dir / "pm" / "InteractiveAgent.md").write_text(agent_content)

        # Mock Path.home(), prompt_list, and subprocess.run
        with patch("cafe.utils.config.Path.home", return_value=temp_global_dir.parent), \
             patch("cafe.ui.cli.prompt_list") as mock_prompt_list, \
             patch("subprocess.run") as mock_run:
            # 模擬使用者選擇角色和 agent
            mock_prompt_list.side_effect = ["pm", "InteractiveAgent (custom)"]
            # Mock less command failure to trigger fallback
            mock_run.side_effect = FileNotFoundError()

            result = runner.invoke(app, ["agent", "cat"])

        assert result.exit_code == 0
        assert "Content" in result.stdout

    def test_agent_cat_nonexistent_agent(self, runner, temp_global_dir):
        """測試查看不存在的 agent."""
        # Mock Path.home()
        with patch("cafe.utils.config.Path.home", return_value=temp_global_dir.parent):
            result = runner.invoke(app, ["agent", "cat", "--role", "developer", "--name", "NonExistent"])

        assert result.exit_code == 1
        assert "not found" in result.stdout

    def test_agent_cat_invalid_role(self, runner, temp_global_dir):
        """測試使用無效的角色."""
        # Mock Path.home()
        with patch("cafe.utils.config.Path.home", return_value=temp_global_dir.parent):
            result = runner.invoke(app, ["agent", "cat", "--role", "invalid", "--name", "Test"])

        assert result.exit_code == 1
        assert "Invalid role" in result.stdout

    def test_agent_cat_no_agents_found(self, runner, temp_global_dir):
        """測試當角色下沒有任何 agents 時."""
        # Mock Path.home() and list_available_agents to return empty list
        with patch("cafe.utils.config.Path.home", return_value=temp_global_dir.parent), \
             patch("cafe.ui.cli.prompt_list") as mock_prompt_list, \
             patch("cafe.ui.init_helpers.list_available_agents", return_value=[]):
            mock_prompt_list.return_value = "developer"

            result = runner.invoke(app, ["agent", "cat"])

        assert result.exit_code == 1
        assert "No agents found" in result.stdout
