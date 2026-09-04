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

        Cursor cannot allow-list individual tools. An explicit empty tool scope
        therefore omits ``--force`` so non-interactive permission requests fail
        closed instead of auto-approving tools.

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

        # Preserve legacy auto-approval only when callers did not provide a
        # capability scope. Decision-only callers pass an explicit empty list.
        if allowed_tools is None or allowed_tools:
            cmd.append("--force")

        # Add output format parameter
        cmd.extend(self.get_output_format())

        return cmd

    def parse_response(
        self,
        output_lines: List[str],
        streaming_log: Optional[List[str]] = None,
    ) -> Tuple[str, TokenUsage, List[PermissionDenial]]:
        """Parse Cursor CLI's stream-json output.

        Args:
            output_lines: List of lines from CLI output
            streaming_log: Streaming output log (optional, not used here)

        Returns:
            (response, token_usage, permission_denials) tuple
        """
        response = ""
        token_usage = TokenUsage()
        permission_denials = []

        # Parse each line as JSON
        for line in output_lines:
            try:
                data = json.loads(line.strip())
                
                # Extract assistant message
                if data.get("type") == "assistant":
                    message = data.get("message", {})
                    content_blocks = message.get("content", [])
                    for block in content_blocks:
                        if block.get("type") == "text":
                            response += block.get("text", "")
                
                # Extract token usage and duration from result
                if data.get("type") == "result":
                    duration_ms = data.get("duration_ms")
                    duration_api_ms = data.get("duration_api_ms")
                    
                    if duration_ms is not None:
                        token_usage.duration_ms = duration_ms
                    if duration_api_ms is not None:
                        token_usage.duration_api_ms = duration_api_ms
                    
                    # Also get response from result if not already extracted
                    if not response and "result" in data:
                        response = data["result"]
                
            except json.JSONDecodeError:
                # If not JSON, skip this line
                continue

        return response, token_usage, permission_denials

    def translate_allowed_tools(self, tools: List[str]) -> List[str]:
        """Convert tool names to Cursor format.

        Cursor doesn't support tool restrictions. Preserve the list only so
        the command builder can distinguish a normal nonempty request from an
        explicit empty capability scope.

        Args:
            tools: List of tool names

        Returns:
            Original list as a capability-scope marker
        """
        return list(tools)

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
        return ["--output-format", "stream-json"]

    def extract_session_id(self, output_lines: List[str]) -> Optional[str]:
        """Extract the acquired Cursor session ID from stream-json output.

        Args:
            output_lines: List of lines from CLI output

        Returns:
            Session ID from the initialization event, when present.
        """
        for line in output_lines:
            try:
                payload = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            session_id = payload.get("session_id")
            if isinstance(session_id, str) and session_id.strip():
                return session_id
        return None

    @property
    def event_driver_conforming(self) -> bool:
        return True

    def extract_event_driver_session(self, records) -> Optional[str]:
        return self._verified_event_driver_session(
            records,
            matches=lambda record: record.get("type") == "system"
            and record.get("subtype") == "init",
            field="session_id",
        )

    def accepts_event_driver_callback(self, records, *, session_id: str, event_id: str) -> bool:
        return self._verified_event_driver_acceptance(
            records,
            session_matches=lambda record: record.get("type") == "system"
            and record.get("subtype") == "init",
            acceptance_matches=lambda record: record.get("type") == "user"
            and self._event_driver_record_contains_text(record.get("message"), event_id),
            session_field="session_id",
            session_id=session_id,
            event_id=event_id,
        )
