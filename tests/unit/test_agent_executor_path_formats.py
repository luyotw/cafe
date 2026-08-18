"""測試 AgentExecutor 路徑格式處理.

確保：
1. 專案根目錄格式路徑（/.cafe/...）正確轉換為 Claude 絕對權限格式
2. 絕對路徑正確轉換為 Claude 絕對權限格式
3. 不同 CLI (Claude, Gemini, Copilot) 正確處理路徑
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

from cafe.agents.executor import AgentExecutor
from cafe.core.types import AgentConfig, AgentCLI


class TestTranslateToolNamesWithPaths:
    """測試 _translate_tool_names() 處理帶路徑工具"""

    def test_preserves_git_ignore_format_paths(self):
        """Git ignore 格式路徑應該被保留"""
        config = AgentConfig(name="Test", cli=AgentCLI.CLAUDE)
        executor = AgentExecutor(config)

        tools = [
            "write(/.cafe/issues/test/spec/spec_001.md)",
            "edit(/.cafe/issues/test/spec/spec_001.md)",
        ]

        result = executor._translate_tool_names(tools)

        # Claude 使用大寫工具名稱, 路徑應該保持不變
        assert result == [
            "Write(/.cafe/issues/test/spec/spec_001.md)",
            "Edit(/.cafe/issues/test/spec/spec_001.md)",
        ]

    def test_preserves_relative_paths(self):
        """相對路徑應該被保留"""
        config = AgentConfig(name="Test", cli=AgentCLI.CLAUDE)
        executor = AgentExecutor(config)

        tools = [
            "write(.cafe/issues/test/spec.md)",
            "edit(src/main.py)",
        ]

        result = executor._translate_tool_names(tools)

        assert result == [
            "Write(.cafe/issues/test/spec.md)",
            "Edit(src/main.py)",
        ]

    def test_translates_tool_names_for_gemini(self):
        """Gemini 應該轉換工具名稱但保留路徑"""
        config = AgentConfig(name="Test", cli=AgentCLI.GEMINI)
        executor = AgentExecutor(config)

        tools = [
            "write(/.cafe/issues/test/spec.md)",
            "edit(/.cafe/issues/test/spec.md)",
            "read(/.cafe/issues/test/spec.md)",
        ]

        result = executor._translate_tool_names(tools)

        # Gemini 工具名稱轉換, 路徑保留
        # Note: edit 也轉換為 write_file, 因為 Gemini CLI 不支援 replace
        assert result == [
            "write_file(/.cafe/issues/test/spec.md)",
            "write_file(/.cafe/issues/test/spec.md)",
            "read_file(/.cafe/issues/test/spec.md)",
        ]


class TestClaudePathProcessing:
    """測試 Claude 執行器路徑處理邏輯"""

    @patch("cafe.agents.cli.claude.get_git_toplevel")
    def test_relative_paths_converted_to_absolute_permission_format(self, mock_get_toplevel):
        """Claude 應將相對路徑轉為不受 cwd 影響的絕對權限格式"""
        from cafe.agents.cli.claude import ClaudeCLI

        # Setup
        mock_get_toplevel.return_value = Path("/Users/me/repo")

        config = AgentConfig(name="Test", cli=AgentCLI.CLAUDE, session_id="test-123")
        cli = ClaudeCLI(config)

        # 使用普通相對路徑 allowed_tools（Phase 傳來格式）
        allowed_tools = [
            "Write(.cafe/issues/test/spec/spec_001.md)",
            "Edit(.cafe/issues/test/spec/spec_001.md)",
            "Write(./src/main.py)",
        ]

        # Execute translate_allowed_tools
        result = cli.translate_allowed_tools(allowed_tools)

        assert "Write(//Users/me/repo/.cafe/issues/test/spec/spec_001.md)" in result
        assert "Edit(//Users/me/repo/.cafe/issues/test/spec/spec_001.md)" in result
        assert "Write(//Users/me/repo/src/main.py)" in result

    @patch("cafe.agents.cli.claude.get_git_toplevel")
    def test_absolute_paths_converted_to_absolute_permission_format(self, mock_get_toplevel):
        """worktree 內的絕對路徑應轉換為 Claude 的雙斜線格式"""
        from cafe.agents.cli.claude import ClaudeCLI

        mock_get_toplevel.return_value = Path("/Users/me/repo")

        config = AgentConfig(name="Test", cli=AgentCLI.CLAUDE)
        cli = ClaudeCLI(config)

        # 使用絕對路徑 allowed_tools
        allowed_tools = [
            "Write(/Users/me/repo/.cafe/issues/test/spec/spec_001.md)",
            "Edit(/Users/me/repo/.cafe/issues/test/spec/spec_001.md)",
        ]

        # Execute translate_allowed_tools
        result = cli.translate_allowed_tools(allowed_tools)

        assert "Write(//Users/me/repo/.cafe/issues/test/spec/spec_001.md)" in result
        assert "Edit(//Users/me/repo/.cafe/issues/test/spec/spec_001.md)" in result


class TestGeminiPathProcessing:
    """測試 Gemini 執行器路徑處理"""

    def test_strips_path_from_write_file_tool(self):
        """Gemini  write_file 應該移除路徑參數（因為 CLI 不支援路徑限制）"""
        from cafe.agents.cli.gemini import GeminiCLI

        config = AgentConfig(name="Test", cli=AgentCLI.GEMINI)
        cli = GeminiCLI(config)

        # 使用普通相對路徑（Phase 傳來格式）
        # Note: 這裡測試都用 write_file, 因為 Gemini 不支援 replace
        allowed_tools = [
            "write_file(.cafe/issues/test/spec.md)",
            "write_file(.cafe/issues/test/plan.md)",
        ]

        # Execute translate_allowed_tools
        result = cli.translate_allowed_tools(allowed_tools)

        # write_file 路徑應該被移除（去重後只保留一個 write_file）
        assert result == ["write_file"]
        # 不應該包含路徑
        assert not any(".cafe" in tool for tool in result)


class TestCopilotPathProcessing:
    """測試 Copilot 執行器路徑處理"""

    def test_passes_tools_directly_with_relative_paths(self):
        """Copilot 應該直接傳遞普通相對路徑, 不轉換為 git ignore format"""
        from cafe.agents.cli.copilot import CopilotCLI

        config = AgentConfig(name="Test", cli=AgentCLI.COPILOT)
        cli = CopilotCLI(config)

        # 使用普通相對路徑（Phase 傳來格式）
        allowed_tools = [
            "write(.cafe/issues/test/spec.md)",
            "write(.cafe/issues/test/plan.md)",
        ]

        # Execute translate_allowed_tools
        # Note: Copilot strips paths, so both become just "write" and deduplicate to 1
        result = cli.translate_allowed_tools(allowed_tools)

        # Copilot strips path parameters and deduplicates
        assert result == ["write"]

        # Now test that build_command creates correct flags
        cmd = cli.build_command("Test prompt", result, None)

        # Should have one --allow-tool parameter for the deduplicated "write"
        assert cmd.count("--allow-tool") == 1
        assert "write" in cmd
        # Paths should be stripped out
        assert ".cafe" not in str(cmd)
