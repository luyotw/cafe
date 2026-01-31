"""Timeline building and filtering logic for cafe summary."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Union

from cafe.core.types import PhaseStatus
from cafe.services.time_formatter import calculate_elapsed_time


@dataclass
class TimelineEntry:
    """Represents a single phase or iteration entry in the timeline.

    Note: end_time and elapsed_time are mutually exclusive:
    - end_time: Set for COMPLETED or FAILED items (completed time)
    - elapsed_time: Set for IN_PROGRESS items (current elapsed duration)
    """

    entry_type: str  # 'phase' or 'iteration'
    name: str
    phase: str
    start_time: datetime
    end_time: Optional[datetime] = None
    elapsed_time: Optional[timedelta] = None
    status: Optional[PhaseStatus] = None
    iteration: Optional[int] = None
    status_code: Optional[str] = None
    # Token usage fields
    cli: Optional[str] = None
    model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None

    def __post_init__(self):
        """Validate and normalize the entry."""
        if self.entry_type not in ("phase", "iteration"):
            raise ValueError(f"entry_type must be 'phase' or 'iteration', got {self.entry_type}")

        # Validate mutually exclusive fields
        if self.end_time is not None and self.elapsed_time is not None:
            raise ValueError("end_time and elapsed_time cannot both be set; they are mutually exclusive")

        # Convert status string to PhaseStatus enum if needed
        if isinstance(self.status, str):
            self.status = PhaseStatus(self.status)


class TimelineBuilder:
    """Builder for creating workflow timeline from phase and iteration data."""

    PHASES = ["spec", "plan", "develop", "review", "pr"]

    def __init__(self, issue_name: str):
        """Initialize timeline builder.

        Args:
            issue_name: Name of the issue to build timeline for
        """
        self.issue_name = issue_name
        self.base_dir = Path(".cafe/issues") / issue_name

    def build_timeline_entries(
        self, phase_statuses: Dict[str, Dict[str, Any]], iteration_data: Dict[str, List[Dict[str, Any]]]
    ) -> List[TimelineEntry]:
        """Build timeline entries from phase and iteration data.

        Args:
            phase_statuses: Dictionary mapping phase names to their status data
            iteration_data: Dictionary mapping phase names to list of iteration status data

        Returns:
            List of TimelineEntry objects in chronological order
        """
        entries: List[TimelineEntry] = []

        # Process each phase
        for phase_name in self.PHASES:
            phase_status = phase_statuses.get(phase_name)
            iterations = iteration_data.get(phase_name, [])

            if not phase_status and not iterations:
                continue  # Skip phases with no data

            # Create phase entry
            if phase_status:
                phase_entry = self._create_phase_entry(phase_name, phase_status)
                if phase_entry:
                    entries.append(phase_entry)

            # Create iteration entries
            for iteration_status in iterations:
                iteration_entry = self._create_iteration_entry(phase_name, iteration_status)
                if iteration_entry:
                    entries.append(iteration_entry)

        # Filter out pending phases with no iterations, sort chronologically
        filtered = self.filter_pending_phases(entries)
        sorted_entries = self.sort_chronologically(filtered)

        return sorted_entries

    def _create_phase_entry(self, phase_name: str, phase_status: Dict[str, Any]) -> Optional[TimelineEntry]:
        """Create a timeline entry for a phase.

        Args:
            phase_name: Name of the phase
            phase_status: Status data from status.json

        Returns:
            TimelineEntry for the phase, or None if timestamp is missing
        """
        timestamp_str = phase_status.get("timestamp", "")
        start_time = self._parse_timestamp(timestamp_str)

        # Skip entries with missing timestamps
        if start_time is None:
            return None

        # Calculate end time and elapsed time based on status
        end_time = None
        elapsed_time = None
        status = PhaseStatus(phase_status.get("status", "pending"))

        # Try to read end_time from status data if available
        end_timestamp_str = phase_status.get("end_time", "")
        if end_timestamp_str:
            end_time = self._parse_timestamp(end_timestamp_str)

        if status == PhaseStatus.IN_PROGRESS:
            # Use utility function for calculating elapsed time
            try:
                elapsed_time = calculate_elapsed_time(start_time)
            except ValueError:
                elapsed_time = None

        return TimelineEntry(
            entry_type="phase",
            name=phase_name.capitalize(),
            phase=phase_name,
            start_time=start_time,
            end_time=end_time,
            elapsed_time=elapsed_time,
            status=status,
            status_code=phase_status.get("status_code"),
        )

    def _create_iteration_entry(self, phase_name: str, iteration_status: Dict[str, Any]) -> Optional[TimelineEntry]:
        """Create a timeline entry for an iteration.

        Args:
            phase_name: Name of the phase
            iteration_status: Status data from iterations.jsonl or iteration status.json

        Returns:
            TimelineEntry for the iteration, or None if timestamp is missing
        """
        timestamp_str = iteration_status.get("timestamp", "")
        start_time = self._parse_timestamp(timestamp_str)

        # Skip entries with missing timestamps
        if start_time is None:
            return None

        iteration_num = iteration_status.get("iteration", 0)

        # Calculate end time and elapsed time based on status
        end_time = None
        elapsed_time = None

        # Map status - iterations.jsonl has status codes like "CAFE_CONFIRMED", "CAFE_NEEDS_CHANGES"
        # These should map to "completed" since they represent completed iterations
        status_str = iteration_status.get("status", "pending")
        if status_str and status_str.startswith("CAFE_"):
            # These are CAFE status codes, map them to completed
            status = PhaseStatus.COMPLETED
            status_code = status_str
        else:
            # These are PhaseStatus values
            try:
                status = PhaseStatus(status_str) if status_str else PhaseStatus.PENDING
            except ValueError:
                status = PhaseStatus.PENDING
            status_code = iteration_status.get("status_code")

        # Try to read end_time from status data if available
        end_timestamp_str = iteration_status.get("end_time", "")
        if end_timestamp_str:
            end_time = self._parse_timestamp(end_timestamp_str)

        if status == PhaseStatus.IN_PROGRESS:
            # Use utility function for calculating elapsed time
            try:
                elapsed_time = calculate_elapsed_time(start_time)
            except ValueError:
                elapsed_time = None

        return TimelineEntry(
            entry_type="iteration",
            name=f"Iteration {iteration_num}",
            phase=phase_name,
            start_time=start_time,
            end_time=end_time,
            elapsed_time=elapsed_time,
            status=status,
            iteration=iteration_num,
            status_code=status_code,
        )

    def _parse_timestamp(self, timestamp_str: str) -> Optional[datetime]:
        """Parse ISO format timestamp to datetime.

        Args:
            timestamp_str: ISO format timestamp string

        Returns:
            datetime object in UTC, or None if timestamp is missing/invalid
        """
        if not timestamp_str:
            return None

        try:
            # Handle 'Z' suffix in ISO format
            if timestamp_str.endswith("Z"):
                timestamp_str = timestamp_str.replace("Z", "+00:00")

            dt = datetime.fromisoformat(timestamp_str)

            # Ensure timezone aware
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            return dt
        except Exception:
            # Return None for unparseable timestamps instead of current time
            return None

    def filter_pending_phases(self, entries: List[TimelineEntry]) -> List[TimelineEntry]:
        """Filter out phase entries, keep only iteration entries.

        Args:
            entries: List of timeline entries

        Returns:
            Filtered list with only iteration entries
        """
        # Keep only iteration entries, filter out all phase entries
        return [entry for entry in entries if entry.entry_type == "iteration"]

    def sort_chronologically(self, entries: List[TimelineEntry]) -> List[TimelineEntry]:
        """Sort entries chronologically using end_time with start_time fallback.

        Args:
            entries: List of timeline entries

        Returns:
            Sorted list with all entries in chronological order by end_time when available,
            falling back to start_time for in-progress entries
        """
        # Sort by end_time when available, fallback to start_time
        return sorted(entries, key=lambda e: e.end_time if e.end_time else e.start_time)

    def convert_to_local_timezone(self, entries: List[TimelineEntry]) -> List[TimelineEntry]:
        """Convert all UTC timestamps to local timezone.

        Args:
            entries: List of timeline entries with UTC times

        Returns:
            List with timestamps converted to local timezone
        """
        local_tz = datetime.now().astimezone().tzinfo

        for entry in entries:
            # Convert start_time
            if entry.start_time.tzinfo is None:
                entry.start_time = entry.start_time.replace(tzinfo=timezone.utc)

            entry.start_time = entry.start_time.astimezone(local_tz)

            # Convert end_time if present
            if entry.end_time:
                if entry.end_time.tzinfo is None:
                    entry.end_time = entry.end_time.replace(tzinfo=timezone.utc)

                entry.end_time = entry.end_time.astimezone(local_tz)

        return entries
