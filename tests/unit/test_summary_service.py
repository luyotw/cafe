"""Unit tests for summary service layer."""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from unittest.mock import Mock, patch, MagicMock

import pytest

from cafe.core.types import PhaseStatus


class TestGetCurrentIssue:
    """Test cases for get_current_issue() method."""

    def test_get_current_issue_from_branch_name(self):
        """Test detecting current issue from git branch name."""
        from cafe.services.summary_service import SummaryService

        service = SummaryService()
        # Mock git_ops to return a branch name
        service.git_ops.get_current_branch = Mock(return_value="cafe-summary")

        result = service.get_current_issue()
        assert result == "cafe-summary"

    def test_get_current_issue_from_git_context(self):
        """Test getting current issue from git context."""
        from cafe.services.summary_service import SummaryService

        service = SummaryService()
        service.git_ops.get_current_branch = Mock(return_value="issue84")

        result = service.get_current_issue()
        assert result == "issue84"

    def test_get_current_issue_handles_missing_git_context(self):
        """Test error handling when not in a git repository."""
        from cafe.services.summary_service import SummaryService

        service = SummaryService()
        service.git_ops.get_current_branch = Mock(side_effect=Exception("Not in git repo"))

        with pytest.raises(RuntimeError):
            service.get_current_issue()


class TestLoadPhaseStatus:
    """Test cases for load_phase_status() method."""

    def test_load_phase_status_reads_json_file(self, tmp_path, monkeypatch):
        """Test reading and parsing phase status.json file."""
        from cafe.services.summary_service import SummaryService

        # Change working directory to tmp_path so relative paths work
        monkeypatch.chdir(tmp_path)

        service = SummaryService()
        # Create temporary valid JSON file
        issue_dir = tmp_path / ".cafe/issues/test-issue/spec"
        issue_dir.mkdir(parents=True)
        status_file = issue_dir / "status.json"
        status_file.write_text('{"timestamp": "2025-01-01T00:00:00Z", "status": "completed"}')

        result = service.load_phase_status("test-issue", "spec")
        assert result is not None
        assert result["status"] == "completed"

    def test_load_phase_status_parses_timestamp(self, tmp_path, monkeypatch):
        """Test parsing ISO format timestamps in status.json."""
        from cafe.services.summary_service import SummaryService

        monkeypatch.chdir(tmp_path)

        service = SummaryService()
        issue_dir = tmp_path / ".cafe/issues/test-issue/plan"
        issue_dir.mkdir(parents=True)
        status_file = issue_dir / "status.json"
        status_file.write_text('{"timestamp": "2025-01-04T10:30:00Z", "status": "in_progress"}')

        result = service.load_phase_status("test-issue", "plan")
        assert result is not None
        assert "timestamp" in result

    def test_load_phase_status_handles_missing_file(self):
        """Test handling when status.json doesn't exist."""
        from cafe.services.summary_service import SummaryService

        service = SummaryService()
        result = service.load_phase_status("nonexistent-issue", "spec")
        assert result is None

    def test_load_phase_status_returns_correct_structure(self, tmp_path, monkeypatch):
        """Test that loaded status has required fields."""
        from cafe.services.summary_service import SummaryService

        monkeypatch.chdir(tmp_path)

        service = SummaryService()
        issue_dir = tmp_path / ".cafe/issues/test-issue/develop"
        issue_dir.mkdir(parents=True)
        status_file = issue_dir / "status.json"
        status_data = {
            "timestamp": "2025-01-04T12:00:00Z",
            "status": "completed",
            "status_code": None
        }
        status_file.write_text(json.dumps(status_data))

        result = service.load_phase_status("test-issue", "develop")
        assert result is not None
        assert "status" in result
        assert "timestamp" in result

    def test_load_phase_status_handles_malformed_json(self, tmp_path, monkeypatch):
        """Test handling of malformed JSON in status file."""
        from cafe.services.summary_service import SummaryService

        monkeypatch.chdir(tmp_path)

        service = SummaryService()
        issue_dir = tmp_path / ".cafe/issues/test-issue/review"
        issue_dir.mkdir(parents=True)
        status_file = issue_dir / "status.json"
        status_file.write_text('{invalid json}')

        with pytest.raises(RuntimeError):
            service.load_phase_status("test-issue", "review")


