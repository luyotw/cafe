"""Display formatter for cafe status timeline."""

from typing import Any, List, Mapping, Optional

from cafe.core.context_packet import (
    format_context_packet_diagnostic,
    validate_context_packet_diagnostic,
)
from cafe.core.cost import combine_cost_summaries, format_cost, source_remainder, summarize_cost
from cafe.core.types import PhaseStatus
from cafe.core.usage import CHAT_USAGE_FIELDS
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


class StatusDisplay:
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

    def render_chat_usage_table(self, groups: List[dict]) -> None:
        """Show known subtotals and coverage separately from phase accounting."""
        if not groups:
            return
        headings = [
            "Phase",
            "CLI",
            "Mode",
            "Requested model",
            "Reported model",
            "Calls",
            "Coverage",
            "Input",
            "Output",
            "Cache creation",
            "Cache write",
            "Cache read",
            "Reasoning",
            "Cost (USD)",
        ]
        rows = []
        for group in groups:
            stats = group.get("stats", {})
            unknown = set(group.get("unknown_fields", []))
            values = []
            for field in CHAT_USAGE_FIELDS:
                value = stats.get(field)
                if value is None:
                    text = "unknown"
                else:
                    text = f"${value:.4f}" if field == "total_cost_usd" else f"{value:,}"
                    if field in unknown:
                        text += " (partial)"
                values.append(text)
            cost_summary = summarize_cost(
                group.get("cost_records", []),
                legacy_cost=stats.get("total_cost_usd"),
                legacy_residual=source_remainder(stats, stats.get("cost_records", [])).get(
                    "total_cost_usd"
                ),
            )
            if "total_cost_usd" in unknown:
                cost_summary["incomplete"] = True
            values[-1] = format_cost(cost_summary)
            if "total_cost_usd" in unknown and "(partial)" not in values[-1]:
                values[-1] += " (partial)"
            coverage = "incomplete" if group.get("incomplete_calls") else "complete"
            if group.get("cost_records") and cost_summary["incomplete"]:
                coverage = "incomplete"
            if group.get("mode") == "interactive":
                coverage = (
                    "incomplete (native subset)" if stats else "incomplete (no native evidence)"
                )
            rows.append(
                [
                    group.get("phase") or "--",
                    group.get("cli") or "unknown",
                    group.get("mode") or "unknown",
                    group.get("requested_model") or "unknown",
                    group.get("reported_model") or "unknown",
                    str(group.get("calls", "unknown")),
                    coverage,
                    *values,
                ]
            )
        if not RICH_AVAILABLE:
            print("\nChat usage (known subtotals)")
            print(" | ".join(headings))
            for row in rows:
                print(" | ".join(row))
            return
        table = Table(title="Chat usage (known subtotals)")
        for heading in headings:
            table.add_column(heading)
        for row in rows:
            table.add_row(*row)
        console.print(table)

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

    def format_current_state(self, status: Mapping[str, str]) -> str:
        """Keep the actionable state separate from historical timing/usage tables."""
        return "Current workflow\n" + "\n".join(
            f"{key}: {status[key]}"
            for key in ("Issue", "Workflow", "State", "Step", "Owner", "Reason", "Task", "Next")
            if key in status
        )

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
        text = self._format_entry(entry, prefix)
        if entry.cost_records or entry.cost_usd is not None:
            text += " | Cost: " + format_cost(self._entry_cost_summary(entry))
        return text

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
            title="📋 CAFE Workflow Status",
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
        table.add_column("Cost (USD)", style="magenta")

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
                format_cost(self._entry_cost_summary(entry)),
            )

        # Print table
        console.print(table)

    def render_model_status_table(self, entries: List[TimelineEntry]) -> None:
        """Render token usage aggregated by phase, CLI, and model.

        Args:
            entries: List of timeline entries to aggregate
        """
        aggregated = self._aggregate_model_usage(entries)

        if not aggregated:
            return

        if not RICH_AVAILABLE:
            # Fallback - print simple text status
            print("\n📊 Model Token Usage Status")
            print("=" * 50)

            # Print simple text table
            for stats in aggregated.values():
                print(f"\n{stats['phase']} - {stats['cli']} - {stats['model']}")
                print(f"  Iterations:    {stats['iterations']}")
                print(f"  Input Tokens:  {self.format_token_count(stats['input_tokens'])}")
                print(f"  Output Tokens: {self.format_token_count(stats['output_tokens'])}")
                print(f"  Cache Write:   {self.format_token_count(stats['cache_write_tokens'])}")
                print(f"  Cache Read:    {self.format_token_count(stats['cache_read_tokens'])}")
                print(
                    f"  Reasoning:     {self.format_token_count(stats['reasoning_output_tokens'])}"
                )
                cost_str = format_cost(combine_cost_summaries(stats["cost_summaries"]))
                print(f"  Cost (USD):    {cost_str}")
            print()
            return

        # Create status table
        table = Table(
            title="📊 Model Token Usage Status",
            show_header=True,
            header_style="bold cyan"
        )

        # Add columns
        table.add_column("Phase", style="yellow")
        table.add_column("CLI", style="green")
        table.add_column("Model", style="blue")
        table.add_column("Iterations", style="cyan", justify="right")
        table.add_column("Input Tokens", style="cyan", justify="right")
        table.add_column("Output Tokens", style="cyan", justify="right")
        table.add_column("Cache Write", style="cyan", justify="right")
        table.add_column("Cache Read", style="cyan", justify="right")
        table.add_column("Reasoning", style="cyan", justify="right")
        table.add_column("Cost (USD)", style="magenta", justify="right")

        # Add rows for each model
        for stats in aggregated.values():
            table.add_row(
                stats["phase"],
                stats["cli"],
                stats["model"],
                str(stats["iterations"]),
                self.format_token_count(stats["input_tokens"]),
                self.format_token_count(stats["output_tokens"]),
                self.format_token_count(stats["cache_write_tokens"]),
                self.format_token_count(stats["cache_read_tokens"]),
                self.format_token_count(stats["reasoning_output_tokens"]),
                format_cost(combine_cost_summaries(stats["cost_summaries"])),
            )

        # Print status table
        console.print()
        console.print(table)

    @staticmethod
    def _legacy_entry_usage(entry):
        """Retain historical counters not represented by invocation records."""
        from cafe.services.cost_summary import unrecorded_usage

        fields = {
            "input_tokens": "input_tokens",
            "output_tokens": "output_tokens",
            "cache_write_tokens": "cache_write_input_tokens",
            "cache_read_tokens": "cache_read_input_tokens",
            "reasoning_output_tokens": "reasoning_output_tokens",
        }
        remaining = unrecorded_usage(
            {
                **{raw: getattr(entry, field) for field, raw in fields.items()},
            },
            entry.cost_records,
        )
        return {field: remaining[raw] for field, raw in fields.items()}

    @staticmethod
    def _legacy_cost_summary(records, aggregate_cost, *, unknown=False, legacy_residual=None):
        summary = summarize_cost(
            records, legacy_cost=aggregate_cost, legacy_residual=legacy_residual
        )
        if records:
            if not summary["counts"]["legacy"] and not unknown:
                return None
            summary = summarize_cost([], legacy_cost=summary["legacy"])
        if unknown:
            summary.update(incomplete=True, unknown=max(1, summary["unknown"]))
        return summary

    def _entry_cost_summary(self, entry):
        summary = summarize_cost(
            entry.cost_records,
            legacy_cost=entry.cost_usd,
        )
        if (
            entry.cost_records
            and not summary["counts"]["legacy"]
            and any(self._legacy_entry_usage(entry).values())
        ):
            summary.update(incomplete=True, unknown=max(1, summary["unknown"]))
        return summary

    def _aggregate_model_usage(self, entries: List[TimelineEntry]) -> dict:
        """Aggregate usage without combining separate workflow phases."""
        aggregated = {}
        seen = set()
        seen_legacy = set()
        for entry in entries:
            if entry.cost_records:
                rows = []
                for record in entry.cost_records:
                    identity = record.get("invocation_id")
                    if identity in seen:
                        continue
                    seen.add(identity)
                    raw = record.get("usage", {})
                    rows.append(
                        (
                            record.get("cli") or "unknown",
                            record.get("model") or "unknown",
                            {
                                "input_tokens": raw.get("input_tokens"),
                                "output_tokens": raw.get("output_tokens"),
                                "cache_write_tokens": raw.get("cache_write_input_tokens"),
                                "cache_read_tokens": raw.get("cache_read_input_tokens"),
                                "reasoning_output_tokens": raw.get("reasoning_output_tokens"),
                            },
                            summarize_cost([record]),
                        )
                    )
                legacy_usage = self._legacy_entry_usage(entry)
                combined = summarize_cost(
                    entry.cost_records,
                    legacy_cost=entry.cost_usd,
                )
                legacy_summary = self._legacy_cost_summary(
                    entry.cost_records,
                    entry.cost_usd,
                    unknown=bool(any(legacy_usage.values()) and not combined["counts"]["legacy"]),
                )
                legacy_id = (entry.phase, entry.iteration, entry.start_time)
                if legacy_summary is not None and legacy_id not in seen_legacy:
                    seen_legacy.add(legacy_id)
                    rows.append((entry.cli or "unknown", entry.model or "unknown",
                                 legacy_usage, legacy_summary))
            else:
                if not entry.cli or not entry.model:
                    continue
                rows = [
                    (
                        entry.cli,
                        entry.model,
                        {
                            key: getattr(entry, key)
                            for key in (
                                "input_tokens",
                                "output_tokens",
                                "cache_write_tokens",
                                "cache_read_tokens",
                                "reasoning_output_tokens",
                            )
                        },
                        summarize_cost([], legacy_cost=entry.cost_usd),
                    )
                ]
            for cli, model, usage, summary in rows:
                phase = entry.phase or "--"
                key = (phase, cli, model)
                if key not in aggregated:
                    aggregated[key] = {
                        "phase": phase,
                        "cli": cli,
                        "model": model,
                        "iterations": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_write_tokens": 0,
                        "cache_read_tokens": 0,
                        "reasoning_output_tokens": 0,
                        "cost_usd": 0.0,
                        "cost_summaries": [],
                        "_iterations": set(),
                    }
                stats = aggregated[key]
                stats["_iterations"].add((entry.iteration, entry.start_time))
                stats["iterations"] = len(stats["_iterations"])
                for field, value in usage.items():
                    stats[field] += value or 0
                stats["cost_usd"] += float(summary["known"])
                stats["cost_summaries"].append(summary)
        return aggregated

    def render_cost_summary(self, entries: List[TimelineEntry], groups: List[dict]) -> None:
        """Show step and workflow known subtotals, including chat coverage gaps."""
        from cafe.core.cost import merge_cost_records
        from cafe.services.cost_summary import summarize_sources

        sources = []
        phases = {}
        for entry in entries:
            if entry.entry_type != "iteration":
                continue
            sources.append(
                dict(
                    source_id=f"{entry.phase}/{entry.iteration}/{entry.start_time}",
                    records=entry.cost_records,
                    legacy_cost=entry.cost_usd,
                    gap=bool(
                        entry.cost_records
                        and any(self._legacy_entry_usage(entry).values())
                        and not summarize_cost(
                            entry.cost_records,
                            legacy_cost=entry.cost_usd,
                        )["counts"]["legacy"]
                    ),
                )
            )
            phase = phases.setdefault(entry.phase, {"records": [], "legacy": []})
            if entry.cost_records:
                phase["records"] = merge_cost_records(phase["records"], entry.cost_records)
                combined = summarize_cost(
                    entry.cost_records,
                    legacy_cost=entry.cost_usd,
                )
                legacy = self._legacy_cost_summary(
                    entry.cost_records,
                    entry.cost_usd,
                    unknown=bool(
                        any(self._legacy_entry_usage(entry).values())
                        and not combined["counts"]["legacy"]
                    ),
                )
                if legacy is not None:
                    phase["legacy"].append(legacy)
            else:
                phase["legacy"].append(summarize_cost([], legacy_cost=entry.cost_usd))
        for index, group in enumerate(groups):
            sources.append(
                dict(
                    source_id=f"chat/{index}",
                    records=group.get("cost_records", []),
                    legacy_cost=group.get("stats", {}).get("total_cost_usd"),
                    legacy_residual=source_remainder(
                        group.get("stats", {}), group.get("cost_records", [])
                    ).get("total_cost_usd"),
                    gap="total_cost_usd" in group.get("unknown_fields", []),
                )
            )
            phase = phases.setdefault(group.get("phase") or "--", {"records": [], "legacy": []})
            if group.get("cost_records"):
                phase["records"] = merge_cost_records(phase["records"], group["cost_records"])
            summary = self._legacy_cost_summary(
                group.get("cost_records", []),
                group.get("stats", {}).get("total_cost_usd"),
                legacy_residual=source_remainder(
                    group.get("stats", {}), group.get("cost_records", [])
                ).get("total_cost_usd"),
                unknown="total_cost_usd" in group.get("unknown_fields", []),
            )
            if summary is not None:
                phase["legacy"].append(summary)
        if not phases:
            return
        summaries = []
        lines = ["Cost (USD; API-equivalent estimates are not subscription invoices)"]
        for name, phase in phases.items():
            parts = list(phase["legacy"])
            if phase["records"]:
                parts.append(summarize_cost(phase["records"]))
            summary = combine_cost_summaries(parts)
            summaries.append(summary)
            lines.append(f"{name}: {format_cost(summary)}")
        workflow_summary = summarize_sources(sources)
        lines.append(f"Workflow: {format_cost(workflow_summary)}")
        if "native_usage" in workflow_summary:
            from cafe.services.cost_summary import format_native_usage

            lines.extend(format_native_usage(workflow_summary["native_usage"]))
        text = "\n".join(lines)
        if RICH_AVAILABLE:
            console.print(text, markup=False)
        else:
            print(text)
