"""Tests for Workflow."""

import pytest
from unittest.mock import MagicMock, patch

from cafe.core.workflow import Workflow, WorkflowError
from cafe.core.phase import Phase
from cafe.core.types import PhaseResult, PhaseStatus


class MockPhase(Phase):
    """Mock phase for testing."""

    def __init__(self, name: str, result: PhaseResult) -> None:
        self.name = name
        self.result = result
        self.executed = False

    def execute(self) -> PhaseResult:
        """測試用的 phase 執行"""
        self.executed = True
        return self.result


class TestWorkflowBasics:
    """Test basic Workflow functionality."""

    def test_init_with_phases(self) -> None:
        """測試使用 phases 初始化 Workflow"""
        phase1 = MockPhase("phase1", PhaseResult(status=PhaseStatus.COMPLETED))
        phase2 = MockPhase("phase2", PhaseResult(status=PhaseStatus.COMPLETED))

        workflow = Workflow(phases=[phase1, phase2])

        assert len(workflow.phases) == 2
        assert workflow.phases[0] == phase1
        assert workflow.phases[1] == phase2

    def test_init_without_phases(self) -> None:
        """測試不帶 phases 初始化 Workflow"""
        workflow = Workflow()

        assert workflow.phases == []

    def test_add_phase(self) -> None:
        """測試動態新增 phase"""
        workflow = Workflow()
        phase = MockPhase("phase1", PhaseResult(status=PhaseStatus.COMPLETED))

        workflow.add_phase(phase)

        assert len(workflow.phases) == 1
        assert workflow.phases[0] == phase


class TestWorkflowExecution:
    """Test workflow execution."""

    def test_execute_single_phase(self) -> None:
        """測試執行單一 phase"""
        phase = MockPhase("phase1", PhaseResult(status=PhaseStatus.COMPLETED))
        workflow = Workflow(phases=[phase])

        results = workflow.execute()

        assert phase.executed
        assert len(results) == 1
        assert results[0].status == PhaseStatus.COMPLETED

    def test_execute_multiple_phases_in_order(self) -> None:
        """測試依序執行多個 phases"""
        phase1 = MockPhase("phase1", PhaseResult(status=PhaseStatus.COMPLETED))
        phase2 = MockPhase("phase2", PhaseResult(status=PhaseStatus.COMPLETED))
        phase3 = MockPhase("phase3", PhaseResult(status=PhaseStatus.COMPLETED))

        workflow = Workflow(phases=[phase1, phase2, phase3])
        results = workflow.execute()

        assert phase1.executed
        assert phase2.executed
        assert phase3.executed
        assert len(results) == 3

    def test_execute_returns_all_results(self) -> None:
        """測試執行回傳所有 phase 結果"""
        phase1 = MockPhase(
            "phase1", PhaseResult(status=PhaseStatus.COMPLETED, message="Phase 1 done")
        )
        phase2 = MockPhase(
            "phase2", PhaseResult(status=PhaseStatus.COMPLETED, message="Phase 2 done")
        )

        workflow = Workflow(phases=[phase1, phase2])
        results = workflow.execute()

        assert results[0].message == "Phase 1 done"
        assert results[1].message == "Phase 2 done"


class TestWorkflowErrorHandling:
    """Test workflow error handling."""

    def test_stop_on_phase_failure(self) -> None:
        """測試當 phase 失敗時停止執行"""
        phase1 = MockPhase("phase1", PhaseResult(status=PhaseStatus.COMPLETED))
        phase2 = MockPhase("phase2", PhaseResult(status=PhaseStatus.FAILED, message="Error"))
        phase3 = MockPhase("phase3", PhaseResult(status=PhaseStatus.COMPLETED))

        workflow = Workflow(phases=[phase1, phase2, phase3])
        results = workflow.execute()

        assert phase1.executed
        assert phase2.executed
        assert not phase3.executed  # Should not execute after failure
        assert len(results) == 2
        assert results[1].status == PhaseStatus.FAILED

    def test_handle_phase_exception(self) -> None:
        """測試處理 phase 執行時的例外"""

        class FailingPhase(Phase):
            def execute(self) -> PhaseResult:
                raise ValueError("Phase error")

        phase1 = MockPhase("phase1", PhaseResult(status=PhaseStatus.COMPLETED))
        phase2 = FailingPhase()
        phase3 = MockPhase("phase3", PhaseResult(status=PhaseStatus.COMPLETED))

        workflow = Workflow(phases=[phase1, phase2, phase3])

        with pytest.raises(WorkflowError, match="Phase execution failed"):
            workflow.execute()

    def test_workflow_with_no_phases_returns_empty(self) -> None:
        """測試沒有 phases 的 workflow 回傳空結果"""
        workflow = Workflow()
        results = workflow.execute()

        assert results == []


