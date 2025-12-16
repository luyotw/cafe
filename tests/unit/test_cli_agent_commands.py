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
