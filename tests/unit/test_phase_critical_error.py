"""Unit tests for critical error handling in phases.

Tests that CriticalPhaseError is properly raised and propagated when agent
execution encounters critical errors like rate limits or missing CLI tools.
"""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from cafe.agents.executor import AgentExecutionError
from cafe.agents.manager import AgentManager
from cafe.core.phase import Phase
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import CriticalPhaseError, PhaseResult, PhaseStatus


class MockPhase(Phase):
    """Mock phase for testing critical error handling."""

    def __init__(self, issue_dir: Path, agent_manager: AgentManager, interactive: bool = True):
        super().__init__(interactive=interactive)
        self.issue_dir = issue_dir
        self.phase_name = "test"
        self.phase_dir = issue_dir / "test"
        self.history_dir = self.phase_dir / "history"
        self.agent_manager = agent_manager
        self.iteration = 1

    def execute(self) -> PhaseResult:
        """Execute phase with critical error handling."""
        try:
            # Simulate calling _execute_agent_iteration
            self._execute_agent_iteration(
                agent_name="test_agent",
                prompt="Test prompt",
                user_input="Test input",
                valid_status_codes=[PhaseStatusCode.CONFIRMED],
            )
            return PhaseResult(status=PhaseStatus.COMPLETED, message="Success")
        except Exception as e:
            return self._handle_exception_in_execute(e, "Test phase failed")


class TestCriticalPhaseError:
    """測試 CriticalPhaseError 建立and屬性."""

    def test_critical_error_creation(self):
        """測試 CriticalPhaseError 可以正確建立並包含必要屬性."""
        error = CriticalPhaseError(
            message="API rate limit exceeded",
            error_type="rate_limit",
            phase_name="SpecPhase"
        )

        assert str(error) == "API rate limit exceeded"
        assert error.error_type == "rate_limit"
        assert error.phase_name == "SpecPhase"

    def test_critical_error_is_exception(self):
        """測試 CriticalPhaseError 是 Exception 子類."""
        error = CriticalPhaseError(
            message="CLI not found",
            error_type="cli_not_found",
            phase_name="PlanPhase"
        )

        assert isinstance(error, Exception)


