"""Tests for AgentExecutor."""

import pytest
from unittest.mock import MagicMock, patch

from cafe.agents.executor import AgentExecutor, AgentExecutionError
from cafe.core.types import AgentConfig, AgentCLI, AgentResponse, TokenUsage


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
            mock_execute.return_value = AgentResponse(
                response="Agent response",
                token_usage=TokenUsage()
            )

            agent_response = executor.execute("Test prompt")

            assert agent_response.response == "Agent response"
            assert isinstance(agent_response.token_usage, TokenUsage)
            assert agent_response.permission_denials == []
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
            mock_execute.return_value = AgentResponse(response="Response with session", token_usage=TokenUsage())

            agent_response = executor.execute("Prompt with session")

            assert agent_response.response == "Response with session"
            assert isinstance(agent_response.token_usage, TokenUsage)
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
        )
        executor = AgentExecutor(config)

        # Mock session creation
        mock_run_result = MagicMock()
        mock_run_result.stdout = '{"session_id": "new-session-123"}'
        mock_run_result.returncode = 0

        # Mock streaming process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"content": "Claude response"}\n',
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        with patch("subprocess.run", return_value=mock_run_result), \
             patch("subprocess.Popen", return_value=mock_process), \
             patch("sys.platform", "win32"):  # Skip select() on Windows
            agent_response = executor._execute_claude("Test prompt")

            assert agent_response.response == "Claude response"
            assert isinstance(agent_response.token_usage, TokenUsage)

    def test_execute_claude_failure(self) -> None:
        """測試 Claude 執行失敗時拋出錯誤"""
        config = AgentConfig(
            name="Roger",
            cli=AgentCLI.CLAUDE,
        )
        executor = AgentExecutor(config)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="",
                stderr="Error: session not found",
                returncode=1
            )

            with pytest.raises(AgentExecutionError, match="Failed to create new session"):
                executor._execute_claude("Test prompt")

    def test_execute_claude_non_json_response(self) -> None:
        """測試 Claude 回傳非 JSON 格式時返回原始輸出"""
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            session_id="test-session"
        )
        executor = AgentExecutor(config)

        # Mock streaming process with non-JSON output
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            "Plain text response\n",
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        with patch("subprocess.run", return_value=MagicMock(stdout='{"session_id": "test-session"}', returncode=0)), \
             patch("subprocess.Popen", return_value=mock_process), \
             patch("sys.platform", "win32"):
            agent_response = executor._execute_claude("Test prompt")

            # With streaming, non-JSON is treated as plain lines
            assert "Plain text response" in agent_response.response
            assert isinstance(agent_response.token_usage, TokenUsage)
            assert agent_response.token_usage.input_tokens == 0

    def test_execute_claude_session_already_in_use_raises_conflict_error(self) -> None:
        """測試當 session 已被使用時，拋出 AgentExecutionError"""
        config = AgentConfig(
            name="Roger",
            cli=AgentCLI.CLAUDE,
        )
        executor = AgentExecutor(config)

        # Mock session creation success
        mock_run_result = MagicMock(
            stdout='{"session_id": "new-session-123"}',
            returncode=0
        )

        # Mock Popen to simulate session in use error
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [""]
        mock_process.stderr.read.return_value = "Error: Session ID is already in use."
        mock_process.wait.return_value = 1

        with patch("subprocess.run", return_value=mock_run_result), \
             patch("subprocess.Popen", return_value=mock_process), \
             patch("sys.platform", "win32"):
            with pytest.raises(AgentExecutionError, match="Claude execution failed"):
                executor._execute_claude("Test prompt")

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

        with patch("subprocess.Popen") as mock_popen, \
             patch("sys.platform", "win32"):  # Skip select() on Windows
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

            agent_response = executor._execute_gemini("Test prompt")

            assert agent_response.response == "Gemini response"
            assert isinstance(agent_response.token_usage, TokenUsage)
            mock_popen.assert_called_once()
            # Verify command structure
            call_args = mock_popen.call_args[0][0]
            assert "gemini" in call_args
            assert "Test prompt" in call_args
            assert "--output-format" in call_args
            assert "stream-json" in call_args

    def test_execute_with_gemini_tool(self) -> None:
        """測試使用 Gemini tool 執行"""
        config = AgentConfig(name="Roger", cli=AgentCLI.GEMINI)
        executor = AgentExecutor(config)

        with patch("subprocess.Popen") as mock_popen, \
             patch("sys.platform", "win32"):  # Skip select() on Windows
            mock_process = MagicMock()
            mock_process.stdout.readline.side_effect = [
                '{"response": "Hi there"}\n',
                '',
            ]
            mock_process.stderr.read.return_value = ""
            mock_process.wait.return_value = 0
            mock_popen.return_value = mock_process

            agent_response = executor.execute("Test prompt")

            assert agent_response.response == "Hi there"
            assert isinstance(agent_response.token_usage, TokenUsage)

    def test_execute_gemini_failure(self) -> None:
        """測試 Gemini 執行失敗時拋出錯誤"""
        config = AgentConfig(name="Roger", cli=AgentCLI.GEMINI)
        executor = AgentExecutor(config)

        with patch("subprocess.Popen") as mock_popen, \
             patch("sys.platform", "win32"):  # Skip select() on Windows
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

        with patch("subprocess.Popen") as mock_popen, \
             patch("sys.platform", "win32"):  # Skip select() on Windows
            mock_process = MagicMock()
            mock_process.stdout.readline.side_effect = [
                "Plain text response\n",
                '',
            ]
            mock_process.stderr.read.return_value = ""
            mock_process.wait.return_value = 0
            mock_popen.return_value = mock_process

            agent_response = executor._execute_gemini("Test prompt")

            assert agent_response.response == "Plain text response\n"
            assert isinstance(agent_response.token_usage, TokenUsage)

    def test_execute_gemini_extracts_only_assistant_messages(self) -> None:
        """測試 Gemini 只提取 assistant 的 messages，過濾掉 tool output 和 user messages"""
        config = AgentConfig(name="Roger", cli=AgentCLI.GEMINI)
        executor = AgentExecutor(config)

        with patch("subprocess.Popen") as mock_popen, \
             patch("sys.platform", "win32"):  # Skip select() on Windows
            mock_process = MagicMock()
            # Simulate Gemini stream-json output with user message, tool output, and assistant messages
            mock_process.stdout.readline.side_effect = [
                '{"type":"message","role":"user","content":"User prompt"}\n',
                '{"type":"tool_result","output":"File content with CAFE_CONFIRMED in history"}\n',
                '{"type":"message","role":"assistant","content":"CAFE_NEED_CLARIFICATION\\n"}\n',
                '{"type":"message","role":"assistant","content":"Here is my response"}\n',
                '{"response": "Full response including tool output"}\n',  # Last line (final result)
                '',
            ]
            mock_process.stderr.read.return_value = ""
            mock_process.wait.return_value = 0
            mock_popen.return_value = mock_process

            agent_response = executor._execute_gemini("Test prompt")

            # Should only contain assistant messages, not user message or tool output
            assert agent_response.response == "CAFE_NEED_CLARIFICATION\nHere is my response"
            assert "User prompt" not in agent_response.response
            assert "File content" not in agent_response.response
            assert "CAFE_CONFIRMED" not in agent_response.response  # Should not pick up status from history
            assert isinstance(agent_response.token_usage, TokenUsage)


