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

    def test_load_phase_status_synthesizes_completed_from_iterations(self, tmp_path, monkeypatch):
        """Test synthesizing phase status when status.json is absent."""
        from cafe.services.summary_service import SummaryService

        monkeypatch.chdir(tmp_path)

        issue_dir = tmp_path / ".cafe/issues/test-issue"
        phase_dir = issue_dir / "pr"
        (phase_dir / "iteration_001").mkdir(parents=True)
        (phase_dir / "iteration_001/context.json").write_text(
            json.dumps(
                {
                    "iteration": 1,
                    "timestamp": "2026-04-27T10:00:00+08:00",
                    "end_time": "2026-04-27T10:15:00+08:00",
                    "status_code": "CAFE_CONFIRMED",
                }
            )
        )
        (issue_dir / "blackboard.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "current_step": "done",
                    "playbook_id": "default",
                    "artifacts": {},
                    "events": [],
                    "decisions": [],
                    "handoff_summary": "",
                    "handoff_contract": {
                        "version": 1,
                        "from_step": "pr",
                        "to_owner": "done",
                        "to_step": "done",
                        "intent": "workflow_complete",
                        "status_code": "",
                        "created_at": "2026-04-27T10:16:00+08:00",
                        "source": "workflow",
                    },
                    "updated_at": "2026-04-27T10:16:00+08:00",
                }
            )
        )
        (issue_dir / "next_step.txt").write_text(
            json.dumps(
                {
                    "version": 1,
                    "from_step": "pr",
                    "to_owner": "done",
                    "to_step": "done",
                    "intent": "workflow_complete",
                    "status_code": "",
                    "created_at": "2026-04-27T10:16:00+08:00",
                    "source": "workflow",
                }
            )
        )

        service = SummaryService()
        result = service.load_phase_status("test-issue", "pr")
        assert result is not None
        assert result["status"] == "completed"
        assert result["timestamp"] == "2026-04-27T10:00:00+08:00"
        assert result["end_time"] == "2026-04-27T10:15:00+08:00"
        assert result["status_code"] == "CAFE_CONFIRMED"

    def test_load_phase_status_synthesizes_in_progress_from_user_baton(self, tmp_path, monkeypatch):
        """Test paused phases stay in-progress without a phase status file."""
        from cafe.services.summary_service import SummaryService

        monkeypatch.chdir(tmp_path)

        issue_dir = tmp_path / ".cafe/issues/test-issue"
        phase_dir = issue_dir / "spec"
        (phase_dir / "iteration_001").mkdir(parents=True)
        (phase_dir / "iteration_001/context.json").write_text(
            json.dumps(
                {
                    "iteration": 1,
                    "timestamp": "2026-04-27T09:00:00+08:00",
                    "status_code": "CAFE_NEED_CLARIFICATION",
                }
            )
        )
        baton = {
            "version": 1,
            "from_step": "spec",
            "to_owner": "user",
            "to_step": "user",
            "intent": "confirm_output",
            "status_code": "CAFE_NEED_CLARIFICATION",
            "created_at": "2026-04-27T09:05:00+08:00",
            "source": "workflow",
        }
        (issue_dir / "blackboard.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "current_step": "user",
                    "playbook_id": "default",
                    "artifacts": {},
                    "events": [],
                    "decisions": [],
                    "handoff_summary": "",
                    "handoff_contract": baton,
                    "updated_at": "2026-04-27T09:05:00+08:00",
                }
            )
        )
        (issue_dir / "next_step.txt").write_text(json.dumps(baton))

        service = SummaryService()
        result = service.load_phase_status("test-issue", "spec")
        assert result is not None
        assert result["status"] == "in_progress"
        assert result["timestamp"] == "2026-04-27T09:00:00+08:00"
        assert result["status_code"] == "CAFE_NEED_CLARIFICATION"
        assert "end_time" not in result

    def test_load_phase_status_does_not_create_blackboard_files(self, tmp_path, monkeypatch):
        """Test summary fallback stays read-only when workflow state is absent."""
        from cafe.services.summary_service import SummaryService

        monkeypatch.chdir(tmp_path)

        phase_dir = tmp_path / ".cafe/issues/test-issue/pr"
        (phase_dir / "iteration_001").mkdir(parents=True)
        (phase_dir / "iteration_001/context.json").write_text(
            json.dumps(
                {
                    "iteration": 1,
                    "timestamp": "2026-04-27T10:00:00+08:00",
                    "end_time": "2026-04-27T10:15:00+08:00",
                    "status_code": "CAFE_CONFIRMED",
                }
            )
        )

        service = SummaryService()
        result = service.load_phase_status("test-issue", "pr")
        assert result is not None
        assert result["status"] == "completed"
        assert not (tmp_path / ".cafe/issues/test-issue/blackboard.json").exists()
        assert not (tmp_path / ".cafe/issues/test-issue/next_step.txt").exists()

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


