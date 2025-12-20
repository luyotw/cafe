"""測試 GeminiCLI 實作."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from cafe.agents.cli.gemini import GeminiCLI
from cafe.core.types import AgentConfig, AgentCLI, TokenUsage


@pytest.fixture
def gemini_config():
    """建立 Gemini 配置."""
    return AgentConfig(name="test_gemini", cli=AgentCLI.GEMINI)


@pytest.fixture
def gemini_config_with_session():
    """建立帶有 session ID 的 Gemini 配置."""
    return AgentConfig(
        name="test_gemini",
        cli=AgentCLI.GEMINI,
        session_id="test-session-456"
    )


class TestGeminiCLIBuildCommand:
    """測試 build_command() 方法."""

    def test_build_basic_command(self, gemini_config):
        """測試基本命令建構."""
        cli = GeminiCLI(gemini_config)
        cmd = cli.build_command("test prompt")

        assert cmd[0] == "gemini"
        assert "-p" in cmd
        assert "test prompt" in cmd

    def test_build_command_with_session(self, gemini_config_with_session):
        """測試命令建構包含 session ID."""
        cli = GeminiCLI(gemini_config_with_session)
        cmd = cli.build_command("test prompt")

        assert "--resume" in cmd
        resume_idx = cmd.index("--resume")
        assert cmd[resume_idx + 1] == "test-session-456"

    def test_build_command_includes_output_format(self, gemini_config):
        """測試命令包含輸出格式參數."""
        cli = GeminiCLI(gemini_config)
        cmd = cli.build_command("test prompt")

        assert "--output-format" in cmd
        format_idx = cmd.index("--output-format")
        assert cmd[format_idx + 1] == "stream-json"


class TestGeminiCLITranslateAllowedTools:
    """測試 translate_allowed_tools() 方法."""

    def test_translate_simple_tools(self, gemini_config):
        """測試轉換簡單工具名稱."""
        cli = GeminiCLI(gemini_config)
        tools = ["read_file", "run_shell_command"]
        result = cli.translate_allowed_tools(tools)

        assert "read_file" in result
        assert "run_shell_command" in result

    def test_translate_write_file_strips_path(self, gemini_config):
        """測試 write_file 工具去除路徑參數."""
        cli = GeminiCLI(gemini_config)
        tools = ["write_file(/path/to/file)", "write_file(/another/path)"]
        result = cli.translate_allowed_tools(tools)

        # write_file 不支援路徑參數，應該被去除並去重
        assert "write_file" in result
        assert len([t for t in result if "write_file" in t]) == 1

    def test_translate_mixed_tools(self, gemini_config):
        """測試混合工具名稱."""
        cli = GeminiCLI(gemini_config)
        tools = ["read_file(/path)", "write_file(/path)", "run_shell_command"]
        result = cli.translate_allowed_tools(tools)

        assert "read_file(/path)" in result
        assert "write_file" in result
        assert "run_shell_command" in result


class TestGeminiCLIAddDirectories:
    """測試 add_directories() 方法."""

    def test_add_directories_to_command(self, gemini_config):
        """測試將目錄加入命令."""
        cli = GeminiCLI(gemini_config)
        cmd = ["gemini", "-p", "test"]
        directories = ["/path/to/dir1", "/path/to/dir2"]

        result = cli.add_directories(cmd, directories)

        assert "--include-directories" in result
        # 每個目錄都應該有獨立的 --include-directories 參數
        include_count = result.count("--include-directories")
        assert include_count == 2
        assert "/path/to/dir1" in result
        assert "/path/to/dir2" in result


class TestGeminiCLIParseResponse:
    """測試 parse_response() 方法."""

    def test_parse_response_extracts_assistant_messages(self, gemini_config):
        """測試解析並提取 assistant 訊息."""
        cli = GeminiCLI(gemini_config)
        output_lines = [
            json.dumps({"type": "message", "role": "user", "content": "User prompt"}),
            json.dumps({"type": "message", "role": "assistant", "content": "First response"}),
            json.dumps({"type": "message", "role": "assistant", "content": "Second response"}),
            json.dumps({"response": "Full response"}),
        ]

        response, token_usage, permission_denials = cli.parse_response(output_lines)

        # 應該只包含 assistant 的訊息，不包含 user 的 prompt echo
        assert response == "First responseSecond response"

    def test_parse_response_fallback_to_full_response(self, gemini_config):
        """測試當沒有 assistant 訊息時，回退到完整回應."""
        cli = GeminiCLI(gemini_config)
        output_lines = [
            json.dumps({"response": "Fallback response"}),
        ]

        response, token_usage, permission_denials = cli.parse_response(output_lines)

        assert response == "Fallback response"


class TestGeminiCLIExtractSessionId:
    """測試 extract_session_id() 方法."""

    def test_extract_session_id_from_init_message(self, gemini_config):
        """測試從 init 訊息提取 session ID."""
        cli = GeminiCLI(gemini_config)
        output_lines = [
            json.dumps({"type": "init", "session_id": "new-session-789"}),
            json.dumps({"type": "message", "role": "assistant", "content": "Hello"}),
        ]

        session_id = cli.extract_session_id(output_lines)

        assert session_id == "new-session-789"

    def test_extract_session_id_not_found(self, gemini_config):
        """測試找不到 session ID 時回傳 None."""
        cli = GeminiCLI(gemini_config)
        output_lines = [
            json.dumps({"type": "message", "role": "assistant", "content": "Hello"}),
        ]

        session_id = cli.extract_session_id(output_lines)

        assert session_id is None


class TestGeminiCLIEnsureGeminiignore:
    """測試 ensure_geminiignore() 方法."""

    @patch("cafe.agents.cli.gemini.Path")
    def test_creates_geminiignore_if_not_exists(self, mock_path, gemini_config):
        """測試如果 .geminiignore 不存在則建立."""
        cli = GeminiCLI(gemini_config)

        # Mock Path 物件
        mock_geminiignore = MagicMock()
        mock_geminiignore.exists.return_value = False
        mock_path.return_value = mock_geminiignore

        cli.ensure_geminiignore()

        # 應該呼叫 write_text 建立檔案
        mock_geminiignore.write_text.assert_called_once()
        call_args = mock_geminiignore.write_text.call_args[0][0]
        assert "!/.cafe" in call_args

    @patch("cafe.agents.cli.gemini.Path")
    def test_appends_pattern_if_missing(self, mock_path, gemini_config):
        """測試如果檔案存在但缺少 pattern，則附加."""
        cli = GeminiCLI(gemini_config)

        # Mock Path 物件
        mock_geminiignore = MagicMock()
        mock_geminiignore.exists.return_value = True
        mock_geminiignore.read_text.return_value = "some other content\n"
        mock_path.return_value = mock_geminiignore

        # Mock open
        with patch("builtins.open", create=True) as mock_open:
            mock_file = MagicMock()
            mock_open.return_value.__enter__.return_value = mock_file

            cli.ensure_geminiignore()

            # 應該附加 pattern
            mock_file.write.assert_called()

    @patch("cafe.agents.cli.gemini.Path")
    def test_does_nothing_if_already_configured(self, mock_path, gemini_config):
        """測試如果已經配置則不做任何事."""
        cli = GeminiCLI(gemini_config)

        # Mock Path 物件
        mock_geminiignore = MagicMock()
        mock_geminiignore.exists.return_value = True
        mock_geminiignore.read_text.return_value = "!/.cafe\n"
        mock_path.return_value = mock_geminiignore

        cli.ensure_geminiignore()

        # 不應該呼叫 write_text 或 open
        mock_geminiignore.write_text.assert_not_called()
