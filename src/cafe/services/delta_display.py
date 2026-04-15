"""Display formatter for iteration output.md delta comparison."""

from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Tuple, Optional

try:
    from rich.console import Console
    from rich.text import Text
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


class DeltaDisplay:
    """Formatter for displaying inline diff between iteration outputs."""

    def __init__(self):
        """Initialize delta display formatter."""
        pass

    def generate_inline_diff(
        self, old_content: str, new_content: str
    ) -> List[Tuple[str, Optional[str], Optional[str]]]:
        """Generate line-by-line diff with operation types.

        Args:
            old_content: Previous iteration content
            new_content: Current iteration content

        Returns:
            List of tuples (operation, old_line, new_line) where:
            - operation: 'equal', 'delete', 'insert', 'replace'
            - old_line: line from old content (None for insert)
            - new_line: line from new content (None for delete)
        """
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)

        matcher = SequenceMatcher(None, old_lines, new_lines)
        diff_data = []

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for i in range(i1, i2):
                    diff_data.append(("equal", old_lines[i], old_lines[i]))
            elif tag == "delete":
                for i in range(i1, i2):
                    diff_data.append(("delete", old_lines[i], None))
            elif tag == "insert":
                for j in range(j1, j2):
                    diff_data.append(("insert", None, new_lines[j]))
            elif tag == "replace":
                # For replace, show both old and new lines
                for i in range(i1, i2):
                    diff_data.append(("delete", old_lines[i], None))
                for j in range(j1, j2):
                    diff_data.append(("insert", None, new_lines[j]))

        return diff_data

    def render_inline_diff(
        self,
        diff_data: List[Tuple[str, Optional[str], Optional[str]]],
        console: Console,
    ) -> None:
        """Render inline diff with Rich text styling.

        Args:
            diff_data: Diff data from generate_inline_diff
            console: Rich console for output
        """
        if not RICH_AVAILABLE:
            return

        # Check if there are any changes
        has_changes = any(op != "equal" for op, _, _ in diff_data)

        if not has_changes:
            console.print()
            console.print("[green]✓ No changes from previous iteration[/green]")
            console.print()
            return

        # Check if diff is too large (more than 1000 lines)
        max_lines = 1000
        is_truncated = len(diff_data) > max_lines

        # Display header
        console.print()
        console.print("[bold cyan]📊 Changes from previous iteration:[/bold cyan]")
        if is_truncated:
            console.print(
                f"[yellow]⚠️  Output truncated to {max_lines} lines (total: {len(diff_data)} lines)[/yellow]"
            )
        console.print("[dim]" + "─" * 60 + "[/dim]")
        console.print()

        # Build styled text (truncate if necessary)
        text = Text()
        display_data = diff_data[:max_lines] if is_truncated else diff_data
        for operation, old_line, new_line in display_data:
            if operation == "equal":
                # Keep an explicit prefix so plain-text logs remain readable.
                line = new_line if new_line is not None else ""
                text.append(f"  {line}")
            elif operation == "delete":
                # Deleted line - red
                line = old_line if old_line is not None else ""
                text.append(f"- {line}", style="red")
            elif operation == "insert":
                # Added line - green
                line = new_line if new_line is not None else ""
                text.append(f"+ {line}", style="green")

        console.print(text)
        console.print()
        console.print("[dim]" + "─" * 60 + "[/dim]")
        if is_truncated:
            console.print(
                f"[yellow]⚠️  {len(diff_data) - max_lines} lines omitted[/yellow]"
            )
        console.print()

    def display_delta(
        self,
        current_file: Path,
        previous_file: Path,
        console: Console,
    ) -> None:
        """Display delta between current and previous iteration outputs.

        Args:
            current_file: Path to current iteration output.md
            previous_file: Path to previous iteration output.md
            console: Rich console for output
        """
        if not RICH_AVAILABLE:
            return

        # Handle missing files
        if not previous_file.exists():
            console.print()
            console.print("[dim]First iteration - no previous version to compare[/dim]")
            console.print()
            return

        if not current_file.exists():
            console.print()
            console.print("[yellow]⚠️  Warning: Current output file not found[/yellow]")
            console.print()
            return

        # Read file contents
        try:
            old_content = previous_file.read_text(encoding="utf-8")
            new_content = current_file.read_text(encoding="utf-8")
        except Exception as e:
            console.print()
            console.print(f"[yellow]⚠️  Warning: Could not read files: {e}[/yellow]")
            console.print()
            return

        # Generate and render diff
        diff_data = self.generate_inline_diff(old_content, new_content)
        self.render_inline_diff(diff_data, console)