class TestAgentExecutionCriticalErrors:
    """測試 _execute_agent_iteration 如何處理 critical errors."""

    def test_rate_limit_error_raises_critical_phase_error(self, tmp_path):
        """當 agent 執行遇到 rate_limit 錯誤時, 應拋出 CriticalPhaseError."""
        agent_manager = Mock(spec=AgentManager)
        rate_limit_error = AgentExecutionError(
            "Claude execution failed: Limit reached · resets 2am",
            error_type="rate_limit"
        )
        agent_manager.execute.side_effect = rate_limit_error
        agent_manager.get_agent.return_value = Mock(
            config=Mock(cli=Mock(value="claude"), session_id="test-session")
        )

        phase = MockPhase(tmp_path, agent_manager)
        phase.history_dir.mkdir(parents=True, exist_ok=True)

        with pytest.raises(CriticalPhaseError) as exc_info:
            phase._execute_agent_iteration(
                agent_name="test_agent",
                prompt="Test prompt",
                user_input="Test input",
                valid_status_codes=[PhaseStatusCode.CONFIRMED],
            )

        # 驗證 CriticalPhaseError 屬性
        error = exc_info.value
        assert error.error_type == "rate_limit"
        assert "MockPhase" in error.phase_name
        assert "Limit reached" in str(error)

    def test_cli_not_found_error_raises_critical_phase_error(self, tmp_path):
        """當 CLI 工具找不到時, 應拋出 CriticalPhaseError."""
        agent_manager = Mock(spec=AgentManager)
        cli_error = AgentExecutionError(
            "CLI tool 'claude' not found",
            error_type="cli_not_found"
        )
        agent_manager.execute.side_effect = cli_error
        agent_manager.get_agent.return_value = Mock(
            config=Mock(cli=Mock(value="claude"), session_id="test-session")
        )

        phase = MockPhase(tmp_path, agent_manager)
        phase.history_dir.mkdir(parents=True, exist_ok=True)

        with pytest.raises(CriticalPhaseError) as exc_info:
            phase._execute_agent_iteration(
                agent_name="test_agent",
                prompt="Test prompt",
                user_input="Test input",
                valid_status_codes=[PhaseStatusCode.CONFIRMED],
            )

        error = exc_info.value
        assert error.error_type == "cli_not_found"
        assert "not found" in str(error)

    def test_critical_error_updates_iteration_history(self, tmp_path):
        """Critical error 發生時, 應更新 iteration history 並標記為 critical."""
        agent_manager = Mock(spec=AgentManager)
        rate_limit_error = AgentExecutionError(
            "Rate limit exceeded",
            error_type="rate_limit"
        )
        agent_manager.execute.side_effect = rate_limit_error
        agent_manager.get_agent.return_value = Mock(
            config=Mock(cli=Mock(value="copilot"), session_id="session-123")
        )

        phase = MockPhase(tmp_path, agent_manager)
        phase.history_dir.mkdir(parents=True, exist_ok=True)

        with pytest.raises(CriticalPhaseError):
            phase._execute_agent_iteration(
                agent_name="test_agent",
                prompt="Test prompt",
                user_input="Test input",
                valid_status_codes=[PhaseStatusCode.CONFIRMED],
            )

        # 檢查 iteration history
        iteration_file = phase.history_dir / "iteration_001.json"
        assert iteration_file.exists()

        with open(iteration_file, "r", encoding="utf-8") as f:
            history_data = json.load(f)

        assert history_data["response"] is None
        assert history_data["status_code"] is None
        assert history_data["error"] == "Rate limit exceeded"
        assert history_data["error_type"] == "rate_limit"
        assert history_data["is_critical"] is True

    def test_non_critical_error_not_converted(self, tmp_path):
        """非 critical error 不應被轉換為 CriticalPhaseError."""
        agent_manager = Mock(spec=AgentManager)
        normal_error = AgentExecutionError(
            "Some other error",
            error_type="unknown"
        )
        agent_manager.execute.side_effect = normal_error
        agent_manager.get_agent.return_value = Mock(
            config=Mock(cli=Mock(value="claude"), session_id="test-session")
        )

        phase = MockPhase(tmp_path, agent_manager)
        phase.history_dir.mkdir(parents=True, exist_ok=True)

        # 應該拋出原始 AgentExecutionError, 不是 CriticalPhaseError
        with pytest.raises(AgentExecutionError) as exc_info:
            phase._execute_agent_iteration(
                agent_name="test_agent",
                prompt="Test prompt",
                user_input="Test input",
                valid_status_codes=[PhaseStatusCode.CONFIRMED],
            )

        # 確認不是 CriticalPhaseError
        assert not isinstance(exc_info.value, CriticalPhaseError)
        assert exc_info.value.error_type == "unknown"


