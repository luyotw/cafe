"""測試 AgentExecutor 的路徑格式處理。

確保：
1. Git ignore 格式的路徑（/.cafe/...）正確傳遞給 agent CLI
2. 絕對路徑正確轉換為 git ignore 格式
3. 不同 CLI (Claude, Gemini, Copilot) 正確處理路徑
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

from cafe.agents.executor import AgentExecutor
from cafe.core.types import AgentConfig, AgentCLI


class TestTranslateToolNamesWithPaths:
    """測試 _translate_tool_names() 處理帶路徑的工具"""

    def test_preserves_git_ignore_format_paths(self):
        """Git ignore 格式路徑應該被保留"""
        config = AgentConfig(name="Test", cli=AgentCLI.CLAUDE)
        executor = AgentExecutor(config)

        tools = [
            "write(/.cafe/issues/test/spec/spec_001.md)",
            "edit(/.cafe/issues/test/spec/spec_001.md)",
        ]

        result = executor._translate_tool_names(tools)

        # Claude 使用大寫工具名稱，路徑應該保持不變
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

        # Gemini 工具名稱轉換，路徑保留
        assert result == [
            "write_file(/.cafe/issues/test/spec.md)",
            "replace(/.cafe/issues/test/spec.md)",
            "read_file(/.cafe/issues/test/spec.md)",
        ]


class TestClaudePathProcessing:
    """測試 Claude 執行器的路徑處理邏輯"""

    @patch("cafe.agents.executor.AgentExecutor._execute_with_streaming")
    @patch("cafe.agents.executor.AgentExecutor._create_new_session")
    @patch("os.getcwd")
    def test_relative_paths_converted_to_git_ignore_format(self, mock_getcwd, mock_create_session, mock_execute):
        """Claude 應該將普通相對路徑轉換為 git ignore 格式"""
        # Setup
        mock_getcwd.return_value = "/Users/me/repo"
        mock_create_session.return_value = "test-session-123"
        mock_execute.return_value = MagicMock(
            response="Test response",
            cli_command_args=[],
        )

        config = AgentConfig(name="Test", cli=AgentCLI.CLAUDE)
        executor = AgentExecutor(config)

        # 使用普通相對路徑的 allowed_tools（Phase 傳來的格式）
        allowed_tools = [
            "Write(.cafe/issues/test/spec/spec_001.md)",
            "Edit(.cafe/issues/test/spec/spec_001.md)",
        ]

        # Execute
        executor._execute_claude("Test prompt", allowed_tools)

        # Assert：檢查傳遞給 _execute_with_streaming 的命令
        call_args = mock_execute.call_args
        cmd = call_args.kwargs["cmd"]

        # 找到 --allowed-tools 參數
        allowed_tools_idx = cmd.index("--allowed-tools")
        tools_arg = cmd[allowed_tools_idx + 1]

        # 應該被轉換為 git ignore 格式（加上前綴 /）
        assert "/.cafe/issues/test/spec/spec_001.md" in tools_arg
        # 不應該包含普通相對路徑
        assert "Write(.cafe" not in tools_arg
        assert "Edit(.cafe" not in tools_arg
        # 不應該包含絕對路徑
        assert "/Users/me/repo/.cafe" not in tools_arg

    @patch("cafe.agents.executor.AgentExecutor._execute_with_streaming")
    @patch("cafe.agents.executor.AgentExecutor._create_new_session")
    @patch("cafe.agents.executor.get_repo_root")
    @patch("cafe.agents.executor.to_git_ignore_path")
    def test_absolute_paths_converted_to_git_ignore_format(
        self, mock_to_git_ignore, mock_get_repo, mock_create_session, mock_execute
    ):
        """絕對路徑應該被轉換為 git ignore 格式"""
        # Setup: Mock git utils functions
        mock_get_repo.return_value = Path("/Users/me/repo")
        mock_to_git_ignore.side_effect = lambda p, r: f"/.cafe/issues/test/spec/{p.name}"

        mock_create_session.return_value = "test-session-123"
        mock_execute.return_value = MagicMock(
            response="Test response",
            cli_command_args=[],
        )

        config = AgentConfig(name="Test", cli=AgentCLI.CLAUDE)
        executor = AgentExecutor(config)

        # 使用絕對路徑的 allowed_tools
        allowed_tools = [
            "Write(/Users/me/repo/.cafe/issues/test/spec/spec_001.md)",
            "Edit(/Users/me/repo/.cafe/issues/test/spec/spec_001.md)",
        ]

        # Execute
        executor._execute_claude("Test prompt", allowed_tools)

        # Assert
        call_args = mock_execute.call_args
        cmd = call_args.kwargs["cmd"]

        allowed_tools_idx = cmd.index("--allowed-tools")
        tools_arg = cmd[allowed_tools_idx + 1]

        # 絕對路徑應該被轉換為 git ignore 格式
        assert "/.cafe/issues/test/spec/spec_001.md" in tools_arg
        # 不應該包含絕對路徑
        assert "/Users/me/repo/.cafe" not in tools_arg


class TestGeminiPathProcessing:
    """測試 Gemini 執行器的路徑處理"""

    @patch("cafe.agents.executor.AgentExecutor._execute_with_streaming")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    def test_strips_path_from_write_file_tool(self, mock_read_text, mock_exists, mock_execute):
        """Gemini 的 write_file 應該移除路徑參數，replace 保持普通相對路徑"""
        # Setup
        mock_exists.return_value = True
        mock_read_text.return_value = "!/.cafe\n"
        mock_execute.return_value = MagicMock(
            response="Test response",
            cli_command_args=[],
        )

        config = AgentConfig(name="Test", cli=AgentCLI.GEMINI)
        executor = AgentExecutor(config)

        # 使用普通相對路徑（Phase 傳來的格式）
        allowed_tools = [
            "write_file(.cafe/issues/test/spec.md)",
            "replace(.cafe/issues/test/spec.md)",
        ]

        # Execute
        executor._execute_gemini("Test prompt", allowed_tools)

        # Assert
        call_args = mock_execute.call_args
        # Gemini 使用位置參數傳遞 cmd
        cmd = call_args.args[0] if call_args.args else call_args.kwargs.get("cmd")

        allowed_tools_idx = cmd.index("--allowed-tools")
        tools_arg = cmd[allowed_tools_idx + 1]

        # write_file 路徑應該被移除，replace 保持普通相對路徑（不轉換為 git ignore format）
        assert tools_arg == "write_file,replace(.cafe/issues/test/spec.md)"
        # 不應該被轉換為 git ignore format
        assert "/.cafe" not in tools_arg


class TestCopilotPathProcessing:
    """測試 Copilot 執行器的路徑處理"""

    @patch("cafe.agents.executor.AgentExecutor._execute_with_streaming")
    @patch("pathlib.Path.exists")
    def test_passes_tools_directly_with_relative_paths(self, mock_exists, mock_execute):
        """Copilot 應該直接傳遞普通相對路徑，不轉換為 git ignore format"""
        # Setup
        mock_exists.return_value = True
        mock_execute.return_value = MagicMock(
            response="Test response",
            cli_command_args=[],
        )

        config = AgentConfig(name="Test", cli=AgentCLI.COPILOT)
        executor = AgentExecutor(config)

        # 使用普通相對路徑（Phase 傳來的格式）
        allowed_tools = [
            "write(.cafe/issues/test/spec.md)",
            "write(.cafe/issues/test/plan.md)",
        ]

        # Execute
        executor._execute_copilot("Test prompt", allowed_tools)

        # Assert
        call_args = mock_execute.call_args
        cmd = call_args.kwargs["cmd"]

        # 應該有兩個 --allow-tool 參數，保持普通相對路徑
        assert cmd.count("--allow-tool") == 2
        assert "write(.cafe/issues/test/spec.md)" in cmd
        assert "write(.cafe/issues/test/plan.md)" in cmd
        # 不應該被轉換為 git ignore format
        assert "write(/.cafe" not in str(cmd)
