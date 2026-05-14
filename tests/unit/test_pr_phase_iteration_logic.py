"""Test PR phase iteration management logic."""
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.phases.pr_phase import PRPhase
from cafe.core.types import PhaseStatus
from cafe.core.status_codes import PhaseStatusCode


def _write_develop_iteration(
    issue_dir: Path,
    *,
    iteration: int = 1,
    timestamp: str = "2026-01-27T10:00:00+08:00",
    end_time: str | None = None,
    response: str = "confirmed",
) -> None:
    develop_dir = issue_dir / "develop" / f"iteration_{iteration:03d}"
    develop_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "iteration": iteration,
        "timestamp": timestamp,
        "response": response,
    }
    if end_time is not None:
        payload["end_time"] = end_time
    (develop_dir / "context.json").write_text(json.dumps(payload))


def _write_pr_iteration(
    issue_dir: Path,
    *,
    iteration: int = 1,
    timestamp: str = "2026-01-27T10:00:00+08:00",
    end_time: str | None = None,
    response: str | None = None,
    status_code: str | None = None,
    user_input: str | None = None,
) -> None:
    pr_dir = issue_dir / "pr" / f"iteration_{iteration:03d}"
    pr_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "iteration": iteration,
        "timestamp": timestamp,
    }
    if end_time is not None:
        payload["end_time"] = end_time
    if response is not None:
        payload["response"] = response
    if status_code is not None:
        payload["status_code"] = status_code
    (pr_dir / "context.json").write_text(json.dumps(payload))
    if user_input is not None:
        (pr_dir / "user_input.md").write_text(user_input)


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
            "status_code": "needs_changes"
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
            "status_code": "confirmed"
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

    def test_get_latest_pr_iteration_info_accepts_end_time_without_status_code(self, tmp_path, mock_dependencies):
        """Completed iterations should still be visible when only end_time/response are present."""
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        pr_dir = issue_dir / "pr"
        iteration_dir = pr_dir / "iteration_001"
        iteration_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        context_file = iteration_dir / "context.json"
        context_file.write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "end_time": "2026-01-27T10:05:00+08:00",
            "response": "ready_for_review"
        }))

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            result = phase._get_latest_pr_iteration_info()

            assert result is not None
            assert result["iteration_number"] == 1
            assert result["status_code"] == "ready_for_review"

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

        _write_develop_iteration(
            issue_dir,
            iteration=1,
            timestamp="2026-01-27T10:00:00+08:00",
            end_time="2026-01-27T10:02:00+08:00",
        )

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

        _write_develop_iteration(
            issue_dir,
            iteration=2,
            timestamp="2026-01-27T10:10:00+08:00",
            end_time="2026-01-27T10:15:00+08:00",
        )

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

        _write_develop_iteration(
            issue_dir,
            iteration=1,
            timestamp="2026-01-27T10:00:00+08:00",
            end_time="2026-01-27T10:05:00+08:00",
        )

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

    def test_get_pr_review_timestamp_uses_latest_completed_iteration_context(self, tmp_path, mock_dependencies):
        """Local PR timestamp should come from the latest completed PR iteration context."""
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        _write_pr_iteration(
            issue_dir,
            iteration=1,
            timestamp="2026-01-27T10:00:00+08:00",
            end_time="2026-01-27T10:05:00+08:00",
            response="confirmed",
        )

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            result = phase._get_pr_review_timestamp()

            assert result == datetime.fromisoformat("2026-01-27T10:05:00+08:00")

    def test_execute_local_review_mode_uses_iteration_context_for_confirmed_short_circuit(
        self, tmp_path, mock_dependencies
    ):
        """Confirmed local reviews should short-circuit from iteration context without status.json."""
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        _write_pr_iteration(
            issue_dir,
            iteration=1,
            timestamp="2026-01-27T10:00:00+08:00",
            end_time="2026-01-27T10:05:00+08:00",
            status_code="confirmed",
        )

        _write_develop_iteration(
            issue_dir,
            iteration=1,
            timestamp="2026-01-27T10:00:00+08:00",
            end_time="2026-01-27T10:02:00+08:00",
        )

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            result = phase._execute_local_review_mode()

            assert result.status == PhaseStatus.COMPLETED
            assert result.data["status_code"] == "confirmed"
            mock_dependencies["git_ops"].get_diff.assert_not_called()

    def test_execute_local_review_mode_needs_changes_points_back_to_make(
        self, tmp_path, mock_dependencies, capsys
    ):
        """Needs-changes local reviews should direct users back through workflow."""
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

            with patch.object(
                phase,
                "_get_latest_pr_iteration_info",
                return_value={"status_code": PhaseStatusCode.NEEDS_CHANGES.value},
            ):
                with patch.object(phase, "_check_if_develop_is_newer_than_pr", return_value=False):
                    result = phase._execute_local_review_mode()

        captured = capsys.readouterr()
        assert result.status == PhaseStatus.COMPLETED
        assert "cafe make" in captured.out

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
            "status_code": "confirmed"
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

    def test_get_incomplete_iteration_info_treats_end_time_as_complete(self, tmp_path, mock_dependencies):
        """Latest iteration with end_time should not be treated as incomplete."""
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        pr_dir = issue_dir / "pr"
        iteration_dir = pr_dir / "iteration_001"
        iteration_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        context_file = iteration_dir / "context.json"
        context_file.write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "end_time": "2026-01-27T10:05:00+08:00",
            "response": "confirmed"
        }))

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            result = phase._get_incomplete_iteration_info()

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
            "status_code": "confirmed"
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
            "status_code": "needs_changes"
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
            "status_code": "needs_changes"
        }))

        user_input_file = iteration_dir / "user_input.md"
        user_input_file.write_text("Please fix")

        _write_develop_iteration(
            issue_dir,
            iteration=2,
            timestamp="2026-01-27T10:10:00+08:00",
            end_time="2026-01-27T10:10:00+08:00",
        )

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

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        _write_develop_iteration(
            issue_dir,
            iteration=1,
            timestamp="2026-01-27T10:00:00+08:00",
            end_time=None,
            response="work in progress",
        )

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
            "status_code": "needs_changes"
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
            "status_code": "needs_changes"
        }))

        user_input_file = iteration_dir / "user_input.md"
        user_input_file.write_text("Please fix")

        _write_develop_iteration(
            issue_dir,
            iteration=1,
            timestamp="2026-01-27T10:10:00+08:00",
            end_time="2026-01-27T10:10:00+08:00",
        )

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
            "status_code": "needs_changes"
        }))

        user_input_file = iteration_dir / "user_input.md"
        user_input_file.write_text("Please fix")

        _write_develop_iteration(
            issue_dir,
            iteration=1,
            timestamp="2026-01-27T10:10:00+08:00",
            end_time=None,
            response="still working",
        )

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
            "response": "needs_changes",
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
                    "needs_changes",
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
            assert result.data["status_code"] == "needs_changes"

            # THE ACTUAL BUG CHECK: verify status_code was persisted to context.json
            saved_context = json.loads(context_file.read_text())
            assert saved_context["status_code"] == "needs_changes", \
                f"Bug: status_code in context.json is {saved_context.get('status_code')!r}, expected 'needs_changes'"

    def test_organize_comments_retries_when_output_md_empty(self, tmp_path, mock_dependencies):
        """Test that _organize_comments_to_todo_list retries when output.md is missing todo list markers."""
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

        # Create output.md WITHOUT todo list content (simulates agent failure / linter revert)
        output_file = iteration_dir / "output.md"
        output_file.write_text("Some unrelated content\n")

        # Create checklist.md with all items checked
        checklist_file = iteration_dir / "checklist.md"
        checklist_file.write_text("- [x] Read PR comments\n- [x] Organize into todo list\n")

        # Create context.json
        context_file = iteration_dir / "context.json"
        context_file.write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "status_code": None,
            "response": "needs_changes",
            "cli": "claude",
            "session_id": "test-session",
        }))

        # Track retry calls to simulate agent fixing output.md on retry
        retry_call_count = 0

        def mock_agent_execute(agent_name, prompt, **kwargs):
            nonlocal retry_call_count
            retry_call_count += 1
            # On first retry, agent writes the todo list to output.md
            output_file.write_text("## Todo List\n- [ ] Fix the bug in line 5\n")
            return ("needs_changes", MagicMock(), [], [], [], None)

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

            with patch.object(phase, "_execute_agent_iteration") as mock_exec:
                mock_exec.return_value = (
                    "needs_changes",
                    PhaseStatusCode.NEEDS_CHANGES,
                )
                with patch.object(phase, "_merge_allowed_tools", return_value=["read", "edit"]):
                    with patch("cafe.utils.checklist_generator.generate_pr_comments_checklist"):
                        with patch("cafe.utils.checklist_validator.validate_checklist") as mock_validate:
                            mock_validate.return_value = MagicMock(is_complete=True)
                            with patch.object(phase, "_print_token_usage_summary"):
                                with patch.object(phase, "_get_allowed_directories", return_value=[str(tmp_path)]):
                                    # Mock agent_manager.execute for retry calls
                                    mock_dependencies["agent_manager"].execute.side_effect = mock_agent_execute
                                    result = phase._organize_comments_to_todo_list(
                                        pr_number=0, pr_url="", branch_name="test",
                                    )

            # Verify: should succeed after retry
            assert result.status == PhaseStatus.COMPLETED
            assert result.data["status_code"] == "needs_changes"
            # Verify agent_manager.execute was called (retry happened)
            assert retry_call_count == 1

    def test_organize_comments_fails_after_max_retries(self, tmp_path, mock_dependencies):
        """Test that _organize_comments_to_todo_list returns FAILED after exhausting all retries."""
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

        # Create output.md WITHOUT todo list content (agent never fixes it)
        output_file = iteration_dir / "output.md"
        output_file.write_text("The linter reverted my changes\n")

        # Create checklist.md with all items checked
        checklist_file = iteration_dir / "checklist.md"
        checklist_file.write_text("- [x] Read PR comments\n- [x] Organize into todo list\n")

        # Create context.json
        context_file = iteration_dir / "context.json"
        context_file.write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "status_code": None,
            "response": "needs_changes",
            "cli": "claude",
            "session_id": "test-session",
        }))

        retry_call_count = 0

        def mock_agent_execute(agent_name, prompt, **kwargs):
            nonlocal retry_call_count
            retry_call_count += 1
            # Agent never fixes output.md - returns but doesn't write todo list
            return ("needs_changes", MagicMock(), [], [], [], None)

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

            with patch.object(phase, "_execute_agent_iteration") as mock_exec:
                mock_exec.return_value = (
                    "needs_changes",
                    PhaseStatusCode.NEEDS_CHANGES,
                )
                with patch.object(phase, "_merge_allowed_tools", return_value=["read", "edit"]):
                    with patch("cafe.utils.checklist_generator.generate_pr_comments_checklist"):
                        with patch("cafe.utils.checklist_validator.validate_checklist") as mock_validate:
                            mock_validate.return_value = MagicMock(is_complete=True)
                            with patch.object(phase, "_print_token_usage_summary"):
                                with patch.object(phase, "_get_allowed_directories", return_value=[str(tmp_path)]):
                                    mock_dependencies["agent_manager"].execute.side_effect = mock_agent_execute
                                    result = phase._organize_comments_to_todo_list(
                                        pr_number=0, pr_url="", branch_name="test",
                                    )

            # Verify: should fail after max retries
            assert result.status == PhaseStatus.FAILED
            assert "missing todo list markers" in result.message
            assert "after 3 retries" in result.message
            # Verify all 3 retries were attempted
            assert retry_call_count == 3


