"""Gemini CLI tool implementation."""

import json
from pathlib import Path
from typing import List, Optional, Tuple

from cafe.agents.cli.abstract import AbstractCLI
from cafe.core.types import PermissionDenial, TokenUsage
from cafe.skills.workspace import ensure_gemini_project_skills


class GeminiCLI(AbstractCLI):
    """Concrete implementation of Gemini CLI tool."""

    def prepare_project_workspace(self, project_root: Path) -> None:
        """Expose project skills to Gemini before execution."""
        ensure_gemini_project_skills(project_root)

    def build_command(
        self,
        prompt: str,
        allowed_tools: Optional[List[str]] = None,
        allowed_directories: Optional[List[str]] = None,
    ) -> List[str]:
        """Build Gemini CLI command line arguments.

        Parameter order: gemini -> -p -> --resume -> --model -> --allowed-tools -> --output-format -> --include-directories

        Args:
            prompt: Prompt text
            allowed_tools: List of allowed tools (already converted format)
            allowed_directories: List of allowed directories

        Returns:
            Complete command line argument list
        """
        # Ensure .geminiignore file exists
        self.ensure_geminiignore()

        cmd = ["gemini", "-p", prompt]

        # If has session_id, add --resume parameter
        if self.config.session_id:
            cmd.extend(["--resume", self.config.session_id])

        # If has model, add --model parameter
        if self.config.model:
            cmd.extend(["--model", self.config.model])

        # If has allowed_tools, add --allowed-tools parameter
        if allowed_tools:
            tools_arg_value = ",".join(allowed_tools)
            cmd.extend(["--allowed-tools", tools_arg_value])

        # Add output format parameter
        cmd.extend(self.get_output_format())

        # If has allowed_directories, add --include-directories parameter
        if allowed_directories:
            cmd = self.add_directories(cmd, allowed_directories)

        return cmd

    def parse_response(
        self,
        output_lines: List[str],
        streaming_log: Optional[List[str]] = None,
    ) -> Tuple[str, TokenUsage, List[PermissionDenial]]:
        """Parse Gemini CLI's stream-json output.

        Args:
            output_lines: List of lines from CLI output
            streaming_log: Streaming output log (optional, not used here)

        Returns:
            (response, token_usage, permission_denials) tuple
        """
        # Extract assistant messages, filter user prompt echo
        assistant_messages = []
        permission_denials = []
        token_usage = TokenUsage()
        full_response = ""

        # Track tool_use and corresponding parameters to create permission_denial on tool_result error
        tool_use_map = {}  # tool_id -> (tool_name, parameters)

        for line in output_lines:
            try:
                data = json.loads(line.strip())

                # Extract assistant messages (filter user messages)
                if data.get("type") == "message" and data.get("role") == "assistant":
                    content = data.get("content", "")
                    if content:
                        assistant_messages.append(content)

                # Extract last line's response as fallback
                if "response" in data:
                    full_response = data["response"]

                # Extract token usage from result
                if data.get("type") == "result":
                    stats = data.get("stats", {})
                    if stats:
                        token_usage.input_tokens = stats.get("input_tokens", 0)
                        token_usage.output_tokens = stats.get("output_tokens", 0)
                        # Gemini uses "cached" field for cache read tokens
                        token_usage.cache_read_input_tokens = stats.get("cached", 0)
                        token_usage.duration_ms = stats.get("duration_ms")
                        # Note: Gemini doesn't separate API duration, use total duration
                        token_usage.duration_api_ms = stats.get("duration_ms")

                # Track tool_use for use on tool_result error
                if data.get("type") == "tool_use":
                    tool_id = data.get("tool_id")
                    tool_name = data.get("tool_name")
                    parameters = data.get("parameters", {})
                    if tool_id and tool_name:
                        tool_use_map[tool_id] = (tool_name, parameters)

                # Check tool_result for errors (especially tool_not_registered)
                if data.get("type") == "tool_result" and data.get("status") == "error":
                    error = data.get("error", {})
                    if error.get("type") == "tool_not_registered":
                        # Get tool name and parameters from previous tool_use
                        tool_id = data.get("tool_id")
                        if tool_id in tool_use_map:
                            tool_name, tool_input = tool_use_map[tool_id]
                            permission_denials.append(
                                PermissionDenial(
                                    tool_name=tool_name,
                                    tool_input=tool_input
                                )
                            )

                # Extract permission denials
                # Note: Assumes Gemini uses similar format to Claude, adjust if actual format differs
                if "permission_denials" in data and data["permission_denials"]:
                    for denial_data in data["permission_denials"]:
                        permission_denials.append(
                            PermissionDenial(
                                tool_name=denial_data.get("tool_name", ""),
                                tool_input=denial_data.get("tool_input", {})
                            )
                        )

            except json.JSONDecodeError:
                # Non-JSON line, ignore
                continue

        # If extracted assistant messages, use them; otherwise use fallback response
        response = "".join(assistant_messages) if assistant_messages else full_response

        return response, token_usage, permission_denials

    def translate_allowed_tools(self, tools: List[str]) -> List[str]:
        """Convert tool names to Gemini format.

        Special handling: write_file doesn't support path parameters, need to remove path part.

        Args:
            tools: List of tool names

        Returns:
            List of converted tool names
        """
        processed_tools = []

        for tool in tools:
            # Special handling: write_file doesn't support path parameters
            if tool.startswith("write_file("):
                tool_name = "write_file"
            else:
                tool_name = tool

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
            cmd.extend(["--include-directories", directory])
        return cmd

    def get_output_format(self) -> List[str]:
        """Get Gemini CLI's output format parameters.

        Returns:
            Output format related command line parameters
        """
        return ["--output-format", "stream-json"]

    def extract_session_id(self, output_lines: List[str]) -> Optional[str]:
        """Extract session ID from output.

        Gemini provides session_id in init message.

        Args:
            output_lines: List of lines from CLI output

        Returns:
            Session ID, or None if not found
        """
        for line in output_lines:
            try:
                data = json.loads(line.strip())
                # Gemini provides session_id in init message
                if data.get("type") == "init" and "session_id" in data:
                    return data["session_id"]
            except json.JSONDecodeError:
                continue
        return None

    def ensure_geminiignore(self) -> None:
        """Ensure .geminiignore file exists and contains necessary configuration.

        Gemini CLI needs .geminiignore to exclude .cafe directory.
        """
        geminiignore_path = Path(".geminiignore")
        required_pattern = "!/.cafe"

        # Check if file exists and contains required pattern
        if geminiignore_path.exists():
            content = geminiignore_path.read_text()
            if required_pattern in content:
                return  # Already correctly configured

            # File exists but missing pattern - append it
            with open(geminiignore_path, "a") as f:
                if not content.endswith("\n"):
                    f.write("\n")
                f.write(f"{required_pattern}\n")
        else:
            # Create new .geminiignore file
            geminiignore_path.write_text(f"{required_pattern}\n")