class TestHandleExceptionInExecute:
    """測試 Phase._handle_exception_in_execute() 方法."""

    def test_critical_error_is_reraised(self, tmp_path):
        """CriticalPhaseError 應該被 re-raise, 不會被轉換為 PhaseResult."""
        agent_manager = Mock(spec=AgentManager)
        phase = MockPhase(tmp_path, agent_manager)

        critical_error = CriticalPhaseError(
            message="Rate limit exceeded",
            error_type="rate_limit",
            phase_name="TestPhase"
        )

        # 應該直接 re-raise
        with pytest.raises(CriticalPhaseError) as exc_info:
            phase._handle_exception_in_execute(critical_error, "Test failed")

        assert exc_info.value is critical_error

    def test_normal_exception_returns_failed_result(self, tmp_path):
        """一般 Exception 應該被轉換為 PhaseResult(FAILED)."""
        agent_manager = Mock(spec=AgentManager)
        phase = MockPhase(tmp_path, agent_manager)

        normal_error = ValueError("Some validation error")

        result = phase._handle_exception_in_execute(normal_error, "Validation failed")

        assert isinstance(result, PhaseResult)
        assert result.status == PhaseStatus.FAILED
        assert "Validation failed" in result.message
        assert "Some validation error" in result.message

    def test_agent_execution_error_non_critical_returns_failed(self, tmp_path):
        """非 critical  AgentExecutionError 應返回 FAILED result."""
        agent_manager = Mock(spec=AgentManager)
        phase = MockPhase(tmp_path, agent_manager)

        error = AgentExecutionError("Token limit", error_type="token_limit")

        result = phase._handle_exception_in_execute(error, "Agent failed")

        assert isinstance(result, PhaseResult)
        assert result.status == PhaseStatus.FAILED
        assert "Agent failed" in result.message


class TestPhaseExecuteWithCriticalError:
    """測試 Phase.execute() 方法如何處理 critical errors."""

    def test_phase_execute_reraises_critical_error(self, tmp_path):
        """Phase.execute() 中遇到 CriticalPhaseError 時應該 re-raise."""
        agent_manager = Mock(spec=AgentManager)
        rate_limit_error = AgentExecutionError(
            "Rate limit exceeded",
            error_type="rate_limit"
        )
        agent_manager.execute.side_effect = rate_limit_error
        agent_manager.get_agent.return_value = Mock(
            config=Mock(cli=Mock(value="claude"), session_id="test-session")
        )

        phase = MockPhase(tmp_path, agent_manager)
        phase.history_dir.mkdir(parents=True, exist_ok=True)

        # 應該拋出 CriticalPhaseError
        with pytest.raises(CriticalPhaseError) as exc_info:
            phase.execute()

        assert exc_info.value.error_type == "rate_limit"

    def test_phase_execute_returns_failed_for_normal_error(self, tmp_path):
        """Phase.execute() 遇到一般錯誤時應返回 FAILED result."""
        agent_manager = Mock(spec=AgentManager)
        normal_error = ValueError("Config error")
        agent_manager.execute.side_effect = normal_error
        agent_manager.get_agent.return_value = Mock(
            config=Mock(cli=Mock(value="claude"), session_id="test-session")
        )

        phase = MockPhase(tmp_path, agent_manager)
        phase.history_dir.mkdir(parents=True, exist_ok=True)

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "Test phase failed" in result.message
        assert "Config error" in result.message


class TestCriticalErrorPropagation:
    """測試 critical error 如何在整個調用鏈中傳播."""

    def test_error_propagates_through_execute_and_handle_agent_response(self, tmp_path):
        """CriticalPhaseError 應該能穿過 _execute_and_handle_agent_response."""
        agent_manager = Mock(spec=AgentManager)
        rate_limit_error = AgentExecutionError(
            "API limit reached",
            error_type="rate_limit"
        )
        agent_manager.execute.side_effect = rate_limit_error
        agent_manager.get_agent.return_value = Mock(
            config=Mock(cli=Mock(value="gemini"), session_id="test-session")
        )

        class TestPhaseWithPrompt(MockPhase):
            def _generate_prompt(self, user_input: str = "") -> str:
                return "Test prompt"

        phase = TestPhaseWithPrompt(tmp_path, agent_manager)
        phase.history_dir.mkdir(parents=True, exist_ok=True)

        # 直接呼叫 _execute_and_handle_agent_response
        with pytest.raises(CriticalPhaseError) as exc_info:
            phase._execute_and_handle_agent_response(
                agent_name="test_agent",
                user_input="Test input",
                valid_status_codes=[PhaseStatusCode.CONFIRMED],
                allowed_tools=["read", "write"],
            )

        assert exc_info.value.error_type == "rate_limit"
        assert "API limit reached" in str(exc_info.value)
