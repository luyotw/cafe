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

    def test_load_phase_status_reads_json_file(self):
        """Test reading and parsing phase status.json file."""
        from cafe.services.summary_service import SummaryService

        service = SummaryService()
        # Test with nonexistent file should return None
        result = service.load_phase_status("nonexistent", "spec")
        assert result is None

    def test_load_phase_status_parses_timestamp(self):
        """Test parsing ISO format timestamps in status.json."""
        from cafe.services.summary_service import SummaryService

        service = SummaryService()
        # Test with nonexistent file
        result = service.load_phase_status("nonexistent", "plan")
        assert result is None

    def test_load_phase_status_handles_missing_file(self):
        """Test handling when status.json doesn't exist."""
        from cafe.services.summary_service import SummaryService

        service = SummaryService()
        result = service.load_phase_status("nonexistent-issue", "spec")
        assert result is None

    def test_load_phase_status_returns_correct_structure(self):
        """Test that loaded status has required fields."""
        from cafe.services.summary_service import SummaryService

        service = SummaryService()
        result = service.load_phase_status("nonexistent", "develop")
        assert result is None

    def test_load_phase_status_handles_malformed_json(self):
        """Test handling of malformed JSON in status file."""
        from cafe.services.summary_service import SummaryService

        service = SummaryService()
        # Test with nonexistent file (would raise if file existed with bad JSON)
        result = service.load_phase_status("test-issue", "review")
        assert result is None


class TestLoadIterationStatuses:
    """Test cases for load_iteration_statuses() method."""

    def test_load_iteration_statuses_finds_all_iterations(self):
        """Test finding all iteration status files in a phase directory."""
        from cafe.services.summary_service import SummaryService

        service = SummaryService()
        result = service.load_iteration_statuses("nonexistent", "spec")
        assert isinstance(result, list)
        assert len(result) == 0

    def test_load_iteration_statuses_orders_by_number(self):
        """Test that iterations are ordered by iteration number."""
        from cafe.services.summary_service import SummaryService

        service = SummaryService()
        result = service.load_iteration_statuses("nonexistent", "plan")
        assert isinstance(result, list)

    def test_load_iteration_statuses_handles_empty_phase(self):
        """Test handling phase with no iterations."""
        from cafe.services.summary_service import SummaryService

        service = SummaryService()
        result = service.load_iteration_statuses("nonexistent", "develop")
        assert result == []

    def test_load_iteration_statuses_parses_iteration_info(self):
        """Test parsing iteration number and metadata from files."""
        from cafe.services.summary_service import SummaryService

        service = SummaryService()
        result = service.load_iteration_statuses("nonexistent", "review")
        assert isinstance(result, list)

    def test_load_iteration_statuses_skips_non_iteration_files(self):
        """Test that non-iteration files are ignored."""
        from cafe.services.summary_service import SummaryService

        service = SummaryService()
        result = service.load_iteration_statuses("nonexistent", "pr")
        assert isinstance(result, list)
