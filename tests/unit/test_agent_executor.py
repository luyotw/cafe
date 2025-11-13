"""Tests for AgentExecutor."""

import pytest
from unittest.mock import MagicMock, patch

from cafe.agents.executor import AgentExecutor, AgentExecutionError
from cafe.core.types import AgentConfig, AgentCLI, TokenUsage


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

        # Mock streaming process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"content": "Claude response"}\n',
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        with patch("subprocess.Popen", return_value=mock_process):
            response, token_usage = executor._execute_claude("Test prompt")

            assert response == "Claude response"
            assert isinstance(token_usage, TokenUsage)

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

        # Mock streaming process with non-JSON output
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            "Plain text response\n",
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        with patch("subprocess.Popen", return_value=mock_process):
            response, token_usage = executor._execute_claude("Test prompt")

            # With streaming, non-JSON is treated as plain lines
            assert "Plain text response" in response
            assert isinstance(token_usage, TokenUsage)
            assert token_usage.input_tokens == 0

    def test_execute_claude_session_already_in_use_raises_conflict_error(self) -> None:
        """測試當 session 已被使用時，拋出 SESSION_CONFLICT 錯誤"""
        config = AgentConfig(
            name="Roger",
            cli=AgentCLI.CLAUDE,
            session_id="old-session-id"
        )
        executor = AgentExecutor(config)

        with patch("subprocess.run") as mock_run:
            # Session already in use error
            mock_run.return_value = MagicMock(
                stdout="",
                stderr="Error: Session ID old-session-id is already in use.",
                returncode=1
            )

        call_count = 0

        def mock_popen_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1

            mock_process = MagicMock()

            # First call: session already in use error
            if call_count == 1:
                mock_process.stdout.readline.side_effect = [""]
                mock_process.stderr.read.return_value = "Error: Session ID old-session-id is already in use."
                mock_process.wait.return_value = 1
            # Second call: retry with new session succeeds
            else:
                mock_process.stdout.readline.side_effect = [
                    '{"content": "Success with new session"}\n',
                    "",
                ]
                mock_process.stderr.read.return_value = ""
                mock_process.wait.return_value = 0

            return mock_process

        # Mock subprocess.run for _create_new_session
        mock_run_result = MagicMock(
            stdout='{"session_id": "new-session-123", "result": "Hi!"}',
            returncode=0
        )

        with patch("subprocess.Popen", side_effect=mock_popen_side_effect):
            with patch("subprocess.run", return_value=mock_run_result):
                response, token_usage = executor._execute_claude("Test prompt")

                # Should succeed with new session
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

            response, token_usage = executor._execute_gemini("Test prompt")

            assert response == "Gemini response"
            assert isinstance(token_usage, TokenUsage)
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

            response, token_usage = executor.execute("Test prompt")

            assert response == "Hi there"
            assert isinstance(token_usage, TokenUsage)

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

            response, token_usage = executor._execute_gemini("Test prompt")

            assert response == "Plain text response\n"
            assert isinstance(token_usage, TokenUsage)

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

            response, token_usage = executor._execute_gemini("Test prompt")

            # Should only contain assistant messages, not user message or tool output
            assert response == "CAFE_NEED_CLARIFICATION\nHere is my response"
            assert "User prompt" not in response
            assert "File content" not in response
            assert "CAFE_CONFIRMED" not in response  # Should not pick up status from history
            assert isinstance(token_usage, TokenUsage)


