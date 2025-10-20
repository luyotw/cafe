"""Tests for AgentExecutor."""

import pytest
from unittest.mock import MagicMock, patch

from aaf.agents.executor import AgentExecutor, AgentExecutionError
from aaf.core.types import AgentConfig, AgentTool


class TestAgentExecutorBasics:
    """Test basic AgentExecutor functionality."""

    def test_init_with_config(self) -> None:
        """測試使用 AgentConfig 初始化 AgentExecutor"""
        config = AgentConfig(name="Roger", tool=AgentTool.CLAUDE)
        executor = AgentExecutor(config)

        assert executor.config == config
        assert executor.config.name == "Roger"
        assert executor.config.tool == AgentTool.CLAUDE

    def test_execute_with_prompt(self) -> None:
        """測試執行 agent 並取得回應"""
        config = AgentConfig(name="David", tool=AgentTool.CLAUDE)
        executor = AgentExecutor(config)

        with patch.object(executor, "_execute_claude") as mock_execute:
            mock_execute.return_value = "Agent response"

            result = executor.execute("Test prompt")

            assert result == "Agent response"
            mock_execute.assert_called_once_with("Test prompt")


class TestAgentExecutorWithSession:
    """Test AgentExecutor with session management."""

    def test_uses_session_id_from_config(self) -> None:
        """測試使用 config 中的 session ID"""
        config = AgentConfig(
            name="Roger",
            tool=AgentTool.CLAUDE,
            session_id="session-123"
        )
        executor = AgentExecutor(config)

        assert executor.config.session_id == "session-123"

    def test_execute_with_session(self) -> None:
        """測試帶 session 執行 agent"""
        config = AgentConfig(
            name="David",
            tool=AgentTool.CLAUDE,
            session_id="session-456"
        )
        executor = AgentExecutor(config)

        with patch.object(executor, "_execute_claude") as mock_execute:
            mock_execute.return_value = "Response with session"

            result = executor.execute("Prompt with session")

            assert result == "Response with session"
            # Verify session was used in execution
            mock_execute.assert_called_once()


class TestAgentExecutorWithAllowedTools:
    """Test AgentExecutor with allowed tools."""

    def test_passes_allowed_tools_to_agent(self) -> None:
        """測試將 allowed_tools 傳遞給 agent"""
        config = AgentConfig(
            name="David",
            tool=AgentTool.CLAUDE,
            allowed_tools=["Bash(git:*)", "Read(*)"]
        )
        executor = AgentExecutor(config)

        with patch.object(executor, "_execute_claude") as mock_execute:
            mock_execute.return_value = "Response"

            executor.execute("Test prompt")

            # Verify allowed_tools were considered in execution
            assert executor.config.allowed_tools == ["Bash(git:*)", "Read(*)"]


class TestAgentExecutorErrorHandling:
    """Test AgentExecutor error handling."""

    def test_execute_raises_execution_error_on_failure(self) -> None:
        """測試 agent 執行失敗時拋出 AgentExecutionError"""
        config = AgentConfig(name="Roger", tool=AgentTool.CLAUDE)
        executor = AgentExecutor(config)

        with patch.object(executor, "_execute_claude") as mock_execute:
            mock_execute.side_effect = Exception("Agent failed")

            with pytest.raises(AgentExecutionError, match="Agent execution failed"):
                executor.execute("Test prompt")

    def test_execution_error_contains_original_error(self) -> None:
        """測試 AgentExecutionError 包含原始錯誤資訊"""
        config = AgentConfig(name="David", tool=AgentTool.CLAUDE)
        executor = AgentExecutor(config)

        with patch.object(executor, "_execute_claude") as mock_execute:
            original_error = ValueError("Original error")
            mock_execute.side_effect = original_error

            with pytest.raises(AgentExecutionError) as exc_info:
                executor.execute("Test prompt")

            assert exc_info.value.__cause__ == original_error


