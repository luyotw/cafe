"""Test PR phase iteration management logic."""
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.phases.pr_phase import PRPhase
from cafe.core.types import PhaseStatus


class TestPRPhaseIterationLogic:
    """Test PR phase iteration decision logic."""

    @pytest.fixture
    def mock_dependencies(self):
        """Create mock dependencies."""
        agent_manager = MagicMock()
        permission_handler = MagicMock()
        git_ops = MagicMock()
        github_ops = MagicMock()

        git_ops.get_current_branch.return_value = "test-issue"
        git_ops.get_repo_root.return_value = Path("/tmp")

        return {
            "agent_manager": agent_manager,
            "permission_handler": permission_handler,
            "git_ops": git_ops,
            "github_ops": github_ops,
        }

    def test_get_latest_pr_iteration_info_no_iterations(self, tmp_path, mock_dependencies):
        """Test _get_latest_pr_iteration_info returns None when no iterations exist."""
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            # Test
            result = phase._get_latest_pr_iteration_info()

            # Assert
            assert result is None

    def test_get_latest_pr_iteration_info_with_user_input(self, tmp_path, mock_dependencies):
        """Test _get_latest_pr_iteration_info detects user_input.md."""
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        pr_dir = issue_dir / "pr"
        iteration_dir = pr_dir / "iteration_001"
        iteration_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        # Create context.json with status_code (completed iteration)
        context_file = iteration_dir / "context.json"
        context_file.write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "end_time": "2026-01-27T10:05:00+08:00",
            "status_code": "CAFE_NEEDS_CHANGES"
        }))

        # Create user_input.md
        user_input_file = iteration_dir / "user_input.md"
        user_input_file.write_text("Please fix the bug")

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            # Test
            result = phase._get_latest_pr_iteration_info()

            # Assert
            assert result is not None
            assert result["iteration_number"] == 1
            assert result["has_user_input"] is True
            assert result["end_time"] == datetime.fromisoformat("2026-01-27T10:05:00+08:00")

    def test_get_latest_pr_iteration_info_without_user_input(self, tmp_path, mock_dependencies):
        """Test _get_latest_pr_iteration_info detects missing user_input.md."""
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        pr_dir = issue_dir / "pr"
        iteration_dir = pr_dir / "iteration_001"
        iteration_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        # Create context.json with status_code (completed iteration, no user_input.md)
        context_file = iteration_dir / "context.json"
        context_file.write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "end_time": "2026-01-27T10:05:00+08:00",
            "status_code": "CAFE_CONFIRMED"
        }))

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            # Test
            result = phase._get_latest_pr_iteration_info()

            # Assert
            assert result is not None
            assert result["iteration_number"] == 1
            assert result["has_user_input"] is False

    def test_should_start_new_iteration_no_iterations(self, tmp_path, mock_dependencies):
        """Test _should_start_new_iteration returns True when no iterations exist."""
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            # Test
            result = phase._should_start_new_iteration(None)

            # Assert
            assert result is True

    def test_should_start_new_iteration_no_user_input(self, tmp_path, mock_dependencies):
        """Test _should_start_new_iteration returns False when latest iteration has no user_input."""
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        pr_iteration_info = {
            "iteration_number": 1,
            "has_user_input": False,
            "end_time": datetime.fromisoformat("2026-01-27T10:05:00+08:00")
        }

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            # Test
            result = phase._should_start_new_iteration(pr_iteration_info)

            # Assert
            assert result is False

    def test_should_start_new_iteration_develop_not_processed(self, tmp_path, mock_dependencies):
        """Test _should_start_new_iteration returns False when develop hasn't processed PR iteration."""
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        # Create develop status (older than PR iteration)
        develop_dir = issue_dir / "develop"
        develop_dir.mkdir(parents=True)
        develop_status_file = develop_dir / "status.json"
        develop_status_file.write_text(json.dumps({
            "phase": "develop",
            "status": "completed",
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "end_time": "2026-01-27T10:02:00+08:00",
            "status_code": "CAFE_CONFIRMED"
        }))

        pr_iteration_info = {
            "iteration_number": 1,
            "has_user_input": True,
            "end_time": datetime.fromisoformat("2026-01-27T10:05:00+08:00")  # Newer than develop
        }

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            # Test
            result = phase._should_start_new_iteration(pr_iteration_info)

            # Assert
            assert result is False  # Waiting for develop to process

    def test_should_start_new_iteration_develop_processed(self, tmp_path, mock_dependencies):
        """Test _should_start_new_iteration returns True when develop has processed PR iteration."""
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        # Create develop status (newer than PR iteration)
        develop_dir = issue_dir / "develop"
        develop_dir.mkdir(parents=True)
        develop_status_file = develop_dir / "status.json"
        develop_status_file.write_text(json.dumps({
            "phase": "develop",
            "status": "completed",
            "iteration": 2,
            "timestamp": "2026-01-27T10:10:00+08:00",
            "end_time": "2026-01-27T10:15:00+08:00",
            "status_code": "CAFE_CONFIRMED"
        }))

        pr_iteration_info = {
            "iteration_number": 1,
            "has_user_input": True,
            "end_time": datetime.fromisoformat("2026-01-27T10:05:00+08:00")  # Older than develop
        }

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            # Test
            result = phase._should_start_new_iteration(pr_iteration_info)

            # Assert
            assert result is True  # Should start new iteration

    def test_has_new_commits_true(self, tmp_path, mock_dependencies):
        """Test _has_new_commits returns True when there are unpushed commits."""
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        mock_dependencies["git_ops"].has_unpushed_commits.return_value = True

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            # Test
            result = phase._has_new_commits()

            # Assert
            assert result is True

    def test_has_new_commits_false(self, tmp_path, mock_dependencies):
        """Test _has_new_commits returns False when there are no unpushed commits."""
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        mock_dependencies["git_ops"].has_unpushed_commits.return_value = False

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            # Test
            result = phase._has_new_commits()

            # Assert
            assert result is False

    def test_get_latest_develop_end_time_no_develop(self, tmp_path, mock_dependencies):
        """Test _get_latest_develop_end_time returns None when develop has never run."""
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            # Test
            result = phase._get_latest_develop_end_time()

            # Assert
            assert result is None

    def test_get_latest_develop_end_time_with_end_time(self, tmp_path, mock_dependencies):
        """Test _get_latest_develop_end_time returns end_time when available."""
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        # Create develop status with end_time
        develop_dir = issue_dir / "develop"
        develop_dir.mkdir(parents=True)
        develop_status_file = develop_dir / "status.json"
        develop_status_file.write_text(json.dumps({
            "phase": "develop",
            "status": "completed",
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "end_time": "2026-01-27T10:05:00+08:00",
            "status_code": "CAFE_CONFIRMED"
        }))

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            # Test
            result = phase._get_latest_develop_end_time()

            # Assert
            assert result is not None
            assert result == datetime.fromisoformat("2026-01-27T10:05:00+08:00")

    def test_get_incomplete_iteration_info_no_iterations(self, tmp_path, mock_dependencies):
        """Test _get_incomplete_iteration_info returns None when no iterations exist."""
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            # Test
            result = phase._get_incomplete_iteration_info()

            # Assert
            assert result is None

    def test_get_incomplete_iteration_info_complete_iteration(self, tmp_path, mock_dependencies):
        """Test _get_incomplete_iteration_info returns None when latest iteration is complete."""
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        pr_dir = issue_dir / "pr"
        iteration_dir = pr_dir / "iteration_001"
        iteration_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        # Create context.json with status_code (complete iteration)
        context_file = iteration_dir / "context.json"
        context_file.write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "status_code": "CAFE_CONFIRMED"
        }))

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            # Test
            result = phase._get_incomplete_iteration_info()

            # Assert
            assert result is None

    def test_get_incomplete_iteration_info_incomplete_with_user_input(self, tmp_path, mock_dependencies):
        """Test _get_incomplete_iteration_info detects incomplete iteration with user_input."""
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        pr_dir = issue_dir / "pr"
        iteration_dir = pr_dir / "iteration_001"
        iteration_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        # Create context.json without status_code (incomplete iteration)
        context_file = iteration_dir / "context.json"
        context_file.write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00"
        }))

        # Create user_input.md
        user_input_file = iteration_dir / "user_input.md"
        user_input_file.write_text("Please fix the bug")

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            # Test
            result = phase._get_incomplete_iteration_info()

            # Assert
            assert result is not None
            assert result["iteration_number"] == 1
            assert result["has_user_input"] is True
            assert result["user_input_path"] == user_input_file

    def test_get_incomplete_iteration_info_incomplete_without_user_input(self, tmp_path, mock_dependencies):
        """Test _get_incomplete_iteration_info detects incomplete iteration without user_input."""
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        pr_dir = issue_dir / "pr"
        iteration_dir = pr_dir / "iteration_001"
        iteration_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        # Create context.json without status_code (incomplete iteration)
        context_file = iteration_dir / "context.json"
        context_file.write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00"
        }))

        # No user_input.md

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            # Test
            result = phase._get_incomplete_iteration_info()

            # Assert
            assert result is not None
            assert result["iteration_number"] == 1
            assert result["has_user_input"] is False
            assert result["user_input_path"] is None

    def test_check_waiting_for_develop_no_iterations(self, tmp_path, mock_dependencies):
        """Test _check_waiting_for_develop returns None when no iterations exist."""
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            # Test
            result = phase._check_waiting_for_develop()

            # Assert
            assert result is None

    def test_check_waiting_for_develop_no_user_input(self, tmp_path, mock_dependencies):
        """Test _check_waiting_for_develop returns None when latest iteration has no user_input."""
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        pr_dir = issue_dir / "pr"
        iteration_dir = pr_dir / "iteration_001"
        iteration_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        # Create context.json with status_code but no user_input
        context_file = iteration_dir / "context.json"
        context_file.write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "end_time": "2026-01-27T10:05:00+08:00",
            "status_code": "CAFE_CONFIRMED"
        }))

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            # Test
            result = phase._check_waiting_for_develop()

            # Assert
            assert result is None

    def test_check_waiting_for_develop_waiting(self, tmp_path, mock_dependencies):
        """Test _check_waiting_for_develop returns iteration info when waiting for develop."""
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        pr_dir = issue_dir / "pr"
        iteration_dir = pr_dir / "iteration_001"
        iteration_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        # Create PR iteration with user_input (NEEDS_CHANGES)
        pr_context_file = iteration_dir / "context.json"
        pr_context_file.write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "end_time": "2026-01-27T10:05:00+08:00",
            "status_code": "CAFE_NEEDS_CHANGES"
        }))

        user_input_file = iteration_dir / "user_input.md"
        user_input_file.write_text("Please fix")

        # Develop hasn't run yet (no develop status)
        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            # Test
            result = phase._check_waiting_for_develop()

            # Assert - should be waiting because develop hasn't processed feedback
            assert result is not None
            assert result["iteration_number"] == 1
            assert result["has_user_input"] is True

    def test_check_waiting_for_develop_not_waiting(self, tmp_path, mock_dependencies):
        """Test _check_waiting_for_develop returns None when develop has processed feedback."""
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        pr_dir = issue_dir / "pr"
        iteration_dir = pr_dir / "iteration_001"
        iteration_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        # Create PR iteration with user_input at 10:00
        pr_context_file = iteration_dir / "context.json"
        pr_context_file.write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "end_time": "2026-01-27T10:05:00+08:00",
            "status_code": "CAFE_NEEDS_CHANGES"
        }))

        user_input_file = iteration_dir / "user_input.md"
        user_input_file.write_text("Please fix")

        # Create develop status at 10:10 (after PR feedback)
        develop_dir = issue_dir / "develop"
        develop_dir.mkdir(parents=True)
        develop_status_file = develop_dir / "status.json"
        develop_status_file.write_text(json.dumps({
            "phase": "develop",
            "status": "completed",
            "timestamp": "2026-01-27T10:10:00+08:00",
            "end_time": "2026-01-27T10:10:00+08:00",
            "iteration": 2
        }))

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            # Test
            result = phase._check_waiting_for_develop()

            # Assert - should NOT be waiting because develop has processed feedback
            assert result is None