class TestWorkflowDataPassing:
    """Test data passing between phases."""

    def test_pass_data_between_phases(self) -> None:
        """測試在 phases 之間傳遞資料"""
        phase1 = MockPhase(
            "phase1",
            PhaseResult(status=PhaseStatus.COMPLETED, data={"key": "value"}),
        )
        phase2 = MockPhase("phase2", PhaseResult(status=PhaseStatus.COMPLETED))

        workflow = Workflow(phases=[phase1, phase2])
        results = workflow.execute()

        # Workflow should make phase1's data available to phase2
        assert results[0].data["key"] == "value"

    def test_access_previous_phase_results(self) -> None:
        """測試存取前一個 phase 的結果"""

        class DataConsumingPhase(Phase):
            def __init__(self, workflow_context: dict) -> None:
                self.context = workflow_context
                self.executed = False

            def execute(self) -> PhaseResult:
                self.executed = True
                # Access data from previous phases via context
                previous_data = self.context.get("previous_results", [])
                return PhaseResult(
                    status=PhaseStatus.COMPLETED,
                    data={"received": len(previous_data)},
                )

        phase1 = MockPhase("phase1", PhaseResult(status=PhaseStatus.COMPLETED))

        workflow = Workflow(phases=[phase1])
        context = {}
        phase2 = DataConsumingPhase(context)
        workflow.add_phase(phase2)

        results = workflow.execute()

        assert phase2.executed
        assert len(results) == 2


class TestWorkflowContext:
    """Test workflow context management."""

    def test_workflow_maintains_context(self) -> None:
        """測試 workflow 維護執行 context"""
        workflow = Workflow()

        assert workflow.context is not None
        assert isinstance(workflow.context, dict)

    def test_context_stores_phase_results(self) -> None:
        """測試 context 儲存 phase 結果"""
        phase1 = MockPhase(
            "phase1",
            PhaseResult(status=PhaseStatus.COMPLETED, data={"step": 1}),
        )
        phase2 = MockPhase(
            "phase2",
            PhaseResult(status=PhaseStatus.COMPLETED, data={"step": 2}),
        )

        workflow = Workflow(phases=[phase1, phase2])
        workflow.execute()

        # Context should contain results from all phases
        assert "phase_results" in workflow.context
        assert len(workflow.context["phase_results"]) == 2


class TestWorkflowSkipPhase:
    """Test workflow phase skipping."""

    def test_skip_phase_does_not_execute(self) -> None:
        """測試跳過的 phase 不會被執行"""
        phase1 = MockPhase("phase1", PhaseResult(status=PhaseStatus.COMPLETED))
        phase2 = MockPhase("phase2", PhaseResult(status=PhaseStatus.COMPLETED))
        phase3 = MockPhase("phase3", PhaseResult(status=PhaseStatus.COMPLETED))

        workflow = Workflow(phases=[phase1, phase2, phase3])
        # Skip phase2
        results = workflow.execute(skip_phases=[1])

        assert phase1.executed
        assert not phase2.executed  # Skipped
        assert phase3.executed
        assert len(results) == 3
        assert results[1].status == PhaseStatus.SKIPPED


class TestWorkflowRetry:
    """Test workflow retry logic."""

    def test_retry_failed_phase(self) -> None:
        """測試重試失敗的 phase"""

        class RetryablePhase(Phase):
            def __init__(self) -> None:
                self.attempt = 0

            def execute(self) -> PhaseResult:
                self.attempt += 1
                if self.attempt < 2:
                    return PhaseResult(
                        status=PhaseStatus.FAILED, message="First attempt failed"
                    )
                return PhaseResult(
                    status=PhaseStatus.COMPLETED, message="Second attempt succeeded"
                )

        phase = RetryablePhase()
        workflow = Workflow(phases=[phase], max_retries=1)

        results = workflow.execute()

        assert phase.attempt == 2
        assert results[0].status == PhaseStatus.COMPLETED

    def test_retry_exhausted_raises_error(self) -> None:
        """測試重試次數用盡後拋出錯誤"""

        class AlwaysFailingPhase(Phase):
            def __init__(self) -> None:
                self.attempt = 0

            def execute(self) -> PhaseResult:
                self.attempt += 1
                return PhaseResult(status=PhaseStatus.FAILED, message="Always fails")

        phase = AlwaysFailingPhase()
        workflow = Workflow(phases=[phase], max_retries=2)

        results = workflow.execute()

        # After exhausting retries, should return failed result
        assert phase.attempt == 3  # Initial + 2 retries
        assert results[0].status == PhaseStatus.FAILED


