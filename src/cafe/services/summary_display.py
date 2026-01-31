"""Display formatter for cafe summary timeline."""

from typing import List, Optional

from cafe.services.timeline_builder import TimelineEntry
from cafe.services.time_formatter import format_timestamp_local, format_timestamp_utc, format_duration, calculate_elapsed_time
from cafe.core.types import PhaseStatus

try:
    from rich.console import Console
    from rich.table import Table
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

console = Console() if RICH_AVAILABLE else None


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

    def format_token_count(self, count: Optional[int]) -> str:
        """Format token count with comma separators.

        Args:
            count: Token count to format

        Returns:
            Formatted string with commas, or "--" for None/0
        """
        if count is None or count == 0:
            return "--"
        return f"{count:,}"

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
            return f"{symbol} {entry.phase.capitalize()} {entry.name}: {start_time} (elapsed: {duration_str})"
        elif entry.end_time:
            duration_str = format_duration(entry.end_time - entry.start_time)
            return f"{symbol} {entry.phase.capitalize()} {entry.name}: {start_time} - {format_timestamp_utc(entry.end_time)} ({duration_str})"
        else:
            return f"{symbol} {entry.phase.capitalize()} {entry.name}: {start_time}"

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
            # Only render iteration entries (phase entries are filtered out)
            formatted = self.format_iteration_entry(entry)
            styled = self.apply_status_styling(formatted, entry.status)
            lines.append(styled)

        lines.append("")
        return "\n".join(lines)

    def render_table(self, entries: List[TimelineEntry]) -> None:
        """Render timeline entries as a table.

        Args:
            entries: List of timeline entries in chronological order
        """
        if not RICH_AVAILABLE:
            # Fallback to vertical timeline if rich is not available
            print(self.render_vertical_timeline(entries))
            return

        if not entries:
            console.print("[dim]No workflow iterations have started yet.[/dim]")
            return

        # Create table
        table = Table(
            title="📋 CAFE Workflow Summary",
            show_header=True,
            header_style="bold cyan"
        )

        # Add columns
        table.add_column("Phase", style="green")
        table.add_column("Iteration", style="cyan", justify="right")
        table.add_column("Status Code", style="yellow")
        table.add_column("Model", style="blue")
        table.add_column("Input Tokens", style="cyan", justify="right")
        table.add_column("Output Tokens", style="cyan", justify="right")
        table.add_column("Cache Read", style="cyan", justify="right")
        table.add_column("Start", style="dim")
        table.add_column("End", style="dim")
        table.add_column("Duration", style="magenta")

        # Add data rows
        for entry in entries:
            # Format start time
            start_str = format_timestamp_local(entry.start_time) if entry.start_time else "N/A"

            # Format end time
            end_str = format_timestamp_local(entry.end_time) if entry.end_time else "N/A"

            # Calculate duration
            if entry.start_time and entry.end_time:
                duration = entry.end_time - entry.start_time
                duration_str = format_duration(duration)
            else:
                duration_str = "N/A"

            # Format token usage
            model_str = entry.model or "--"
            input_tokens_str = self.format_token_count(entry.input_tokens)
            output_tokens_str = self.format_token_count(entry.output_tokens)
            cache_read_str = self.format_token_count(entry.cache_read_tokens)

            # Add row
            table.add_row(
                entry.phase,
                str(entry.iteration) if entry.iteration else "N/A",
                entry.status_code or "N/A",
                model_str,
                input_tokens_str,
                output_tokens_str,
                cache_read_str,
                start_str,
                end_str,
                duration_str
            )

        # Print table
        console.print(table)
