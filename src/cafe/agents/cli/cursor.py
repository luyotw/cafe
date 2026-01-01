"""Cursor CLI tool implementation."""

import json
from typing import List, Optional, Tuple

from cafe.agents.cli.abstract import AbstractCLI
from cafe.core.types import PermissionDenial, TokenUsage


class CursorCLI(AbstractCLI):
    """Concrete implementation of Cursor CLI tool."""

    def build_command(
        self,
        prompt: str,
        allowed_tools: Optional[List[str]] = None,
        allowed_directories: Optional[List[str]] = None,
    ) -> List[str]:
        """Build Cursor CLI command line arguments.

        Note: Cursor doesn't support tool restrictions and directory restrictions, always uses --force to automatically approve all tools.

        Parameter order: cursor-agent -> -p -> --model -> --resume -> --force -> --output-format

        Args:
            prompt: Prompt text
            allowed_tools: List of allowed tools (Cursor doesn't support, will be ignored)
            allowed_directories: List of allowed directories (Cursor doesn't support, will be ignored)

        Returns:
            Complete command line argument list
        """
        cmd = ["cursor-agent", "-p", prompt]

        # If has model, add --model parameter
        if self.config.model:
            cmd.extend(["--model", self.config.model])

        # If has session_id, add --resume parameter
        if self.config.session_id:
            cmd.extend(["--resume", self.config.session_id])

        # Cursor doesn't support allowed-tools, use --force to automatically approve all tools
        cmd.append("--force")

        # Add output format parameter
        cmd.extend(self.get_output_format())

        return cmd

    def parse_response(
        self,
        output_lines: List[str],
        streaming_log: Optional[List[str]] = None,
    ) -> Tuple[str, TokenUsage, List[PermissionDenial]]:
        """Parse Cursor CLI's JSON output.

        Args:
            output_lines: List of lines from CLI output
            streaming_log: Streaming output log (optional, not used here)

        Returns:
            (response, token_usage, permission_denials) tuple
        """
        full_output = "".join(output_lines)
        token_usage = TokenUsage()
        permission_denials = []

        # Parse JSON output
        try:
            data = json.loads(full_output.strip())
            response = data.get("response", full_output)
            # TODO: If Cursor provides token usage or permission denials, parse here
        except json.JSONDecodeError:
            # If not JSON, return original output
            response = full_output

        return response, token_usage, permission_denials

    def translate_allowed_tools(self, tools: List[str]) -> List[str]:
        """Convert tool names to Cursor format.

        Cursor doesn't support tool restrictions, return empty list.

        Args:
            tools: List of tool names

        Returns:
            Empty list (Cursor doesn't support tool restrictions)
        """
        # Cursor doesn't support tool restrictions, return empty list
        return []

    def add_directories(self, cmd: List[str], directories: List[str]) -> List[str]:
        """Add allowed directories to command line arguments.

        Cursor doesn't support directory restrictions, return original command.

        Args:
            cmd: Current command line arguments
            directories: List of directories

        Returns:
            Original command (no changes)
        """
        # Cursor doesn't support directory restrictions, return original command
        return cmd

    def get_output_format(self) -> List[str]:
        """Get Cursor CLI's output format parameters.

        Returns:
            Output format related command line parameters
        """
        return ["--output-format", "json"]

    def extract_session_id(self, output_lines: List[str]) -> Optional[str]:
        """Extract session ID from output.

        Cursor automatically manages session, no need to extract from output.

        Args:
            output_lines: List of lines from CLI output

        Returns:
            None (Cursor automatically manages session)
        """
        # Cursor automatically manages session, no need to extract from output
        return None
