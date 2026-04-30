"""Tests for context.json end_time field saving."""

import json
from pathlib import Path
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from cafe.core.phase import Phase
from cafe.core.status_codes import PhaseStatusCode


class ConcretePhase(Phase):
    """Concrete phase implementation for testing."""

    def __init__(self, phase_dir: Path, **kwargs):
        super().__init__(**kwargs)
        self.phase_dir = phase_dir
        self.iteration = 1

    def execute(self):
        pass


class TestContextEndTime:
    """Test context.json contains end_time field"""

    def test_context_json_contains_end_time_field(self, tmp_path):
        """Verify context.json contains end_time field when iteration ends"""
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir()
        phase = ConcretePhase(phase_dir=phase_dir)
        phase.iteration = 1

        # Save user input (simulating iteration start)
        phase._save_user_input("Test user input")

        # Simulate agent execution completion, update iteration history
        phase._update_iteration_history(
            phase_specific_data={"response": "Test response"},
            status_code=None,
        )

        # Verify context.json contains end_time
        context_file = phase._get_iteration_dir(1) / "context.json"
        assert context_file.exists()

        with open(context_file, "r", encoding="utf-8") as f:
            context_data = json.load(f)

        # Verify end_time exists and has correct format
        assert "end_time" in context_data
        assert context_data["end_time"] is not None
        # Verify it's an ISO format timestamp string
        from datetime import datetime
        datetime.fromisoformat(context_data["end_time"].replace('Z', '+00:00'))


class TestContextJsonEndTime:
    """Test context.json end_time field"""

    def test_context_json_contains_end_time_after_update(self, tmp_path):
        """Verify _update_iteration_history() saves end_time to context.json"""
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir()

        phase = ConcretePhase(phase_dir=phase_dir)
        phase.iteration = 1

        # Simulate agent response
        phase_specific_data = {
            "response": "Test response",
            "streaming_log": [],
        }

        # Call _update_iteration_history
        phase._update_iteration_history(
            phase_specific_data=phase_specific_data,
            status_code=MagicMock(value="CAFE_CONFIRMED"),
        )

        # Verify context.json contains end_time field
        iteration_dir = phase._get_iteration_dir(1)
        context_file = iteration_dir / "context.json"
        assert context_file.exists()

        with open(context_file, "r", encoding="utf-8") as f:
            context_data = json.load(f)

        # Verify end_time field exists and has correct format
        assert "end_time" in context_data
        assert context_data["end_time"] is not None
        # Verify it's an ISO format timestamp string
        from datetime import datetime
        datetime.fromisoformat(context_data["end_time"].replace("Z", "+00:00"))

    def test_update_iteration_history_omits_status_code_when_persist_disabled(self, tmp_path):
        """Verify status_code key is not persisted for baton-first workflow steps."""
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir()

        phase = ConcretePhase(phase_dir=phase_dir)
        phase.iteration = 1

        phase._update_iteration_history(
            phase_specific_data={
                "response": "done without status",
                "status_code": "stale",
            },
            status_code=MagicMock(value="CAFE_CONFIRMED"),
            persist_status=False,
        )

        context_file = phase._get_iteration_dir(1) / "context.json"
        context_data = json.loads(context_file.read_text(encoding="utf-8"))

        assert context_data["response"] == "done without status"
        assert "status_code" not in context_data

        iterations_file = phase_dir / "iterations.jsonl"
        iteration_entry = json.loads(iterations_file.read_text(encoding="utf-8").strip())
        assert "status" not in iteration_entry