class TestCursorExecution:
    """Test Cursor-specific execution."""

    def test_execute_cursor_calls_cli(self) -> None:
        """測試執行 Cursor 會呼叫 cursor-agent CLI with streaming"""
        config = AgentConfig(name="David", cli=AgentCLI.CURSOR)
        executor = AgentExecutor(config)

        with patch("subprocess.Popen") as mock_popen:
            # Mock process
            mock_process = MagicMock()
            mock_process.stdout.readline.side_effect = [
                '{"response": "Cursor response"}\n',
                '',  # EOF
            ]
            mock_process.stderr.read.return_value = ""
            mock_process.wait.return_value = 0
            mock_popen.return_value = mock_process

            response, token_usage = executor._execute_cursor("Test prompt")

            assert response == "Cursor response"
            assert isinstance(token_usage, TokenUsage)
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

        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.stdout.readline.side_effect = [
                '{"response": "Hello from Cursor"}\n',
                '',
            ]
            mock_process.stderr.read.return_value = ""
            mock_process.wait.return_value = 0
            mock_popen.return_value = mock_process

            response, token_usage = executor.execute("Test prompt")

            assert response == "Hello from Cursor"
            assert isinstance(token_usage, TokenUsage)

    def test_execute_cursor_failure(self) -> None:
        """測試 Cursor 執行失敗時拋出錯誤"""
        config = AgentConfig(name="David", cli=AgentCLI.CURSOR)
        executor = AgentExecutor(config)

        with patch("subprocess.Popen") as mock_popen:
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

        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.stdout.readline.side_effect = [
                "Plain text from Cursor\n",
                '',
            ]
            mock_process.stderr.read.return_value = ""
            mock_process.wait.return_value = 0
            mock_popen.return_value = mock_process

            response, token_usage = executor._execute_cursor("Test prompt")

            assert response == "Plain text from Cursor\n"
            assert isinstance(token_usage, TokenUsage)


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

        with patch("subprocess.Popen", return_value=mock_process):
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

        # Mock streaming process without usage data
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"content": "Response without usage"}\n',
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        with patch("subprocess.Popen", return_value=mock_process):
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

        with patch("subprocess.Popen", return_value=mock_process):
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

        with patch("subprocess.Popen", return_value=mock_process):
            response, token_usage = executor._execute_with_streaming(
                cmd=["test", "cmd"],
                cli_name="TestCLI",
                parse_stream_json=False,
            )

        # Check response
        assert response == "Line 1\nLine 2\nLine 3\n"
        assert isinstance(token_usage, TokenUsage)

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

        with patch("subprocess.Popen", return_value=mock_process):
            response, token_usage = executor._execute_with_streaming(
                cmd=["claude", "--print", "test"],
                cli_name="Claude",
                parse_stream_json=True,
            )

        # Check response content
        assert response == "Hello world"

        # Check token usage
        assert token_usage.input_tokens == 10
        assert token_usage.output_tokens == 5
        assert token_usage.total_cost_usd == 0.01

        # Check session_id was saved
        assert executor.config.session_id == "test-session-123"

        # Check output was printed
        captured = capsys.readouterr()
        assert "Claude Response (streaming):" in captured.out
        assert "Hello world" in captured.out

    def test_execute_with_streaming_handles_error(self) -> None:
        """測試 streaming 執行失敗時拋出錯誤"""
        config = AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        executor = AgentExecutor(config)

        # Mock failed process
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [""]
        mock_process.stderr.read.return_value = "Error: command failed"
        mock_process.wait.return_value = 1

        with patch("subprocess.Popen", return_value=mock_process):
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

        with patch("subprocess.Popen", return_value=mock_process):
            response, token_usage = executor._execute_with_streaming(
                cmd=["claude", "test"],
                cli_name="Claude",
                parse_stream_json=True,
            )

        # Should extract content from valid JSON, skip invalid
        assert "Valid JSON" in response
        assert " more valid" in response

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

        with patch("subprocess.Popen", return_value=mock_process):
            response, token_usage = executor._execute_claude("Test prompt")

        assert response == "Response text"
        assert token_usage.input_tokens == 5
        assert token_usage.output_tokens == 2

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

        with patch("subprocess.Popen", return_value=mock_process):
            response, token_usage = executor._execute_claude("Test prompt")

            # Should extract text from message.content[] and concatenate
            assert response == "Hello from Claude with new format"
            assert isinstance(token_usage, TokenUsage)
            assert token_usage.input_tokens == 100
            assert token_usage.output_tokens == 50

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

        with patch("subprocess.Popen", return_value=mock_process):
            response, token_usage = executor._execute_claude("Analyze file")

            # Should only extract text blocks, skipping tool_use
            assert response == "Analysis: Complete"


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

        with patch("subprocess.Popen", return_value=mock_process):
            with patch("pathlib.Path.exists", return_value=False):
                response, token_usage = executor._execute_copilot("Test prompt")

        assert "Copilot response line 1" in response
        assert "Copilot response line 2" in response

        # Verify streaming output was shown
        captured = capsys.readouterr()
        assert "Copilot Response (streaming):" in captured.out


