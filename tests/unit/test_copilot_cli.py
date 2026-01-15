"""測試 CopilotCLI 實作."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from cafe.agents.cli.copilot import CopilotCLI
from cafe.core.types import AgentConfig, AgentCLI, TokenUsage


@pytest.fixture
def copilot_config():
    """建立 Copilot 配置."""
    return AgentConfig(name="test_copilot", cli=AgentCLI.COPILOT)


@pytest.fixture
def copilot_config_with_session():
    """建立帶有 session ID 的 Copilot 配置."""
    return AgentConfig(
        name="test_copilot",
        cli=AgentCLI.COPILOT,
        session_id="test-session-uuid"
    )


class TestCopilotCLIBuildCommand:
    """測試 build_command() 方法."""

    def test_build_basic_command(self, copilot_config):
        """測試基本命令建構."""
        cli = CopilotCLI(copilot_config)
        cmd = cli.build_command("test prompt")

        assert cmd[0] == "copilot"
        assert "-p" in cmd
        assert "test prompt" in cmd
        # 沒有 allowed_tools 時，應該有 --allow-all-tools
        assert "--allow-all-tools" in cmd

    def test_build_command_with_session(self, copilot_config_with_session):
        """測試命令建構包含 session ID."""
        cli = CopilotCLI(copilot_config_with_session)
        cmd = cli.build_command("test prompt")

        assert "--resume" in cmd
        resume_idx = cmd.index("--resume")
        assert cmd[resume_idx + 1] == "test-session-uuid"

    def test_build_command_with_allowed_tools(self, copilot_config):
        """測試命令建構包含 allowed_tools."""
        cli = CopilotCLI(copilot_config)
        cmd = cli.build_command("test prompt", allowed_tools=["shell", "write"])

        # 應該有多個 --allow-tool 參數
        assert "--allow-tool" in cmd
        assert "shell" in cmd
        assert "write" in cmd
        # 不應該有 --allow-all-tools
        assert "--allow-all-tools" not in cmd

    def test_build_command_with_allowed_directories(self, copilot_config):
        """測試命令建構包含 allowed_directories."""
        cli = CopilotCLI(copilot_config)
        cmd = cli.build_command("test prompt", allowed_directories=["/path/to/dir"])

        assert "--add-dir" in cmd
        assert "/path/to/dir" in cmd


class TestCopilotCLITranslateAllowedTools:
    """測試 translate_allowed_tools() 方法."""

    def test_translate_simple_tools(self, copilot_config):
        """測試轉換簡單工具名稱."""
        cli = CopilotCLI(copilot_config)
        tools = ["shell", "write", "read"]
        result = cli.translate_allowed_tools(tools)

        # Copilot 直接使用工具名稱，不做轉換
        assert result == ["shell", "write", "read"]

    def test_translate_tools_with_paths_strips_paths(self, copilot_config):
        """測試轉換帶有路徑的工具名稱（去除路徑參數）."""
        cli = CopilotCLI(copilot_config)
        tools = ["write(/path/to/file)", "read(/another/path)", "shell"]
        result = cli.translate_allowed_tools(tools)

        # 應該去除路徑參數，只保留工具名稱
        assert "write" in result
        assert "read" in result
        assert "shell" in result
        # 不應該包含路徑
        assert "write(/path/to/file)" not in result


class TestCopilotCLIAddDirectories:
    """測試 add_directories() 方法."""

    def test_add_directories_to_command(self, copilot_config):
        """測試將目錄加入命令."""
        cli = CopilotCLI(copilot_config)
        cmd = ["copilot", "-p", "test"]
        directories = ["/path/to/dir1", "/path/to/dir2"]

        result = cli.add_directories(cmd, directories)

        assert "--add-dir" in result
        # 每個目錄都應該有獨立的 --add-dir 參數
        add_dir_count = result.count("--add-dir")
        assert add_dir_count == 2
        assert "/path/to/dir1" in result
        assert "/path/to/dir2" in result


class TestCopilotCLIGetOutputFormat:
    """測試 get_output_format() 方法."""

    def test_get_output_format_returns_empty(self, copilot_config):
        """測試取得輸出格式參數（Copilot 不使用 output format）."""
        cli = CopilotCLI(copilot_config)
        result = cli.get_output_format()

        # Copilot 不使用 output format 參數
        assert result == []


class TestCopilotCLIParseResponse:
    """測試 parse_response() 方法."""

    def test_parse_response_plain_text(self, copilot_config):
        """測試解析純文字回應."""
        cli = CopilotCLI(copilot_config)
        output_lines = ["Line 1\n", "Line 2\n", "Line 3"]

        response, token_usage, permission_denials, model = cli.parse_response(output_lines)

        # 應該連接所有行
        assert response == "Line 1\nLine 2\nLine 3"
        assert isinstance(token_usage, TokenUsage)
        assert len(permission_denials) == 0
        assert model is None  # No usage summary, so no model

    def test_parse_response_with_usage_summary(self, copilot_config):
        """測試解析包含使用統計的回應."""
        cli = CopilotCLI(copilot_config)
        output_lines = [
            "HI! 👋\n",
            "\n",
            "I'm GitHub Copilot CLI.\n",
            "\n",
            "\n",
            "Total usage est:       1 Premium request\n",
            "Total duration (API):  7s\n",
            "Total duration (wall): 11s\n",
            "Total code changes:    0 lines added, 0 lines removed\n",
            "Usage by model:\n",
            "    claude-sonnet-4.5    14.2k input, 53 output, 10.2k cache read (Est. 1 Premium request)\n"
        ]

        response, token_usage, permission_denials, model = cli.parse_response(output_lines)

        # Response 應該不包含統計資訊
        assert "Total usage est:" not in response
        assert response.startswith("HI! 👋")
        
        # Model 應該被提取
        assert model == "claude-sonnet-4.5"
        
        # Token usage 應該正確提取
        assert token_usage.input_tokens == 14200
        assert token_usage.output_tokens == 53
        assert token_usage.cache_read_input_tokens == 10200
        assert token_usage.duration_api_ms == 7000
        assert token_usage.duration_ms == 11000

    def test_parse_response_without_cache(self, copilot_config):
        """測試解析不包含 cache 的使用統計."""
        cli = CopilotCLI(copilot_config)
        output_lines = [
            "Response text\n",
            "\n",
            "\n",
            "Total usage est:       1 Premium request\n",
            "Total duration (API):  5s\n",
            "Total duration (wall): 8s\n",
            "Usage by model:\n",
            "    claude-sonnet-4.5    1.5k input, 120 output (Est. 1 Premium request)\n"
        ]

        response, token_usage, permission_denials, model = cli.parse_response(output_lines)

        assert model == "claude-sonnet-4.5"
        assert token_usage.input_tokens == 1500
        assert token_usage.output_tokens == 120
        assert token_usage.cache_read_input_tokens == 0
        assert token_usage.duration_api_ms == 5000
        assert token_usage.duration_ms == 8000

    def test_parse_token_count_with_k_suffix(self, copilot_config):
        """測試解析帶 k 後綴的 token 數量."""
        cli = CopilotCLI(copilot_config)
        
        assert cli._parse_token_count("14.2k") == 14200
        assert cli._parse_token_count("1.5k") == 1500
        assert cli._parse_token_count("10k") == 10000
        
    def test_parse_token_count_without_suffix(self, copilot_config):
        """測試解析不帶後綴的 token 數量."""
        cli = CopilotCLI(copilot_config)
        
        assert cli._parse_token_count("53") == 53
        assert cli._parse_token_count("120") == 120
        assert cli._parse_token_count("1000") == 1000

    def test_parse_response_with_minute_duration(self, copilot_config):
        """測試解析包含分鐘格式的 duration."""
        cli = CopilotCLI(copilot_config)
        output_lines = [
            "Response text\n",
            "\n",
            "\n",
            "Total usage est:       1 Premium request\n",
            "Total duration (API):  1m 7.26s\n",
            "Total duration (wall): 1m 18.152s\n",
            "Usage by model:\n",
            "    claude-sonnet-4.5    232.5k input, 4.8k output, 217.7k cache read (Est. 1 Premium request)\n"
        ]

        response, token_usage, permission_denials, model = cli.parse_response(output_lines)

        # Verify model was extracted
        assert model == "claude-sonnet-4.5"
        
        # Verify token usage
        assert token_usage.input_tokens == 232500
        assert token_usage.output_tokens == 4800
        assert token_usage.cache_read_input_tokens == 217700
        
        # Verify duration parsing (1m 7.26s = 67.26s = 67260ms)
        assert token_usage.duration_api_ms == 67260
        # 1m 18.152s = 78.152s = 78152ms
        assert token_usage.duration_ms == 78152


class TestCopilotCLIExtractSessionId:
    """測試 extract_session_id() 方法."""

    @patch("cafe.agents.cli.copilot.Path")
    @patch("cafe.agents.cli.copilot.time.sleep")
    def test_extract_session_id_from_filesystem(self, mock_sleep, mock_path_class, copilot_config):
        """測試從檔案系統提取 session ID."""
        cli = CopilotCLI(copilot_config)

        # Mock Path.home() 和 session directory
        mock_home = MagicMock()
        mock_path_class.home.return_value = mock_home

        mock_session_dir = MagicMock()
        mock_session_dir.exists.return_value = True

        # Mock 初始檔案列表 (只有舊 session)
        mock_file1 = MagicMock()
        mock_file1.name = "old-session.jsonl"
        mock_file1.is_file.return_value = True

        # 設定 Path.home() / ".copilot" / "session-state" 回傳 mock_session_dir
        mock_home.__truediv__.return_value.__truediv__.return_value = mock_session_dir

        # 第一次呼叫 iterdir (record_existing_sessions)
        mock_session_dir.iterdir.return_value = [mock_file1]

        # 記錄初始 sessions
        cli.record_existing_sessions()

        # Mock 新檔案
        mock_file2 = MagicMock()
        mock_file2.name = "new-session-uuid.jsonl"
        mock_file2.is_file.return_value = True

        # 第二次呼叫 iterdir (extract_session_id) - 新增了 session
        mock_session_dir.iterdir.return_value = [mock_file1, mock_file2]

        output_lines = []
        session_id = cli.extract_session_id(output_lines)

        # 應該提取新的 session ID (去除 .jsonl)
        assert session_id == "new-session-uuid"

    @patch("cafe.agents.cli.copilot.Path")
    def test_extract_session_id_no_new_session(self, mock_path_class, copilot_config):
        """測試沒有新 session 時回傳 None."""
        cli = CopilotCLI(copilot_config)

        mock_home = MagicMock()
        mock_path_class.home.return_value = mock_home

        mock_session_dir = MagicMock()
        mock_session_dir.exists.return_value = True

        mock_file1 = MagicMock()
        mock_file1.name = "existing-session.jsonl"
        mock_file1.is_file.return_value = True

        mock_session_dir.iterdir.return_value = [mock_file1]

        mock_home.__truediv__.return_value.__truediv__.return_value = mock_session_dir

        # 記錄初始 sessions
        cli.record_existing_sessions()

        # 沒有新增 session
        output_lines = []
        session_id = cli.extract_session_id(output_lines)

        assert session_id is None
