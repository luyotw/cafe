"""Display utilities for AAF UI."""

import sys
from typing import Optional

# Import readline to enable better line editing with Unicode support
try:
    import readline
    # Enable UTF-8 encoding in readline
    readline.parse_and_bind('set input-meta on')
    readline.parse_and_bind('set output-meta on')
    readline.parse_and_bind('set convert-meta off')
except ImportError:
    # readline not available on Windows
    pass

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text


class Display:
    """Display utilities using Rich for better formatting and input handling."""

    def __init__(self) -> None:
        """Initialize display with Rich console."""
        self.console = Console()

    def show_phase_status(
        self,
        phase_name: str,
        status: str,
        message: str = ""
    ) -> None:
        """Show phase status with color coding.

        Args:
            phase_name: Name of the phase
            status: Phase status (completed, failed, in_progress, etc.)
            message: Optional status message
        """
        # Map status to colors and icons
        status_map = {
            "completed": ("green", "✅"),
            "failed": ("red", "❌"),
            "in_progress": ("yellow", "🔄"),
            "skipped": ("blue", "⏭️"),
        }

        color, icon = status_map.get(status.lower(), ("white", "●"))

        status_text = Text()
        status_text.append(f"{icon} ", style=color)
        status_text.append(f"{phase_name}: ", style="bold")
        status_text.append(status.upper(), style=f"bold {color}")

        if message:
            status_text.append(f"\n  {message}", style="dim")

        self.console.print(status_text)

    def get_multiline_input(
        self,
        prompt: str = "請輸入內容",
        end_marker: str = "END",
        show_line_numbers: bool = True
    ) -> str:
        """Get multiline input from user with proper Unicode support.

        This method properly handles Chinese and other multi-byte characters
        with backspace support using Python's built-in input() with readline.

        Args:
            prompt: Prompt message to display
            end_marker: String that marks end of input (case-insensitive)
            show_line_numbers: Show line numbers as input prompt (default: True)

        Returns:
            Combined multiline input as string
        """
        # Use plain print to avoid Rich compressing empty lines
        print(f"\033[1m{prompt}\033[0m")
        print(f"\033[2m（單獨一行輸入 {end_marker} 表示結束）\033[0m")

        lines = []
        line_num = 1
        while True:
            try:
                # Use input() which works with readline for proper Unicode handling
                # The readline module configuration at the top of this file
                # ensures proper multi-byte character support
                if show_line_numbers:
                    line = input(f"\033[2m{line_num:2d}│\033[0m ")
                else:
                    line = input()

                if line.strip().upper() == end_marker.upper():
                    break

                lines.append(line)
                line_num += 1

            except (EOFError, KeyboardInterrupt):
                break

        return '\n'.join(lines)

    def format_agent_response(
        self,
        agent_name: str,
        response: str,
        status_code: Optional[str] = None
    ) -> str:
        """Format agent response for display.

        Args:
            agent_name: Name of the agent
            response: Agent's response text
            status_code: Optional status code to highlight

        Returns:
            Formatted response string
        """
        # Remove status code from response if present
        if status_code and response.startswith(status_code):
            response = response[len(status_code):].lstrip('\n')

        # Create formatted output
        formatted = f"[bold cyan]{agent_name}:[/bold cyan]\n{response}"

        if status_code:
            formatted = f"[bold yellow]{status_code}[/bold yellow]\n\n" + formatted

        return formatted

    def show_permission_request(
        self,
        command: str,
        description: str = ""
    ) -> None:
        """Display permission request in a panel.

        Args:
            command: Command requesting permission
            description: Optional description of what the command does
        """
        content = Text()
        content.append("Command: ", style="bold")
        content.append(f"{command}\n", style="cyan")

        if description:
            content.append("\nDescription: ", style="bold")
            content.append(description, style="dim")

        panel = Panel(
            content,
            title="[yellow]Permission Request[/yellow]",
            border_style="yellow",
        )

        self.console.print(panel)