class TestCursorExecution:
    """Test Cursor-specific execution."""

    def test_execute_cursor_calls_cli(self) -> None:
        """測試執行 Cursor 會呼叫 cursor-agent CLI with streaming"""
        config = AgentConfig(name="David", cli=AgentCLI.CURSOR)
        executor = AgentExecutor(config)

        with patch("subprocess.Popen") as mock_popen, \
             patch("sys.platform", "win32"):
            # Mock process
            mock_process = MagicMock()
            mock_process.stdout.readline.side_effect = [
                '{"response": "Cursor response"}\n',
                '',  # EOF
            ]
            mock_process.stderr.read.return_value = ""
            mock_process.wait.return_value = 0
            mock_popen.return_value = mock_process

            agent_response = executor._execute_cursor("Test prompt")

            assert agent_response.response == "Cursor response"
            assert isinstance(agent_response.token_usage, TokenUsage)
            mock_popen.assert_called_once()
            # Verify command structure
            call_args = mock_popen.call_args[0][0]
            assert "cursor-agent" in call_args
            assert "-p" in call_args
            assert "Test prompt" in call_args
            assert "--output-format" in call_args
            assert "json" in call_args

    def test_execute_with_cursor_tool(self) -> None:
        """測試使用 Cursor tool 執行"""
        config = AgentConfig(name="David", cli=AgentCLI.CURSOR)
        executor = AgentExecutor(config)

        with patch("subprocess.Popen") as mock_popen, \
             patch("sys.platform", "win32"):
            mock_process = MagicMock()
            mock_process.stdout.readline.side_effect = [
                '{"response": "Hello from Cursor"}\n',
                '',
            ]
            mock_process.stderr.read.return_value = ""
            mock_process.wait.return_value = 0
            mock_popen.return_value = mock_process

            agent_response = executor.execute("Test prompt")

            assert agent_response.response == "Hello from Cursor"
            assert isinstance(agent_response.token_usage, TokenUsage)

    def test_execute_cursor_failure(self) -> None:
        """測試 Cursor 執行失敗時拋出錯誤"""
        config = AgentConfig(name="David", cli=AgentCLI.CURSOR)
        executor = AgentExecutor(config)

        with patch("subprocess.Popen") as mock_popen, \
             patch("sys.platform", "win32"):
            mock_process = MagicMock()
            mock_process.stdout.readline.return_value = ''
            mock_process.stderr.read.return_value = "Error: Connection failed"
            mock_process.wait.return_value = 1
            mock_popen.return_value = mock_process

            with pytest.raises(AgentExecutionError, match="Cursor execution failed"):
                executor._execute_cursor("Test prompt")

    def test_execute_cursor_non_json_response(self) -> None:
        """測試 Cursor 回傳非 JSON 格式時返回原始輸出"""
        config = AgentConfig(name="David", cli=AgentCLI.CURSOR)
        executor = AgentExecutor(config)

        with patch("subprocess.Popen") as mock_popen, \
             patch("sys.platform", "win32"):
            mock_process = MagicMock()
            mock_process.stdout.readline.side_effect = [
                "Plain text from Cursor\n",
                '',
            ]
            mock_process.stderr.read.return_value = ""
            mock_process.wait.return_value = 0
            mock_popen.return_value = mock_process

            agent_response = executor._execute_cursor("Test prompt")

            assert agent_response.response == "Plain text from Cursor\n"
            assert isinstance(agent_response.token_usage, TokenUsage)


