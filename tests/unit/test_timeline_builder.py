"""Unit tests for timeline building and filtering logic."""

import pytest
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any
from unittest.mock import Mock

from cafe.core.types import PhaseStatus
from cafe.services.timeline_builder import TimelineEntry, TimelineBuilder


class TestBuildTimelineEntries:
    """Test cases for build_timeline_entries() method."""

    def test_build_timeline_entries_creates_phase_entries(self):
        """Test that timeline entries exclude phase entries (only iterations are included)."""
        builder = TimelineBuilder("test-issue")
        phase_statuses = {
            "spec": {"timestamp": "2025-01-01T00:00:00Z", "status": "completed"}
        }
        iteration_data = {}

        entries = builder.build_timeline_entries(phase_statuses, iteration_data)
        # Phase-only entries should be filtered out
        assert len(entries) == 0
        assert not any(e.entry_type == "phase" for e in entries)

    def test_build_timeline_entries_creates_iteration_entries(self):
        """Test creating timeline entries for iterations."""
        builder = TimelineBuilder("test-issue")
        phase_statuses = {}
        iteration_data = {
            "spec": [{"iteration": 1, "timestamp": "2025-01-02T00:00:00Z", "status": "completed"}]
        }

        entries = builder.build_timeline_entries(phase_statuses, iteration_data)
        assert len(entries) > 0
        assert any(e.entry_type == "iteration" for e in entries)

    def test_build_timeline_entries_includes_all_required_fields(self):
        """Test that timeline entries have all required fields."""
        builder = TimelineBuilder("test-issue")
        phase_statuses = {}
        iteration_data = {
            "spec": [{"iteration": 1, "timestamp": "2025-01-01T00:00:00Z", "status": "completed"}]
        }

        entries = builder.build_timeline_entries(phase_statuses, iteration_data)
        assert len(entries) > 0
        for entry in entries:
            assert hasattr(entry, 'entry_type')
            assert hasattr(entry, 'name')
            assert hasattr(entry, 'phase')
            assert hasattr(entry, 'start_time')

    def test_build_timeline_entries_handles_empty_phases(self):
        """Test handling phases with no iterations."""
        builder = TimelineBuilder("test-issue")
        entries = builder.build_timeline_entries({}, {})
        assert isinstance(entries, list)

    def test_build_timeline_entries_creates_correct_structure(self):
        """Test the data structure of created timeline entries."""
        builder = TimelineBuilder("test-issue")
        phase_statuses = {
            "plan": {"timestamp": "2025-01-03T00:00:00Z", "status": "in_progress"}
        }
        iteration_data = {
            "plan": [{"iteration": 1, "timestamp": "2025-01-04T00:00:00Z", "status": "completed"}]
        }

        entries = builder.build_timeline_entries(phase_statuses, iteration_data)
        for entry in entries:
            assert isinstance(entry, TimelineEntry)


class TestFilterPendingPhases:
    """Test cases for filter_pending_phases() logic."""

    def test_filter_pending_phases_removes_phases_with_no_data(self):
        """Test that phases with no iterations are removed."""
        builder = TimelineBuilder("test-issue")
        pending_phase = TimelineEntry(
            entry_type="phase",
            name="Spec",
            phase="spec",
            start_time=datetime.now(timezone.utc),
            status=PhaseStatus.PENDING
        )
        entries = [pending_phase]

        filtered = builder.filter_pending_phases(entries)
        assert len(filtered) == 0

    def test_filter_pending_phases_keeps_phases_with_iterations(self):
        """Test that phases with iterations are kept."""
        builder = TimelineBuilder("test-issue")
        phase = TimelineEntry(
            entry_type="phase",
            name="Spec",
            phase="spec",
            start_time=datetime.now(timezone.utc),
            status=PhaseStatus.IN_PROGRESS
        )
        iteration = TimelineEntry(
            entry_type="iteration",
            name="Iteration 1",
            phase="spec",
            start_time=datetime.now(timezone.utc),
            status=PhaseStatus.COMPLETED,
            iteration=1
        )
        entries = [phase, iteration]

        filtered = builder.filter_pending_phases(entries)
        assert len(filtered) > 0

    def test_filter_pending_phases_handles_all_statuses(self):
        """Test filtering works with all phase statuses."""
        builder = TimelineBuilder("test-issue")
        entries = [
            TimelineEntry("phase", "Spec", "spec", datetime.now(timezone.utc), status=PhaseStatus.PENDING),
            TimelineEntry("phase", "Plan", "plan", datetime.now(timezone.utc), status=PhaseStatus.IN_PROGRESS),
            TimelineEntry("phase", "Develop", "develop", datetime.now(timezone.utc), status=PhaseStatus.COMPLETED),
        ]

        filtered = builder.filter_pending_phases(entries)
        assert isinstance(filtered, list)

    def test_filter_pending_phases_empty_result(self):
        """Test filtering when all phases are pending."""
        builder = TimelineBuilder("test-issue")
        entries = []

        filtered = builder.filter_pending_phases(entries)
        assert filtered == []

    def test_filter_pending_phases_all_phases_started(self):
        """Test filtering when all phases have data."""
        builder = TimelineBuilder("test-issue")
        entries = [
            TimelineEntry("phase", "Spec", "spec", datetime.now(timezone.utc), status=PhaseStatus.COMPLETED),
            TimelineEntry("iteration", "Iteration 1", "spec", datetime.now(timezone.utc), status=PhaseStatus.COMPLETED, iteration=1),
        ]

        filtered = builder.filter_pending_phases(entries)
        assert len(filtered) > 0


