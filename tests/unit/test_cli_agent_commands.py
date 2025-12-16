"""測試 cafe agent 指令集的 CLI 介面。"""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from typer.testing import CliRunner

from cafe.ui.cli import app


@pytest.fixture
def runner():
    """建立 CLI runner。"""
    return CliRunner()


@pytest.fixture
def temp_cafe_dir(tmp_path):
    """建立暫時的 .cafe 目錄結構。"""
    cafe_dir = tmp_path / ".cafe"
    agents_dir = cafe_dir / "agents"

    # 建立角色目錄
    for role in ["pm", "developer", "reviewer"]:
        (agents_dir / role).mkdir(parents=True)

    return cafe_dir


class TestAgentLsCommand:
    """測試 cafe agent ls 指令。"""

    def test_agent_ls_with_no_agents(self, runner, temp_cafe_dir, monkeypatch):
        """測試沒有任何 agent 時的 ls 輸出。"""
        # 將 HOME 設定為 temp_cafe_dir 的父目錄
        monkeypatch.setenv("HOME", str(temp_cafe_dir.parent))

        result = runner.invoke(app, ["agent", "ls"])

        assert result.exit_code == 0
        assert "No agents found" in result.stdout

    def test_agent_ls_with_multiple_agents(self, runner, temp_cafe_dir, monkeypatch):
        """測試有多個 agents 時按角色分類列出。"""
        # 將 HOME 設定為 temp_cafe_dir 的父目錄
        monkeypatch.setenv("HOME", str(temp_cafe_dir.parent))

        # 建立測試用的 agent 檔案
        agents_dir = temp_cafe_dir / "agents"

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

        result = runner.invoke(app, ["agent", "ls"])

        assert result.exit_code == 0
        # 驗證輸出包含角色分類
        assert "pm" in result.stdout.lower()
        assert "developer" in result.stdout.lower()
        assert "reviewer" in result.stdout.lower()
        # 驗證輸出包含 agent 名稱
        assert "Roger" in result.stdout
        assert "David" in result.stdout
        assert "John" in result.stdout
        assert "Richard" in result.stdout


class TestAgentRmCommand:
    """測試 cafe agent rm 指令。"""

    def test_agent_rm_success(self, runner, temp_cafe_dir, monkeypatch):
        """測試成功刪除 agent 檔案。"""
        # 將 HOME 設定為 temp_cafe_dir 的父目錄
        monkeypatch.setenv("HOME", str(temp_cafe_dir.parent))

        # 建立測試用的 agent 檔案
        agents_dir = temp_cafe_dir / "agents"
        agent_file = agents_dir / "developer" / "John.md"
        agent_file.write_text("---\nname: John\n---\nRules")

        # Mock typer.confirm 回傳 True
        with patch("typer.confirm", return_value=True):
            result = runner.invoke(app, ["agent", "rm", "developer/John.md"])

        assert result.exit_code == 0
        assert "deleted successfully" in result.stdout
        assert not agent_file.exists()

    def test_agent_rm_file_not_found(self, runner, temp_cafe_dir, monkeypatch):
        """測試刪除不存在的 agent 檔案。"""
        # 將 HOME 設定為 temp_cafe_dir 的父目錄
        monkeypatch.setenv("HOME", str(temp_cafe_dir.parent))

        result = runner.invoke(app, ["agent", "rm", "developer/NonExistent.md"])

        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    def test_agent_rm_user_cancels(self, runner, temp_cafe_dir, monkeypatch):
        """測試使用者取消刪除操作。"""
        # 將 HOME 設定為 temp_cafe_dir 的父目錄
        monkeypatch.setenv("HOME", str(temp_cafe_dir.parent))

        # 建立測試用的 agent 檔案
        agents_dir = temp_cafe_dir / "agents"
        agent_file = agents_dir / "developer" / "John.md"
        agent_file.write_text("---\nname: John\n---\nRules")

        # Mock typer.confirm 回傳 False
        with patch("typer.confirm", return_value=False):
            result = runner.invoke(app, ["agent", "rm", "developer/John.md"])

        assert result.exit_code == 0
        assert "cancelled" in result.stdout.lower()
        assert agent_file.exists()  # 檔案仍然存在


class TestAgentCreateCommand:
    """測試 cafe agent create 指令。"""

    def test_agent_create_success(self, runner, temp_cafe_dir, monkeypatch):
        """測試成功建立 agent 檔案。"""
        # 將 HOME 設定為 temp_cafe_dir 的父目錄
        monkeypatch.setenv("HOME", str(temp_cafe_dir.parent))

        # Mock inquirer.prompt 回傳使用者輸入
        with patch("inquirer.prompt") as mock_prompt:
            mock_prompt.side_effect = [
                {"role": "developer"},
                {"name": "Michael"},
                {"description": "A senior Rust developer"},
            ]

            # Mock subprocess.run for editor
            with patch("subprocess.run") as mock_run:
                # Create temp file with code of conduct
                import tempfile
                import os

                with tempfile.NamedTemporaryFile(mode="w+", delete=False) as tf:
                    tf.write("Always write safe Rust code")
                    temp_path = tf.name

                def side_effect(cmd, **kwargs):
                    # Write to the temp file when "editor" is called
                    if len(cmd) >= 2:
                        with open(cmd[1], "w") as f:
                            f.write("Always write safe Rust code")
                    return Mock(returncode=0)

                mock_run.side_effect = side_effect

                result = runner.invoke(app, ["agent", "create"])

                # Clean up temp file
                try:
                    os.unlink(temp_path)
                except:
                    pass

        assert result.exit_code == 0
        assert "created successfully" in result.stdout

        # 驗證檔案內容
        agent_file = temp_cafe_dir / "agents" / "developer" / "Michael.md"
        assert agent_file.exists()

        content = agent_file.read_text()
        assert "name: Michael" in content
        assert "description: A senior Rust developer" in content
        assert "Always write safe Rust code" in content

    def test_agent_create_file_already_exists(self, runner, temp_cafe_dir, monkeypatch):
        """測試建立已存在的 agent 檔案。"""
        # 將 HOME 設定為 temp_cafe_dir 的父目錄
        monkeypatch.setenv("HOME", str(temp_cafe_dir.parent))

        # 建立已存在的 agent 檔案
        agents_dir = temp_cafe_dir / "agents"
        agent_file = agents_dir / "developer" / "Michael.md"
        agent_file.write_text("---\nname: Michael\n---\nExisting")

        # Mock inquirer.prompt 回傳使用者輸入
        with patch("inquirer.prompt") as mock_prompt:
            mock_prompt.side_effect = [
                {"role": "developer"},
                {"name": "Michael"},
            ]

            result = runner.invoke(app, ["agent", "create"])

        assert result.exit_code == 1
        assert "already exists" in result.stdout