class TestTokenUsageTracking:
    """Test token usage tracking functionality."""

    def test_execute_returns_token_usage(self) -> None:
        """測試 execute 回傳 token usage 資訊"""
        config = AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        executor = AgentExecutor(config)

        # Mock streaming process with usage data
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"content": "Test response"}\n',
            '{"usage": {"input_tokens": 100, "output_tokens": 50, "cache_creation_input_tokens": 200, "cache_read_input_tokens": 300}}\n',
            '{"total_cost_usd": 0.05}\n',
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        mock_run_result = MagicMock(stdout='{"session_id": "test-session"}', returncode=0)

        with patch("subprocess.run", return_value=mock_run_result), \
             patch("subprocess.Popen", return_value=mock_process), \
             patch("sys.platform", "win32"):
            agent_response = executor.execute("Test prompt")

            assert agent_response.response == "Test response"
            assert isinstance(agent_response.token_usage, TokenUsage)
            assert agent_response.token_usage.input_tokens == 100
            assert agent_response.token_usage.output_tokens == 50
            assert agent_response.token_usage.cache_creation_input_tokens == 200
            assert agent_response.token_usage.cache_read_input_tokens == 300
            assert agent_response.token_usage.total_cost_usd == 0.05

    def test_execute_without_usage_data_returns_empty_token_usage(self) -> None:
        """測試當 CLI 沒有回傳 usage 資料時，回傳空的 TokenUsage"""
        config = AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        executor = AgentExecutor(config)

        # Mock streaming process without usage data
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"content": "Response without usage"}\n',
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        mock_run_result = MagicMock(stdout='{"session_id": "test-session"}', returncode=0)

        with patch("subprocess.run", return_value=mock_run_result), \
             patch("subprocess.Popen", return_value=mock_process), \
             patch("sys.platform", "win32"):
            agent_response = executor.execute("Test prompt")

            assert agent_response.response == "Response without usage"
            assert isinstance(agent_response.token_usage, TokenUsage)
            assert agent_response.token_usage.input_tokens == 0
            assert agent_response.token_usage.output_tokens == 0
            assert agent_response.token_usage.total_cost_usd == 0.0

    def test_get_total_token_usage_sums_across_calls(self) -> None:
        """測試 get_total_token_usage 會累計所有呼叫的 token usage"""
        config = AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        executor = AgentExecutor(config)

        call_count = 0
        def mock_readline_side_effect():
            nonlocal call_count
            call_count += 1

            if call_count <= 3:  # First execute call
                return [
                    '{"content": "First"}\n',
                    '{"usage": {"input_tokens": 10, "output_tokens": 5}}\n',
                    '{"total_cost_usd": 0.01}\n',
                    "",
                ]
            else:  # Second execute call
                return [
                    '{"content": "Second"}\n',
                    '{"usage": {"input_tokens": 20, "output_tokens": 10}}\n',
                    '{"total_cost_usd": 0.02}\n',
                    "",
                ]

        mock_process = MagicMock()
        # Set up to be called twice
        mock_process.stdout.readline.side_effect = [
            '{"content": "First"}\n',
            '{"usage": {"input_tokens": 10, "output_tokens": 5}}\n',
            '{"total_cost_usd": 0.01}\n',
            "",
            '{"content": "Second"}\n',
            '{"usage": {"input_tokens": 20, "output_tokens": 10}}\n',
            '{"total_cost_usd": 0.02}\n',
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        mock_run_result = MagicMock(stdout='{"session_id": "test-session"}', returncode=0)

        with patch("subprocess.run", return_value=mock_run_result), \
             patch("subprocess.Popen", return_value=mock_process), \
             patch("sys.platform", "win32"):
            executor.execute("First prompt")
            executor.execute("Second prompt")

            total_usage = executor.get_total_token_usage()

            assert total_usage.input_tokens == 30
            assert total_usage.output_tokens == 15
            assert total_usage.total_cost_usd == 0.03


class TestStreamingExecution:
    """測試 streaming 輸出功能"""

    def test_execute_with_streaming_line_by_line(self, capsys) -> None:
        """測試 line-by-line streaming（Copilot 風格）"""
        config = AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        executor = AgentExecutor(config)

        # Mock Popen process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            "Line 1\n",
            "Line 2\n",
            "Line 3\n",
            "",  # End of stream
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        with patch("subprocess.run", return_value=MagicMock(stdout='{"session_id": "test-session"}', returncode=0)), \
             patch("subprocess.Popen", return_value=mock_process), \
             patch("sys.platform", "win32"):
            agent_response = executor._execute_with_streaming(
                cmd=["test", "cmd"],
                cli_name="TestCLI",
                parse_stream_json=False,
            )

        # Check response (should be complete output for copilot style)
        assert agent_response.response == "Line 1\nLine 2\nLine 3\n"
        assert isinstance(agent_response.token_usage, TokenUsage)
        # Check streaming_log (should contain all lines)
        assert agent_response.streaming_log == ["Line 1\n", "Line 2\n", "Line 3\n"]

        # Check output was printed
        captured = capsys.readouterr()
        assert "TestCLI Response (streaming):" in captured.out
        assert "Line 1" in captured.out
        assert "Line 2" in captured.out
        assert "Line 3" in captured.out

    def test_execute_with_streaming_stream_json(self, capsys) -> None:
        """測試 stream-json parsing（Claude 風格）"""
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        executor = AgentExecutor(config)

        # Mock Popen process with stream-json output
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"content": "Hello "}\n',
            '{"content": "world"}\n',
            '{"session_id": "test-session-123"}\n',
            '{"usage": {"input_tokens": 10, "output_tokens": 5}}\n',
            '{"total_cost_usd": 0.01}\n',
            "",  # End of stream
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        with patch("subprocess.run", return_value=MagicMock(stdout='{"session_id": "test-session"}', returncode=0)), \
             patch("subprocess.Popen", return_value=mock_process), \
             patch("sys.platform", "win32"):
            agent_response = executor._execute_with_streaming(
                cmd=["claude", "--print", "test"],
                cli_name="Claude",
                parse_stream_json=True,
            )

        # Check response content (should be last fragment only)
        assert agent_response.response == "world"
        # Check streaming_log (should contain all fragments)
        assert agent_response.streaming_log == ["Hello ", "world"]

        # Check token usage
        assert agent_response.token_usage.input_tokens == 10
        assert agent_response.token_usage.output_tokens == 5
        assert agent_response.token_usage.total_cost_usd == 0.01

        # Check session_id was saved
        assert executor.config.session_id == "test-session-123"

        # Check output was printed (with newlines between chunks)
        captured = capsys.readouterr()
        assert "Claude Response (streaming):" in captured.out
        assert "Hello" in captured.out
        assert "world" in captured.out

    def test_execute_with_streaming_handles_error(self) -> None:
        """測試 streaming 執行失敗時拋出錯誤"""
        config = AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        executor = AgentExecutor(config)

        # Mock failed process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [""]
        mock_process.stderr.read.return_value = "Error: command failed"
        mock_process.wait.return_value = 1

        with patch("subprocess.run", return_value=MagicMock(stdout='{"session_id": "test-session"}', returncode=0)), \
             patch("subprocess.Popen", return_value=mock_process), \
             patch("sys.platform", "win32"):
            with pytest.raises(AgentExecutionError, match="Claude execution failed with code 1"):
                executor._execute_with_streaming(
                    cmd=["claude", "test"],
                    cli_name="Claude",
                    parse_stream_json=True,
                )

    def test_execute_with_streaming_handles_malformed_json(self, capsys) -> None:
        """測試處理格式錯誤的 JSON（回退到 plain text）"""
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        executor = AgentExecutor(config)

        # Mock process with invalid JSON line
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"content": "Valid JSON"}\n',
            'Not valid JSON\n',
            '{"content": " more valid"}\n',
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        with patch("subprocess.run", return_value=MagicMock(stdout='{"session_id": "test-session"}', returncode=0)), \
             patch("subprocess.Popen", return_value=mock_process), \
             patch("sys.platform", "win32"):
            agent_response = executor._execute_with_streaming(
                cmd=["claude", "test"],
                cli_name="Claude",
                parse_stream_json=True,
            )

        # Should have last fragment as response
        assert agent_response.response == " more valid"
        # Should have all valid JSON contents in streaming_log
        assert agent_response.streaming_log == ["Valid JSON", " more valid"]

        # Check that invalid line was still printed
        captured = capsys.readouterr()
        assert "Not valid JSON" in captured.out


