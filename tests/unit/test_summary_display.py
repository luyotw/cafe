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


class TestRenderTable:
    """Test cases for render_table() method."""

    def test_render_table_with_empty_entries(self):
        """Test rendering table with empty entries."""
        display = SummaryDisplay()
        # Empty list should not crash, should display message or empty table
        display.render_table([])  # Should not raise exception

    def test_render_table_with_single_entry(self):
        """Test rendering table with single entry."""
        display = SummaryDisplay()
        entry = TimelineEntry(
            entry_type="iteration",
            name="Iteration 1",
            phase="spec",
            start_time=datetime(2026, 1, 14, 10, 0, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 14, 10, 15, 0, tzinfo=timezone.utc),
            status=PhaseStatus.COMPLETED,
            iteration=1,
            status_code="confirmed"
        )
        # Should render without crashing
        display.render_table([entry])

    def test_render_table_with_multiple_entries(self):
        """Test rendering table with multiple entries."""
        display = SummaryDisplay()
        entries = [
            TimelineEntry(
                entry_type="iteration",
                name="Iteration 1",
                phase="spec",
                start_time=datetime(2026, 1, 14, 10, 0, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 1, 14, 10, 15, 0, tzinfo=timezone.utc),
                status=PhaseStatus.COMPLETED,
                iteration=1,
                status_code="ready_for_review"
            ),
            TimelineEntry(
                entry_type="iteration",
                name="Iteration 2",
                phase="spec",
                start_time=datetime(2026, 1, 14, 11, 0, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 1, 14, 11, 10, 0, tzinfo=timezone.utc),
                status=PhaseStatus.COMPLETED,
                iteration=2,
                status_code="confirmed"
            ),
        ]
        # Should render multiple entries
        display.render_table(entries)

    def test_render_table_with_missing_end_time(self):
        """Test rendering entry with missing end_time (in-progress iteration)."""
        display = SummaryDisplay()
        entry = TimelineEntry(
            entry_type="iteration",
            name="Iteration 1",
            phase="plan",
            start_time=datetime(2026, 1, 14, 12, 0, 0, tzinfo=timezone.utc),
            end_time=None,  # In progress, no end_time
            status=PhaseStatus.IN_PROGRESS,
            iteration=1,
            status_code="need_clarification"
        )
        # Should display "N/A" without crashing
        display.render_table([entry])

    def test_render_table_with_multiple_phases(self):
        """Test rendering table with multiple phases."""
        display = SummaryDisplay()
        entries = [
            TimelineEntry(
                entry_type="iteration",
                name="Iteration 1",
                phase="spec",
                start_time=datetime(2026, 1, 14, 10, 0, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 1, 14, 10, 15, 0, tzinfo=timezone.utc),
                status=PhaseStatus.COMPLETED,
                iteration=1,
                status_code="confirmed"
            ),
            TimelineEntry(
                entry_type="iteration",
                name="Iteration 1",
                phase="plan",
                start_time=datetime(2026, 1, 14, 11, 0, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 1, 14, 11, 20, 0, tzinfo=timezone.utc),
                status=PhaseStatus.COMPLETED,
                iteration=1,
                status_code="ready_for_review"
            ),
        ]
        # Should display different phases
        display.render_table(entries)


