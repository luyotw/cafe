"""測試 spec phase 的 output format 驗證功能"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from cafe.phases.spec_phase import SpecPhase
from cafe.core.types import PhaseStatus, PhaseResult
from cafe.core.status_codes import PhaseStatusCode


class TestSpecPhaseVerifyOutputFormat:
    """測試 SpecPhase._verify_output_format 方法"""

    @pytest.fixture
    def setup_spec_phase(self, tmp_path):
        """Setup spec phase with minimal config"""
        # Create directory structure
        issue_dir = tmp_path / ".cafe" / "issues" / "test"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True)

        # Create spec file
        spec_file = spec_dir / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec\n\n問題在這裡", encoding="utf-8")

        # Create mocks
        mock_agent_manager = MagicMock()
        mock_permission_handler = MagicMock()
        mock_git_ops = MagicMock()

        # Create phase instance
        from cafe.core.types import WorkflowMode
        phase = SpecPhase(
            issue_name="test",
            pm_agent="test_pm",
            interactive=True,
            agent_manager=mock_agent_manager,
            permission_handler=mock_permission_handler,
            git_ops=mock_git_ops,
            workflow_mode=WorkflowMode.LOCAL,
        )
        phase.spec_file = str(spec_file)
        phase.iteration = 1
        phase.phase_dir = spec_dir
        phase.issue_dir = issue_dir

        return phase, spec_file

    def test_verify_output_format_exists_and_callable(self, setup_spec_phase):
        """測試 _verify_output_format 方法存在且可呼叫"""
        phase, spec_file = setup_spec_phase

        # Verify method exists
        assert hasattr(phase, '_verify_output_format')
        assert callable(phase._verify_output_format)

    def test_verify_output_format_calls_agent(self, setup_spec_phase):
        """測試 _verify_output_format 會呼叫 agent"""
        phase, spec_file = setup_spec_phase

        # Mock agent_manager.execute
        phase.agent_manager.execute.return_value = (
            "CAFE_NEED_CLARIFICATION",
            {"input_tokens": 50, "output_tokens": 10}
        )

        # Call _verify_output_format
        result, response = phase._verify_output_format(
            agent_name="test_pm",
            response="CAFE_NEED_CLARIFICATION",
            spec_file_pattern="test_pattern",
            allowed_tools=["read", "edit"],
            valid_status_codes=[PhaseStatusCode.NEED_CLARIFICATION, PhaseStatusCode.READY_FOR_REVIEW],
        )

        # Verify agent.execute was called
        assert phase.agent_manager.execute.called

    def test_verify_output_format_returns_corrected_response(self, setup_spec_phase):
        """測試 _verify_output_format 回傳修正後的 response"""
        phase, spec_file = setup_spec_phase

        # Agent returns corrected status code
        phase.agent_manager.execute.return_value = (
            "CAFE_READY_FOR_REVIEW",  # Changed from NEED_CLARIFICATION
            {"input_tokens": 50, "output_tokens": 10}
        )

        # Call _verify_output_format with original response
        result, response = phase._verify_output_format(
            agent_name="test_pm",
            response="CAFE_NEED_CLARIFICATION",
            spec_file_pattern="test_pattern",
            allowed_tools=["read", "edit"],
            valid_status_codes=[PhaseStatusCode.NEED_CLARIFICATION, PhaseStatusCode.READY_FOR_REVIEW],
        )

        # Verify it returns the corrected response
        assert response == "CAFE_READY_FOR_REVIEW"
        assert result is None  # No error

    def test_verify_output_format_handles_verification_failure_gracefully(self, setup_spec_phase):
        """測試當驗證失敗時，gracefully 處理"""
        phase, spec_file = setup_spec_phase

        # Mock agent manager to raise exception
        phase.agent_manager.execute.side_effect = Exception("Test error")

        # Call _verify_output_format
        result, response = phase._verify_output_format(
            agent_name="test_pm",
            response="CAFE_NEED_CLARIFICATION",
            spec_file_pattern="test_pattern",
            allowed_tools=["read", "edit"],
            valid_status_codes=[PhaseStatusCode.NEED_CLARIFICATION],
        )

        # Should return None, None (use original response)
        assert result is None
        assert response is None