class TestClaudeStreamingExecution:
    """測試 Claude streaming 執行"""

    def test_execute_claude_uses_streaming(self, capsys) -> None:
        """測試 Claude 使用 streaming 執行"""
        config = AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        executor = AgentExecutor(config)

        # Mock streaming process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"content": "Response "}\n',
            '{"content": "text"}\n',
            '{"usage": {"input_tokens": 5, "output_tokens": 2}}\n',
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        with patch("subprocess.run", return_value=MagicMock(stdout='{"session_id": "test-session"}', returncode=0)), \
             patch("subprocess.Popen", return_value=mock_process), \
             patch("sys.platform", "win32"):
            agent_response = executor._execute_claude("Test prompt")

        assert agent_response.response == "text"  # Last fragment only
        assert agent_response.streaming_log == ["Response ", "text"]
        assert agent_response.token_usage.input_tokens == 5
        assert agent_response.token_usage.output_tokens == 2

        # Verify streaming output was shown
        captured = capsys.readouterr()
        assert "Claude Response (streaming):" in captured.out

    def test_execute_claude_with_new_message_format(self) -> None:
        """測試 Claude 新的 message.content[] JSON 格式能正確解析"""
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            session_id="test-session"
        )
        executor = AgentExecutor(config)

        # Mock streaming process with new Claude JSON format
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"type": "system", "subtype": "init", "session_id": "new-session-123"}\n',
            '{"type": "assistant", "message": {"content": [{"type": "text", "text": "Hello from Claude"}]}}\n',
            '{"type": "assistant", "message": {"content": [{"type": "text", "text": " with new format"}]}}\n',
            '{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "tool123"}]}}\n',  # Non-text content, should be ignored
            '{"usage": {"input_tokens": 100, "output_tokens": 50}}\n',
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        with patch("subprocess.run", return_value=MagicMock(stdout='{"session_id": "test-session"}', returncode=0)), \
             patch("subprocess.Popen", return_value=mock_process), \
             patch("sys.platform", "win32"):
            agent_response = executor._execute_claude("Test prompt")

            # Should extract text from message.content[] - last fragment only
            assert agent_response.response == " with new format"
            assert agent_response.streaming_log == ["Hello from Claude", " with new format"]
            assert isinstance(agent_response.token_usage, TokenUsage)
            assert agent_response.token_usage.input_tokens == 100
            assert agent_response.token_usage.output_tokens == 50

    def test_execute_claude_with_mixed_content_types(self) -> None:
        """測試 Claude message.content[] 包含多種類型時只提取 text"""
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
        )
        executor = AgentExecutor(config)

        # Mock streaming with mixed content types
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"type": "assistant", "message": {"content": [{"type": "text", "text": "Analysis: "}, {"type": "tool_use", "id": "read_file"}, {"type": "text", "text": "Complete"}]}}\n',
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        with patch("subprocess.run", return_value=MagicMock(stdout='{"session_id": "test-session"}', returncode=0)), \
             patch("subprocess.Popen", return_value=mock_process), \
             patch("sys.platform", "win32"):
            agent_response = executor._execute_claude("Analyze file")

            # Should only extract text blocks, skipping tool_use - last fragment only
            assert agent_response.response == "Complete"
            assert agent_response.streaming_log == ["Analysis: ", "Complete"]


class TestCopilotStreamingExecution:
    """測試 Copilot streaming 執行"""

    def test_execute_copilot_uses_streaming(self, capsys) -> None:
        """測試 Copilot 使用 streaming 執行"""
        config = AgentConfig(name="David", cli=AgentCLI.COPILOT)
        executor = AgentExecutor(config)

        # Mock streaming process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            "Copilot response line 1\n",
            "Copilot response line 2\n",
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        with patch("subprocess.Popen", return_value=mock_process), \
             patch("pathlib.Path.exists", return_value=False), \
             patch("sys.platform", "win32"):
            agent_response = executor._execute_copilot("Test prompt")

            # For copilot style, response is complete output (all lines joined)
            assert agent_response.response == "Copilot response line 1\nCopilot response line 2\n"
            # streaming_log contains all lines
            assert agent_response.streaming_log == ["Copilot response line 1\n", "Copilot response line 2\n"]

            # Verify streaming output was shown
            captured = capsys.readouterr()
            assert "Copilot Response (streaming):" in captured.out


