"""Time formatting utilities for cafe summary."""

from datetime import datetime, timedelta, timezone
from typing import Optional


def format_timestamp_local(dt: datetime) -> str:
    """Format datetime to human-readable local time string.

    Args:
        dt: Datetime object to format

    Returns:
        Human-readable string in local timezone (e.g., "Jan 4, 2025 at 2:30 PM")
    """
    # Ensure datetime is timezone-aware
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    # Convert to local timezone
    local_dt = dt.astimezone()

    # Format: "Jan 4, 2025 at 2:30 PM"
    return local_dt.strftime("%b %d, %Y at %I:%M %p")


def format_duration(elapsed: timedelta) -> str:
    """Format timedelta to human-readable duration string.

    Args:
        elapsed: Timedelta object to format

    Returns:
        Human-readable string (e.g., "2 hours 15 minutes", "1 day 3 hours")
    """
    if elapsed is None:
        return ""

    total_seconds = int(elapsed.total_seconds())

    if total_seconds < 0:
        return "negative duration"

    if total_seconds == 0:
        return "0 seconds"

    days = total_seconds // (24 * 3600)
    remaining = total_seconds % (24 * 3600)
    hours = remaining // 3600
    remaining = remaining % 3600
    minutes = remaining // 60
    seconds = remaining % 60

    parts = []

    if days > 0:
        parts.append(f"{days} day{'s' if days > 1 else ''}")

    if hours > 0:
        parts.append(f"{hours} hour{'s' if hours > 1 else ''}")

    if minutes > 0:
        parts.append(f"{minutes} minute{'s' if minutes > 1 else ''}")

    # For very short durations, show seconds if no minutes
    if len(parts) == 0 and seconds > 0:
        parts.append(f"{seconds} second{'s' if seconds > 1 else ''}")

    # Only show up to 2 components (e.g., "1 day 3 hours", not "1 day 3 hours 15 minutes")
    if len(parts) > 2:
        parts = parts[:2]

    return " ".join(parts)


def calculate_elapsed_time(start_time: datetime) -> timedelta:
    """Calculate elapsed time from start_time to now.

    Args:
        start_time: Starting datetime (should be in UTC or timezone-aware)

    Returns:
        Timedelta representing elapsed time

    Raises:
        ValueError: If start_time is in the future
    """
    # Ensure start_time is timezone-aware
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)

    elapsed = now - start_time

    if elapsed.total_seconds() < 0:
        raise ValueError(f"start_time is in the future: {start_time}")

    return elapsed
