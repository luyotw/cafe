"""Tests for AgentExecutor."""

import pytest
from unittest.mock import MagicMock, patch

from aaf.agents.executor import AgentExecutor, AgentExecutionError
from aaf.core.types import AgentConfig, AgentCLI, TokenUsage


class TestAgentExecutorBasics:
    """Test basic AgentExecutor functionality."""

    def test_init_with_config(self) -> None:
        """測試使用 AgentConfig 初始化 AgentExecutor"""
        config = AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        executor = AgentExecutor(config)

        assert executor.config == config
        assert executor.config.name == "Roger"
        assert executor.config.cli == AgentCLI.CLAUDE

    def test_execute_with_prompt(self) -> None:
        """測試執行 agent 並取得回應"""
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        executor = AgentExecutor(config)

        with patch.object(executor, "_execute_claude") as mock_execute:
            mock_execute.return_value = ("Agent response", TokenUsage())

            response, token_usage = executor.execute("Test prompt")

            assert response == "Agent response"
            assert isinstance(token_usage, TokenUsage)
            mock_execute.assert_called_once_with("Test prompt", None)


class TestAgentExecutorWithSession:
    """Test AgentExecutor with session management."""

    def test_uses_session_id_from_config(self) -> None:
        """測試使用 config 中的 session ID"""
        config = AgentConfig(
            name="Roger",
            cli=AgentCLI.CLAUDE,
            session_id="session-123"
        )
        executor = AgentExecutor(config)

        assert executor.config.session_id == "session-123"

    def test_execute_with_session(self) -> None:
        """測試帶 session 執行 agent"""
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            session_id="session-456"
        )
        executor = AgentExecutor(config)

        with patch.object(executor, "_execute_claude") as mock_execute:
            mock_execute.return_value = ("Response with session", TokenUsage())

            response, token_usage = executor.execute("Prompt with session")

            assert response == "Response with session"
            assert isinstance(token_usage, TokenUsage)
            # Verify session was used in execution
            mock_execute.assert_called_once()


class TestAgentExecutorErrorHandling:
    """Test AgentExecutor error handling."""

    def test_execute_raises_execution_error_on_failure(self) -> None:
        """測試 agent 執行失敗時拋出 AgentExecutionError"""
        config = AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        executor = AgentExecutor(config)

        with patch.object(executor, "_execute_claude") as mock_execute:
            mock_execute.side_effect = Exception("Agent failed")

            with pytest.raises(AgentExecutionError, match="Agent execution failed"):
                executor.execute("Test prompt")

    def test_execution_error_contains_original_error(self) -> None:
        """測試 AgentExecutionError 包含原始錯誤資訊"""
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE)
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
            cli=AgentCLI.CLAUDE,
            session_id="test-session"
        )
        executor = AgentExecutor(config)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout='{"result": "Claude response"}',
                returncode=0
            )

            response, token_usage = executor._execute_claude("Test prompt")

            assert response == "Claude response"
            assert isinstance(token_usage, TokenUsage)
            mock_run.assert_called_once()

    def test_execute_claude_failure(self) -> None:
        """測試 Claude 執行失敗時拋出錯誤"""
        config = AgentConfig(
            name="Roger",
            cli=AgentCLI.CLAUDE,
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
            cli=AgentCLI.CLAUDE,
            session_id="test-session"
        )
        executor = AgentExecutor(config)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="Plain text response",
                returncode=0
            )

            response, token_usage = executor._execute_claude("Test prompt")

            assert response == "Plain text response"
            assert isinstance(token_usage, TokenUsage)
            assert token_usage.input_tokens == 0

    def test_execute_claude_session_already_in_use_creates_new_session(self) -> None:
        """測試當 session 已被使用時，自動創建新 session 並重試"""
        config = AgentConfig(
            name="Roger",
            cli=AgentCLI.CLAUDE,
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
            response, token_usage = executor._execute_claude("Test prompt")

            # Should have called run 3 times:
            # 1. Initial attempt (fails with "already in use")
            # 2. Create new session
            # 3. Retry with new session
            assert mock_run.call_count == 3
            assert response == "Success with new session"
            assert isinstance(token_usage, TokenUsage)
            # Session ID should be updated
            assert executor.config.session_id == "new-session-123"

    def test_create_new_session_success(self) -> None:
        """測試成功創建新 session"""
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE)
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
        config = AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
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

    def test_execute_gemini_calls_cli(self) -> None:
        """測試執行 Gemini 會呼叫 gemini CLI with streaming"""
        config = AgentConfig(name="Roger", cli=AgentCLI.GEMINI)
        executor = AgentExecutor(config)

        with patch("subprocess.Popen") as mock_popen:
            # Mock process
            mock_process = MagicMock()
            mock_process.stdout.readline.side_effect = [
                '{"chunk": "Gemini "}\n',
                '{"chunk": "response"}\n',
                '{"response": "Gemini response"}\n',
                '',  # EOF
            ]
            mock_process.stderr.read.return_value = ""
            mock_process.wait.return_value = 0
            mock_popen.return_value = mock_process

            response, token_usage = executor._execute_gemini("Test prompt")

            assert response == "Gemini response"
            assert isinstance(token_usage, TokenUsage)
            mock_popen.assert_called_once()
            # Verify command structure
            call_args = mock_popen.call_args[0][0]
            assert "gemini" in call_args
            assert "Test prompt" in call_args
            assert "--output-format" in call_args
            assert "streaming-json" in call_args

    def test_execute_with_gemini_tool(self) -> None:
        """測試使用 Gemini tool 執行"""
        config = AgentConfig(name="Roger", cli=AgentCLI.GEMINI)
        executor = AgentExecutor(config)

        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.stdout.readline.side_effect = [
                '{"response": "Hi there"}\n',
                '',
            ]
            mock_process.stderr.read.return_value = ""
            mock_process.wait.return_value = 0
            mock_popen.return_value = mock_process

            response, token_usage = executor.execute("Test prompt")

            assert response == "Hi there"
            assert isinstance(token_usage, TokenUsage)

    def test_execute_gemini_failure(self) -> None:
        """測試 Gemini 執行失敗時拋出錯誤"""
        config = AgentConfig(name="Roger", cli=AgentCLI.GEMINI)
        executor = AgentExecutor(config)

        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.stdout.readline.return_value = ''
            mock_process.stderr.read.return_value = "Error: API key not found"
            mock_process.wait.return_value = 1
            mock_popen.return_value = mock_process

            with pytest.raises(AgentExecutionError, match="Gemini execution failed"):
                executor._execute_gemini("Test prompt")

    def test_execute_gemini_non_json_response(self) -> None:
        """測試 Gemini 回傳非 JSON 格式時返回原始輸出"""
        config = AgentConfig(name="Roger", cli=AgentCLI.GEMINI)
        executor = AgentExecutor(config)

        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.stdout.readline.side_effect = [
                "Plain text response\n",
                '',
            ]
            mock_process.stderr.read.return_value = ""
            mock_process.wait.return_value = 0
            mock_popen.return_value = mock_process

            response, token_usage = executor._execute_gemini("Test prompt")

            assert response == "Plain text response\n"
            assert isinstance(token_usage, TokenUsage)


