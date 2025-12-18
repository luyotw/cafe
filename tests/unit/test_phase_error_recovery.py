"""Unit tests for phase error recovery functionality.

Tests the error recovery mechanism when agent execution fails after writing output files.
"""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from cafe.agents.executor import AgentExecutionError
from cafe.agents.manager import AgentManager
from cafe.core.phase import Phase
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import PhaseResult, PhaseStatus


class MockPhase(Phase):
    """Mock phase for testing error recovery."""

    def __init__(self, issue_dir: Path, agent_manager: AgentManager, interactive: bool = True):
        super().__init__(interactive=interactive)
        self.issue_dir = issue_dir
        self.phase_name = "test"
        self.phase_dir = issue_dir / "test"
        self.history_dir = self.phase_dir / "history"
        self.agent_manager = agent_manager
        self.iteration = 1

    def execute(self) -> PhaseResult:
        """Dummy execute method."""
        return PhaseResult(status=PhaseStatus.COMPLETED, message="Test")


class TestDetectWrittenOutputFiles:
    """測試 _detect_written_output_files 方法."""

    def test_base_implementation_returns_empty_list(self, tmp_path):
        """Base implementation 應返回空列表."""
        agent_manager = Mock(spec=AgentManager)
        phase = MockPhase(tmp_path, agent_manager)

        result = phase._detect_written_output_files()

        assert result == []


class TestRecoverFromWrittenFiles:
    """測試 _recover_from_written_files 方法."""

    def test_no_written_files_returns_none(self, tmp_path):
        """當沒有寫入檔案時, 應返回 (None, None)."""
        agent_manager = Mock(spec=AgentManager)
        phase = MockPhase(tmp_path, agent_manager)
        valid_codes = [PhaseStatusCode.CONFIRMED, PhaseStatusCode.NEED_CLARIFICATION]

        response, status_code = phase._recover_from_written_files([], valid_codes)

        assert response is None
        assert status_code is None

    def test_successful_recovery_with_valid_status_code(self, tmp_path):
        """成功恢復：檔案存在且包含有效 status code."""
        agent_manager = Mock(spec=AgentManager)
        phase = MockPhase(tmp_path, agent_manager)

        # 建立測試檔案
        test_file = tmp_path / "test_output.md"
        test_file.write_text("CAFE_CONFIRMED\n\nThis is the response content.", encoding="utf-8")

        valid_codes = [PhaseStatusCode.CONFIRMED, PhaseStatusCode.NEED_CLARIFICATION]

        response, status_code = phase._recover_from_written_files([test_file], valid_codes)

        assert response == "CAFE_CONFIRMED\n\nThis is the response content."
        assert status_code == PhaseStatusCode.CONFIRMED

    def test_recovery_with_no_status_code_in_file(self, tmp_path):
        """檔案存在但沒有 status code：應返回 (response, None)."""
        agent_manager = Mock(spec=AgentManager)
        phase = MockPhase(tmp_path, agent_manager)

        # 建立測試檔案（沒有 status code）
        test_file = tmp_path / "test_output.md"
        test_file.write_text("This is content without status code.", encoding="utf-8")

        valid_codes = [PhaseStatusCode.CONFIRMED, PhaseStatusCode.NEED_CLARIFICATION]

        response, status_code = phase._recover_from_written_files([test_file], valid_codes)

        assert response == "This is content without status code."
        assert status_code is None

    def test_recovery_fails_when_file_unreadable(self, tmp_path, capsys):
        """檔案無法讀取時, 應返回 (None, None) 並印出警告."""
        agent_manager = Mock(spec=AgentManager)
        phase = MockPhase(tmp_path, agent_manager)

        # 使用不存在檔案
        non_existent_file = tmp_path / "non_existent.md"
        valid_codes = [PhaseStatusCode.CONFIRMED]

        response, status_code = phase._recover_from_written_files([non_existent_file], valid_codes)

        assert response is None
        assert status_code is None

        # 檢查是否印出警告訊息
        captured = capsys.readouterr()
        assert "⚠️  Failed to recover from written files:" in captured.out

    def test_recovery_uses_first_file_when_multiple_files(self, tmp_path):
        """當有多個檔案時, 應使用第一個檔案進行恢復."""
        agent_manager = Mock(spec=AgentManager)
        phase = MockPhase(tmp_path, agent_manager)

        # 建立兩個測試檔案
        file1 = tmp_path / "file1.md"
        file1.write_text("CAFE_CONFIRMED\n\nFirst file content.", encoding="utf-8")

        file2 = tmp_path / "file2.md"
        file2.write_text("CAFE_NEED_CLARIFICATION\n\nSecond file content.", encoding="utf-8")

        valid_codes = [PhaseStatusCode.CONFIRMED, PhaseStatusCode.NEED_CLARIFICATION]

        response, status_code = phase._recover_from_written_files([file1, file2], valid_codes)

        # 應使用第一個檔案
        assert response == "CAFE_CONFIRMED\n\nFirst file content."
        assert status_code == PhaseStatusCode.CONFIRMED


