"""Unit tests for summary display formatter."""

import pytest
from datetime import datetime, timezone

from cafe.core.types import PhaseStatus
from cafe.services.timeline_builder import TimelineEntry
from cafe.services.summary_display import SummaryDisplay


class TestFormatPhaseEntry:
    """Test cases for format_phase_entry() method."""

    def test_format_phase_entry_completed(self):
        """Test formatting completed phase entry."""
        display = SummaryDisplay()
        entry = TimelineEntry(
            entry_type="phase",
            name="Spec",
            phase="spec",
            start_time=datetime(2025, 1, 4, 10, 0, 0, tzinfo=timezone.utc),
            status=PhaseStatus.COMPLETED
        )
        result = display.format_phase_entry(entry)
        assert isinstance(result, str)
        assert "Spec" in result

    def test_format_phase_entry_in_progress(self):
        """Test formatting in_progress phase entry."""
        display = SummaryDisplay()
        entry = TimelineEntry(
            entry_type="phase",
            name="Plan",
            phase="plan",
            start_time=datetime.now(timezone.utc),
            status=PhaseStatus.IN_PROGRESS
        )
        result = display.format_phase_entry(entry)
        assert isinstance(result, str)

    def test_format_phase_entry_failed(self):
        """Test formatting failed phase entry."""
        display = SummaryDisplay()
        entry = TimelineEntry(
            entry_type="phase",
            name="Develop",
            phase="develop",
            start_time=datetime(2025, 1, 4, 12, 0, 0, tzinfo=timezone.utc),
            status=PhaseStatus.FAILED
        )
        result = display.format_phase_entry(entry)
        assert isinstance(result, str)


class TestFormatIterationEntry:
    """Test cases for format_iteration_entry() method."""

    def test_format_iteration_entry_completed(self):
        """Test formatting completed iteration entry."""
        display = SummaryDisplay()
        entry = TimelineEntry(
            entry_type="iteration",
            name="Iteration 1",
            phase="spec",
            start_time=datetime(2025, 1, 4, 11, 0, 0, tzinfo=timezone.utc),
            status=PhaseStatus.COMPLETED,
            iteration=1
        )
        result = display.format_iteration_entry(entry)
        assert isinstance(result, str)
        assert "Iteration 1" in result

    def test_format_iteration_entry_in_progress(self):
        """Test formatting in_progress iteration entry."""
        display = SummaryDisplay()
        entry = TimelineEntry(
            entry_type="iteration",
            name="Iteration 2",
            phase="plan",
            start_time=datetime.now(timezone.utc),
            status=PhaseStatus.IN_PROGRESS,
            iteration=2
        )
        result = display.format_iteration_entry(entry)
        assert isinstance(result, str)


class TestApplyStatusStyling:
    """Test cases for apply_status_styling() method."""

    def test_apply_status_styling_completed(self):
        """Test status styling for completed items."""
        display = SummaryDisplay()
        result = display.apply_status_styling("Test", PhaseStatus.COMPLETED)
        assert isinstance(result, str)

    def test_apply_status_styling_in_progress(self):
        """Test status styling for in_progress items."""
        display = SummaryDisplay()
        result = display.apply_status_styling("Test", PhaseStatus.IN_PROGRESS)
        assert isinstance(result, str)
        # Check for either rich or fallback styling
        assert "ACTIVE" in result or "yellow" in result

    def test_apply_status_styling_failed(self):
        """Test status styling for failed items."""
        display = SummaryDisplay()
        result = display.apply_status_styling("Test", PhaseStatus.FAILED)
        assert isinstance(result, str)


class TestRenderVerticalTimeline:
    """Test cases for render_vertical_timeline() method."""

    def test_render_vertical_timeline_empty(self):
        """Test rendering empty timeline."""
        display = SummaryDisplay()
        result = display.render_vertical_timeline([])
        assert isinstance(result, str)

    def test_render_vertical_timeline_single_entry(self):
        """Test rendering single entry timeline."""
        display = SummaryDisplay()
        entry = TimelineEntry(
            entry_type="phase",
            name="Spec",
            phase="spec",
            start_time=datetime(2025, 1, 4, 10, 0, 0, tzinfo=timezone.utc),
            status=PhaseStatus.COMPLETED
        )
        result = display.render_vertical_timeline([entry])
        assert isinstance(result, str)
        assert "Spec" in result

    def test_render_vertical_timeline_multiple_entries(self):
        """Test rendering multiple entries in chronological order."""
        display = SummaryDisplay()
        entries = [
            TimelineEntry(
                entry_type="phase",
                name="Spec",
                phase="spec",
                start_time=datetime(2025, 1, 4, 10, 0, 0, tzinfo=timezone.utc),
                status=PhaseStatus.COMPLETED
            ),
            TimelineEntry(
                entry_type="iteration",
                name="Iteration 1",
                phase="spec",
                start_time=datetime(2025, 1, 4, 11, 0, 0, tzinfo=timezone.utc),
                status=PhaseStatus.COMPLETED,
                iteration=1
            ),
        ]
        result = display.render_vertical_timeline(entries)
        assert isinstance(result, str)
        assert "Spec" in result