class TestCLICommandArgsGeneration:
    """測試 CLI command args 生成功能"""

    def test_execute_claude_generates_cli_command_args_without_allowed_tools(self) -> None:
        """測試 Claude 在沒有 allowed_tools 時生成正確的 cli_command_args"""
        config = AgentConfig(
            name="Roger",
            cli=AgentCLI.CLAUDE,
            session_id="test-session-123"
        )
        executor = AgentExecutor(config)

        # Mock streaming process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"content": "Test response"}\n',
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        with patch("subprocess.run", return_value=MagicMock(stdout='{"session_id": "test-session-123"}', returncode=0)), \
             patch("subprocess.Popen", return_value=mock_process), \
             patch("sys.platform", "win32"):
            agent_response = executor._execute_claude("Test prompt")

            # Verify cli_command_args is set
            assert agent_response.cli_command_args is not None
            assert isinstance(agent_response.cli_command_args, list)

            # Check basic args
            assert "--resume" in agent_response.cli_command_args
            assert "test-session-123" in agent_response.cli_command_args
            assert "--output-format" in agent_response.cli_command_args
            assert "stream-json" in agent_response.cli_command_args
            assert "--verbose" in agent_response.cli_command_args
            assert "--add-dir" in agent_response.cli_command_args
            assert ".cafe" in agent_response.cli_command_args

            # No allowed-tools when not specified
            assert "--allowed-tools" not in agent_response.cli_command_args

    def test_execute_claude_generates_cli_command_args_with_allowed_tools(self) -> None:
        """測試 Claude 在有 allowed_tools 時生成正確的 cli_command_args"""
        config = AgentConfig(
            name="Roger",
            cli=AgentCLI.CLAUDE,
            session_id="test-session-123"
        )
        executor = AgentExecutor(config)

        # Mock streaming process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"content": "Test response"}\n',
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        allowed_tools = ["Write", "Read", "Edit(/views/admin/topics.php)"]

        with patch("subprocess.run", return_value=MagicMock(stdout='{"session_id": "test-session-123"}', returncode=0)), \
             patch("subprocess.Popen", return_value=mock_process), \
             patch("sys.platform", "win32"):
            agent_response = executor._execute_claude("Test prompt", allowed_tools=allowed_tools)

            # Verify cli_command_args contains allowed-tools，值與實際命令參數一致
            assert agent_response.cli_command_args is not None
            assert "--allowed-tools" in agent_response.cli_command_args

            # Find the allowed-tools value
            allowed_tools_idx = agent_response.cli_command_args.index("--allowed-tools")
            allowed_tools_value = agent_response.cli_command_args[allowed_tools_idx + 1]

            # 不再額外加雙引號，應直接是傳給 CLI 的參數值
            assert allowed_tools_value == "Write,Read,Edit(/views/admin/topics.php)"

    def test_execute_gemini_generates_cli_command_args_without_allowed_tools(self) -> None:
        """測試 Gemini 在沒有 allowed_tools 時生成正確的 cli_command_args"""
        config = AgentConfig(
            name="Roger",
            cli=AgentCLI.GEMINI,
            session_id="test-session-456"
        )
        executor = AgentExecutor(config)

        # Mock streaming process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"response": "Gemini response"}\n',
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        with patch("subprocess.Popen", return_value=mock_process), \
             patch("sys.platform", "win32"):
            agent_response = executor._execute_gemini("Test prompt")

            # Verify cli_command_args is set
            assert agent_response.cli_command_args is not None
            assert isinstance(agent_response.cli_command_args, list)

            # Check basic args (Gemini doesn't use --session parameter)
            assert "--output-format" in agent_response.cli_command_args
            assert "stream-json" in agent_response.cli_command_args
            assert "--include-directories" in agent_response.cli_command_args
            assert ".cafe" in agent_response.cli_command_args

            # No allowed-tools when not specified
            assert "--allowed-tools" not in agent_response.cli_command_args

    def test_execute_gemini_generates_cli_command_args_with_allowed_tools(self) -> None:
        """測試 Gemini 在有 allowed_tools 時生成正確的 cli_command_args"""
        config = AgentConfig(
            name="Roger",
            cli=AgentCLI.GEMINI,
            session_id="test-session-456"
        )
        executor = AgentExecutor(config)

        # Mock streaming process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"response": "Gemini response"}\n',
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        allowed_tools = ["write_file", "read_file", "replace(/views/admin/topics.php)"]

        with patch("subprocess.Popen", return_value=mock_process), \
             patch("sys.platform", "win32"):
            agent_response = executor._execute_gemini("Test prompt", allowed_tools=allowed_tools)

            # Verify cli_command_args contains allowed-tools，值與實際命令參數一致
            assert agent_response.cli_command_args is not None
            assert "--allowed-tools" in agent_response.cli_command_args

            # Find the allowed-tools value
            allowed_tools_idx = agent_response.cli_command_args.index("--allowed-tools")
            allowed_tools_value = agent_response.cli_command_args[allowed_tools_idx + 1]

            # 不再額外加雙引號，應直接是傳給 CLI 的參數值
            assert allowed_tools_value == "write_file,read_file,replace(/views/admin/topics.php)"

    def test_execute_cursor_generates_cli_command_args(self) -> None:
        """測試 Cursor 生成正確的 cli_command_args"""
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CURSOR,
            session_id="cursor-session-789"
        )
        executor = AgentExecutor(config)

        # Mock streaming process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"response": "Cursor response"}\n',
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        allowed_tools = ["Write", "Read", "Edit(/test.php)"]

        with patch("subprocess.Popen", return_value=mock_process), \
             patch("sys.platform", "win32"):
            agent_response = executor._execute_cursor("Test prompt", allowed_tools=allowed_tools)

            # Verify cli_command_args is set
            assert agent_response.cli_command_args is not None
            assert isinstance(agent_response.cli_command_args, list)

            # Check basic args (Cursor doesn't use --session parameter)
            assert "--output-format" in agent_response.cli_command_args
            assert "json" in agent_response.cli_command_args

            # Check for allowed-tools (Cursor does not have this parameter, we can only use --force to allow all operations)
            assert "--force" in agent_response.cli_command_args

    def test_execute_copilot_generates_cli_command_args_with_multiple_allow_tool_flags(self) -> None:
        """測試 Copilot 使用多個 --allow-tool flags 生成 cli_command_args"""
        config = AgentConfig(
            name="David",
            cli=AgentCLI.COPILOT,
        )
        executor = AgentExecutor(config)

        # Mock streaming process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            "Copilot response\n",
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        allowed_tools = ["write", "shell", "edit(/test.php)"]

        with patch("subprocess.Popen", return_value=mock_process), \
             patch("pathlib.Path.exists", return_value=False), \
             patch("sys.platform", "win32"):
            agent_response = executor._execute_copilot("Test prompt", allowed_tools=allowed_tools)

            # Verify cli_command_args is set
            assert agent_response.cli_command_args is not None
            assert isinstance(agent_response.cli_command_args, list)

            # Count --allow-tool flags
            allow_tool_count = agent_response.cli_command_args.count("--allow-tool")
            assert allow_tool_count == 3  # One for each tool

            # Verify each tool is present (no quotes needed for Copilot)
            assert "write" in agent_response.cli_command_args
            assert "shell" in agent_response.cli_command_args
            assert "edit(/test.php)" in agent_response.cli_command_args

    def test_execute_claude_with_model_parameter(self) -> None:
        """測試 Claude 在配置中有 model 時會加入 --model 參數"""
        config = AgentConfig(
            name="Roger",
            cli=AgentCLI.CLAUDE,
            session_id="test-session-123",
            model="opus"
        )
        executor = AgentExecutor(config)

        # Mock streaming process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"content": "Test response"}\n',
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        with patch("subprocess.run", return_value=MagicMock(stdout='{"session_id": "test-session-123"}', returncode=0)), \
             patch("subprocess.Popen", return_value=mock_process) as mock_popen, \
             patch("sys.platform", "win32"):
            agent_response = executor._execute_claude("Test prompt")

            # Verify --model parameter is in command
            call_args = mock_popen.call_args[0][0]
            assert "--model" in call_args
            assert "opus" in call_args

            # Verify cli_command_args contains model
            assert "--model" in agent_response.cli_command_args
            assert "opus" in agent_response.cli_command_args

    def test_execute_gemini_with_model_parameter(self) -> None:
        """測試 Gemini 在配置中有 model 時會加入 --model 參數"""
        config = AgentConfig(
            name="Roger",
            cli=AgentCLI.GEMINI,
            model="gemini-2.0-flash-exp"
        )
        executor = AgentExecutor(config)

        # Mock streaming process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"response": "Test response"}\n',
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        with patch("subprocess.Popen", return_value=mock_process) as mock_popen, \
             patch("sys.platform", "win32"):
            agent_response = executor._execute_gemini("Test prompt")

            # Verify --model parameter is in command
            call_args = mock_popen.call_args[0][0]
            assert "--model" in call_args
            assert "gemini-2.0-flash-exp" in call_args

            # Verify cli_command_args contains model
            assert "--model" in agent_response.cli_command_args
            assert "gemini-2.0-flash-exp" in agent_response.cli_command_args

    def test_execute_cursor_with_model_parameter(self) -> None:
        """測試 Cursor 在配置中有 model 時會加入 --model 參數"""
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CURSOR,
            model="claude-3-5-sonnet-20241022"
        )
        executor = AgentExecutor(config)

        # Mock streaming process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"response": "Test response"}\n',
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        with patch("subprocess.Popen", return_value=mock_process) as mock_popen, \
             patch("sys.platform", "win32"):
            agent_response = executor._execute_cursor("Test prompt")

            # Verify --model parameter is in command
            call_args = mock_popen.call_args[0][0]
            assert "--model" in call_args
            assert "claude-3-5-sonnet-20241022" in call_args

            # Verify cli_command_args contains model
            assert "--model" in agent_response.cli_command_args
            assert "claude-3-5-sonnet-20241022" in agent_response.cli_command_args

    def test_execute_copilot_with_model_parameter(self) -> None:
        """測試 Copilot 在配置中有 model 時會加入 --model 參數"""
        config = AgentConfig(
            name="David",
            cli=AgentCLI.COPILOT,
            model="gpt-4"
        )
        executor = AgentExecutor(config)

        # Mock streaming process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            "Copilot response\n",
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        with patch("subprocess.Popen", return_value=mock_process) as mock_popen, \
             patch("pathlib.Path.exists", return_value=False), \
             patch("sys.platform", "win32"):
            agent_response = executor._execute_copilot("Test prompt")

            # Verify --model parameter is in command
            call_args = mock_popen.call_args[0][0]
            assert "--model" in call_args
            assert "gpt-4" in call_args

            # Verify cli_command_args contains model
            assert "--model" in agent_response.cli_command_args
            assert "gpt-4" in agent_response.cli_command_args