class TestAgentExecutionWithRecovery:
    """測試 _execute_agent_iteration 中錯誤恢復邏輯."""

    def test_normal_execution_without_error(self, tmp_path):
        """正常執行（無錯誤）：應不觸發恢復邏輯."""
        agent_manager = Mock(spec=AgentManager)
        agent_manager.execute.return_value = (
            "CAFE_CONFIRMED\n\nResponse content",
            Mock(input_tokens=100, output_tokens=50),
            [],
            [],
        
            []  # streaming_log
        )
        agent_manager.get_agent.return_value = Mock(
            config=Mock(cli=Mock(value="claude"), session_id="test-session")
        )

        phase = MockPhase(tmp_path, agent_manager)
        phase.history_dir.mkdir(parents=True, exist_ok=True)

        valid_codes = [PhaseStatusCode.CONFIRMED]

        response, status_code = phase._execute_agent_iteration(
            agent_name="test_agent",
            prompt="Test prompt",
            user_input="Test input",
            valid_status_codes=valid_codes,
            allowed_tools=["read", "write"],
        )

        assert response == "CAFE_CONFIRMED\n\nResponse content"
        assert status_code == PhaseStatusCode.CONFIRMED

        # 檢查 iteration history 檔案
        iteration_file = phase.history_dir / "iteration_001.json"
        assert iteration_file.exists()
        with open(iteration_file, "r", encoding="utf-8") as f:
            history_data = json.load(f)
        assert history_data["response"] == "CAFE_CONFIRMED\n\nResponse content"
        assert history_data["status_code"] == "CAFE_CONFIRMED"
        assert "recovered_from_error" not in history_data

    def test_agent_error_with_no_output_files(self, tmp_path):
        """Agent 執行失敗且沒有輸出檔案：應 re-raise 錯誤."""
        agent_manager = Mock(spec=AgentManager)
        error = AgentExecutionError("Token limit exceeded", error_type="TOKEN_LIMIT")
        # 模擬 executor 在錯誤物件上附帶實際 CLI 參數
        error.cli_command_args = ["--output-format", "stream-json", "--include-directories", ".cafe"]
        agent_manager.execute.side_effect = error
        agent_manager.get_agent.return_value = Mock(
            config=Mock(cli=Mock(value="claude"), session_id="test-session")
        )

        phase = MockPhase(tmp_path, agent_manager)
        phase.history_dir.mkdir(parents=True, exist_ok=True)

        valid_codes = [PhaseStatusCode.CONFIRMED]

        with pytest.raises(AgentExecutionError):
            phase._execute_agent_iteration(
                agent_name="test_agent",
                prompt="Test prompt",
                user_input="Test input",
                valid_status_codes=valid_codes,
            )

        # 檢查是否建立了 error log
        error_log = phase.history_dir / "execution_error_001.log"
        assert error_log.exists()

        # 檢查 iteration history 是否更新了錯誤資訊
        iteration_file = phase.history_dir / "iteration_001.json"
        assert iteration_file.exists()
        with open(iteration_file, "r", encoding="utf-8") as f:
            history_data = json.load(f)
        assert history_data["response"] is None
        assert history_data["status_code"] is None
        assert "error" in history_data
        assert "Token limit exceeded" in history_data["error"]
        # 應該記錄實際 CLI 參數, 方便除錯
        assert history_data["cli_command_args"] == [
            "--output-format",
            "stream-json",
            "--include-directories",
            ".cafe",
        ]

    def test_agent_error_with_written_files_successful_recovery(self, tmp_path):
        """Agent 執行失敗但有輸出檔案：應成功恢復."""
        # 建立測試輸出檔案
        output_file = tmp_path / "test" / "test_001.md"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text("CAFE_CONFIRMED\n\nReview completed successfully.", encoding="utf-8")

        agent_manager = Mock(spec=AgentManager)
        agent_manager.execute.side_effect = AgentExecutionError(
            "Token limit exceeded", error_type="TOKEN_LIMIT"
        )
        agent_manager.get_agent.return_value = Mock(
            config=Mock(cli=Mock(value="claude"), session_id="test-session")
        )

        # 建立 phase 並覆寫 _detect_written_output_files
        phase = MockPhase(tmp_path, agent_manager)
        phase._detect_written_output_files = Mock(return_value=[output_file])
        phase.history_dir.mkdir(parents=True, exist_ok=True)

        valid_codes = [PhaseStatusCode.CONFIRMED]

        # 應成功恢復而不 raise 錯誤
        response, status_code = phase._execute_agent_iteration(
            agent_name="test_agent",
            prompt="Test prompt",
            user_input="Test input",
            valid_status_codes=valid_codes,
        )

        assert response == "CAFE_CONFIRMED\n\nReview completed successfully."
        assert status_code == PhaseStatusCode.CONFIRMED

        # 檢查 iteration history 包含恢復 metadata
        iteration_file = phase.history_dir / "iteration_001.json"
        assert iteration_file.exists()
        with open(iteration_file, "r", encoding="utf-8") as f:
            history_data = json.load(f)
        assert history_data["response"] == "CAFE_CONFIRMED\n\nReview completed successfully."
        assert history_data["status_code"] == "CAFE_CONFIRMED"
        assert history_data["recovered_from_error"] is True
        assert "Token limit exceeded" in history_data["original_error"]

        # 檢查是否建立了 error log
        error_log = phase.history_dir / "execution_error_001.log"
        assert error_log.exists()

    def test_agent_error_with_written_files_no_status_code(self, tmp_path):
        """Agent 執行失敗, 有輸出檔案但沒有 status code：應 re-raise 錯誤."""
        # 建立測試輸出檔案（沒有 status code）
        output_file = tmp_path / "test" / "test_001.md"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text("Content without status code.", encoding="utf-8")

        agent_manager = Mock(spec=AgentManager)
        agent_manager.execute.side_effect = AgentExecutionError(
            "Token limit exceeded", error_type="TOKEN_LIMIT"
        )
        agent_manager.get_agent.return_value = Mock(
            config=Mock(cli=Mock(value="claude"), session_id="test-session")
        )

        phase = MockPhase(tmp_path, agent_manager)
        phase._detect_written_output_files = Mock(return_value=[output_file])
        phase.history_dir.mkdir(parents=True, exist_ok=True)

        valid_codes = [PhaseStatusCode.CONFIRMED]

        with pytest.raises(AgentExecutionError):
            phase._execute_agent_iteration(
                agent_name="test_agent",
                prompt="Test prompt",
                user_input="Test input",
                valid_status_codes=valid_codes,
            )

        # 檢查 iteration history 更新了錯誤資訊
        iteration_file = phase.history_dir / "iteration_001.json"
        assert iteration_file.exists()
        with open(iteration_file, "r", encoding="utf-8") as f:
            history_data = json.load(f)
        assert history_data["response"] is None
        assert history_data["error"] == "Token limit exceeded"

    def test_recovery_updates_iteration_history_with_metadata(self, tmp_path):
        """恢復成功時, iteration history 應包含恢復 metadata."""
        output_file = tmp_path / "test" / "test_001.md"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text("CAFE_CONFIRMED\n\nRecovered content.", encoding="utf-8")

        agent_manager = Mock(spec=AgentManager)
        agent_manager.execute.side_effect = AgentExecutionError(
            "API error", error_type="API_ERROR"
        )
        agent_manager.get_agent.return_value = Mock(
            config=Mock(cli=Mock(value="gemini"), session_id="session-123")
        )

        phase = MockPhase(tmp_path, agent_manager)
        phase._detect_written_output_files = Mock(return_value=[output_file])
        phase.history_dir.mkdir(parents=True, exist_ok=True)

        valid_codes = [PhaseStatusCode.CONFIRMED]

        phase._execute_agent_iteration(
            agent_name="test_agent",
            prompt="Test prompt",
            user_input="Test input",
            valid_status_codes=valid_codes,
        )

        # 檢查 metadata
        iteration_file = phase.history_dir / "iteration_001.json"
        with open(iteration_file, "r", encoding="utf-8") as f:
            history_data = json.load(f)

        assert history_data["recovered_from_error"] is True
        assert history_data["original_error"] == "API error"
        assert history_data["response"] == "CAFE_CONFIRMED\n\nRecovered content."
        assert history_data["status_code"] == "CAFE_CONFIRMED"

    def test_failed_recovery_updates_iteration_history_with_error(self, tmp_path):
        """恢復失敗時, iteration history 應包含錯誤資訊."""
        # 建立無法讀取檔案（使用不存在檔案）
        non_existent_file = tmp_path / "non_existent.md"

        agent_manager = Mock(spec=AgentManager)
        agent_manager.execute.side_effect = AgentExecutionError(
            "Session conflict", error_type="SESSION_CONFLICT"
        )
        agent_manager.get_agent.return_value = Mock(
            config=Mock(cli=Mock(value="claude"), session_id="session-456")
        )

        phase = MockPhase(tmp_path, agent_manager)
        phase._detect_written_output_files = Mock(return_value=[non_existent_file])
        phase.history_dir.mkdir(parents=True, exist_ok=True)

        valid_codes = [PhaseStatusCode.CONFIRMED]

        with pytest.raises(AgentExecutionError):
            phase._execute_agent_iteration(
                agent_name="test_agent",
                prompt="Test prompt",
                user_input="Test input",
                valid_status_codes=valid_codes,
            )

        # 檢查錯誤資訊
        iteration_file = phase.history_dir / "iteration_001.json"
        with open(iteration_file, "r", encoding="utf-8") as f:
            history_data = json.load(f)

        assert history_data["response"] is None
        assert history_data["status_code"] is None
        assert history_data["error"] == "Session conflict"
        assert history_data.get("error_type") == "SESSION_CONFLICT"

    def test_error_log_file_created_with_recovery_info(self, tmp_path):
        """錯誤發生時, 應建立包含恢復資訊 error log."""
        output_file = tmp_path / "test" / "test_002.md"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text("CAFE_NEED_CLARIFICATION\n\nQuestion content.", encoding="utf-8")

        agent_manager = Mock(spec=AgentManager)
        agent_manager.execute.side_effect = AgentExecutionError(
            "Rate limit", error_type="RATE_LIMIT"
        )
        agent_manager.get_agent.return_value = Mock(
            config=Mock(cli=Mock(value="copilot"), session_id="session-789")
        )

        phase = MockPhase(tmp_path, agent_manager)
        phase._detect_written_output_files = Mock(return_value=[output_file])
        phase.iteration = 2
        phase.history_dir.mkdir(parents=True, exist_ok=True)

        valid_codes = [PhaseStatusCode.CONFIRMED, PhaseStatusCode.NEED_CLARIFICATION]

        phase._execute_agent_iteration(
            agent_name="test_agent",
            prompt="Test prompt",
            user_input="Test input",
            valid_status_codes=valid_codes,
        )

        # 檢查 error log 內容
        error_log = phase.history_dir / "execution_error_002.log"
        assert error_log.exists()

        log_content = error_log.read_text(encoding="utf-8")
        assert "Error: Rate limit" in log_content
        assert str(output_file) in log_content
        assert "Recovered response: True" in log_content
        assert "Recovered status: CAFE_NEED_CLARIFICATION" in log_content
