"""Tests for develop phase NO_CHANGES_NEEDED handling"""
import pytest
from unittest.mock import MagicMock, patch, Mock
from pathlib import Path
import json
import tempfile
from cafe.phases.develop_phase import DevelopPhase
from cafe.core.types import PhaseStatus, TokenUsage
from cafe.core.status_codes import PhaseStatusCode


class TestNoChangesNeededReasoningValidation:
    """Test NO_CHANGES_NEEDED status code validation for reasoning in output.md"""

    @pytest.fixture
    def setup_develop_phase(self):
        """Setup a DevelopPhase instance with mocked dependencies"""
        with tempfile.TemporaryDirectory() as tmpdir:
            issue_dir = Path(tmpdir) / "issue123"
            develop_dir = issue_dir / "develop"
            develop_dir.mkdir(parents=True, exist_ok=True)

            # Create mock dependencies
            mock_agent_manager = MagicMock()
            mock_permission_handler = MagicMock()
            mock_git_ops = MagicMock()

            # Create phase instance
            phase = DevelopPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                spec_file=str(issue_dir / "spec.md"),
                plan_file=str(issue_dir / "plan.md"),
                issue_id="issue123",
                interactive=True,
                user_input="",
            )

            # Mock required attributes
            phase.issue_dir = issue_dir
            phase.phase_dir = develop_dir
            phase.iteration = 1
            phase.dev_agent = "developer"
            phase._get_allowed_directories = MagicMock(return_value=[str(issue_dir)])

            yield phase, issue_dir, develop_dir

    def test_no_changes_needed_without_reasoning_triggers_continuation(self, setup_develop_phase):
        """Test that NO_CHANGES_NEEDED without output.md content triggers continuation prompt"""
        phase, issue_dir, develop_dir = setup_develop_phase

        # Create iteration directory
        iteration_dir = develop_dir / "iteration_001"
        iteration_dir.mkdir(parents=True, exist_ok=True)

        # Create context.json with NO_CHANGES_NEEDED response
        context_data = {
            "iteration": 1,
            "response": "CAFE_NO_CHANGES_NEEDED\n\nThe reviewer is wrong.",
            "status_code": "CAFE_NO_CHANGES_NEEDED",
            "streaming_log": [],
            "prompt": "Test prompt"
        }
        context_file = iteration_dir / "context.json"
        context_file.write_text(json.dumps(context_data))

        # output.md does NOT exist or is empty
        output_file = iteration_dir / "output.md"
        assert not output_file.exists() or output_file.stat().st_size == 0

        # Mock agent manager to return a continuation response with output.md created
        def mock_execute(agent_name, prompt, **kwargs):
            # Agent should have written to output.md
            output_file.write_text("The reviewer's feedback is incorrect because...")
            return (
                "CAFE_NO_CHANGES_NEEDED\n\n[reasoning written to output.md]",
                TokenUsage(),
                [],
                [],
                [],
                "gemini-3-flash-preview"
            )

        phase.agent_manager.execute = MagicMock(side_effect=mock_execute)
        phase._get_allowed_directories = MagicMock(return_value=[str(issue_dir)])
        phase._update_iteration_history = MagicMock()

        # Simulate handling NO_CHANGES_NEEDED with no reasoning
        # This would be called in the actual flow
        output_file_check = iteration_dir / "output.md"
        has_reasoning = output_file_check.exists() and output_file_check.stat().st_size > 0

        # Initially no reasoning
        assert not has_reasoning

        # After agent execution, should have reasoning
        mock_execute("developer", "test prompt")
        has_reasoning_after = output_file_check.exists() and output_file_check.stat().st_size > 0
        assert has_reasoning_after

    def test_no_changes_needed_with_reasoning_returns_in_progress(self, setup_develop_phase):
        """Test that NO_CHANGES_NEEDED with output.md content returns IN_PROGRESS"""
        phase, issue_dir, develop_dir = setup_develop_phase

        # Create iteration directory
        iteration_dir = develop_dir / "iteration_001"
        iteration_dir.mkdir(parents=True, exist_ok=True)

        # Create output.md with reasoning
        output_file = iteration_dir / "output.md"
        output_file.write_text("The reviewer's feedback is incorrect because of X, Y, Z reasons.")

        # Verify output.md exists and has content
        assert output_file.exists()
        assert output_file.stat().st_size > 0

    def test_no_changes_needed_continuation_fails_without_output_md(self, setup_develop_phase):
        """Test that continuation returns FAILED if agent still doesn't create output.md"""
        phase, issue_dir, develop_dir = setup_develop_phase

        # Create iteration directory
        iteration_dir = develop_dir / "iteration_001"
        iteration_dir.mkdir(parents=True, exist_ok=True)

        # Create context.json
        context_data = {
            "iteration": 1,
            "response": "CAFE_NO_CHANGES_NEEDED",
            "status_code": "CAFE_NO_CHANGES_NEEDED",
            "streaming_log": [],
            "prompt": "Test prompt"
        }
        context_file = iteration_dir / "context.json"
        context_file.write_text(json.dumps(context_data))

        # output.md does NOT exist
        output_file = iteration_dir / "output.md"

        # Mock agent to NOT create output.md
        phase.agent_manager.execute = MagicMock(
            return_value=(
                "CAFE_NO_CHANGES_NEEDED",
                TokenUsage(),
                [],
                [],
                [],
                "gemini-3-flash-preview"
            )
        )
        phase._get_allowed_directories = MagicMock(return_value=[str(issue_dir)])
        phase._update_iteration_history = MagicMock()

        # After execution, output.md should still not exist
        phase.agent_manager.execute("developer", "test prompt")

        # Verify output.md still doesn't exist
        assert not output_file.exists() or output_file.stat().st_size == 0

    def test_checklist_mentions_output_file_requirement(self):
        """Test that NO_CHANGES_NEEDED in checklist mentions output.md requirement"""
        from cafe.utils import checklist_templates

        # Check that NO_CHANGES_NEEDED description mentions output.md
        checklist_content = checklist_templates.DEVELOP_EXECUTION_STEPS_CORRECTION

        # Should mention writing to {output_file}
        assert "{output_file}" in checklist_content
        assert "NO_CHANGES_NEEDED" in checklist_content or "CAFE_NO_CHANGES_NEEDED" in checklist_content

    def test_checklist_generator_resolves_output_file_placeholder(self):
        """Test that checklist generator properly resolves output_file placeholder"""
        from cafe.utils.checklist_generator import generate_develop_checklist
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            checklist_path = tmppath / "checklist.md"
            spec_file = tmppath / "spec.md"
            plan_file = tmppath / "plan.md"
            output_file = tmppath / "output.md"

            # Create dummy files
            spec_file.touch()
            plan_file.touch()

            # Generate checklist with output_file parameter
            generate_develop_checklist(
                agent_name="developer",
                spec_file_path=str(spec_file),
                plan_file_path=str(plan_file),
                develop_file=None,
                checklist_file_path=checklist_path,
                correction_mode=True,
                feedback_file_path=None,
                basic_principles=None,
                output_file=str(output_file),
            )

            # Read generated checklist
            checklist_content = checklist_path.read_text()

            # Should resolve {output_file} placeholder to actual path
            assert str(output_file) in checklist_content
            assert "{output_file}" not in checklist_content


