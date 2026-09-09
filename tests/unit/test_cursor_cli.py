"""測試 CursorCLI 實作."""

import json
import pytest

from cafe.agents.cli.cursor import CursorCLI
from cafe.core.types import AgentConfig, AgentCLI, TokenUsage


@pytest.fixture
def cursor_config():
    """建立 Cursor 配置."""
    return AgentConfig(name="test_cursor", cli=AgentCLI.CURSOR)


@pytest.fixture
def cursor_config_with_session():
    """建立帶有 session ID 的 Cursor 配置."""
    return AgentConfig(
        name="test_cursor",
        cli=AgentCLI.CURSOR,
        session_id="test-session-789"
    )


class TestCursorCLIBuildCommand:
    """測試 build_command() 方法."""

    def test_build_basic_command(self, cursor_config):
        """測試基本命令建構."""
        cli = CursorCLI(cursor_config)
        cmd = cli.build_command("test prompt")

        assert cmd[0] == "cursor-agent"
        assert "-p" in cmd
        assert "test prompt" in cmd
        # Cursor 一律使用 --force
        assert "--force" in cmd

    def test_build_command_with_session(self, cursor_config_with_session):
        """測試命令建構包含 session ID."""
        cli = CursorCLI(cursor_config_with_session)
        cmd = cli.build_command("test prompt")

        assert "--resume" in cmd
        resume_idx = cmd.index("--resume")
        assert cmd[resume_idx + 1] == "test-session-789"

    def test_build_command_includes_output_format(self, cursor_config):
        """測試命令包含輸出格式參數."""
        cli = CursorCLI(cursor_config)
        cmd = cli.build_command("test prompt")

        assert "--output-format" in cmd
        format_idx = cmd.index("--output-format")
        assert cmd[format_idx + 1] == "stream-json"

    def test_build_command_ignores_allowed_tools(self, cursor_config):
        """測試 Cursor 忽略 allowed_tools 參數."""
        cli = CursorCLI(cursor_config)
        # 即使傳入 allowed_tools，也應該被忽略
        cmd = cli.build_command("test prompt", allowed_tools=["read", "write"])

        # 不應該有 --allowed-tools 參數
        assert "--allowed-tools" not in cmd
        # 應該有 --force
        assert "--force" in cmd

    def test_build_command_ignores_allowed_directories(self, cursor_config):
        """測試 Cursor 忽略 allowed_directories 參數."""
        cli = CursorCLI(cursor_config)
        cmd = cli.build_command("test prompt", allowed_directories=["/path/to/dir"])

        # 不應該有任何目錄相關參數
        assert "--add-dir" not in cmd
        assert "--include-directories" not in cmd


class TestCursorCLITranslateAllowedTools:
    """測試 translate_allowed_tools() 方法."""

    def test_translate_preserves_scope_marker(self, cursor_config):
        """測試保留工具範圍，供命令建構區分空權限與一般執行。"""
        cli = CursorCLI(cursor_config)
        tools = ["read", "write", "bash"]
        result = cli.translate_allowed_tools(tools)

        assert result == tools


class TestCursorCLIAddDirectories:
    """測試 add_directories() 方法."""

    def test_add_directories_returns_unchanged(self, cursor_config):
        """測試加入目錄不改變命令（Cursor 不支援）."""
        cli = CursorCLI(cursor_config)
        cmd = ["cursor-agent", "-p", "test"]
        directories = ["/path/to/dir1", "/path/to/dir2"]

        result = cli.add_directories(cmd, directories)

        # 應該回傳原始命令，不做任何改變
        assert result == cmd


class TestCursorCLIGetOutputFormat:
    """測試 get_output_format() 方法."""

    def test_get_output_format(self, cursor_config):
        """測試取得輸出格式參數."""
        cli = CursorCLI(cursor_config)
        result = cli.get_output_format()

        assert "--output-format" in result
        assert "stream-json" in result


class TestCursorCLIParseResponse:
    """測試 parse_response() 方法."""

    def test_parse_response_with_stream_json(self, cursor_config):
        """測試解析 stream-json 格式的回應."""
        cli = CursorCLI(cursor_config)
        output_lines = [
            json.dumps({"type": "system", "subtype": "init", "session_id": "test-session", "model": "Auto"}),
            json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "say HI"}]}}),
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "HI 🙂"}]}}),
            json.dumps({"type": "result", "subtype": "success", "duration_ms": 7896, "duration_api_ms": 7896, "result": "HI 🙂"}),
        ]

        response, token_usage, permission_denials = cli.parse_response(output_lines)

        assert response == "HI 🙂"
        assert token_usage.duration_ms == 7896
        assert token_usage.duration_api_ms == 7896
        assert isinstance(token_usage, TokenUsage)
        assert len(permission_denials) == 0

    def test_parse_response_extracts_from_assistant_message(self, cursor_config):
        """測試從 assistant 訊息中提取回應."""
        cli = CursorCLI(cursor_config)
        output_lines = [
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "First part "},
                {"type": "text", "text": "Second part"}
            ]}}),
            json.dumps({"type": "result", "duration_ms": 5000, "duration_api_ms": 4500}),
        ]

        response, token_usage, permission_denials = cli.parse_response(output_lines)

        assert response == "First part Second part"
        assert token_usage.duration_ms == 5000
        assert token_usage.duration_api_ms == 4500

    def test_parse_response_fallback_to_result(self, cursor_config):
        """測試當沒有 assistant 訊息時從 result 提取回應."""
        cli = CursorCLI(cursor_config)
        output_lines = [
            json.dumps({"type": "result", "result": "Fallback response", "duration_ms": 3000}),
        ]

        response, token_usage, permission_denials = cli.parse_response(output_lines)

        assert response == "Fallback response"
        assert token_usage.duration_ms == 3000

    def test_parse_response_with_invalid_json(self, cursor_config):
        """測試解析非 JSON 格式的回應."""
        cli = CursorCLI(cursor_config)
        output_lines = [
            "Some non-JSON output",
            json.dumps({"type": "result", "result": "Valid response", "duration_ms": 1000}),
        ]

        response, token_usage, permission_denials = cli.parse_response(output_lines)

        # 應該跳過非 JSON 行，提取有效的回應
        assert response == "Valid response"
        assert token_usage.duration_ms == 1000


class TestCursorCLIExtractSessionId:
    """測試 extract_session_id() 方法."""

    def test_extract_session_id_from_init_event(self, cursor_config):
        """測試從 Cursor stream-json init event 提取 session ID."""
        cli = CursorCLI(cursor_config)
        output_lines = [
            "not-json",
            json.dumps({"type": "system", "subtype": "init", "session_id": "cursor-session"}),
            json.dumps({"response": "test"}),
        ]

        session_id = cli.extract_session_id(output_lines)

        assert session_id == "cursor-session"