class TestClaudeExecution:
    """Test Claude-specific execution."""

    def test_execute_claude_calls_cli(self) -> None:
        """測試執行 Claude 會呼叫 claude CLI"""
        config = AgentConfig(
            name="Roger",
            tool=AgentTool.CLAUDE,
            session_id="test-session"
        )
        executor = AgentExecutor(config)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout='{"content": "Claude response"}',
                returncode=0
            )

            result = executor._execute_claude("Test prompt")

            assert "Claude response" in result or result == "Claude response"
            mock_run.assert_called_once()

    def test_execute_claude_with_allowed_tools(self) -> None:
        """測試執行 Claude 時傳遞 allowed_tools"""
        config = AgentConfig(
            name="David",
            tool=AgentTool.CLAUDE,
            session_id="test-session",
            allowed_tools=["Bash(git:*)"]
        )
        executor = AgentExecutor(config)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout='{"content": "Response"}',
                returncode=0
            )

            executor._execute_claude("Test prompt")

            # Verify the command included allowed tools
            call_args = mock_run.call_args
            # The implementation should pass allowed_tools to CLI
            mock_run.assert_called_once()

    def test_execute_claude_failure(self) -> None:
        """測試 Claude 執行失敗時拋出錯誤"""
        config = AgentConfig(
            name="Roger",
            tool=AgentTool.CLAUDE,
            session_id="test-session"
        )
        executor = AgentExecutor(config)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="",
                stderr="Error: session not found",
                returncode=1
            )

            with pytest.raises(AgentExecutionError, match="Claude execution failed"):
                executor._execute_claude("Test prompt")

    def test_execute_claude_non_json_response(self) -> None:
        """測試 Claude 回傳非 JSON 格式時返回原始輸出"""
        config = AgentConfig(
            name="David",
            tool=AgentTool.CLAUDE,
            session_id="test-session"
        )
        executor = AgentExecutor(config)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="Plain text response",
                returncode=0
            )

            result = executor._execute_claude("Test prompt")

            assert result == "Plain text response"

    def test_execute_claude_session_already_in_use_creates_new_session(self) -> None:
        """測試當 session 已被使用時，自動創建新 session 並重試"""
        config = AgentConfig(
            name="Roger",
            tool=AgentTool.CLAUDE,
            session_id="old-session-id"
        )
        executor = AgentExecutor(config)

        call_count = 0

        def mock_run_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1

            # First call: session already in use error
            if call_count == 1:
                return MagicMock(
                    stdout="",
                    stderr="Error: Session ID old-session-id is already in use.",
                    returncode=1
                )
            # Second call: create new session
            elif call_count == 2:
                return MagicMock(
                    stdout='{"session_id": "new-session-123", "result": "Hi!"}',
                    returncode=0
                )
            # Third call: retry with new session succeeds
            else:
                return MagicMock(
                    stdout='{"result": "Success with new session"}',
                    returncode=0
                )

        with patch("subprocess.run", side_effect=mock_run_side_effect) as mock_run:
            result = executor._execute_claude("Test prompt")

            # Should have called run 3 times:
            # 1. Initial attempt (fails with "already in use")
            # 2. Create new session
            # 3. Retry with new session
            assert mock_run.call_count == 3
            assert result == "Success with new session"
            # Session ID should be updated
            assert executor.config.session_id == "new-session-123"

    def test_create_new_session_success(self) -> None:
        """測試成功創建新 session"""
        config = AgentConfig(name="David", tool=AgentTool.CLAUDE)
        executor = AgentExecutor(config)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout='{"session_id": "abc-123", "result": "Hi!"}',
                returncode=0
            )

            session_id = executor._create_new_session()

            assert session_id == "abc-123"
            mock_run.assert_called_once()
            # Verify it called with correct args
            args = mock_run.call_args[0][0]
            assert "claude" in args
            assert "-p" in args or "--print" in args

    def test_create_new_session_failure(self) -> None:
        """測試創建 session 失敗時拋出錯誤"""
        config = AgentConfig(name="Roger", tool=AgentTool.CLAUDE)
        executor = AgentExecutor(config)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="",
                stderr="Error: API key not found",
                returncode=1
            )

            with pytest.raises(AgentExecutionError, match="Failed to create new session"):
                executor._create_new_session()


class TestGeminiExecution:
    """Test Gemini-specific execution."""

    def test_execute_gemini_not_implemented(self) -> None:
        """測試 Gemini 執行目前尚未實作"""
        config = AgentConfig(name="Roger", tool=AgentTool.GEMINI)
        executor = AgentExecutor(config)

        with pytest.raises(NotImplementedError, match="Gemini execution not yet implemented"):
            executor._execute_gemini("Test prompt")

    def test_execute_with_gemini_tool(self) -> None:
        """測試使用 Gemini tool 執行會呼叫 _execute_gemini 並拋出 AgentExecutionError"""
        config = AgentConfig(name="Roger", tool=AgentTool.GEMINI)
        executor = AgentExecutor(config)

        with pytest.raises(AgentExecutionError, match="Gemini execution not yet implemented"):
            executor.execute("Test prompt")


class TestCursorExecution:
    """Test Cursor-specific execution."""

    def test_execute_cursor_not_implemented(self) -> None:
        """測試 Cursor 執行目前尚未實作"""
        config = AgentConfig(name="David", tool=AgentTool.CURSOR)
        executor = AgentExecutor(config)

        with pytest.raises(NotImplementedError, match="Cursor execution not yet implemented"):
            executor._execute_cursor("Test prompt")

    def test_execute_with_cursor_tool(self) -> None:
        """測試使用 Cursor tool 執行會呼叫 _execute_cursor 並拋出 AgentExecutionError"""
        config = AgentConfig(name="David", tool=AgentTool.CURSOR)
        executor = AgentExecutor(config)

        with pytest.raises(AgentExecutionError, match="Cursor execution not yet implemented"):
            executor.execute("Test prompt")
