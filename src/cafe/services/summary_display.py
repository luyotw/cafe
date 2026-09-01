"""Display formatter for cafe summary timeline."""

from typing import Any, List, Mapping, Optional

from cafe.core.context_packet import (
    format_context_packet_diagnostic,
    validate_context_packet_diagnostic,
)
from cafe.core.types import PhaseStatus
from cafe.services.time_formatter import (
    calculate_elapsed_time,
    format_duration,
    format_timestamp_local,
    format_timestamp_utc,
)
from cafe.services.timeline_builder import TimelineEntry

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

    def format_context_packets(self, packets: List[Mapping[str, Any]]) -> str:
        """Render the independent, narrow Context Packets read model."""
        validated_packets = []
        for packet in packets:
            try:
                validated_packets.append(validate_context_packet_diagnostic(packet))
            except ValueError:
                continue
        if not validated_packets:
            return ""
        lines = ["Context Packets", "Consumer | Source | Requested | Effective | Reason"]
        for packet in validated_packets:
            source = packet.get("source")
            source_name = source.get("artifact_name", "unknown") if isinstance(source, Mapping) else "unknown"
            consumer = f"{packet.get('consumer', 'unknown')}#{packet.get('iteration', '?')}"
            diagnostic = format_context_packet_diagnostic(packet)
            lines.append(
                " | ".join(
                    [
                        consumer,
                        str(source_name),
                        str(packet.get("requested_mode", "")),
                        str(packet.get("effective_mode", "")),
                        diagnostic,
                    ]
                )
            )
        return "\n".join(lines)

    def format_driver_status(self, status: Optional[Mapping[str, Any]]) -> str:
        """Render the safe workflow-driver read model."""
        if not status:
            return ""
        policy = status.get("policy", {})
        driver = policy.get("driver", {}) if isinstance(policy, Mapping) else {}
        progress = status.get("progress", {})
        lines = [
            "Workflow Driver",
            f"Authority: {status.get('authority_path', '')}",
            f"Ownership: {driver.get('mode', '') if isinstance(driver, Mapping) else ''}",
            f"Lifecycle: {status.get('lifecycle', '')}",
            (
                "Progress: "
                f"{progress.get('current_step', '') if isinstance(progress, Mapping) else ''} -> "
                f"{progress.get('requested_action', '') if isinstance(progress, Mapping) else ''}"
            ),
        ]
        if isinstance(driver, Mapping) and driver.get("mode") == "delegated":
            lines.append(
                f"Delegated driver: {driver.get('cli', '')} / {driver.get('model', '')}"
            )
        pause_reason = status.get("pause_reason")
        if isinstance(pause_reason, str) and pause_reason:
            lines.append(f"Pause reason: {pause_reason}")
        decisions = status.get("decisions", [])
        if isinstance(decisions, list) and decisions:
            latest = decisions[-1]
            if isinstance(latest, Mapping):
                lines.append(
                    f"Latest decision: {latest.get('sequence', '')} {latest.get('action', '')}"
                )
        guidance = status.get("notification_guidance")
        if isinstance(guidance, Mapping) and guidance.get("proactive") is False:
            command = guidance.get("inspection_command", "cafe status")
            lines.append(
                "Notifications: unavailable; this conversation will not be proactively "
                f"updated. Inspect durable progress with {command}"
            )
        mismatch = status.get("model_mismatch")
        if isinstance(mismatch, Mapping):
            lines.append(
                "Model mismatch: requested "
                f"{mismatch.get('requested_model', '')}; reported "
                f"{mismatch.get('reported_model', '')}"
            )
        return "\n".join(lines)

    def _format_entry(self, entry: TimelineEntry, prefix: str) -> str:
        """Format an entry for display with the given prefix.

        Args:
            entry: Timeline entry to format
            prefix: Prefix string (e.g., "[Phase] Spec" or "Develop Iteration 1")

        Returns:
            Formatted string for the entry
        """
        symbol = self.STATUS_SYMBOLS.get(entry.status, "?")
        start_time = format_timestamp_utc(entry.start_time)

        # If we have end_time, show fixed duration (regardless of status)
        if entry.end_time:
            duration_str = format_duration(entry.end_time - entry.start_time)
            return f"{symbol} {prefix}: {start_time} - {format_timestamp_utc(entry.end_time)} ({duration_str})"
        elif entry.status == PhaseStatus.IN_PROGRESS:
            elapsed = calculate_elapsed_time(entry.start_time)
            duration_str = format_duration(elapsed)
            return f"{symbol} {prefix}: {start_time} (elapsed: {duration_str})"
        else:
            return f"{symbol} {prefix}: {start_time}"

    def format_phase_entry(self, entry: TimelineEntry) -> str:
        """Format a phase entry for display.

        Args:
            entry: Phase timeline entry

        Returns:
            Formatted string for the phase
        """
        prefix = f"[Phase] {entry.name}"
        return self._format_entry(entry, prefix)

    def format_iteration_entry(self, entry: TimelineEntry) -> str:
        """Format an iteration entry for display.

        Args:
            entry: Iteration timeline entry

        Returns:
            Formatted string for the iteration
        """
        prefix = f"{entry.phase.capitalize()} {entry.name}"
        return self._format_entry(entry, prefix)

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
        table.add_column("Start", style="dim")
        table.add_column("End", style="dim")
        table.add_column("Duration", style="magenta")
        table.add_column("CLI", style="blue")
        table.add_column("Model", style="blue", no_wrap=False, overflow="fold")
        table.add_column("Input Tokens", style="cyan", justify="right")
        table.add_column("Output Tokens", style="cyan", justify="right")
        table.add_column("Cache Write", style="cyan", justify="right")
        table.add_column("Cache Read", style="cyan", justify="right")
        table.add_column("Reasoning", style="cyan", justify="right")

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
            cache_write_str = self.format_token_count(entry.cache_write_tokens)
            cache_read_str = self.format_token_count(entry.cache_read_tokens)
            reasoning_str = self.format_token_count(entry.reasoning_output_tokens)

            # Add row
            table.add_row(
                entry.phase,
                str(entry.iteration) if entry.iteration else "N/A",
                start_str,
                end_str,
                duration_str,
                entry.cli or "--",
                model_str,
                input_tokens_str,
                output_tokens_str,
                cache_write_str,
                cache_read_str,
                reasoning_str,
            )

        # Print table
        console.print(table)

    def render_model_summary_table(self, entries: List[TimelineEntry]) -> None:
        """Render aggregated token usage statistics by model.

        Args:
            entries: List of timeline entries to aggregate
        """
        if not RICH_AVAILABLE:
            # Fallback - print simple text summary
            print("\n📊 Model Token Usage Summary")
            print("=" * 50)

            # Aggregate token usage by cli-model combination
            aggregated = {}
            for entry in entries:
                if not entry.cli or not entry.model:
                    continue

                key = f"{entry.cli}-{entry.model}"
                if key not in aggregated:
                    aggregated[key] = {
                        "cli": entry.cli,
                        "model": entry.model,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_write_tokens": 0,
                        "cache_read_tokens": 0,
                        "reasoning_output_tokens": 0,
                        "cost_usd": 0.0,
                    }

                if entry.input_tokens:
                    aggregated[key]["input_tokens"] += entry.input_tokens
                if entry.output_tokens:
                    aggregated[key]["output_tokens"] += entry.output_tokens
                if entry.cache_write_tokens:
                    aggregated[key]["cache_write_tokens"] += entry.cache_write_tokens
                if entry.cache_read_tokens:
                    aggregated[key]["cache_read_tokens"] += entry.cache_read_tokens
                if entry.reasoning_output_tokens:
                    aggregated[key]["reasoning_output_tokens"] += entry.reasoning_output_tokens
                if entry.cost_usd:
                    aggregated[key]["cost_usd"] += entry.cost_usd

            if not aggregated:
                return

            # Print simple text table
            for key in sorted(aggregated.keys()):
                stats = aggregated[key]
                print(f"\n{stats['cli']} - {stats['model']}")
                print(f"  Input Tokens:  {self.format_token_count(stats['input_tokens'])}")
                print(f"  Output Tokens: {self.format_token_count(stats['output_tokens'])}")
                print(f"  Cache Write:   {self.format_token_count(stats['cache_write_tokens'])}")
                print(f"  Cache Read:    {self.format_token_count(stats['cache_read_tokens'])}")
                print(
                    f"  Reasoning:     {self.format_token_count(stats['reasoning_output_tokens'])}"
                )
                cost_str = f"${stats['cost_usd']:.4f}" if stats['cost_usd'] > 0 else "--"
                print(f"  Cost (USD):    {cost_str}")
            print()
            return

        # Aggregate token usage by cli-model combination
        aggregated = {}
        for entry in entries:
            if not entry.cli or not entry.model:
                continue  # Skip entries without model info

            key = f"{entry.cli}-{entry.model}"
            if key not in aggregated:
                aggregated[key] = {
                    "cli": entry.cli,
                    "model": entry.model,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_write_tokens": 0,
                    "cache_read_tokens": 0,
                    "reasoning_output_tokens": 0,
                    "cost_usd": 0.0,
                }

            # Accumulate token counts and costs
            if entry.input_tokens:
                aggregated[key]["input_tokens"] += entry.input_tokens
            if entry.output_tokens:
                aggregated[key]["output_tokens"] += entry.output_tokens
            if entry.cache_write_tokens:
                aggregated[key]["cache_write_tokens"] += entry.cache_write_tokens
            if entry.cache_read_tokens:
                aggregated[key]["cache_read_tokens"] += entry.cache_read_tokens
            if entry.reasoning_output_tokens:
                aggregated[key]["reasoning_output_tokens"] += entry.reasoning_output_tokens
            if entry.cost_usd:
                aggregated[key]["cost_usd"] += entry.cost_usd

        # If no token data, don't show the table
        if not aggregated:
            return

        # Create summary table
        table = Table(
            title="📊 Model Token Usage Summary",
            show_header=True,
            header_style="bold cyan"
        )

        # Add columns
        table.add_column("CLI", style="green")
        table.add_column("Model", style="blue")
        table.add_column("Input Tokens", style="cyan", justify="right")
        table.add_column("Output Tokens", style="cyan", justify="right")
        table.add_column("Cache Write", style="cyan", justify="right")
        table.add_column("Cache Read", style="cyan", justify="right")
        table.add_column("Reasoning", style="cyan", justify="right")
        table.add_column("Cost (USD)", style="magenta", justify="right")

        # Add rows for each model
        for key in sorted(aggregated.keys()):
            stats = aggregated[key]
            table.add_row(
                stats["cli"],
                stats["model"],
                self.format_token_count(stats["input_tokens"]),
                self.format_token_count(stats["output_tokens"]),
                self.format_token_count(stats["cache_write_tokens"]),
                self.format_token_count(stats["cache_read_tokens"]),
                self.format_token_count(stats["reasoning_output_tokens"]),
                f"${stats['cost_usd']:.4f}" if stats['cost_usd'] > 0 else "--",
            )

        # Print summary table
        console.print()
        console.print(table)