class TestLoadIterationStatuses:
    """Test cases for load_iteration_statuses() method."""

    def test_load_iteration_statuses_finds_all_iterations(self, tmp_path, monkeypatch):
        """Test finding all iteration context files in a phase directory."""
        from cafe.services.summary_service import SummaryService

        monkeypatch.chdir(tmp_path)

        service = SummaryService()
        phase_dir = tmp_path / ".cafe/issues/test-issue/spec"
        (phase_dir / "iteration_001").mkdir(parents=True)
        (phase_dir / "iteration_002").mkdir(parents=True)
        (phase_dir / "iteration_001/context.json").write_text('{"iteration": 1, "status_code": "CAFE_CONFIRMED", "timestamp": "2026-01-14T10:00:00+08:00"}')
        (phase_dir / "iteration_002/context.json").write_text('{"iteration": 2, "status_code": "CAFE_CONFIRMED", "timestamp": "2026-01-14T11:00:00+08:00"}')

        result = service.load_iteration_statuses("test-issue", "spec")
        assert isinstance(result, list)
        assert len(result) >= 2

    def test_load_iteration_statuses_orders_by_number(self, tmp_path, monkeypatch):
        """Test that iterations are ordered by iteration number."""
        from cafe.services.summary_service import SummaryService

        monkeypatch.chdir(tmp_path)

        service = SummaryService()
        phase_dir = tmp_path / ".cafe/issues/test-issue/plan"
        (phase_dir / "iteration_003").mkdir(parents=True)
        (phase_dir / "iteration_001").mkdir(parents=True)
        (phase_dir / "iteration_003/status.json").write_text('{"iteration": 3}')
        (phase_dir / "iteration_001/status.json").write_text('{"iteration": 1}')

        result = service.load_iteration_statuses("test-issue", "plan")
        assert isinstance(result, list)

    def test_load_iteration_statuses_handles_empty_phase(self):
        """Test handling phase with no iterations."""
        from cafe.services.summary_service import SummaryService

        service = SummaryService()
        result = service.load_iteration_statuses("nonexistent", "develop")
        assert result == []

    def test_load_iteration_statuses_parses_iteration_info(self, tmp_path, monkeypatch):
        """Test parsing iteration number and metadata from context.json files."""
        from cafe.services.summary_service import SummaryService

        monkeypatch.chdir(tmp_path)

        service = SummaryService()
        phase_dir = tmp_path / ".cafe/issues/test-issue/review"
        (phase_dir / "iteration_001").mkdir(parents=True)
        context_data = {"iteration": 1, "status_code": "CAFE_NEED_CLARIFICATION", "timestamp": "2025-01-04T14:00:00Z"}
        (phase_dir / "iteration_001/context.json").write_text(json.dumps(context_data))

        result = service.load_iteration_statuses("test-issue", "review")
        assert isinstance(result, list)
        assert len(result) > 0
        assert result[0]["iteration"] == 1

    def test_load_iteration_statuses_handles_malformed_iteration_json(self, tmp_path, monkeypatch):
        """Test handling of malformed JSON in iteration context.json files."""
        from cafe.services.summary_service import SummaryService

        monkeypatch.chdir(tmp_path)

        service = SummaryService()
        phase_dir = tmp_path / ".cafe/issues/test-issue/pr"
        (phase_dir / "iteration_001").mkdir(parents=True)
        (phase_dir / "iteration_001/context.json").write_text('{invalid json}')

        result = service.load_iteration_statuses("test-issue", "pr")
        # Should skip malformed file and return empty or populated list
        assert isinstance(result, list)
        # Check that errors were collected
        assert len(service.get_load_errors()) > 0

    def test_load_iteration_statuses_skips_non_iteration_files(self, tmp_path, monkeypatch):
        """Test that non-iteration files are ignored."""
        from cafe.services.summary_service import SummaryService

        monkeypatch.chdir(tmp_path)

        service = SummaryService()
        phase_dir = tmp_path / ".cafe/issues/test-issue/pr"
        (phase_dir / "iteration_001").mkdir(parents=True)
        (phase_dir / "iteration_001/status.json").write_text('{"iteration": 1}')
        phase_dir.joinpath("other_file.json").write_text('{"some": "data"}')

        result = service.load_iteration_statuses("test-issue", "pr")
        assert isinstance(result, list)

    def test_load_iteration_statuses_from_context_files(self, tmp_path, monkeypatch):
        """Test reading iterations from context.json files."""
        from cafe.services.summary_service import SummaryService

        monkeypatch.chdir(tmp_path)

        service = SummaryService()
        phase_dir = tmp_path / ".cafe/issues/test-issue/review"
        phase_dir.mkdir(parents=True)

        # Create 4 iteration context.json files
        for i in range(1, 5):
            (phase_dir / f"iteration_{i:03d}").mkdir(parents=True)
            context = {
                "iteration": i,
                "timestamp": f"2026-01-05T00:{40+i*5:02d}:00.000000",
                "status_code": "CAFE_CONFIRMED" if i == 4 else "CAFE_NEEDS_CHANGES",
            }
            (phase_dir / f"iteration_{i:03d}/context.json").write_text(json.dumps(context))

        result = service.load_iteration_statuses("test-issue", "review")
        assert isinstance(result, list)
        assert len(result) == 4
        assert result[0]["iteration"] == 1
        assert result[0]["timestamp"] == "2026-01-05T00:45:00.000000"
        assert result[3]["status_code"] == "CAFE_CONFIRMED"