class TestDefaultEditPermission:
    """測試 edit 權限不會自動加入（phases 需要明確指定）"""

    def test_does_not_add_edit_permission_for_claude(self):
        """測試 Claude 不會自動加入 Edit 權限"""
        config = AgentConfig(name="test", cli=AgentCLI.CLAUDE)
        executor = AgentExecutor(config)

        # Mock streaming process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"content": "Test"}\n',
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        with patch("subprocess.run", return_value=MagicMock(stdout='{"session_id": "test"}', returncode=0)), \
             patch("subprocess.Popen", return_value=mock_process), \
             patch("sys.platform", "win32"):
            # Provide tools without 'edit'
            agent_response = executor.execute("Test", allowed_tools=["read", "write"])

            # Should NOT automatically add Edit
            assert agent_response.cli_command_args is not None
            assert "--allowed-tools" in agent_response.cli_command_args
            allowed_tools_idx = agent_response.cli_command_args.index("--allowed-tools")
            allowed_tools_value = agent_response.cli_command_args[allowed_tools_idx + 1]
            # Should only contain Read and Write
            assert "Read" in allowed_tools_value
            assert "Write" in allowed_tools_value
            assert "Edit" not in allowed_tools_value

    def test_respects_explicit_edit_permission_for_claude(self):
        """測試 Claude 會保留明確指定的 Edit 權限"""
        config = AgentConfig(name="test", cli=AgentCLI.CLAUDE)
        executor = AgentExecutor(config)

        # Mock streaming process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"content": "Test"}\n',
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        with patch("subprocess.run", return_value=MagicMock(stdout='{"session_id": "test"}', returncode=0)), \
             patch("subprocess.Popen", return_value=mock_process), \
             patch("sys.platform", "win32"):
            # Explicitly provide edit permission
            agent_response = executor.execute("Test", allowed_tools=["read", "edit", "write"])

            # Should keep Edit as specified
            assert agent_response.cli_command_args is not None
            assert "--allowed-tools" in agent_response.cli_command_args
            allowed_tools_idx = agent_response.cli_command_args.index("--allowed-tools")
            allowed_tools_value = agent_response.cli_command_args[allowed_tools_idx + 1]
            # Should contain Read, Write, and Edit
            assert "Read" in allowed_tools_value
            assert "Write" in allowed_tools_value
            assert "Edit" in allowed_tools_value
            # Count occurrences of "Edit" - should be exactly 1
            assert allowed_tools_value.count("Edit") == 1

    def test_does_not_add_replace_permission_for_gemini(self):
        """測試 Gemini 不會自動加入 replace 權限"""
        config = AgentConfig(name="test", cli=AgentCLI.GEMINI)
        executor = AgentExecutor(config)

        # Mock streaming process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"response": "Test"}\n',
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        with patch("subprocess.Popen", return_value=mock_process), \
             patch("sys.platform", "win32"):
            # Provide tools without 'edit'
            agent_response = executor.execute("Test", allowed_tools=["read", "write"])

            # Should NOT automatically add replace
            assert agent_response.cli_command_args is not None
            assert "--allowed-tools" in agent_response.cli_command_args
            allowed_tools_idx = agent_response.cli_command_args.index("--allowed-tools")
            allowed_tools_value = agent_response.cli_command_args[allowed_tools_idx + 1]
            # Should only contain read_file and write_file
            assert "read_file" in allowed_tools_value
            assert "write_file" in allowed_tools_value
            assert "replace" not in allowed_tools_value

    def test_does_not_add_write_permission_for_copilot(self):
        """測試 Copilot 不會自動加入額外的 write 權限"""
        config = AgentConfig(name="test", cli=AgentCLI.COPILOT)
        executor = AgentExecutor(config)

        # Mock streaming process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            "Test response\n",
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        with patch("subprocess.Popen", return_value=mock_process), \
             patch("pathlib.Path.exists", return_value=False), \
             patch("sys.platform", "win32"):
            # Provide tools without 'edit'
            agent_response = executor.execute("Test", allowed_tools=["read", "bash"])

            # Should NOT automatically add write
            assert agent_response.cli_command_args is not None
            # Count --allow-tool flags (should only have read and bash)
            allow_tool_count = agent_response.cli_command_args.count("--allow-tool")
            assert allow_tool_count == 2  # read, bash only


