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

        # Create context.json
        context_file = iteration_dir / "context.json"
        context_file.write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "end_time": "2026-01-27T10:05:00+08:00"
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

        # Create context.json only (no user_input.md)
        context_file = iteration_dir / "context.json"
        context_file.write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "end_time": "2026-01-27T10:05:00+08:00"
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