class TestGetLastSeenCommentIds:
    """Test _get_last_seen_comment_ids() method for retrieving previously seen comment IDs."""

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

    def _make_phase(self, issue_dir, mock_dependencies):
        """Helper to create PRPhase instance."""
        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Test Spec")
        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            return PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

    def test_no_pr_iterations_returns_empty_set(self, tmp_path, mock_dependencies):
        """Test _get_last_seen_comment_ids returns empty set when no PR iterations exist.

        情境：pr/ 目錄不存在或沒有任何 iteration
        預期：返回空 set（向後兼容）
        """
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = self._make_phase(issue_dir, mock_dependencies)
            result = phase._get_last_seen_comment_ids()

        assert result == set()

    def test_reads_from_artifact_file_when_present(self, tmp_path, mock_dependencies):
        """Test _get_last_seen_comment_ids reads runtime artifact first."""
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        artifact_dir = issue_dir / "pr" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "pr_last_seen_comments.json").write_text(
            json.dumps({"last_seen_comment_ids": ["A1", "B2"]})
        )

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = self._make_phase(issue_dir, mock_dependencies)
            result = phase._get_last_seen_comment_ids()

        assert result == {"A1", "B2"}

    def test_artifact_takes_precedence_over_legacy_context(self, tmp_path, mock_dependencies):
        """Artifact data should override older context.json snapshots."""
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        pr_dir = issue_dir / "pr"

        iter_dir = pr_dir / "iteration_001"
        iter_dir.mkdir(parents=True)
        (iter_dir / "context.json").write_text(
            json.dumps(
                {
                    "iteration": 1,
                    "status_code": "ready_for_review",
                    "last_seen_comment_ids": ["OLD_CONTEXT"],
                }
            )
        )

        artifact_dir = pr_dir / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "pr_last_seen_comments.json").write_text(
            json.dumps({"last_seen_comment_ids": ["NEW_ARTIFACT"]})
        )

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = self._make_phase(issue_dir, mock_dependencies)
            result = phase._get_last_seen_comment_ids()

        assert result == {"NEW_ARTIFACT"}

    def test_latest_iteration_has_last_seen_comment_ids(self, tmp_path, mock_dependencies):
        """Test _get_last_seen_comment_ids returns IDs from latest iteration context.

        情境：最新一輪 iteration 的 context.json 包含 last_seen_comment_ids
        預期：返回該 set
        """
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        pr_dir = issue_dir / "pr"
        iter_dir = pr_dir / "iteration_001"
        iter_dir.mkdir(parents=True)

        context_file = iter_dir / "context.json"
        context_file.write_text(json.dumps({
            "iteration": 1,
            "status_code": "ready_for_review",
            "last_seen_comment_ids": ["123456", "IC_kwDOQCpNoM111", "789012"]
        }))

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = self._make_phase(issue_dir, mock_dependencies)
            result = phase._get_last_seen_comment_ids()

        assert result == {"123456", "IC_kwDOQCpNoM111", "789012"}

    def test_searches_backwards_when_latest_lacks_field(self, tmp_path, mock_dependencies):
        """Test _get_last_seen_comment_ids searches backwards to find the field.

        情境：iteration_002（comment-fetch 輪）沒有 last_seen_comment_ids，
              但 iteration_001（push 輪）有
        預期：找到 iteration_001 的 IDs 並返回
        """
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        pr_dir = issue_dir / "pr"

        # iteration_001: push iteration with last_seen_comment_ids
        iter_001 = pr_dir / "iteration_001"
        iter_001.mkdir(parents=True)
        (iter_001 / "context.json").write_text(json.dumps({
            "iteration": 1,
            "status_code": "ready_for_review",
            "last_seen_comment_ids": ["R1", "T1"]
        }))

        # iteration_002: comment-fetch iteration without last_seen_comment_ids
        iter_002 = pr_dir / "iteration_002"
        iter_002.mkdir(parents=True)
        (iter_002 / "context.json").write_text(json.dumps({
            "iteration": 2,
            "status_code": "needs_changes"
            # last_seen_comment_ids 欄位不存在
        }))

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = self._make_phase(issue_dir, mock_dependencies)
            result = phase._get_last_seen_comment_ids()

        assert result == {"R1", "T1"}

    def test_no_iteration_has_last_seen_comment_ids_returns_empty(self, tmp_path, mock_dependencies):
        """Test _get_last_seen_comment_ids returns empty set when no iteration has the field.

        情境：所有 iteration 都沒有 last_seen_comment_ids（舊版本 context.json）
        預期：返回空 set（向後兼容）
        """
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        pr_dir = issue_dir / "pr"

        iter_001 = pr_dir / "iteration_001"
        iter_001.mkdir(parents=True)
        (iter_001 / "context.json").write_text(json.dumps({
            "iteration": 1,
            "status_code": "ready_for_review"
            # 沒有 last_seen_comment_ids（舊版本）
        }))

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = self._make_phase(issue_dir, mock_dependencies)
            result = phase._get_last_seen_comment_ids()

        assert result == set()