class TestToolNameTranslation:
    """測試工具名稱轉換邏輯"""

    def test_translate_simple_tool_names_for_claude(self):
        """測試轉換簡單工具名給 Claude"""
        config = AgentConfig(
            name="test",
            cli=AgentCLI.CLAUDE,
            agent_dir="agents"
        )
        executor = AgentExecutor(config)

        tools = ["write", "read", "bash", "edit"]
        translated = executor._translate_tool_names(tools)

        assert translated == ["Write", "Read", "Bash", "Edit"]

    def test_translate_tool_names_with_file_patterns_for_claude(self):
        """測試轉換帶檔案 pattern 的工具名給 Claude"""
        config = AgentConfig(
            name="test",
            cli=AgentCLI.CLAUDE,
            agent_dir="agents"
        )
        executor = AgentExecutor(config)

        tools = [
            "write",
            "read",
            "bash",
            "edit(/home/user/test.php)"
        ]
        translated = executor._translate_tool_names(tools)

        assert translated == [
            "Write",
            "Read",
            "Bash",
            "Edit(/home/user/test.php)"
        ]

    def test_translate_tool_names_with_command_patterns_for_claude(self):
        """測試轉換帶命令 pattern 的工具名給 Claude"""
        config = AgentConfig(
            name="test",
            cli=AgentCLI.CLAUDE,
            agent_dir="agents"
        )
        executor = AgentExecutor(config)

        tools = [
            "bash(git status)",
            "read(/path/to/file.txt)"
        ]
        translated = executor._translate_tool_names(tools)

        assert translated == [
            "Bash(git status)",
            "Read(/path/to/file.txt)"
        ]

    def test_translate_tool_names_for_gemini(self):
        """測試轉換工具名給 Gemini"""
        config = AgentConfig(
            name="test",
            cli=AgentCLI.GEMINI,
            agent_dir="agents"
        )
        executor = AgentExecutor(config)

        tools = ["write", "read", "edit"]
        translated = executor._translate_tool_names(tools)

        assert translated == ["write_file", "read_file", "replace"]

    def test_translate_tool_names_with_patterns_for_gemini(self):
        """測試轉換帶 pattern 的工具名給 Gemini"""
        config = AgentConfig(
            name="test",
            cli=AgentCLI.GEMINI,
            agent_dir="agents"
        )
        executor = AgentExecutor(config)

        tools = [
            "edit(/home/user/test.php)",
            "bash(git status)"
        ]
        translated = executor._translate_tool_names(tools)

        assert translated == [
            "replace(/home/user/test.php)",
            "run_shell_command(git status)"
        ]

    def test_translate_returns_none_for_empty_tools(self):
        """測試空工具列表返回 None"""
        config = AgentConfig(
            name="test",
            cli=AgentCLI.CLAUDE,
            agent_dir="agents"
        )
        executor = AgentExecutor(config)

        assert executor._translate_tool_names(None) is None
        assert executor._translate_tool_names([]) is None


class TestGeminiIgnoreSetup:
    """測試 Gemini .geminiignore 自動設定"""

    def test_creates_geminiignore_if_not_exists(self, tmp_path):
        """測試不存在時建立 .geminiignore"""
        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            config = AgentConfig(name="test", cli=AgentCLI.GEMINI)
            executor = AgentExecutor(config)

            # Should not exist initially
            geminiignore = tmp_path / ".geminiignore"
            assert not geminiignore.exists()

            # Call ensure method
            executor._ensure_geminiignore()

            # Should now exist with correct content
            assert geminiignore.exists()
            content = geminiignore.read_text()
            assert "!/.cafe" in content
        finally:
            os.chdir(original_cwd)

    def test_appends_pattern_if_missing(self, tmp_path):
        """測試檔案存在但缺少 pattern 時追加"""
        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            # Create existing .geminiignore without the required pattern
            geminiignore = tmp_path / ".geminiignore"
            geminiignore.write_text("*.log\n*.tmp\n")

            config = AgentConfig(name="test", cli=AgentCLI.GEMINI)
            executor = AgentExecutor(config)

            executor._ensure_geminiignore()

            # Should append the pattern
            content = geminiignore.read_text()
            assert "*.log" in content
            assert "*.tmp" in content
            assert "!/.cafe" in content
        finally:
            os.chdir(original_cwd)

    def test_does_nothing_if_already_configured(self, tmp_path):
        """測試已正確配置時不修改"""
        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            # Create .geminiignore with required pattern
            geminiignore = tmp_path / ".geminiignore"
            original_content = "*.log\n!/.cafe\n*.tmp\n"
            geminiignore.write_text(original_content)

            config = AgentConfig(name="test", cli=AgentCLI.GEMINI)
            executor = AgentExecutor(config)

            executor._ensure_geminiignore()

            # Content should remain unchanged
            assert geminiignore.read_text() == original_content
        finally:
            os.chdir(original_cwd)


