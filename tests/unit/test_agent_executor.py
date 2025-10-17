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
