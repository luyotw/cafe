"""Copilot CLI tool implementation."""

from pathlib import Path
from typing import List, Optional, Tuple

from cafe.agents.cli.abstract import AbstractCLI
from cafe.core.types import PermissionDenial, TokenUsage


class CopilotCLI(AbstractCLI):
    """Concrete implementation of Copilot CLI tool."""

    def build_command(
        self,
        prompt: str,
        allowed_tools: Optional[List[str]] = None,
        allowed_directories: Optional[List[str]] = None,
    ) -> List[str]:
        """Build Copilot CLI command line arguments.

        Parameter order: copilot -> -p -> --model -> --allow-tool/--allow-all-tools -> --resume -> --add-dir

        Args:
            prompt: Prompt text
            allowed_tools: List of allowed tools (already converted format)
            allowed_directories: List of allowed directories

        Returns:
            Complete command line argument list
        """
        cmd = ["copilot", "-p", prompt]

        # If has model, add --model parameter
        if self.config.model:
            cmd.extend(["--model", self.config.model])

        # Add allowed tools parameter
        if allowed_tools:
            # Use --allow-tool for each tool
            for tool in allowed_tools:
                cmd.extend(["--allow-tool", tool])
        elif allowed_tools is None:
            # Use --allow-all-tools to automatically approve all tools
            cmd.append("--allow-all-tools")

        # If has session_id, add --resume parameter
        if self.config.session_id:
            cmd.extend(["--resume", self.config.session_id])

        # If has allowed_directories, add --add-dir parameter
        if allowed_directories:
            cmd = self.add_directories(cmd, allowed_directories)

        return cmd

    def parse_response(
        self,
        output_lines: List[str],
        streaming_log: Optional[List[str]] = None,
        stderr_output: str = "",
    ) -> Tuple[str, TokenUsage, List[PermissionDenial], Optional[str]]:
        """Parse Copilot CLI's plain text output.

        Args:
            output_lines: List of lines from CLI output (stdout)
            streaming_log: Streaming output log (optional, not used here)
            stderr_output: Output from stderr (may contain usage summary)

        Returns:
            (response, token_usage, permission_denials, model) tuple
        """
        import re
        
        # Copilot uses plain text output, join all lines from stdout
        full_output = "".join(output_lines)
        
        # Copilot may output usage summary to stderr, append it for parsing
        if stderr_output:
            full_output = full_output + "\n" + stderr_output
        token_usage = TokenUsage()
        permission_denials = []
        model: Optional[str] = None

        # Extract token usage from usage summary at the end
        # Example formats:
        # Old: Usage by model:
        #          claude-sonnet-4.5    14.3k input, 39 output, 10.2k cache read (Est. 1 Premium request)
        # New: Breakdown by AI model:
        #       claude-sonnet-4.5       16.0k in, 74 out, 0 cached (Est. 1 Premium request)
        #       claude-sonnet-4.5       1.2m in, 4.6k out, 1.1m cached (Est. 1 Premium request)
        # Token counts can have k (thousands) or m (millions) suffix

        # Try new format first
        usage_match = re.search(r'Breakdown by AI model:\s*\n\s+([\w.-]+)\s+([\d.]+[km]?)\s+in,\s+([\d.]+[km]?)\s+out(?:,\s+([\d.]+[km]?)\s+cached)?', full_output)

        # Fall back to old format if new format not found
        if not usage_match:
            usage_match = re.search(r'Usage by model:\s*\n\s+([\w.-]+)\s+([\d.]+[km]?)\s+input,\s+([\d.]+[km]?)\s+output(?:,\s+([\d.]+[km]?)\s+cache read)?', full_output)
        
        if usage_match:
            # Extract model name
            model = usage_match.group(1)
            
            # Parse input tokens
            input_str = usage_match.group(2)
            token_usage.input_tokens = self._parse_token_count(input_str)
            
            # Parse output tokens
            output_str = usage_match.group(3)
            token_usage.output_tokens = self._parse_token_count(output_str)
            
            # Parse cache read tokens (optional)
            if usage_match.group(4):
                cache_str = usage_match.group(4)
                token_usage.cache_read_input_tokens = self._parse_token_count(cache_str)
        
        # Extract duration from "Total duration (API):" line
        # Format examples: "5s", "1m 7.26s", "1m 7s"
        api_duration_match = re.search(r'Total duration \(API\):\s+((?:(\d+)m\s+)?([\d.]+)s)', full_output)
        if api_duration_match:
            minutes = int(api_duration_match.group(2)) if api_duration_match.group(2) else 0
            seconds = float(api_duration_match.group(3))
            total_seconds = minutes * 60 + seconds
            token_usage.duration_api_ms = int(total_seconds * 1000)
        
        # Extract total duration from "Total duration (wall):" line
        # Format examples: "8s", "1m 18.152s", "2m 30s"
        wall_duration_match = re.search(r'Total duration \(wall\):\s+((?:(\d+)m\s+)?([\d.]+)s)', full_output)
        if wall_duration_match:
            minutes = int(wall_duration_match.group(2)) if wall_duration_match.group(2) else 0
            seconds = float(wall_duration_match.group(3))
            total_seconds = minutes * 60 + seconds
            token_usage.duration_ms = int(total_seconds * 1000)
        
        # Remove the usage summary from response (everything after "Total usage est:")
        response_parts = full_output.split("\n\nTotal usage est:")
        response = response_parts[0] if response_parts else full_output

        return response, token_usage, permission_denials, model

    def _parse_token_count(self, token_str: str) -> int:
        """Parse token count string (e.g., '14.3k', '1.2m', or '39') to integer.

        Args:
            token_str: Token count string with optional k (thousands) or m (millions) suffix

        Returns:
            Token count as integer
        """
        token_str = token_str.strip()
        if token_str.endswith('m'):
            number = float(token_str[:-1])
            return int(number * 1_000_000)
        elif token_str.endswith('k'):
            number = float(token_str[:-1])
            return int(number * 1000)
        else:
            return int(token_str)

    def translate_allowed_tools(self, tools: List[str]) -> List[str]:
        """Convert tool names to Copilot format.

        Copilot uses simple tool names, need to remove path parameters.

        Args:
            tools: List of tool names

        Returns:
            List of converted tool names
        """
        processed_tools = []

        for tool in tools:
            # If tool name has path parameter, remove path part
            if "(" in tool and ")" in tool:
                tool_name = tool.split("(")[0].lower()
            else:
                tool_name = tool.lower()

            # Remove duplicates
            if tool_name not in processed_tools:
                processed_tools.append(tool_name)

        return processed_tools

    def add_directories(self, cmd: List[str], directories: List[str]) -> List[str]:
        """Add allowed directories to command line arguments.

        Args:
            cmd: Current command line arguments
            directories: List of directories

        Returns:
            Updated command line arguments
        """
        for directory in directories:
            cmd.extend(["--add-dir", directory])
        return cmd

    def get_output_format(self) -> List[str]:
        """Get Copilot CLI's output format parameters.

        Returns:
            Empty list (Copilot doesn't use output format parameter)
        """
        # Copilot doesn't use output format parameter
        return []

    @property
    def event_driver_conforming(self) -> bool:
        return True

    def build_event_driver_command(
        self,
        prompt: str,
        allowed_tools: Optional[List[str]] = None,
        allowed_directories: Optional[List[str]] = None,
    ) -> List[str]:
        command = self.build_command(prompt, allowed_tools, allowed_directories)
        command.append("--output-format=json")
        return command

    def record_existing_sessions(self) -> None:
        """Snapshot ordinary Copilot sessions before its plain-text command runs."""
        session_dir = Path.home() / ".copilot" / "session-state"
        self._existing_sessions = (
            {entry.name for entry in session_dir.iterdir() if entry.is_file() or entry.is_dir()}
            if session_dir.exists()
            else set()
        )

    def extract_event_driver_session(self, records) -> Optional[str]:
        if not records or records[-1].get("type") != "result":
            return None
        terminal_records = [record for record in records if record.get("type") == "result"]
        if len(terminal_records) != 1 or terminal_records[0].get("status") != "success":
            return None
        return self._verified_event_driver_session(
            terminal_records,
            matches=lambda record: record.get("type") == "result"
            and record.get("status") == "success",
            field="sessionId",
        )

    def accepts_event_driver_callback(
        self, records, *, session_id: str, event_id: str
    ) -> bool:
        return self._verified_event_driver_acceptance(
            records,
            session_matches=lambda record: record.get("type") == "session.start",
            acceptance_matches=lambda record: record.get("type") == "user.message"
            and self._event_driver_record_contains_text(record.get("data"), event_id),
            session_field="sessionId",
            session_id=session_id,
            event_id=event_id,
        )

    def extract_session_id(self, output_lines: List[str]) -> Optional[str]:
        """Discover the session created by ordinary plain-text Copilot execution."""
        session_dir = Path.home() / ".copilot" / "session-state"
        if not session_dir.exists():
            return None
        current = {
            entry.name for entry in session_dir.iterdir() if entry.is_file() or entry.is_dir()
        }
        created = current - getattr(self, "_existing_sessions", set())
        if len(created) != 1:
            return None
        return next(iter(created)).removesuffix(".jsonl")