class TestWorkflowAutoSkipCompleted:
    """Test workflow automatic skip of completed phases."""

    def test_skip_completed_phase_automatically(self, tmp_path) -> None:
        """測試自動跳過已完成的 phase"""
        from pathlib import Path
        from datetime import datetime
        from cafe.core.types import PhaseProgress
        import json

        # Create a phase with status.json indicating completion
        status_file = tmp_path / "status.json"
        progress = PhaseProgress(
            phase="spec",
            status=PhaseStatus.COMPLETED,
            status_code="CONFIRMED",
            timestamp=datetime.now(),
            iteration=1,
            message="Phase completed with CONFIRMED",
        )
        with open(status_file, 'w', encoding='utf-8') as f:
            json.dump(progress.to_dict(), f, ensure_ascii=False, indent=2)

        class PhaseWithStatus(Phase):
            def __init__(self, status_file: Path) -> None:
                self._status_file = status_file
                self.executed = False

            def get_status_file(self) -> Path:
                return self._status_file

            def execute(self) -> PhaseResult:
                self.executed = True
                return PhaseResult(status=PhaseStatus.COMPLETED)

        phase1 = PhaseWithStatus(status_file)
        phase2 = MockPhase("phase2", PhaseResult(status=PhaseStatus.COMPLETED))

        workflow = Workflow(phases=[phase1, phase2])
        results = workflow.execute()

        # Phase1 should be auto-skipped
        assert not phase1.executed
        assert results[0].status == PhaseStatus.SKIPPED
        assert "already completed" in results[0].message.lower()

        # Phase2 should execute normally
        assert phase2.executed
        assert results[1].status == PhaseStatus.COMPLETED

    def test_do_not_skip_incomplete_phase(self, tmp_path) -> None:
        """測試不跳過未完成的 phase"""
        from pathlib import Path
        from datetime import datetime
        from cafe.core.types import PhaseProgress
        import json

        # Create a phase with status.json indicating IN_PROGRESS
        status_file = tmp_path / "status.json"
        progress = PhaseProgress(
            phase="spec",
            status=PhaseStatus.IN_PROGRESS,
            status_code="NEED_CLARIFICATION",
            timestamp=datetime.now(),
            iteration=1,
            message="Iteration 1",
        )
        with open(status_file, 'w', encoding='utf-8') as f:
            json.dump(progress.to_dict(), f, ensure_ascii=False, indent=2)

        class PhaseWithStatus(Phase):
            def __init__(self, status_file: Path) -> None:
                self._status_file = status_file
                self.executed = False

            def get_status_file(self) -> Path:
                return self._status_file

            def execute(self) -> PhaseResult:
                self.executed = True
                return PhaseResult(status=PhaseStatus.COMPLETED)

        phase = PhaseWithStatus(status_file)
        workflow = Workflow(phases=[phase])
        results = workflow.execute()

        # Phase should execute because it's not completed
        assert phase.executed
        assert results[0].status == PhaseStatus.COMPLETED

    def test_do_not_skip_phase_without_status_file(self) -> None:
        """測試不跳過沒有 status.json 的 phase"""
        from pathlib import Path

        class PhaseWithStatus(Phase):
            def __init__(self) -> None:
                self.executed = False

            def get_status_file(self) -> Path:
                return Path("/nonexistent/status.json")

            def execute(self) -> PhaseResult:
                self.executed = True
                return PhaseResult(status=PhaseStatus.COMPLETED)

        phase = PhaseWithStatus()
        workflow = Workflow(phases=[phase])
        results = workflow.execute()

        # Phase should execute because status.json doesn't exist
        assert phase.executed
        assert results[0].status == PhaseStatus.COMPLETED

    def test_do_not_skip_phase_without_get_status_file_method(self) -> None:
        """測試不跳過沒有 get_status_file() 方法的 phase"""
        phase = MockPhase("phase1", PhaseResult(status=PhaseStatus.COMPLETED))
        workflow = Workflow(phases=[phase])
        results = workflow.execute()

        # Phase should execute normally
        assert phase.executed
        assert results[0].status == PhaseStatus.COMPLETED