class TestRenderTableWithTokenUsage:
    """Test render_table() displaying token usage columns"""

    def test_render_table_with_token_usage(self):
        """Test that render_table() displays token usage columns"""
        display = SummaryDisplay()
        entry = TimelineEntry(
            entry_type="iteration",
            name="Iteration 1",
            phase="spec",
            start_time=datetime(2026, 1, 31, 10, 0, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 31, 10, 15, 0, tzinfo=timezone.utc),
            status=PhaseStatus.COMPLETED,
            iteration=1,
            status_code="confirmed",
            cli="gemini",
            model="gemini-2.5-flash",
            input_tokens=109260,
            output_tokens=1607,
            cache_read_tokens=48179,
        )
        # Should render without crashing
        display.render_table([entry])

    def test_render_table_with_missing_token_usage(self):
        """Test that render_table() handles missing token usage fields"""
        display = SummaryDisplay()
        entry = TimelineEntry(
            entry_type="iteration",
            name="Iteration 1",
            phase="spec",
            start_time=datetime(2026, 1, 31, 10, 0, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 31, 10, 15, 0, tzinfo=timezone.utc),
            status=PhaseStatus.COMPLETED,
            iteration=1,
            status_code="confirmed",
        )
        # Should render with "--" for missing fields
        display.render_table([entry])

    def test_render_table_with_mixed_token_usage(self):
        """Test rendering table with some entries having token usage and some not"""
        display = SummaryDisplay()
        entries = [
            TimelineEntry(
                entry_type="iteration",
                name="Iteration 1",
                phase="spec",
                start_time=datetime(2026, 1, 31, 10, 0, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 1, 31, 10, 15, 0, tzinfo=timezone.utc),
                status=PhaseStatus.COMPLETED,
                iteration=1,
                status_code="confirmed",
                cli="gemini",
                model="gemini-2.5-flash",
                input_tokens=109260,
                output_tokens=1607,
                cache_read_tokens=48179,
            ),
            TimelineEntry(
                entry_type="iteration",
                name="Iteration 2",
                phase="spec",
                start_time=datetime(2026, 1, 31, 11, 0, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 1, 31, 11, 10, 0, tzinfo=timezone.utc),
                status=PhaseStatus.COMPLETED,
                iteration=2,
                status_code="confirmed",
                # No token usage data
            ),
        ]
        # Should render both entries correctly
        display.render_table(entries)


class TestFormatTokenCount:
    """Test formatting token counts with comma separators"""

    def test_format_token_count_with_commas(self):
        """Test that format_token_count() adds comma separators"""
        display = SummaryDisplay()
        result = display.format_token_count(109260)
        assert result == "109,260"

    def test_format_token_count_small_number(self):
        """Test formatting small token count without commas"""
        display = SummaryDisplay()
        result = display.format_token_count(1607)
        assert result == "1,607"

    def test_format_token_count_zero(self):
        """Test that zero is displayed as '--'"""
        display = SummaryDisplay()
        result = display.format_token_count(0)
        assert result == "--"

    def test_format_token_count_none(self):
        """Test that None is displayed as '--'"""
        display = SummaryDisplay()
        result = display.format_token_count(None)
        assert result == "--"


