"""Test develop phase review feedback and PR comments priority."""
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.core.types import PhaseStatus, WorkflowMode
from cafe.core.status_codes import PhaseStatusCode
from cafe.phases.develop_phase import DevelopPhase


class TestDevelopPhaseReviewPriority:
    """Test that review feedback takes priority over PR comments."""

    @pytest.fixture
    def mock_git_ops(self):
        """Create mock GitOperations."""
        git_ops = MagicMock()
        git_ops.has_unpushed_commits.return_value = True
        git_ops.get_latest_unpushed_commit_timestamp.return_value = "2026-01-07T11:36:17+08:00"
        git_ops.get_unpushed_commits.return_value = ["abc123"]
        return git_ops

    @pytest.fixture
    def mock_agent_manager(self):
        """Create mock AgentManager."""
        return MagicMock()

    @pytest.fixture
    def mock_permission_handler(self):
        """Create mock PermissionHandler."""
        return MagicMock()

    def test_review_feedback_prevents_early_completion_when_pr_comments_older(
        self, tmp_path, mock_git_ops, mock_agent_manager, mock_permission_handler
    ):
        """Test that newer review feedback prevents early completion even when PR comments are older."""
        # Arrange: Create directory structure
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Test spec")

        plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("Test plan")

        develop_dir = issue_dir / "develop"
        develop_dir.mkdir(parents=True, exist_ok=True)

        # Create existing develop progress (older than review)
        history_dir = develop_dir / "history"
        history_dir.mkdir(parents=True, exist_ok=True)

        develop_status_file = develop_dir / "status.json"
        develop_status_file.write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-07T10:00:00+08:00",  # Before review and commits
            "status_code": "CAFE_CONFIRMED"
        }))

        # Create review feedback (newer than commits, requesting changes)
        review_dir = issue_dir / "review"
        review_status_file = review_dir / "status.json"
        review_status_file.parent.mkdir(parents=True, exist_ok=True)
        review_status_file.write_text(json.dumps({
            "status_code": "CAFE_NEEDS_CHANGES",
            "timestamp": "2026-01-07T11:39:41+08:00"  # After commits
        }))

        review_output = review_dir / "iteration_001" / "output.md"
        review_output.parent.mkdir(parents=True, exist_ok=True)
        review_output.write_text("Please fix commit message")

        # Mock PR comment timestamp (very old)
        def mock_get_latest_pr_comment_timestamp():
            return datetime.fromisoformat("2026-01-07T03:31:33+00:00")

        # Act: Create phase with pr_number set
        phase = DevelopPhase(
            agent_manager=mock_agent_manager,
            permission_handler=mock_permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            pr_number="123",  # This triggers PR comment check
        )

        with patch.object(phase, '_get_latest_pr_comment_timestamp', mock_get_latest_pr_comment_timestamp):
            # Call _check_if_already_completed_with_review (develop phase specific method)
            result = phase._check_if_already_completed_with_review()

        # Assert: Should return None (continue execution) because review feedback is newer
        assert result is None  # Continue execution, don't return early

    def test_no_review_feedback_allows_normal_pr_flow(
        self, tmp_path, mock_git_ops, mock_agent_manager, mock_permission_handler
    ):
        """Test that when there's no review feedback, PR comment flow proceeds normally."""
        # Arrange: Create directory structure without review feedback
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Test spec")

        plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("Test plan")

        develop_dir = issue_dir / "develop"
        develop_dir.mkdir(parents=True, exist_ok=True)

        # Create existing develop progress
        history_dir = develop_dir / "history"
        history_dir.mkdir(parents=True, exist_ok=True)

        develop_status_file = develop_dir / "status.json"
        develop_status_file.write_text(json.dumps({
            "status": "completed",
            "iteration": 1,
            "timestamp": "2026-01-07T10:00:00+08:00",
            "status_code": "CAFE_CONFIRMED"
        }))

        # No review feedback (no review/status.json)

        # Mock PR comment timestamp (older than commits)
        def mock_get_latest_pr_comment_timestamp():
            return datetime.fromisoformat("2026-01-07T03:31:33+00:00")

        # Act: Create phase with pr_number set
        phase = DevelopPhase(
            agent_manager=mock_agent_manager,
            permission_handler=mock_permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            pr_number="123",
        )

        with patch.object(phase, '_get_latest_pr_comment_timestamp', mock_get_latest_pr_comment_timestamp):
            # Call _check_if_already_completed_with_review (develop phase specific method)
            result = phase._check_if_already_completed_with_review()

        # Assert: Should return None (continue with normal flow) when no review feedback exists
        # The actual early completion is handled by base class _check_if_already_completed()
        assert result is None

    def test_review_feedback_checked_before_pr_comments_without_pr_number(
        self, tmp_path, mock_git_ops, mock_agent_manager, mock_permission_handler
    ):
        """Test that review feedback is checked even without pr_number."""
        # Arrange: Create directory structure
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Test spec")

        plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("Test plan")

        develop_dir = issue_dir / "develop"
        develop_dir.mkdir(parents=True, exist_ok=True)

        # Create existing develop progress
        history_dir = develop_dir / "history"
        history_dir.mkdir(parents=True, exist_ok=True)

        develop_status_file = develop_dir / "status.json"
        develop_status_file.write_text(json.dumps({
            "status": "completed",
            "iteration": 1,
            "timestamp": "2026-01-07T10:00:00+08:00",
            "status_code": "CAFE_CONFIRMED"
        }))

        # Create review feedback
        review_dir = issue_dir / "review"
        review_status_file = review_dir / "status.json"
        review_status_file.parent.mkdir(parents=True, exist_ok=True)
        review_status_file.write_text(json.dumps({
            "status_code": "CAFE_NEEDS_CHANGES",
            "timestamp": "2026-01-07T11:00:00+08:00"
        }))

        review_output = review_dir / "iteration_001" / "output.md"
        review_output.parent.mkdir(parents=True, exist_ok=True)
        review_output.write_text("Please fix issues")

        # Act: Create phase WITHOUT pr_number
        phase = DevelopPhase(
            agent_manager=mock_agent_manager,
            permission_handler=mock_permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            pr_number=None,  # No PR number
        )

        # Call _check_if_already_completed_with_review (develop phase specific method)
        result = phase._check_if_already_completed_with_review()

        # Assert: Should return None (continue execution) because of review feedback
        assert result is None
