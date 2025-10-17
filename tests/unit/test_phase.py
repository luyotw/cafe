"""Tests for Phase base class."""

import pytest
from abc import ABC
from unittest.mock import MagicMock

from aaf.core.phase import Phase
from aaf.core.types import PhaseResult, PhaseStatus


class ConcretePhase(Phase):
    """Concrete implementation for testing."""

    def execute(self) -> PhaseResult:
        """測試用的簡單實作"""
        return PhaseResult(status=PhaseStatus.COMPLETED, message="Test phase completed")


class TestPhase:
    """Test Phase base class."""

    def test_phase_is_abstract(self) -> None:
        """測試 Phase 是抽象基礎類別，無法直接實例化"""
        with pytest.raises(TypeError):
            Phase()  # type: ignore

    def test_concrete_phase_can_be_instantiated(self) -> None:
        """測試具體實作的 Phase 可以被實例化"""
        phase = ConcretePhase()
        assert isinstance(phase, Phase)

    def test_execute_returns_phase_result(self) -> None:
        """測試 execute 方法回傳 PhaseResult"""
        phase = ConcretePhase()
        result = phase.execute()

        assert isinstance(result, PhaseResult)
        assert result.status == PhaseStatus.COMPLETED
        assert result.message == "Test phase completed"

    def test_phase_must_implement_execute(self) -> None:
        """測試子類別必須實作 execute 方法"""

        class IncompletePhase(Phase):
            pass

        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            IncompletePhase()  # type: ignore


class TestPhaseWithDependencies:
    """Test Phase with dependencies injection."""

    def test_phase_accepts_dependencies(self) -> None:
        """測試 Phase 可以接受依賴注入（如 git、session manager）"""

        class PhaseWithDeps(Phase):
            def __init__(self, git_ops: MagicMock, session_mgr: MagicMock) -> None:
                self.git = git_ops
                self.session = session_mgr

            def execute(self) -> PhaseResult:
                # Use dependencies
                branch = self.git.get_current_branch()
                return PhaseResult(
                    status=PhaseStatus.COMPLETED, data={"branch": branch}
                )

        mock_git = MagicMock()
        mock_git.get_current_branch.return_value = "feature-branch"
        mock_session = MagicMock()

        phase = PhaseWithDeps(mock_git, mock_session)
        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data["branch"] == "feature-branch"
        mock_git.get_current_branch.assert_called_once()


class TestPhaseErrorHandling:
    """Test Phase error handling."""

    def test_phase_can_return_failed_status(self) -> None:
        """測試 Phase 可以回傳失敗狀態"""

        class FailingPhase(Phase):
            def execute(self) -> PhaseResult:
                return PhaseResult(
                    status=PhaseStatus.FAILED, message="Something went wrong"
                )

        phase = FailingPhase()
        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert result.message == "Something went wrong"

    def test_phase_can_return_data_with_result(self) -> None:
        """測試 Phase 可以在結果中回傳額外資料"""

        class DataPhase(Phase):
            def execute(self) -> PhaseResult:
                return PhaseResult(
                    status=PhaseStatus.COMPLETED,
                    message="Phase completed with data",
                    data={"commits": 3, "files_changed": 5},
                )

        phase = DataPhase()
        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data["commits"] == 3
        assert result.data["files_changed"] == 5

    def test_phase_exception_propagates_to_caller(self) -> None:
        """測試 Phase 執行時的例外會傳播給呼叫者處理"""

        class ExceptionPhase(Phase):
            def execute(self) -> PhaseResult:
                raise ValueError("Test error")

        phase = ExceptionPhase()

        # Phase 不處理例外，由外層的 workflow 處理
        with pytest.raises(ValueError, match="Test error"):
            phase.execute()