class TestSortChronologically:
    """Test cases for sort_chronologically() method."""

    def test_sort_chronologically_orders_by_timestamp(self):
        """Test that entries are ordered by start time."""
        builder = TimelineBuilder("test-issue")
        now = datetime.now(timezone.utc)
        entries = [
            TimelineEntry("phase", "Plan", "plan", now + timedelta(days=1), status=PhaseStatus.PENDING),
            TimelineEntry("phase", "Spec", "spec", now, status=PhaseStatus.COMPLETED),
        ]

        sorted_entries = builder.sort_chronologically(entries)
        assert sorted_entries[0].phase == "spec"
        assert sorted_entries[1].phase == "plan"

    def test_sort_chronologically_handles_same_timestamp(self):
        """Test handling entries with identical timestamps."""
        builder = TimelineBuilder("test-issue")
        now = datetime.now(timezone.utc)
        entries = [
            TimelineEntry("phase", "Plan", "plan", now, status=PhaseStatus.COMPLETED),
            TimelineEntry("phase", "Spec", "spec", now, status=PhaseStatus.COMPLETED),
        ]

        sorted_entries = builder.sort_chronologically(entries)
        assert len(sorted_entries) == 2

    def test_sort_chronologically_empty_list(self):
        """Test sorting empty timeline."""
        builder = TimelineBuilder("test-issue")
        sorted_entries = builder.sort_chronologically([])
        assert sorted_entries == []

    def test_sort_chronologically_single_entry(self):
        """Test sorting single entry."""
        builder = TimelineBuilder("test-issue")
        entries = [TimelineEntry("phase", "Spec", "spec", datetime.now(timezone.utc), status=PhaseStatus.COMPLETED)]

        sorted_entries = builder.sort_chronologically(entries)
        assert len(sorted_entries) == 1

    def test_sort_chronologically_mixed_phases_iterations(self):
        """Test sorting mixed phase and iteration entries."""
        builder = TimelineBuilder("test-issue")
        now = datetime.now(timezone.utc)
        entries = [
            TimelineEntry("iteration", "Iteration 2", "spec", now + timedelta(hours=2), status=PhaseStatus.COMPLETED, iteration=2),
            TimelineEntry("phase", "Spec", "spec", now, status=PhaseStatus.COMPLETED),
            TimelineEntry("iteration", "Iteration 1", "spec", now + timedelta(hours=1), status=PhaseStatus.COMPLETED, iteration=1),
        ]

        sorted_entries = builder.sort_chronologically(entries)
        assert sorted_entries[0].entry_type == "phase"
        assert sorted_entries[1].iteration == 1
        assert sorted_entries[2].iteration == 2


class TestTimezoneConversion:
    """Test cases for timezone conversion."""

    def test_timezone_conversion_utc_to_local(self):
        """Test converting UTC timestamps to local timezone."""
        builder = TimelineBuilder("test-issue")
        utc_time = datetime(2025, 1, 4, 12, 0, 0, tzinfo=timezone.utc)
        entries = [TimelineEntry("phase", "Spec", "spec", utc_time, status=PhaseStatus.COMPLETED)]

        converted = builder.convert_to_local_timezone(entries)
        assert len(converted) == 1
        assert converted[0].start_time.tzinfo is not None

    def test_timezone_conversion_naive_datetime(self):
        """Test handling naive datetime objects."""
        builder = TimelineBuilder("test-issue")
        naive_time = datetime(2025, 1, 4, 12, 0, 0)
        entries = [TimelineEntry("phase", "Spec", "spec", naive_time, status=PhaseStatus.COMPLETED)]

        converted = builder.convert_to_local_timezone(entries)
        assert len(converted) == 1

    def test_timezone_conversion_aware_datetime(self):
        """Test handling timezone-aware datetime objects."""
        builder = TimelineBuilder("test-issue")
        aware_time = datetime(2025, 1, 4, 12, 0, 0, tzinfo=timezone.utc)
        entries = [TimelineEntry("phase", "Spec", "spec", aware_time, status=PhaseStatus.COMPLETED)]

        converted = builder.convert_to_local_timezone(entries)
        assert len(converted) == 1

    def test_timezone_conversion_preserves_time_instant(self):
        """Test that conversion preserves the actual moment in time."""
        builder = TimelineBuilder("test-issue")
        utc_time = datetime(2025, 1, 4, 12, 0, 0, tzinfo=timezone.utc)
        entries = [TimelineEntry("phase", "Spec", "spec", utc_time, status=PhaseStatus.COMPLETED)]

        converted = builder.convert_to_local_timezone(entries)
        # The instant in time should be the same
        assert converted[0].start_time.timestamp() == utc_time.timestamp()

    def test_timezone_conversion_different_zones(self):
        """Test conversion from various timezone sources."""
        builder = TimelineBuilder("test-issue")
        entries = [
            TimelineEntry("phase", "Spec", "spec", datetime.now(timezone.utc), status=PhaseStatus.COMPLETED),
            TimelineEntry("phase", "Plan", "plan", datetime.now(timezone.utc), status=PhaseStatus.COMPLETED),
        ]

        converted = builder.convert_to_local_timezone(entries)
        assert len(converted) == 2


