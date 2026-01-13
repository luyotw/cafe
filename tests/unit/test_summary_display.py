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
    """測試 render_table() 方法的表格顯示功能"""

    def test_render_table_with_empty_entries(self):
        """測試空資料的表格渲染"""
        display = SummaryDisplay()
        # 空列表不應該崩潰，應該顯示訊息或空表格
        display.render_table([])  # Should not raise exception

    def test_render_table_with_single_entry(self):
        """測試單一 entry 的表格顯示"""
        display = SummaryDisplay()
        entry = TimelineEntry(
            entry_type="iteration",
            name="Iteration 1",
            phase="spec",
            start_time=datetime(2026, 1, 14, 10, 0, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 14, 10, 15, 0, tzinfo=timezone.utc),
            status=PhaseStatus.COMPLETED,
            iteration=1,
            status_code="CAFE_CONFIRMED"
        )
        # 應該能渲染而不崩潰
        display.render_table([entry])

    def test_render_table_with_multiple_entries(self):
        """測試多個 entries 的表格顯示"""
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
                status_code="CAFE_READY_FOR_REVIEW"
            ),
            TimelineEntry(
                entry_type="iteration",
                name="Iteration 2",
                phase="spec",
                start_time=datetime(2026, 1, 14, 11, 0, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 1, 14, 11, 10, 0, tzinfo=timezone.utc),
                status=PhaseStatus.COMPLETED,
                iteration=2,
                status_code="CAFE_CONFIRMED"
            ),
        ]
        # 應該能渲染多個 entries
        display.render_table(entries)

    def test_render_table_with_missing_end_time(self):
        """測試缺少 end_time 的 entry（進行中的 iteration）"""
        display = SummaryDisplay()
        entry = TimelineEntry(
            entry_type="iteration",
            name="Iteration 1",
            phase="plan",
            start_time=datetime(2026, 1, 14, 12, 0, 0, tzinfo=timezone.utc),
            end_time=None,  # 進行中，沒有 end_time
            status=PhaseStatus.IN_PROGRESS,
            iteration=1,
            status_code="CAFE_NEED_CLARIFICATION"
        )
        # 應該顯示 "N/A" 而不是崩潰
        display.render_table([entry])

    def test_render_table_with_multiple_phases(self):
        """測試跨多個 phases 的表格顯示"""
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
                status_code="CAFE_CONFIRMED"
            ),
            TimelineEntry(
                entry_type="iteration",
                name="Iteration 1",
                phase="plan",
                start_time=datetime(2026, 1, 14, 11, 0, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 1, 14, 11, 20, 0, tzinfo=timezone.utc),
                status=PhaseStatus.COMPLETED,
                iteration=1,
                status_code="CAFE_READY_FOR_REVIEW"
            ),
        ]
        # 應該能顯示不同 phases
        display.render_table(entries)