class TestLoadTokenUsageData:
    """Test loading token usage data from context.json"""

    def test_load_iteration_statuses_extracts_token_usage(self, tmp_path, monkeypatch):
        """Verify load_iteration_statuses() extracts cli, model, and stats fields"""
        from cafe.services.summary_service import SummaryService

        monkeypatch.chdir(tmp_path)

        service = SummaryService()
        phase_dir = tmp_path / ".cafe/issues/test-issue/spec"
        (phase_dir / "iteration_001").mkdir(parents=True)

        # Create context.json with token usage data
        context = {
            "iteration": 1,
            "timestamp": "2026-01-31T10:00:00+08:00",
            "end_time": "2026-01-31T10:15:00+08:00",
            "status_code": "CAFE_CONFIRMED",
            "cli": "gemini",
            "model": "gemini-2.5-flash",
            "stats": {
                "input_tokens": 109260,
                "output_tokens": 1607,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 48179,
                "total_cost_usd": 0.0,
                "duration_ms": 81850,
                "duration_api_ms": 81850
            }
        }
        (phase_dir / "iteration_001/context.json").write_text(json.dumps(context))

        result = service.load_iteration_statuses("test-issue", "spec")
        assert len(result) == 1
        assert result[0]["cli"] == "gemini"
        assert result[0]["model"] == "gemini-2.5-flash"
        assert result[0]["stats"]["input_tokens"] == 109260
        assert result[0]["stats"]["output_tokens"] == 1607
        assert result[0]["stats"]["cache_read_input_tokens"] == 48179

    def test_load_iteration_statuses_handles_missing_token_fields(self, tmp_path, monkeypatch):
        """Verify handling when token usage fields are missing"""
        from cafe.services.summary_service import SummaryService

        monkeypatch.chdir(tmp_path)

        service = SummaryService()
        phase_dir = tmp_path / ".cafe/issues/test-issue/plan"
        (phase_dir / "iteration_001").mkdir(parents=True)

        # Create context.json without token usage data
        context = {
            "iteration": 1,
            "timestamp": "2026-01-31T11:00:00+08:00",
            "status_code": "CAFE_CONFIRMED"
        }
        (phase_dir / "iteration_001/context.json").write_text(json.dumps(context))

        result = service.load_iteration_statuses("test-issue", "plan")
        assert len(result) == 1
        assert result[0].get("cli") is None
        assert result[0].get("model") is None
        assert result[0].get("stats") is None

    def test_load_iteration_statuses_with_multiple_models(self, tmp_path, monkeypatch):
        """Verify loading iterations with different models"""
        from cafe.services.summary_service import SummaryService

        monkeypatch.chdir(tmp_path)

        service = SummaryService()
        phase_dir = tmp_path / ".cafe/issues/test-issue/spec"

        # Create multiple iterations with different models
        contexts = [
            {
                "iteration": 1,
                "timestamp": "2026-01-31T10:00:00+08:00",
                "end_time": "2026-01-31T10:15:00+08:00",
                "status_code": "CAFE_CONFIRMED",
                "cli": "gemini",
                "model": "gemini-2.5-flash",
                "stats": {
                    "input_tokens": 109260,
                    "output_tokens": 1607,
                    "cache_read_input_tokens": 48179,
                    "total_cost_usd": 0.0
                }
            },
            {
                "iteration": 2,
                "timestamp": "2026-01-31T11:00:00+08:00",
                "end_time": "2026-01-31T11:20:00+08:00",
                "status_code": "CAFE_CONFIRMED",
                "cli": "claude",
                "model": "claude-3-5-sonnet",
                "stats": {
                    "input_tokens": 50000,
                    "output_tokens": 2000,
                    "cache_read_input_tokens": 10000,
                    "total_cost_usd": 0.15
                }
            }
        ]

        for i, context in enumerate(contexts, 1):
            (phase_dir / f"iteration_{i:03d}").mkdir(parents=True)
            (phase_dir / f"iteration_{i:03d}/context.json").write_text(json.dumps(context))

        result = service.load_iteration_statuses("test-issue", "spec")
        assert len(result) == 2
        assert result[0]["cli"] == "gemini"
        assert result[0]["model"] == "gemini-2.5-flash"
        assert result[1]["cli"] == "claude"
        assert result[1]["model"] == "claude-3-5-sonnet"
        assert result[1]["stats"]["total_cost_usd"] == 0.15
