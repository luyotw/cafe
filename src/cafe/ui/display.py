"""Display utilities for CAFE UI."""

import sys
from typing import Optional

from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.key_binding import KeyBindings
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
        """Get multiline input from user with Enter to submit, Alt+Enter for newline.

        Uses prompt_toolkit for better input handling:
        - Enter: Submit input (like AI chat tools)
        - Alt+Enter: Add new line
        - Ctrl+C twice within 1 second: Cancel and raise KeyboardInterrupt

        Args:
            prompt: Prompt message to display
            end_marker: Not used anymore (kept for compatibility)
            show_line_numbers: Not used anymore (kept for compatibility)

        Returns:
            User input as string
        """
        from time import time
        import shutil

        # Get terminal width
        terminal_width = shutil.get_terminal_size().columns

        # Display prompt
        print(f"\033[1m{prompt}\033[0m")
        print(f"\033[2m（按 Enter 送出，Shift+Enter 或 Alt+Enter 換行）\033[0m")
        print("─" * terminal_width)

        # Create key bindings
        kb = KeyBindings()

        # Track Ctrl+C presses
        last_ctrl_c_time = [0]  # Use list to allow modification in closure

        @kb.add('c-c')
        def _(event):
            """Ctrl+C - need to press twice within 1 second to cancel."""
            current_time = time()
            if current_time - last_ctrl_c_time[0] < 1.0:
                # Second press within 1 second - cancel
                raise KeyboardInterrupt()
            else:
                # First press - show hint
                last_ctrl_c_time[0] = current_time
                # Ring the bell to get user's attention
                import sys
                sys.stdout.write('\a')
                sys.stdout.flush()

        @kb.add('c-j')  # Shift+Enter sends \n which is Ctrl+J in terminal codes
        def _(event):
            """Shift+Enter - insert newline."""
            event.current_buffer.insert_text('\n')

        @kb.add('escape', 'enter')  # Alt+Enter adds newline
        def handle_alt_enter(event):
            """Alt+Enter - insert newline."""
            event.current_buffer.insert_text('\n')

        try:
            # Get input with prompt_toolkit
            # multiline=False means Enter submits by default (like AI chat tools)
            user_input = pt_prompt(
                '> ',  # Prompt with > prefix
                multiline=False,
                key_bindings=kb,
                enable_open_in_editor=False,
                enable_system_prompt=False,
                enable_suspend=True,  # Allow Ctrl+Z to suspend
                prompt_continuation=lambda width, line_number, is_soft_wrap: '  ' if not is_soft_wrap else '',
            )

            # Print bottom border after input
            print("─" * terminal_width)

            return user_input.strip()

        except KeyboardInterrupt:
            # Ctrl+C pressed twice - propagate to cancel phase
            print("─" * terminal_width)
            raise

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
