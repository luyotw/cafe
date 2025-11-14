"""Tests for permission denial functionality."""

import pytest
from unittest.mock import MagicMock, patch

from cafe.agents.executor import AgentExecutor
from cafe.core.types import AgentConfig, AgentCLI, PermissionDenial, TokenUsage


class TestPermissionDenialParsing:
    """測試 permission denial 解析功能"""

    def test_claude_permission_denials_parsed_from_stream(self):
        """測試從 Claude stream-json 中解析 permission_denials"""
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        executor = AgentExecutor(config)

        mock_run_result = MagicMock(stdout='{"session_id": "test-session"}', returncode=0)
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"content": "I need to read a file"}\n',
            '{"permission_denials": [{"tool_name": "Read", "tool_input": {"file_path": "/etc/passwd"}}]}\n',
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        with patch("subprocess.run", return_value=mock_run_result), \
             patch("subprocess.Popen", return_value=mock_process), \
             patch("sys.platform", "win32"):
            agent_response = executor._execute_claude("Read /etc/passwd")

            assert len(agent_response.permission_denials) == 1
            denial = agent_response.permission_denials[0]
            assert denial.tool_name == "Read"
            assert denial.tool_input["file_path"] == "/etc/passwd"

    def test_multiple_permission_denials(self):
        """測試解析多個 permission denials"""
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        executor = AgentExecutor(config)

        mock_run_result = MagicMock(stdout='{"session_id": "test-session"}', returncode=0)
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"content": "I need permissions"}\n',
            '{"permission_denials": [{"tool_name": "Read", "tool_input": {"file_path": "/etc/passwd"}}, {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}]}\n',
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        with patch("subprocess.run", return_value=mock_run_result), \
             patch("subprocess.Popen", return_value=mock_process), \
             patch("sys.platform", "win32"):
            agent_response = executor._execute_claude("Test")

            assert len(agent_response.permission_denials) == 2
            assert agent_response.permission_denials[0].tool_name == "Read"
            assert agent_response.permission_denials[1].tool_name == "Bash"

    def test_no_permission_denials(self):
        """測試沒有 permission denials 時返回空列表"""
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        executor = AgentExecutor(config)

        mock_run_result = MagicMock(stdout='{"session_id": "test-session"}', returncode=0)
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"content": "Normal response"}\n',
            "",
        ]
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0

        with patch("subprocess.run", return_value=mock_run_result), \
             patch("subprocess.Popen", return_value=mock_process), \
             patch("sys.platform", "win32"):
            agent_response = executor._execute_claude("Test")

            assert agent_response.permission_denials == []


class TestPermissionDenialModel:
    """測試 PermissionDenial 模型功能"""

    def test_to_allowed_tool_pattern_with_file_path(self):
        """測試將 file_path 轉換為 allowed_tools 格式"""
        denial = PermissionDenial(
            tool_name="Read",
            tool_input={"file_path": "/etc/passwd"}
        )

        pattern = denial.to_allowed_tool_pattern()
        assert pattern == "Read(/etc/passwd)"

    def test_to_allowed_tool_pattern_with_bash_command(self):
        """測試將 bash command 轉換為 allowed_tools 格式"""
        denial = PermissionDenial(
            tool_name="Bash",
            tool_input={"command": "git status"}
        )

        pattern = denial.to_allowed_tool_pattern()
        assert pattern == "Bash(git status)"

    def test_to_allowed_tool_pattern_with_long_command(self):
        """測試長命令只取前兩個詞"""
        denial = PermissionDenial(
            tool_name="Bash",
            tool_input={"command": "git commit -m 'test message'"}
        )

        pattern = denial.to_allowed_tool_pattern()
        assert pattern == "Bash(git commit)"

    def test_to_allowed_tool_pattern_without_params(self):
        """測試沒有特定參數時只返回工具名稱"""
        denial = PermissionDenial(
            tool_name="SomeTool",
            tool_input={}
        )

        pattern = denial.to_allowed_tool_pattern()
        assert pattern == "SomeTool"