class TestWriteToolPathStripping:
    """Test Write/write_file tool path parameter stripping for Claude and Gemini."""

    def test_claude_write_path_stripping(self) -> None:
        """測試 Claude Write(/path) 會被轉換為 Write"""
        executor = AgentExecutor(
            AgentConfig(name="test", cli=AgentCLI.CLAUDE, session_id="test123")
        )

        # Mock streaming process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"content": "Done"}\n',
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        # Mock subprocess.run for session creation (not used since session_id is provided, but needed for safety)
        mock_run_result = MagicMock(stdout='{"session_id": "test123"}', returncode=0)

        with patch("subprocess.run", return_value=mock_run_result), \
             patch("subprocess.Popen", return_value=mock_process), \
             patch("sys.platform", "win32"):
            allowed_tools = ["Read", "Write(/.cafe/test.txt)", "Write(/.cafe/test2.txt)"]
            response = executor.execute("test prompt", allowed_tools)

            # Check the command args that were constructed
            assert response.cli_command_args is not None
            tools_index = response.cli_command_args.index("--allowed-tools")
            tools_value = response.cli_command_args[tools_index + 1]

            # Should contain Read and Write with paths (converted to git ignore format)
            assert "Read" in tools_value
            assert "Write(/.cafe/test.txt)" in tools_value
            assert "Write(/.cafe/test2.txt)" in tools_value
            # Should have separate Write entries for different paths
            tools_list = tools_value.strip('"').split(",")
            write_tools = [t for t in tools_list if t.startswith("Write")]
            assert len(write_tools) == 2  # Two different Write paths

    def test_gemini_write_path_stripping(self) -> None:
        """測試 Gemini write_file(/path) 會被轉換為 write_file"""
        executor = AgentExecutor(
            AgentConfig(name="test", cli=AgentCLI.GEMINI)
        )

        # Mock streaming process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"response": "Done"}\n',
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        with patch("subprocess.Popen", return_value=mock_process), \
             patch("sys.platform", "win32"):
            allowed_tools = ["read_file", "write_file(/.cafe/test.txt)", "write_file(/.cafe/test2.txt)"]
            response = executor.execute("test prompt", allowed_tools)

            # Check the command args that were constructed
            assert response.cli_command_args is not None
            tools_index = response.cli_command_args.index("--allowed-tools")
            tools_value = response.cli_command_args[tools_index + 1]

            # Should only contain read_file and write_file (no duplicates, no paths)
            assert "read_file" in tools_value
            assert "write_file" in tools_value
            # Should not contain paths
            assert "/.cafe" not in tools_value
            # Should not have duplicate write_file
            tools_list = tools_value.strip('"').split(",")
            assert tools_list.count("write_file") == 1

    def test_write_deduplication_with_mixed_tools(self) -> None:
        """測試混合使用 Write 和 Write(/path) 時的去重"""
        executor = AgentExecutor(
            AgentConfig(name="test", cli=AgentCLI.CLAUDE, session_id="test123")
        )

        # Mock streaming process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"content": "Done"}\n',
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        # Mock subprocess.run for session creation (not used since session_id is provided, but needed for safety)
        mock_run_result = MagicMock(stdout='{"session_id": "test123"}', returncode=0)

        with patch("subprocess.run", return_value=mock_run_result), \
             patch("subprocess.Popen", return_value=mock_process), \
             patch("sys.platform", "win32"):
            allowed_tools = ["Read", "Write", "Write(/.cafe/test.txt)", "Edit"]
            response = executor.execute("test prompt", allowed_tools)

            # Check deduplication
            assert response.cli_command_args is not None
            tools_index = response.cli_command_args.index("--allowed-tools")
            tools_value = response.cli_command_args[tools_index + 1]
            tools_list = tools_value.strip('"').split(",")

            # Should have exactly one Write
            assert tools_list.count("Write") == 1
            assert "Read" in tools_value
            assert "Edit" in tools_value


class TestGeminiSessionManagement:
    """Test Gemini session management functionality."""

    def test_gemini_passes_resume_parameter_when_session_id_exists(self) -> None:
        """測試當 config.session_id 存在時，CLI 指令包含 --resume {session_id}"""
        config = AgentConfig(
            name="Roger",
            cli=AgentCLI.GEMINI,
            session_id="test-session-123"
        )
        executor = AgentExecutor(config)

        with patch("subprocess.Popen") as mock_popen, \
             patch("sys.platform", "win32"):
            # Mock process
            mock_process = MagicMock()
            mock_process.stdout.readline.side_effect = [
                '{"type":"init","session_id":"test-session-123"}\n',
                '{"type":"message","role":"assistant","content":"Response"}\n',
                '{"response": "Response"}\n',
                '',
            ]
            mock_process.stderr.read.return_value = ""
            mock_process.wait.return_value = 0
            mock_popen.return_value = mock_process

            executor._execute_gemini("Test prompt")

            # Verify command includes --resume parameter
            call_args = mock_popen.call_args[0][0]
            assert "--resume" in call_args
            resume_index = call_args.index("--resume")
            assert call_args[resume_index + 1] == "test-session-123"

    def test_gemini_does_not_pass_resume_parameter_when_no_session_id(self) -> None:
        """測試當 config.session_id 不存在時，CLI 指令不包含 --resume"""
        config = AgentConfig(name="Roger", cli=AgentCLI.GEMINI)
        executor = AgentExecutor(config)

        with patch("subprocess.Popen") as mock_popen, \
             patch("sys.platform", "win32"):
            # Mock process
            mock_process = MagicMock()
            mock_process.stdout.readline.side_effect = [
                '{"type":"init","session_id":"new-session-456"}\n',
                '{"type":"message","role":"assistant","content":"Response"}\n',
                '{"response": "Response"}\n',
                '',
            ]
            mock_process.stderr.read.return_value = ""
            mock_process.wait.return_value = 0
            mock_popen.return_value = mock_process

            executor._execute_gemini("Test prompt")

            # Verify command does not include --resume parameter
            call_args = mock_popen.call_args[0][0]
            assert "--resume" not in call_args

    def test_gemini_extracts_session_id_from_init_message(self) -> None:
        """測試從 Gemini init 訊息中提取 session_id"""
        config = AgentConfig(name="Roger", cli=AgentCLI.GEMINI)
        executor = AgentExecutor(config)

        with patch("subprocess.Popen") as mock_popen, \
             patch("sys.platform", "win32"):
            # Mock process with init message containing session_id
            mock_process = MagicMock()
            mock_process.stdout.readline.side_effect = [
                '{"type":"init","session_id":"extracted-session-789","model":"auto"}\n',
                '{"type":"message","role":"assistant","content":"Hello"}\n',
                '{"response": "Hello"}\n',
                '',
            ]
            mock_process.stderr.read.return_value = ""
            mock_process.wait.return_value = 0
            mock_popen.return_value = mock_process

            # Execute without existing session_id
            executor._execute_gemini("Test prompt")

            # Verify session_id was extracted and stored
            assert executor.config.session_id == "extracted-session-789"

    def test_gemini_does_not_overwrite_existing_session_id(self) -> None:
        """測試當 config.session_id 已存在時，不應被新的 session_id 覆蓋"""
        config = AgentConfig(
            name="Roger",
            cli=AgentCLI.GEMINI,
            session_id="existing-session-123"
        )
        executor = AgentExecutor(config)

        with patch("subprocess.Popen") as mock_popen, \
             patch("sys.platform", "win32"):
            # Mock process returns different session_id
            mock_process = MagicMock()
            mock_process.stdout.readline.side_effect = [
                '{"type":"init","session_id":"existing-session-123","model":"auto"}\n',
                '{"type":"message","role":"assistant","content":"Response"}\n',
                '{"response": "Response"}\n',
                '',
            ]
            mock_process.stderr.read.return_value = ""
            mock_process.wait.return_value = 0
            mock_popen.return_value = mock_process

            executor._execute_gemini("Test prompt")

            # Should keep existing session_id
            assert executor.config.session_id == "existing-session-123"