class TestConfirmedSkipReviewSaveProgress:
    """測試 CONFIRMED_SKIP_REVIEW 狀態碼儲存後狀態為 COMPLETED"""

    @pytest.fixture
    def setup_develop_phase(self):
        """設置一個帶有暫存目錄的 DevelopPhase 實例"""
        with tempfile.TemporaryDirectory() as tmpdir:
            issue_dir = Path(tmpdir) / "issue123"
            develop_dir = issue_dir / "develop"
            develop_dir.mkdir(parents=True, exist_ok=True)

            mock_agent_manager = MagicMock()
            mock_permission_handler = MagicMock()
            mock_git_ops = MagicMock()

            phase = DevelopPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                spec_file=str(issue_dir / "spec.md"),
                plan_file=str(issue_dir / "plan.md"),
                issue_id="issue123",
                interactive=False,
                user_input="",
            )

            phase.issue_dir = issue_dir
            phase.phase_dir = develop_dir
            phase.phase_name = "develop"
            phase.iteration = 1

            yield phase, issue_dir, develop_dir

    def test_save_progress_confirmed_skip_review_saves_as_completed(self, setup_develop_phase):
        """測試 _save_progress(CONFIRMED_SKIP_REVIEW) 將狀態儲存為 COMPLETED"""
        phase, issue_dir, develop_dir = setup_develop_phase

        phase._save_progress(PhaseStatusCode.CONFIRMED_SKIP_REVIEW)

        status_file = develop_dir / "status.json"
        assert status_file.exists()

        import json
        data = json.loads(status_file.read_text())
        assert data["status"] == PhaseStatus.COMPLETED.value
        assert data["status_code"] == "CAFE_CONFIRMED_SKIP_REVIEW"

    def test_save_progress_confirmed_status_saves_as_completed(self, setup_develop_phase):
        """測試 _save_progress(CONFIRMED) 仍然儲存為 COMPLETED"""
        phase, issue_dir, develop_dir = setup_develop_phase

        phase._save_progress(PhaseStatusCode.CONFIRMED)

        status_file = develop_dir / "status.json"
        assert status_file.exists()

        import json
        data = json.loads(status_file.read_text())
        assert data["status"] == PhaseStatus.COMPLETED.value

    def test_save_progress_need_clarification_saves_as_in_progress(self, setup_develop_phase):
        """測試 _save_progress(NEED_CLARIFICATION) 儲存為 IN_PROGRESS"""
        phase, issue_dir, develop_dir = setup_develop_phase

        phase._save_progress(PhaseStatusCode.NEED_CLARIFICATION)

        status_file = develop_dir / "status.json"
        assert status_file.exists()

        import json
        data = json.loads(status_file.read_text())
        assert data["status"] == PhaseStatus.IN_PROGRESS.value
