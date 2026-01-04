"""Unit tests for time formatting utilities."""

import pytest
from datetime import datetime, timedelta, timezone

from cafe.services.time_formatter import format_timestamp_local, format_duration, calculate_elapsed_time


class TestFormatTimestampLocal:
    """Test cases for format_timestamp_local() function."""

    def test_format_timestamp_local_iso_format(self):
        """Test formatting ISO format timestamps to human-readable local time."""
        dt = datetime(2025, 1, 4, 14, 30, 0, tzinfo=timezone.utc)
        result = format_timestamp_local(dt)
        assert "Jan 04" in result or "Jan 4" in result
        assert "2025" in result

    def test_format_timestamp_local_utc_to_local(self):
        """Test converting UTC timestamp to local timezone."""
        dt = datetime(2025, 1, 4, 12, 0, 0, tzinfo=timezone.utc)
        result = format_timestamp_local(dt)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_format_timestamp_local_naive_datetime(self):
        """Test handling naive datetime (assumes UTC)."""
        dt = datetime(2025, 1, 4, 10, 0, 0)
        result = format_timestamp_local(dt)
        assert isinstance(result, str)

    def test_format_timestamp_local_aware_datetime(self):
        """Test handling timezone-aware datetime."""
        dt = datetime(2025, 1, 4, 10, 0, 0, tzinfo=timezone.utc)
        result = format_timestamp_local(dt)
        assert isinstance(result, str)

    def test_format_timestamp_local_readable_format(self):
        """Test that output format is human-readable."""
        dt = datetime(2025, 1, 4, 14, 30, 0, tzinfo=timezone.utc)
        result = format_timestamp_local(dt)
        assert "2025" in result and ("PM" in result or "pm" in result or "14" in result)


class TestFormatDuration:
    """Test cases for format_duration() function."""

    def test_format_duration_hours_and_minutes(self):
        """Test formatting duration with hours and minutes."""
        td = timedelta(hours=2, minutes=15)
        result = format_duration(td)
        assert "hour" in result and "minute" in result

    def test_format_duration_days_and_hours(self):
        """Test formatting duration with days and hours."""
        td = timedelta(days=1, hours=3)
        result = format_duration(td)
        assert "day" in result and "hour" in result

    def test_format_duration_only_minutes(self):
        """Test formatting duration with only minutes."""
        td = timedelta(minutes=45)
        result = format_duration(td)
        assert "minute" in result

    def test_format_duration_only_hours(self):
        """Test formatting duration with only hours."""
        td = timedelta(hours=2)
        result = format_duration(td)
        assert "hour" in result

    def test_format_duration_only_days(self):
        """Test formatting duration with only days."""
        td = timedelta(days=3)
        result = format_duration(td)
        assert "day" in result

    def test_format_duration_less_than_minute(self):
        """Test formatting duration less than a minute."""
        td = timedelta(seconds=30)
        result = format_duration(td)
        assert isinstance(result, str)

    def test_format_duration_zero(self):
        """Test formatting zero duration."""
        td = timedelta(0)
        result = format_duration(td)
        assert isinstance(result, str)

    def test_format_duration_large_value(self):
        """Test formatting very large duration."""
        td = timedelta(days=10, hours=5)
        result = format_duration(td)
        assert "day" in result and "hour" in result


class TestCalculateElapsedTime:
    """Test cases for calculate_elapsed_time() function."""

    def test_calculate_elapsed_time_recent_start(self):
        """Test calculating elapsed time from recent start."""
        start = datetime.now(timezone.utc) - timedelta(seconds=10)
        elapsed = calculate_elapsed_time(start)
        assert isinstance(elapsed, timedelta)
        assert 0 < elapsed.total_seconds() < 20

    def test_calculate_elapsed_time_older_start(self):
        """Test calculating elapsed time from past start."""
        start = datetime.now(timezone.utc) - timedelta(hours=1)
        elapsed = calculate_elapsed_time(start)
        assert elapsed.total_seconds() > 3600

    def test_calculate_elapsed_time_seconds_precision(self):
        """Test elapsed time calculation precision."""
        start = datetime.now(timezone.utc) - timedelta(seconds=30)
        elapsed = calculate_elapsed_time(start)
        assert 25 < elapsed.total_seconds() < 35

    def test_calculate_elapsed_time_with_timezone(self):
        """Test elapsed time calculation with timezone-aware datetimes."""
        start = datetime.now(timezone.utc) - timedelta(minutes=5)
        elapsed = calculate_elapsed_time(start)
        assert 290 < elapsed.total_seconds() < 310

    def test_calculate_elapsed_time_future_datetime_handled(self):
        """Test handling of future datetime (edge case)."""
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        with pytest.raises(ValueError):
            calculate_elapsed_time(future)
