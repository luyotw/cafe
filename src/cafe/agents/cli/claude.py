"""Claude CLI tool implementation."""

import json
import logging
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from cafe.agents.cli.abstract import AbstractCLI
from cafe.core.types import PermissionDenial, TokenUsage
from cafe.utils.git_utils import get_repo_root, to_git_ignore_path

logger = logging.getLogger(__name__)


class ClaudeCLI(AbstractCLI):
    """Concrete implementation of Claude CLI tool."""

    def build_command(
        self,
        prompt: str,
        allowed_tools: Optional[List[str]] = None,
        allowed_directories: Optional[List[str]] = None,
    ) -> List[str]:
        """Build Claude CLI command line arguments.

        Parameter order: claude -> --resume -> -p -> --model -> --allowed-tools -> --output-format -> --add-dir

        Args:
            prompt: Prompt text
            allowed_tools: List of allowed tools (already converted format)
            allowed_directories: List of allowed directories

        Returns:
            Complete command line argument list
        """
        cmd = ["claude"]

        # 1. If has session_id, add --resume parameter (must be before -p)
        if self.config.session_id:
            cmd.extend(["--resume", self.config.session_id])

        # 2. Add -p parameter (always at front, except --resume)
        cmd.extend(["-p", prompt])

        # 3. If has model, add --model parameter (must be after -p)
        if self.config.model:
            cmd.extend(["--model", self.config.model])

        # 4. If has allowed_tools, add --allowed-tools parameter
        if allowed_tools:
            tools_arg_value = ",".join(allowed_tools)
            cmd.extend(["--allowed-tools", tools_arg_value])

        # 5. Add output format parameter
        cmd.extend(self.get_output_format())

        # 6. If has allowed_directories, add --add-dir parameter
        if allowed_directories:
            cmd = self.add_directories(cmd, allowed_directories)

        return cmd

    def parse_response(
        self,
        output_lines: List[str],
        streaming_log: Optional[List[str]] = None,
    ) -> Tuple[str, TokenUsage, List[PermissionDenial]]:
        """Parse Claude CLI's stream-json output.

        Args:
            output_lines: List of lines from CLI output
            streaming_log: Streaming output log (optional, not used here)

        Returns:
            (response, token_usage, permission_denials) tuple
        """
        response_text = ""
        token_usage = TokenUsage()
        permission_denials = []

        for line in output_lines:
            try:
                data = json.loads(line.strip())

                # Extract content (new format: message.content[] or old format: content)
                if "message" in data and "content" in data["message"]:
                    for content_block in data["message"]["content"]:
                        if content_block.get("type") == "text":
                            response_text = content_block.get("text", "")
                elif "content" in data:
                    response_text = data["content"]

                # Extract token usage
                if "usage" in data:
                    usage_data = data["usage"]
                    token_usage = TokenUsage(
                        input_tokens=usage_data.get("input_tokens", 0),
                        output_tokens=usage_data.get("output_tokens", 0),
                        cache_creation_input_tokens=usage_data.get("cache_creation_input_tokens", 0),
                        cache_read_input_tokens=usage_data.get("cache_read_input_tokens", 0),
                    )

                if "total_cost_usd" in data:
                    token_usage.total_cost_usd = data["total_cost_usd"]

                # Extract permission denials
                if "permission_denials" in data and data["permission_denials"]:
                    for denial_data in data["permission_denials"]:
                        permission_denials.append(
                            PermissionDenial(
                                tool_name=denial_data["tool_name"],
                                tool_input=denial_data["tool_input"]
                            )
                        )

            except json.JSONDecodeError:
                # Non-JSON line, ignore
                continue

        return response_text, token_usage, permission_denials

    def translate_allowed_tools(self, tools: List[str]) -> List[str]:
        """Convert tool names to Claude format (capitalize first letter, convert paths to git-ignore format).

        Args:
            tools: List of tool names (lowercase format, e.g. ["read", "write(/path)"])

        Returns:
            List of converted tool names (e.g. ["Read", "Write(/.cafe/config.yaml)"])
        """
        processed_tools = []

        for tool in tools:
            # Handle tools with paths or commands (e.g. write(/path) or bash(git status))
            if "(" in tool and ")" in tool:
                tool_name = tool.split("(")[0].lower()
                path_or_cmd = tool.split("(")[1].rstrip(")")

                # Determine if it's a path or command
                # If tool_name is bash, treat as command, don't convert path format
                if tool_name == "bash":
                    # Command parameter, use directly, don't add / prefix
                    processed_tool = f"{tool_name.capitalize()}({path_or_cmd})"
                else:
                    # Path parameter, needs conversion to git-ignore format
                    path_obj = Path(path_or_cmd)

                    # Distinguish path types:
                    # 1. Already git ignore format (starts with / but not absolute path)
                    # 2. Absolute path (system path, e.g. /Users/...)
                    # 3. Relative path (e.g. .cafe/... or src/...)
                    is_git_ignore_format = path_or_cmd.startswith("/") and not path_obj.is_absolute()

                    if is_git_ignore_format:
                        # Already git ignore format, keep unchanged
                        processed_tool = f"{tool_name.capitalize()}({path_or_cmd})"
                    elif path_obj.is_absolute():
                        # Absolute path, convert to git ignore format
                        try:
                            repo_root = get_repo_root()
                            git_ignore_path = to_git_ignore_path(path_obj, repo_root)
                            processed_tool = f"{tool_name.capitalize()}({git_ignore_path})"
                        except (ValueError, OSError) as e:
                            # Conversion failed, use original path and log warning
                            logger.warning(
                                f"Failed to convert path to git-ignore format: {path_or_cmd}. "
                                f"Error: {e}. Using original path."
                            )
                            processed_tool = f"{tool_name.capitalize()}({path_or_cmd})"
                    else:
                        # Relative path, add / prefix (git ignore format)
                        git_ignore_path = "/" + path_or_cmd
                        processed_tool = f"{tool_name.capitalize()}({git_ignore_path})"
            else:
                # Tool has no path parameter, just capitalize first letter
                processed_tool = tool.capitalize()

            # Remove duplicates
            if processed_tool not in processed_tools:
                processed_tools.append(processed_tool)

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
        """Get Claude CLI's output format parameters.

        Returns:
            Output format related command line parameters
        """
        return ["--output-format", "stream-json", "--verbose"]

    def extract_session_id(self, output_lines: List[str]) -> Optional[str]:
        """Extract session ID from output.

        Args:
            output_lines: List of lines from CLI output

        Returns:
            Session ID, or None if not found
        """
        for line in output_lines:
            try:
                data = json.loads(line.strip())
                if "session_id" in data:
                    return data["session_id"]
            except json.JSONDecodeError:
                continue
        return None

    def create_session(self) -> str:
        """Create new Claude session.

        Execute a simple Claude command to create new session, and extract session ID from response.

        Returns:
            New session ID

        Raises:
            Exception: If session creation fails or session ID cannot be extracted
        """
        cmd = ["claude", "-p", "Say 'hi'", "--output-format", "json"]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )

        try:
            response_data = json.loads(result.stdout)

            # Check for errors (e.g. usage limit reached)
            if response_data.get("is_error"):
                error_msg = response_data.get("result", "Unknown error")
                print(f"\n⚠️  Claude API Error: {error_msg}\n")
                raise Exception(f"Claude API error: {error_msg}")

            session_id = response_data.get("session_id")
            if not session_id:
                raise Exception("No session_id in response")
            return session_id
        except json.JSONDecodeError as e:
            raise Exception(f"Failed to parse Claude response: {e}") from e