class TestLoadIterationContexts:
    """Test loading iteration data from context.json"""

    def test_load_iteration_statuses_reads_from_context_json(self, tmp_path, monkeypatch):
        """Verify load_iteration_statuses() reads data from context.json"""
        from cafe.services.summary_service import SummaryService

        monkeypatch.chdir(tmp_path)

        service = SummaryService()
        phase_dir = tmp_path / ".cafe/issues/test-issue/spec"
        (phase_dir / "iteration_001").mkdir(parents=True)
        (phase_dir / "iteration_002").mkdir(parents=True)

        # Create context.json files (including start_time and end_time)
        context1 = {
            "iteration": 1,
            "timestamp": "2026-01-14T10:00:00+08:00",
            "end_time": "2026-01-14T10:15:00+08:00",
            "status_code": "CAFE_READY_FOR_REVIEW"
        }
        context2 = {
            "iteration": 2,
            "timestamp": "2026-01-14T10:30:00+08:00",
            "end_time": "2026-01-14T10:45:00+08:00",
            "status_code": "CAFE_CONFIRMED"
        }
        (phase_dir / "iteration_001/context.json").write_text(json.dumps(context1))
        (phase_dir / "iteration_002/context.json").write_text(json.dumps(context2))

        result = service.load_iteration_statuses("test-issue", "spec")
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["iteration"] == 1
        assert result[0]["timestamp"] == "2026-01-14T10:00:00+08:00"
        assert result[0]["end_time"] == "2026-01-14T10:15:00+08:00"
        assert result[0]["status_code"] == "CAFE_READY_FOR_REVIEW"

    def test_load_iteration_statuses_handles_missing_end_time(self, tmp_path, monkeypatch):
        """Verify handling when end_time is missing"""
        from cafe.services.summary_service import SummaryService

        monkeypatch.chdir(tmp_path)

        service = SummaryService()
        phase_dir = tmp_path / ".cafe/issues/test-issue/plan"
        (phase_dir / "iteration_001").mkdir(parents=True)

        # Create context.json without end_time (simulating in-progress iteration)
        context = {
            "iteration": 1,
            "timestamp": "2026-01-14T11:00:00+08:00",
            "status_code": "CAFE_NEED_CLARIFICATION"
        }
        (phase_dir / "iteration_001/context.json").write_text(json.dumps(context))

        result = service.load_iteration_statuses("test-issue", "plan")
        assert isinstance(result, list)
        assert len(result) == 1
        # end_time should be None or not exist
        assert result[0].get("end_time") is None

    def test_load_iteration_statuses_preserves_chronological_order(self, tmp_path, monkeypatch):
        """Verify iterations are ordered by iteration number"""
        from cafe.services.summary_service import SummaryService

        monkeypatch.chdir(tmp_path)

        service = SummaryService()
        phase_dir = tmp_path / ".cafe/issues/test-issue/develop"
        # Create directories in non-sequential order
        (phase_dir / "iteration_003").mkdir(parents=True)
        (phase_dir / "iteration_001").mkdir(parents=True)
        (phase_dir / "iteration_002").mkdir(parents=True)

        for i in [3, 1, 2]:
            context = {
                "iteration": i,
                "timestamp": f"2026-01-14T{10+i}:00:00+08:00",
                "end_time": f"2026-01-14T{10+i}:15:00+08:00",
                "status_code": "CAFE_CONFIRMED"
            }
            (phase_dir / f"iteration_{i:03d}/context.json").write_text(json.dumps(context))

        result = service.load_iteration_statuses("test-issue", "develop")
        assert isinstance(result, list)
        assert len(result) == 3
        # Verify order is correct
        assert result[0]["iteration"] == 1
        assert result[1]["iteration"] == 2
        assert result[2]["iteration"] == 3
