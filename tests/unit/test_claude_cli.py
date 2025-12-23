"""測試 ClaudeCLI 實作."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from cafe.agents.cli.claude import ClaudeCLI
from cafe.core.types import AgentConfig, AgentCLI, PermissionDenial, TokenUsage


@pytest.fixture
def claude_config():
    """建立 Claude 配置."""
    return AgentConfig(name="test_claude", cli=AgentCLI.CLAUDE)


@pytest.fixture
def claude_config_with_session():
    """建立帶有 session ID 的 Claude 配置."""
    return AgentConfig(
        name="test_claude",
        cli=AgentCLI.CLAUDE,
        session_id="test-session-123"
    )


@pytest.fixture
def claude_config_with_model():
    """建立帶有 model 的 Claude 配置."""
    return AgentConfig(
        name="test_claude",
        cli=AgentCLI.CLAUDE,
        model="opus"
    )


class TestClaudeCLIBuildCommand:
    """測試 build_command() 方法."""

    def test_build_basic_command_without_session(self, claude_config):
        """測試基本命令建構 (沒有 session)."""
        cli = ClaudeCLI(claude_config)
        cmd = cli.build_command("test prompt")

        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "test prompt" in cmd
        assert "--resume" not in cmd

    def test_build_command_with_session(self, claude_config_with_session):
        """測試命令建構包含 session ID."""
        cli = ClaudeCLI(claude_config_with_session)
        cmd = cli.build_command("test prompt")

        assert "--resume" in cmd
        resume_idx = cmd.index("--resume")
        assert cmd[resume_idx + 1] == "test-session-123"

        # --resume 必須在 -p 之前
        p_idx = cmd.index("-p")
        assert resume_idx < p_idx

    def test_build_command_with_model(self, claude_config_with_model):
        """測試命令建構包含 model 參數."""
        cli = ClaudeCLI(claude_config_with_model)
        cmd = cli.build_command("test prompt")

        assert "--model" in cmd
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == "opus"

        # --model 必須在 -p 之後
        p_idx = cmd.index("-p")
        assert model_idx > p_idx

    def test_build_command_parameter_order(self, claude_config_with_session, claude_config_with_model):
        """測試參數順序: claude -> --resume -> -p -> --model -> 其他."""
        config = AgentConfig(
            name="test",
            cli=AgentCLI.CLAUDE,
            session_id="session-123",
            model="sonnet"
        )
        cli = ClaudeCLI(config)
        cmd = cli.build_command("test prompt")

        assert cmd[0] == "claude"
        assert cmd[1] == "--resume"
        assert cmd[2] == "session-123"
        assert cmd[3] == "-p"
        assert cmd[4] == "test prompt"
        assert "--model" in cmd[5:]

    def test_build_command_includes_output_format(self, claude_config):
        """測試命令包含輸出格式參數."""
        cli = ClaudeCLI(claude_config)
        cmd = cli.build_command("test prompt")

        assert "--output-format" in cmd
        format_idx = cmd.index("--output-format")
        assert cmd[format_idx + 1] == "stream-json"
        assert "--verbose" in cmd


class TestClaudeCLITranslateAllowedTools:
    """測試 translate_allowed_tools() 方法."""

    def test_translate_simple_tools(self, claude_config):
        """測試轉換簡單工具名稱."""
        cli = ClaudeCLI(claude_config)
        tools = ["read", "write", "bash"]
        result = cli.translate_allowed_tools(tools)

        assert "Read" in result
        assert "Write" in result
        assert "Bash" in result

    @patch('cafe.agents.cli.claude.get_repo_root')
    def test_translate_tools_with_relative_paths(self, mock_repo_root, claude_config):
        """測試轉換帶有相對路徑的工具 (轉為 git-ignore 格式)."""
        mock_repo_root.return_value = Path("/test/repo")
        cli = ClaudeCLI(claude_config)
        tools = ["read(.cafe/config.yaml)", "write(src/main.py)"]
        result = cli.translate_allowed_tools(tools)

        # 相對路徑應該加上 / 前綴 (git-ignore 格式)
        assert "Read(/.cafe/config.yaml)" in result
        assert "Write(/src/main.py)" in result

    @patch('cafe.agents.cli.claude.get_repo_root')
    @patch('cafe.agents.cli.claude.to_git_ignore_path')
    def test_translate_tools_with_absolute_paths(self, mock_to_git, mock_repo_root, claude_config):
        """測試轉換帶有絕對路徑的工具 (轉為 git-ignore 格式)."""
        mock_repo_root.return_value = Path("/test/repo")
        mock_to_git.return_value = "/.cafe/config.yaml"

        cli = ClaudeCLI(claude_config)
        tools = ["read(/test/repo/.cafe/config.yaml)"]
        result = cli.translate_allowed_tools(tools)

        # 絕對路徑應該轉換為 git-ignore 格式
        assert "Read(/.cafe/config.yaml)" in result

    def test_translate_tools_with_git_ignore_format_paths(self, claude_config):
        """測試轉換已經是 git-ignore 格式的路徑 (保持不變)."""
        cli = ClaudeCLI(claude_config)
        tools = ["read(/.cafe/config.yaml)", "write(/src/main.py)"]
        result = cli.translate_allowed_tools(tools)

        # 已經是 git-ignore 格式，保持不變
        assert "Read(/.cafe/config.yaml)" in result
        assert "Write(/src/main.py)" in result

    def test_translate_tools_with_commands(self, claude_config):
        """測試轉換帶有命令的工具."""
        cli = ClaudeCLI(claude_config)
        tools = ["bash(git status)", "bash(npm install)"]
        result = cli.translate_allowed_tools(tools)

        assert "Bash(git status)" in result
        assert "Bash(npm install)" in result

    def test_translate_tools_deduplication(self, claude_config):
        """測試工具去重."""
        cli = ClaudeCLI(claude_config)
        tools = ["read", "read", "write(/.cafe/test)", "write(/.cafe/test)"]
        result = cli.translate_allowed_tools(tools)

        # 應該去除重複項目
        assert len([t for t in result if "Read" in t and "(" not in t]) == 1
        assert len([t for t in result if "Write(/.cafe/test)" in t]) == 1


class TestClaudeCLIAddDirectories:
    """測試 add_directories() 方法."""

    def test_add_directories_to_command(self, claude_config):
        """測試將目錄加入命令."""
        cli = ClaudeCLI(claude_config)
        cmd = ["claude", "-p", "test"]
        directories = ["/path/to/dir1", "/path/to/dir2"]

        result = cli.add_directories(cmd, directories)

        assert "--add-dir" in result
        # 每個目錄都應該有獨立的 --add-dir 參數
        add_dir_count = result.count("--add-dir")
        assert add_dir_count == 2
        assert "/path/to/dir1" in result
        assert "/path/to/dir2" in result

    def test_add_directories_empty_list(self, claude_config):
        """測試空目錄列表."""
        cli = ClaudeCLI(claude_config)
        cmd = ["claude", "-p", "test"]
        directories = []

        result = cli.add_directories(cmd, directories)

        # 空列表不應該改變命令
        assert result == cmd


class TestClaudeCLIGetOutputFormat:
    """測試 get_output_format() 方法."""

    def test_get_output_format(self, claude_config):
        """測試取得輸出格式參數."""
        cli = ClaudeCLI(claude_config)
        result = cli.get_output_format()

        assert "--output-format" in result
        assert "stream-json" in result
        assert "--verbose" in result


class TestClaudeCLIParseResponse:
    """測試 parse_response() 方法."""

    def test_parse_response_with_json_content(self, claude_config):
        """測試解析 JSON 格式的回應."""
        cli = ClaudeCLI(claude_config)
        output_lines = [
            json.dumps({"message": {"content": [{"type": "text", "text": "Hello"}]}}),
            json.dumps({
                "message": {"content": [{"type": "text", "text": "World"}]},
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_creation_input_tokens": 10,
                    "cache_read_input_tokens": 5,
                },
                "total_cost_usd": 0.001,
            }),
        ]

        response, token_usage, permission_denials = cli.parse_response(output_lines)

        assert response == "World"  # 只保留最後一個 fragment
        assert token_usage.input_tokens == 100
        assert token_usage.output_tokens == 50
        assert token_usage.cache_creation_input_tokens == 10
        assert token_usage.cache_read_input_tokens == 5
        assert token_usage.total_cost_usd == 0.001
        assert len(permission_denials) == 0

    def test_parse_response_with_permission_denials(self, claude_config):
        """測試解析包含 permission denials 的回應."""
        cli = ClaudeCLI(claude_config)
        output_lines = [
            json.dumps({
                "message": {"content": [{"type": "text", "text": "Done"}]},
                "permission_denials": [
                    {"tool_name": "Read", "tool_input": {"file_path": "/test/file.txt"}},
                    {"tool_name": "Bash", "tool_input": {"command": "git status"}},
                ],
            }),
        ]

        response, token_usage, permission_denials = cli.parse_response(output_lines)

        assert len(permission_denials) == 2
        assert permission_denials[0].tool_name == "Read"
        assert permission_denials[0].tool_input == {"file_path": "/test/file.txt"}
        assert permission_denials[1].tool_name == "Bash"
        assert permission_denials[1].tool_input == {"command": "git status"}

    def test_parse_response_with_old_format(self, claude_config):
        """測試解析舊格式的回應 (直接 content 欄位)."""
        cli = ClaudeCLI(claude_config)
        output_lines = [
            json.dumps({"content": "Old format response"}),
        ]

        response, token_usage, permission_denials = cli.parse_response(output_lines)

        assert response == "Old format response"

    def test_parse_response_with_non_json_lines(self, claude_config):
        """測試解析包含非 JSON 行的回應."""
        cli = ClaudeCLI(claude_config)
        output_lines = [
            "Some non-JSON output\n",
            json.dumps({"message": {"content": [{"type": "text", "text": "Valid JSON"}]}}),
        ]

        response, token_usage, permission_denials = cli.parse_response(output_lines)

        # 應該忽略非 JSON 行
        assert response == "Valid JSON"


class TestClaudeCLIExtractSessionId:
    """測試 extract_session_id() 方法."""

    def test_extract_session_id_from_output(self, claude_config):
        """測試從輸出中提取 session ID."""
        cli = ClaudeCLI(claude_config)
        output_lines = [
            json.dumps({"session_id": "new-session-456"}),
            json.dumps({"message": {"content": [{"type": "text", "text": "Hello"}]}}),
        ]

        session_id = cli.extract_session_id(output_lines)

        assert session_id == "new-session-456"

    def test_extract_session_id_returns_first_found(self, claude_config):
        """測試提取第一個找到的 session ID."""
        cli = ClaudeCLI(claude_config)
        output_lines = [
            json.dumps({"session_id": "first-session"}),
            json.dumps({"session_id": "second-session"}),
        ]

        session_id = cli.extract_session_id(output_lines)

        assert session_id == "first-session"

    def test_extract_session_id_not_found(self, claude_config):
        """測試找不到 session ID 時回傳 None."""
        cli = ClaudeCLI(claude_config)
        output_lines = [
            json.dumps({"message": {"content": [{"type": "text", "text": "Hello"}]}}),
        ]

        session_id = cli.extract_session_id(output_lines)

        assert session_id is None
