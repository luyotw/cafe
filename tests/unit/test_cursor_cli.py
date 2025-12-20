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
        assert cmd[format_idx + 1] == "json"

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

    def test_translate_returns_empty_list(self, cursor_config):
        """測試轉換工具名稱回傳空列表（Cursor 不支援工具限制）."""
        cli = CursorCLI(cursor_config)
        tools = ["read", "write", "bash"]
        result = cli.translate_allowed_tools(tools)

        # Cursor 不支援工具限制，應該回傳空列表
        assert result == []


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
        assert "json" in result


class TestCursorCLIParseResponse:
    """測試 parse_response() 方法."""

    def test_parse_response_with_json(self, cursor_config):
        """測試解析 JSON 格式的回應."""
        cli = CursorCLI(cursor_config)
        json_response = json.dumps({"response": "Cursor response text"})
        output_lines = [json_response]

        response, token_usage, permission_denials = cli.parse_response(output_lines)

        assert response == "Cursor response text"
        assert isinstance(token_usage, TokenUsage)
        assert len(permission_denials) == 0

    def test_parse_response_with_invalid_json(self, cursor_config):
        """測試解析非 JSON 格式的回應."""
        cli = CursorCLI(cursor_config)
        output_lines = ["Some non-JSON output"]

        response, token_usage, permission_denials = cli.parse_response(output_lines)

        # 應該回傳原始輸出
        assert response == "Some non-JSON output"
        assert isinstance(token_usage, TokenUsage)
        assert len(permission_denials) == 0


class TestCursorCLIExtractSessionId:
    """測試 extract_session_id() 方法."""

    def test_extract_session_id_not_supported(self, cursor_config):
        """測試 Cursor 不支援從輸出提取 session ID."""
        cli = CursorCLI(cursor_config)
        output_lines = [json.dumps({"response": "test"})]

        session_id = cli.extract_session_id(output_lines)

        # Cursor 自動管理 session，不從輸出提取
        assert session_id is None