class TestPhaseComparisonWithMissingEndTime:
    """Test phase comparisons gracefully handle missing end_time."""

    @pytest.fixture
    def mock_dependencies(self):
        """Create mock dependencies."""
        agent_manager = MagicMock()
        permission_handler = MagicMock()
        git_ops = MagicMock()
        github_ops = MagicMock()

        git_ops.get_current_branch.return_value = "test-issue"
        git_ops.get_repo_root.return_value = Path("/tmp")

        return {
            "agent_manager": agent_manager,
            "permission_handler": permission_handler,
            "git_ops": git_ops,
            "github_ops": github_ops,
        }

    def test_get_latest_develop_end_time_returns_none_when_end_time_missing(self, tmp_path, mock_dependencies):
        """Test _get_latest_develop_end_time returns None when end_time is missing (not falling back to timestamp)."""
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        develop_dir = issue_dir / "develop"
        develop_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        # Create develop status.json without end_time
        develop_status_file = develop_dir / "status.json"
        develop_status_file.write_text(json.dumps({
            "phase": "develop",
            "status": "in_progress",
            "timestamp": "2026-01-27T10:00:00+08:00",
            "iteration": 1
        }))

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            # Test
            result = phase._get_latest_develop_end_time()

            # Assert - should return None, not falling back to timestamp
            assert result is None

    def test_get_latest_pr_iteration_info_returns_none_end_time_when_missing(self, tmp_path, mock_dependencies):
        """Test _get_latest_pr_iteration_info returns None for end_time when it's missing (no fallback to timestamp)."""
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        pr_dir = issue_dir / "pr"
        iteration_dir = pr_dir / "iteration_001"
        iteration_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        # Create context.json with status_code but no end_time
        context_file = iteration_dir / "context.json"
        context_file.write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "status_code": "CAFE_NEEDS_CHANGES"
        }))

        user_input_file = iteration_dir / "user_input.md"
        user_input_file.write_text("Please fix the bug")

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            # Test
            result = phase._get_latest_pr_iteration_info()

            # Assert - end_time should be None, not falling back to timestamp
            assert result is not None
            assert result["end_time"] is None

    def test_should_start_new_iteration_returns_false_when_pr_end_time_is_none(self, tmp_path, mock_dependencies):
        """Test _should_start_new_iteration handles None pr_end_time gracefully (returns False to wait)."""
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        pr_dir = issue_dir / "pr"
        iteration_dir = pr_dir / "iteration_001"
        iteration_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        # Create PR iteration without end_time
        context_file = iteration_dir / "context.json"
        context_file.write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "status_code": "CAFE_NEEDS_CHANGES"
        }))

        user_input_file = iteration_dir / "user_input.md"
        user_input_file.write_text("Please fix")

        # Create develop status with end_time
        develop_dir = issue_dir / "develop"
        develop_dir.mkdir(parents=True)
        develop_status_file = develop_dir / "status.json"
        develop_status_file.write_text(json.dumps({
            "phase": "develop",
            "status": "completed",
            "timestamp": "2026-01-27T10:10:00+08:00",
            "end_time": "2026-01-27T10:10:00+08:00",
            "iteration": 1
        }))

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            pr_iteration_info = phase._get_latest_pr_iteration_info()
            result = phase._should_start_new_iteration(pr_iteration_info)

            # Assert - should return False because pr_end_time is None (wait for phase to complete)
            assert result is False

    def test_should_start_new_iteration_returns_false_when_develop_end_time_is_none(self, tmp_path, mock_dependencies):
        """Test _should_start_new_iteration handles None develop_end_time gracefully (returns False to wait)."""
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        pr_dir = issue_dir / "pr"
        iteration_dir = pr_dir / "iteration_001"
        iteration_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        # Create PR iteration with end_time
        context_file = iteration_dir / "context.json"
        context_file.write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "end_time": "2026-01-27T10:05:00+08:00",
            "status_code": "CAFE_NEEDS_CHANGES"
        }))

        user_input_file = iteration_dir / "user_input.md"
        user_input_file.write_text("Please fix")

        # Create develop status without end_time (in progress)
        develop_dir = issue_dir / "develop"
        develop_dir.mkdir(parents=True)
        develop_status_file = develop_dir / "status.json"
        develop_status_file.write_text(json.dumps({
            "phase": "develop",
            "status": "in_progress",
            "timestamp": "2026-01-27T10:10:00+08:00",
            "iteration": 1
        }))

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            pr_iteration_info = phase._get_latest_pr_iteration_info()
            result = phase._should_start_new_iteration(pr_iteration_info)

            # Assert - should return False because develop_end_time is None (waiting for develop to complete)
            assert result is False

    def test_organize_comments_saves_status_code_to_context(self, tmp_path, mock_dependencies):
        """Test that _organize_comments_to_todo_list saves status_code to context.json.

        Regression test: _execute_agent_iteration intentionally saves status_code=None
        to context.json (waiting for checklist validation). _organize_comments_to_todo_list
        must save the final status_code back to context.json before returning.
        Without this fix, context.json ends up with status_code: null.
        """
        from cafe.core.status_codes import PhaseStatusCode

        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        pr_dir = issue_dir / "pr"
        iteration_dir = pr_dir / "iteration_001"
        iteration_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        # Create user_input.md (PR comments)
        user_input_file = iteration_dir / "user_input.md"
        user_input_file.write_text("Please fix the bug in line 5\n")

        # Create output.md with todo list content (agent would write this)
        output_file = iteration_dir / "output.md"
        output_file.write_text("## Todo List\n- [ ] Fix the bug in line 5\n")

        # Create checklist.md with all items checked (validation will pass)
        checklist_file = iteration_dir / "checklist.md"
        checklist_file.write_text("- [x] Read PR comments\n- [x] Organize into todo list\n")

        # Create context.json as _execute_agent_iteration would leave it (status_code=None)
        context_file = iteration_dir / "context.json"
        context_file.write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "status_code": None,
            "response": "CAFE_NEEDS_CHANGES",
            "cli": "claude",
            "session_id": "test-session",
        }))

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )
            phase.iteration = 1
            phase.issue_dir = issue_dir
            phase.phase_dir = pr_dir
            phase.post_todo_list = False

            # Mock _execute_agent_iteration to return NEEDS_CHANGES
            # (simulates agent processing PR comments and deciding changes are needed)
            with patch.object(phase, "_execute_agent_iteration") as mock_exec:
                mock_exec.return_value = (
                    "CAFE_NEEDS_CHANGES",
                    PhaseStatusCode.NEEDS_CHANGES,
                )
                # Mock _merge_allowed_tools
                with patch.object(phase, "_merge_allowed_tools", return_value=["read", "edit"]):
                    # Mock checklist generation (it overwrites our checklist file)
                    with patch("cafe.utils.checklist_generator.generate_pr_comments_checklist"):
                        # Mock checklist validation to pass
                        with patch("cafe.utils.checklist_validator.validate_checklist") as mock_validate:
                            mock_validate.return_value = MagicMock(is_complete=True)
                            # Mock _print_token_usage_summary
                            with patch.object(phase, "_print_token_usage_summary"):
                                result = phase._organize_comments_to_todo_list(
                                    pr_number=0, pr_url="", branch_name="test",
                                )

            # Verify the method returned correctly
            assert result.status == PhaseStatus.COMPLETED
            assert result.data["status_code"] == "CAFE_NEEDS_CHANGES"

            # THE ACTUAL BUG CHECK: verify status_code was persisted to context.json
            saved_context = json.loads(context_file.read_text())
            assert saved_context["status_code"] == "CAFE_NEEDS_CHANGES", \
                f"Bug: status_code in context.json is {saved_context.get('status_code')!r}, expected 'CAFE_NEEDS_CHANGES'"