class TestSortChronologicallyWithEndTime:
    """Test sort_chronologically uses end_time with start_time fallback"""

    def test_completed_entries_sorted_by_end_time(self):
        """Verify completed entries are sorted by end_time when available"""
        from datetime import datetime, timezone
        from cafe.services.timeline_builder import TimelineBuilder, TimelineEntry
        from cafe.core.types import PhaseStatus

        builder = TimelineBuilder("test-issue")

        # Create entries with different start and end times
        entry1 = TimelineEntry(
            entry_type="iteration",
            name="Iteration 1",
            phase="spec",
            start_time=datetime(2026, 1, 27, 10, 0, tzinfo=timezone.utc),  # Started first
            end_time=datetime(2026, 1, 27, 10, 20, tzinfo=timezone.utc),    # Ended last
            status=PhaseStatus.COMPLETED
        )
        entry2 = TimelineEntry(
            entry_type="iteration",
            name="Iteration 2",
            phase="plan",
            start_time=datetime(2026, 1, 27, 10, 5, tzinfo=timezone.utc),  # Started second
            end_time=datetime(2026, 1, 27, 10, 10, tzinfo=timezone.utc),    # Ended first
            status=PhaseStatus.COMPLETED
        )

        entries = [entry1, entry2]
        sorted_entries = builder.sort_chronologically(entries)

        # Should be sorted by end_time: entry2 (10:10) before entry1 (10:20)
        assert sorted_entries[0].name == "Iteration 2"
        assert sorted_entries[1].name == "Iteration 1"

    def test_in_progress_entries_sorted_by_start_time(self):
        """Verify in-progress entries (no end_time) are sorted by start_time"""
        from datetime import datetime, timezone
        from cafe.services.timeline_builder import TimelineBuilder, TimelineEntry
        from cafe.core.types import PhaseStatus

        builder = TimelineBuilder("test-issue")

        entry1 = TimelineEntry(
            entry_type="iteration",
            name="Iteration 1",
            phase="spec",
            start_time=datetime(2026, 1, 27, 10, 5, tzinfo=timezone.utc),  # Started second
            end_time=None,
            status=PhaseStatus.IN_PROGRESS
        )
        entry2 = TimelineEntry(
            entry_type="iteration",
            name="Iteration 2",
            phase="plan",
            start_time=datetime(2026, 1, 27, 10, 0, tzinfo=timezone.utc),  # Started first
            end_time=None,
            status=PhaseStatus.IN_PROGRESS
        )

        entries = [entry1, entry2]
        sorted_entries = builder.sort_chronologically(entries)

        # Should be sorted by start_time: entry2 (10:00) before entry1 (10:05)
        assert sorted_entries[0].name == "Iteration 2"
        assert sorted_entries[1].name == "Iteration 1"

    def test_mixed_completed_and_in_progress_entries(self):
        """Verify mixed completed/in-progress entries sort correctly"""
        from datetime import datetime, timezone
        from cafe.services.timeline_builder import TimelineBuilder, TimelineEntry
        from cafe.core.types import PhaseStatus

        builder = TimelineBuilder("test-issue")

        # Completed entry (has end_time)
        completed = TimelineEntry(
            entry_type="iteration",
            name="Completed",
            phase="spec",
            start_time=datetime(2026, 1, 27, 10, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 27, 10, 15, tzinfo=timezone.utc),
            status=PhaseStatus.COMPLETED
        )
        # In-progress entry (no end_time)
        in_progress = TimelineEntry(
            entry_type="iteration",
            name="In Progress",
            phase="plan",
            start_time=datetime(2026, 1, 27, 10, 10, tzinfo=timezone.utc),
            end_time=None,
            status=PhaseStatus.IN_PROGRESS
        )

        entries = [in_progress, completed]
        sorted_entries = builder.sort_chronologically(entries)

        # Completed (end 10:15) should come after in_progress (start 10:10, treated as 10:10)
        assert sorted_entries[0].name == "In Progress"
        assert sorted_entries[1].name == "Completed"
