"""Tests for phase progress (status.json) functionality."""

import json
from pathlib import Path
from datetime import datetime

import pytest

from cafe.core.phase import Phase
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import PhaseStatus


class ConcretePhase(Phase):
    """Concrete phase implementation for testing."""

    def __init__(self, phase_dir: Path, **kwargs):
        super().__init__(**kwargs)
        self.phase_dir = phase_dir
        self.phase_name = "test_phase"
        self.iteration = 1

    def execute(self):
        pass


class TestSaveProgressEndTime:
    """Test that _save_progress always sets end_time field"""

    def test_end_time_is_set_for_in_progress_phase(self, tmp_path):
        """Verify end_time is set in status.json for in-progress phases"""
        phase_dir = tmp_path / "test_phase"
        phase_dir.mkdir()
        phase = ConcretePhase(phase_dir=phase_dir)

        # Save progress with an in-progress status code
        phase._save_progress(PhaseStatusCode.NEED_CLARIFICATION)

        # Read status.json
        status_file = phase_dir / "status.json"
        assert status_file.exists()

        with open(status_file, "r", encoding="utf-8") as f:
            status_data = json.load(f)

        # Verify end_time is set
        assert "end_time" in status_data
        assert status_data["end_time"] is not None
        # Verify it's a valid ISO format timestamp
        datetime.fromisoformat(status_data["end_time"].replace("Z", "+00:00"))
        # Verify status is IN_PROGRESS
        assert status_data["status"] == PhaseStatus.IN_PROGRESS.value

    def test_end_time_is_set_for_completed_phase(self, tmp_path):
        """Verify end_time is set in status.json for completed phases"""
        phase_dir = tmp_path / "test_phase"
        phase_dir.mkdir()
        phase = ConcretePhase(phase_dir=phase_dir)

        # Save progress with a completed status code
        phase._save_progress(PhaseStatusCode.CONFIRMED)

        # Read status.json
        status_file = phase_dir / "status.json"
        assert status_file.exists()

        with open(status_file, "r", encoding="utf-8") as f:
            status_data = json.load(f)

        # Verify end_time is set
        assert "end_time" in status_data
        assert status_data["end_time"] is not None
        # Verify it's a valid ISO format timestamp
        datetime.fromisoformat(status_data["end_time"].replace("Z", "+00:00"))
        # Verify status is COMPLETED
        assert status_data["status"] == PhaseStatus.COMPLETED.value

    def test_end_time_is_set_for_ready_for_review_status(self, tmp_path):
        """Verify end_time is set for READY_FOR_REVIEW status"""
        phase_dir = tmp_path / "test_phase"
        phase_dir.mkdir()
        phase = ConcretePhase(phase_dir=phase_dir)

        # Save progress with READY_FOR_REVIEW status code
        phase._save_progress(PhaseStatusCode.READY_FOR_REVIEW)

        # Read status.json
        status_file = phase_dir / "status.json"
        assert status_file.exists()

        with open(status_file, "r", encoding="utf-8") as f:
            status_data = json.load(f)

        # Verify end_time is set
        assert "end_time" in status_data
        assert status_data["end_time"] is not None
        # Verify it's a valid ISO format timestamp
        datetime.fromisoformat(status_data["end_time"].replace("Z", "+00:00"))
        # Verify status is COMPLETED (READY_FOR_REVIEW is a completed status)
        assert status_data["status"] == PhaseStatus.COMPLETED.value
