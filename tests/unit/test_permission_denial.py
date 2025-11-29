"""Tests for permission denial functionality."""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cafe.agents.executor import AgentExecutor
from cafe.agents.manager import AgentManager
from cafe.core.permission import PermissionHandler
from cafe.core.phase import Phase
from cafe.core.types import AgentConfig, AgentCLI, PermissionDenial, TokenUsage, PhaseResult, PhaseStatus


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
        assert pattern == "read(/etc/passwd)"

    def test_to_allowed_tool_pattern_with_bash_command(self):
        """測試將 bash command 轉換為 allowed_tools 格式"""
        denial = PermissionDenial(
            tool_name="Bash",
            tool_input={"command": "git status"}
        )

        pattern = denial.to_allowed_tool_pattern()
        assert pattern == "bash(git status)"

    def test_to_allowed_tool_pattern_with_long_command(self):
        """測試長命令只取前兩個詞"""
        denial = PermissionDenial(
            tool_name="Bash",
            tool_input={"command": "git commit -m 'test message'"}
        )

        pattern = denial.to_allowed_tool_pattern()
        assert pattern == "bash(git commit)"

    def test_to_allowed_tool_pattern_without_params(self):
        """測試沒有特定參數時只返回工具名稱"""
        denial = PermissionDenial(
            tool_name="SomeTool",
            tool_input={}
        )

        pattern = denial.to_allowed_tool_pattern()
        assert pattern == "sometool"


class TestPermissionDenialStorage:
    """測試 permission denial 儲存功能"""

    def test_permission_denials_saved_to_iteration_json(self, tmp_path: Path):
        """測試 permission_denials 被儲存到 iteration JSON"""
        # 創建測試 phase
        class TestPhase(Phase):
            def __init__(self, agent_manager: MagicMock, permission_handler: MagicMock, history_dir: str):
                self.agent_manager = agent_manager
                self.permission_handler = permission_handler
                self.history_dir = Path(history_dir)
                self.iteration = 1

            def execute(self) -> PhaseResult:
                # 模擬 agent 返回 permission denials
                response, token_usage, permission_denials, _, _ = self.agent_manager.execute(
                    "David", "Test prompt"
                )

                self._update_iteration_history(
                    phase_specific_data={
                        "response": response,
                        "permission_denials": [denial.model_dump() for denial in permission_denials]
                    }
                )

                return PhaseResult(
                    status=PhaseStatus.COMPLETED,
                    message="Test completed"
                )

            def _save_progress(self, status_code) -> None:
                """Mock save progress"""
                pass

        # 設置 mock
        agent_manager = MagicMock(spec=AgentManager)
        permission_denials = [
            PermissionDenial(
                tool_name="Read",
                tool_input={"file_path": "/etc/passwd"}
            )
        ]
        agent_manager.execute.return_value = (
            "I need permission to read /etc/passwd",
            TokenUsage(),
            permission_denials,
            None,
            []
        )
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        # 創建 history 目錄
        history_dir = tmp_path / "history"
        history_dir.mkdir(parents=True)

        phase = TestPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            history_dir=str(history_dir)
        )

        # 執行 phase
        result = phase.execute()

        # 檢查結果
        assert result.status == PhaseStatus.COMPLETED

        # 檢查 iteration JSON 是否包含 permission_denials
        iteration_file = history_dir / "iteration_001.json"
        assert iteration_file.exists()

        with open(iteration_file, "r") as f:
            iteration_data = json.load(f)

        assert "permission_denials" in iteration_data
        assert len(iteration_data["permission_denials"]) == 1
        assert iteration_data["permission_denials"][0]["tool_name"] == "Read"
        assert iteration_data["permission_denials"][0]["tool_input"]["file_path"] == "/etc/passwd"

    def test_empty_permission_denials_saved(self, tmp_path: Path):
        """測試沒有 permission denials 時儲存空列表"""
        class TestPhase(Phase):
            def __init__(self, agent_manager: MagicMock, permission_handler: MagicMock, history_dir: str):
                self.agent_manager = agent_manager
                self.permission_handler = permission_handler
                self.history_dir = Path(history_dir)
                self.iteration = 1

            def execute(self) -> PhaseResult:
                response, token_usage, permission_denials, _, _ = self.agent_manager.execute(
                    "David", "Test prompt"
                )

                self._update_iteration_history(
                    phase_specific_data={
                        "response": response,
                        "permission_denials": [denial.model_dump() for denial in permission_denials]
                    }
                )

                return PhaseResult(
                    status=PhaseStatus.COMPLETED,
                    message="Test completed"
                )

            def _save_progress(self, status_code) -> None:
                """Mock save progress"""
                pass

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = (
            "Normal response without denials",
            TokenUsage(),
            [],  # No permission denials
            None,
            []
        )
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        history_dir = tmp_path / "history"
        history_dir.mkdir(parents=True)

        phase = TestPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            history_dir=str(history_dir)
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED

        iteration_file = history_dir / "iteration_001.json"
        assert iteration_file.exists()

        with open(iteration_file, "r") as f:
            iteration_data = json.load(f)

        assert "permission_denials" in iteration_data
        assert iteration_data["permission_denials"] == []

    def test_multiple_permission_denials_storage(self, tmp_path: Path):
        """測試多個 permission denials 的儲存"""
        class TestPhase(Phase):
            def __init__(self, agent_manager: MagicMock, permission_handler: MagicMock, history_dir: str):
                self.agent_manager = agent_manager
                self.permission_handler = permission_handler
                self.history_dir = Path(history_dir)
                self.iteration = 1

            def execute(self) -> PhaseResult:
                response, token_usage, permission_denials, _, _ = self.agent_manager.execute(
                    "David", "Test prompt"
                )

                self._update_iteration_history(
                    phase_specific_data={
                        "response": response,
                        "permission_denials": [denial.model_dump() for denial in permission_denials]
                    }
                )

                return PhaseResult(
                    status=PhaseStatus.COMPLETED,
                    message="Test completed"
                )

            def _save_progress(self, status_code) -> None:
                """Mock save progress"""
                pass

        agent_manager = MagicMock(spec=AgentManager)
        permission_denials = [
            PermissionDenial(
                tool_name="Read",
                tool_input={"file_path": "/etc/passwd"}
            ),
            PermissionDenial(
                tool_name="Bash",
                tool_input={"command": "rm -rf /"}
            ),
            PermissionDenial(
                tool_name="Write",
                tool_input={"file_path": "/etc/hosts"}
            )
        ]
        agent_manager.execute.return_value = (
            "I need multiple permissions",
            TokenUsage(),
            permission_denials,
            None,
            []
        )
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        history_dir = tmp_path / "history"
        history_dir.mkdir(parents=True)

        phase = TestPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            history_dir=str(history_dir)
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED

        iteration_file = history_dir / "iteration_001.json"
        assert iteration_file.exists()

        with open(iteration_file, "r") as f:
            iteration_data = json.load(f)

        assert "permission_denials" in iteration_data
        assert len(iteration_data["permission_denials"]) == 3
        assert iteration_data["permission_denials"][0]["tool_name"] == "Read"
        assert iteration_data["permission_denials"][1]["tool_name"] == "Bash"
        assert iteration_data["permission_denials"][2]["tool_name"] == "Write"