class TestCursorExecution:
    """Test Cursor-specific execution."""

    def test_execute_cursor_not_implemented(self) -> None:
        """測試 Cursor 執行目前尚未實作"""
        config = AgentConfig(name="David", cli=AgentCLI.CURSOR)
        executor = AgentExecutor(config)

        with pytest.raises(NotImplementedError, match="Cursor execution not yet implemented"):
            executor._execute_cursor("Test prompt")

    def test_execute_with_cursor_tool(self) -> None:
        """測試使用 Cursor tool 執行會呼叫 _execute_cursor 並拋出 AgentExecutionError"""
        config = AgentConfig(name="David", cli=AgentCLI.CURSOR)
        executor = AgentExecutor(config)

        with pytest.raises(AgentExecutionError, match="Cursor execution not yet implemented"):
            executor.execute("Test prompt")


class TestTokenUsageTracking:
    """Test token usage tracking functionality."""

    def test_execute_returns_token_usage(self) -> None:
        """測試 execute 回傳 token usage 資訊"""
        config = AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        executor = AgentExecutor(config)

        mock_claude_output = {
            "result": "Test response",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 200,
                "cache_read_input_tokens": 300
            },
            "total_cost_usd": 0.05
        }

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=str(mock_claude_output).replace("'", '"'),
                returncode=0
            )

            response, token_usage = executor.execute("Test prompt")

            assert response == "Test response"
            assert isinstance(token_usage, TokenUsage)
            assert token_usage.input_tokens == 100
            assert token_usage.output_tokens == 50
            assert token_usage.cache_creation_input_tokens == 200
            assert token_usage.cache_read_input_tokens == 300
            assert token_usage.total_cost_usd == 0.05

    def test_execute_without_usage_data_returns_empty_token_usage(self) -> None:
        """測試當 CLI 沒有回傳 usage 資料時，回傳空的 TokenUsage"""
        config = AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        executor = AgentExecutor(config)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout='{"result": "Response without usage"}',
                returncode=0
            )

            response, token_usage = executor.execute("Test prompt")

            assert response == "Response without usage"
            assert isinstance(token_usage, TokenUsage)
            assert token_usage.input_tokens == 0
            assert token_usage.output_tokens == 0
            assert token_usage.total_cost_usd == 0.0

    def test_get_total_token_usage_sums_across_calls(self) -> None:
        """測試 get_total_token_usage 會累計所有呼叫的 token usage"""
        config = AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        executor = AgentExecutor(config)

        with patch("subprocess.run") as mock_run:
            # First call
            mock_run.return_value = MagicMock(
                stdout='{"result": "First", "usage": {"input_tokens": 10, "output_tokens": 5}, "total_cost_usd": 0.01}',
                returncode=0
            )
            executor.execute("First prompt")

            # Second call
            mock_run.return_value = MagicMock(
                stdout='{"result": "Second", "usage": {"input_tokens": 20, "output_tokens": 10}, "total_cost_usd": 0.02}',
                returncode=0
            )
            executor.execute("Second prompt")

            total_usage = executor.get_total_token_usage()

            assert total_usage.input_tokens == 30
            assert total_usage.output_tokens == 15
            assert total_usage.total_cost_usd == 0.03
