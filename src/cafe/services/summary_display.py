"""Display formatter for cafe summary timeline."""

from typing import List

from cafe.services.timeline_builder import TimelineEntry
from cafe.services.time_formatter import format_timestamp_utc, format_duration, calculate_elapsed_time
from cafe.core.types import PhaseStatus

try:
    import rich
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


class SummaryDisplay:
    """Formatter for rendering workflow timeline."""

    # Status symbols and colors
    STATUS_SYMBOLS = {
        PhaseStatus.COMPLETED: "✓",
        PhaseStatus.IN_PROGRESS: "→",
        PhaseStatus.FAILED: "✗",
        PhaseStatus.PENDING: "○",
        PhaseStatus.SKIPPED: "⊘",
    }

    def __init__(self):
        """Initialize display formatter."""
        pass

    def format_phase_entry(self, entry: TimelineEntry) -> str:
        """Format a phase entry for display.

        Args:
            entry: Phase timeline entry

        Returns:
            Formatted string for the phase
        """
        symbol = self.STATUS_SYMBOLS.get(entry.status, "?")
        start_time = format_timestamp_utc(entry.start_time)

        if entry.status == PhaseStatus.IN_PROGRESS:
            elapsed = calculate_elapsed_time(entry.start_time)
            duration_str = format_duration(elapsed)
            return f"{symbol} [Phase] {entry.name}: {start_time} (elapsed: {duration_str})"
        elif entry.end_time:
            duration_str = format_duration(entry.end_time - entry.start_time)
            return f"{symbol} [Phase] {entry.name}: {start_time} - {format_timestamp_utc(entry.end_time)} ({duration_str})"
        else:
            return f"{symbol} [Phase] {entry.name}: {start_time}"

    def format_iteration_entry(self, entry: TimelineEntry) -> str:
        """Format an iteration entry for display.

        Args:
            entry: Iteration timeline entry

        Returns:
            Formatted string for the iteration
        """
        symbol = self.STATUS_SYMBOLS.get(entry.status, "?")
        start_time = format_timestamp_utc(entry.start_time)

        if entry.status == PhaseStatus.IN_PROGRESS:
            elapsed = calculate_elapsed_time(entry.start_time)
            duration_str = format_duration(elapsed)
            return f"  {symbol} {entry.name}: {start_time} (elapsed: {duration_str})"
        elif entry.end_time:
            duration_str = format_duration(entry.end_time - entry.start_time)
            return f"  {symbol} {entry.name}: {start_time} - {format_timestamp_utc(entry.end_time)} ({duration_str})"
        else:
            return f"  {symbol} {entry.name}: {start_time}"

    def apply_status_styling(self, text: str, status: PhaseStatus) -> str:
        """Apply styling based on status.

        Args:
            text: Text to style
            status: Status to apply styling for

        Returns:
            Styled text (with color codes if terminal supports it)
        """
        if not RICH_AVAILABLE:
            # Fallback to simple text styling
            if status == PhaseStatus.IN_PROGRESS:
                return f"[ACTIVE] {text}"
            elif status == PhaseStatus.FAILED:
                return f"[FAILED] {text}"
            elif status == PhaseStatus.SKIPPED:
                return f"[SKIPPED] {text}"
            return text

        # Use rich library for enhanced styling
        if status == PhaseStatus.COMPLETED:
            return f"[green]{text}[/green]"
        elif status == PhaseStatus.IN_PROGRESS:
            return f"[yellow]{text}[/yellow]"
        elif status == PhaseStatus.FAILED:
            return f"[red]{text}[/red]"
        elif status == PhaseStatus.SKIPPED:
            return f"[dim]{text}[/dim]"
        elif status == PhaseStatus.PENDING:
            return f"[blue]{text}[/blue]"
        return text

    def render_vertical_timeline(self, entries: List[TimelineEntry]) -> str:
        """Render all timeline entries as vertical timeline.

        Args:
            entries: List of timeline entries in chronological order

        Returns:
            Formatted string containing the complete timeline
        """
        if not entries:
            return "No workflow phases have started yet."

        lines = ["", "📋 CAFE Workflow Timeline", "=" * 50, ""]

        for entry in entries:
            if entry.entry_type == "phase":
                formatted = self.format_phase_entry(entry)
            else:
                formatted = self.format_iteration_entry(entry)

            styled = self.apply_status_styling(formatted, entry.status)
            lines.append(styled)

        lines.append("")
        return "\n".join(lines)