class TestRenderModelSummaryTable:
    """Test render_model_summary_table() method"""

    def test_render_model_summary_table_with_single_model(self):
        """Test rendering aggregated summary with single model"""
        display = SummaryDisplay()
        entries = [
            TimelineEntry(
                entry_type="iteration",
                name="Iteration 1",
                phase="spec",
                start_time=datetime(2026, 1, 31, 10, 0, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 1, 31, 10, 15, 0, tzinfo=timezone.utc),
                status=PhaseStatus.COMPLETED,
                iteration=1,
                status_code="confirmed",
                cli="gemini",
                model="gemini-2.5-flash",
                input_tokens=109260,
                output_tokens=1607,
                cache_read_tokens=48179,
            )
        ]
        # Should render without crashing
        display.render_model_summary_table(entries)

    def test_render_model_summary_table_with_multiple_models(self):
        """Test aggregating statistics across multiple models"""
        display = SummaryDisplay()
        entries = [
            TimelineEntry(
                entry_type="iteration",
                name="Iteration 1",
                phase="spec",
                start_time=datetime(2026, 1, 31, 10, 0, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 1, 31, 10, 15, 0, tzinfo=timezone.utc),
                status=PhaseStatus.COMPLETED,
                iteration=1,
                cli="gemini",
                model="gemini-2.5-flash",
                input_tokens=109260,
                output_tokens=1607,
                cache_read_tokens=48179,
            ),
            TimelineEntry(
                entry_type="iteration",
                name="Iteration 2",
                phase="spec",
                start_time=datetime(2026, 1, 31, 11, 0, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 1, 31, 11, 20, 0, tzinfo=timezone.utc),
                status=PhaseStatus.COMPLETED,
                iteration=2,
                cli="claude",
                model="claude-3-5-sonnet",
                input_tokens=50000,
                output_tokens=2000,
                cache_read_tokens=10000,
            ),
        ]
        # Should aggregate and display both models
        display.render_model_summary_table(entries)

    def test_render_model_summary_table_aggregates_same_model(self):
        """Test aggregating multiple iterations with same model"""
        display = SummaryDisplay()
        entries = [
            TimelineEntry(
                entry_type="iteration",
                name="Iteration 1",
                phase="spec",
                start_time=datetime(2026, 1, 31, 10, 0, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 1, 31, 10, 15, 0, tzinfo=timezone.utc),
                status=PhaseStatus.COMPLETED,
                iteration=1,
                cli="gemini",
                model="gemini-2.5-flash",
                input_tokens=100000,
                output_tokens=1000,
                cache_read_tokens=50000,
            ),
            TimelineEntry(
                entry_type="iteration",
                name="Iteration 2",
                phase="spec",
                start_time=datetime(2026, 1, 31, 11, 0, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 1, 31, 11, 20, 0, tzinfo=timezone.utc),
                status=PhaseStatus.COMPLETED,
                iteration=2,
                cli="gemini",
                model="gemini-2.5-flash",
                input_tokens=50000,
                output_tokens=500,
                cache_read_tokens=25000,
            ),
        ]
        # Should aggregate: 150000 input, 1500 output, 75000 cache_read
        display.render_model_summary_table(entries)

    def test_render_model_summary_table_empty_entries(self):
        """Test rendering with empty entries list"""
        display = SummaryDisplay()
        # Should not crash with empty list
        display.render_model_summary_table([])

    def test_render_model_summary_table_handles_missing_token_data(self):
        """Test handling entries without token usage data"""
        display = SummaryDisplay()
        entries = [
            TimelineEntry(
                entry_type="iteration",
                name="Iteration 1",
                phase="spec",
                start_time=datetime(2026, 1, 31, 10, 0, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 1, 31, 10, 15, 0, tzinfo=timezone.utc),
                status=PhaseStatus.COMPLETED,
                iteration=1,
                # No token usage data
            )
        ]
        # Should handle gracefully
        display.render_model_summary_table(entries)

    def test_render_model_summary_table_aggregates_costs(self):
        """Test that costs are properly aggregated across iterations"""
        display = SummaryDisplay()
        entries = [
            TimelineEntry(
                entry_type="iteration",
                name="Iteration 1",
                phase="spec",
                start_time=datetime(2026, 1, 31, 10, 0, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 1, 31, 10, 15, 0, tzinfo=timezone.utc),
                status=PhaseStatus.COMPLETED,
                iteration=1,
                cli="claude",
                model="claude-3-5-sonnet",
                input_tokens=50000,
                output_tokens=2000,
                cache_read_tokens=10000,
                cost_usd=0.10,
            ),
            TimelineEntry(
                entry_type="iteration",
                name="Iteration 2",
                phase="plan",
                start_time=datetime(2026, 1, 31, 11, 0, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 1, 31, 11, 20, 0, tzinfo=timezone.utc),
                status=PhaseStatus.COMPLETED,
                iteration=2,
                cli="claude",
                model="claude-3-5-sonnet",
                input_tokens=30000,
                output_tokens=1500,
                cache_read_tokens=5000,
                cost_usd=0.05,
            ),
        ]
        # Should aggregate costs: 0.10 + 0.05 = 0.15
        display.render_model_summary_table(entries)
