"""Claude CLI tool implementation."""

import json
import logging
from pathlib import Path
from typing import List, Optional, Tuple

from cafe.agents.cli.abstract import AbstractCLI
from cafe.core.types import PermissionDenial, TokenUsage
from cafe.utils.git_utils import get_git_toplevel

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
                if not isinstance(data, dict):
                    continue

                # Extract content (new format: message.content[] or old format: content)
                message = data.get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), list):
                    for content_block in message["content"]:
                        if not isinstance(content_block, dict):
                            continue
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
        """Convert tool names and paths to Claude permission-rule format.

        Args:
            tools: List of tool names (lowercase format, e.g. ["read", "write(/path)"])

        Returns:
            List of converted tool names (e.g. ["Read", "Write(//repo/.cafe/config.yaml)"])
        """
        processed_tools = []
        tool_name_map = {
            "bash": "Bash",
            "read": "Read",
            "write": "Write",
            "edit": "Edit",
            "grep": "Grep",
            "glob": "Glob",
            "ls": "LS",
            "webfetch": "WebFetch",
            "web_fetch": "WebFetch",
            "websearch": "WebSearch",
            "web_search": "WebSearch",
        }

        for tool in tools:
            # Handle tools with paths or commands (e.g. write(/path) or bash(git status))
            if "(" in tool and ")" in tool:
                tool_name = tool.split("(")[0].lower()
                path_or_cmd = tool.split("(")[1].rstrip(")")
                display_tool_name = tool_name_map.get(tool_name, tool.split("(")[0])

                # Determine if it's a path or command
                # If tool_name is bash, treat as command, don't convert path format
                if tool_name == "bash":
                    # Command parameter, use directly, don't add / prefix
                    processed_tool = f"{display_tool_name}({path_or_cmd})"
                else:
                    permission_path = self._to_permission_path(path_or_cmd)
                    processed_tool = f"{display_tool_name}({permission_path})"
            else:
                # Tool has no path parameter, normalize known Claude tool names.
                processed_tool = tool_name_map.get(tool.lower(), tool)

            # Remove duplicates
            if processed_tool not in processed_tools:
                processed_tools.append(processed_tool)

        return processed_tools

    @staticmethod
    def _to_permission_path(path: str) -> str:
        """Return a cwd-independent Claude permission path.

        Claude uses ``//path`` for absolute filesystem permission rules. CAFE's
        historical single-leading-slash form is repository-root relative, while
        unprefixed paths are also repository relative. Resolve both against the
        active checkout so a resumed agent can change directories without
        invalidating its workflow-artifact permissions.
        """
        if path.startswith("//"):
            return path

        try:
            checkout_root = get_git_toplevel().resolve()
        except (ValueError, OSError):
            checkout_root = Path.cwd().resolve()

        path_obj = Path(path)
        if path_obj.is_absolute():
            try:
                path_obj.relative_to(checkout_root)
                absolute_path = path_obj
            except ValueError:
                # A single leading slash is CAFE's legacy repository-root form.
                absolute_path = checkout_root / path.lstrip("/")
        else:
            absolute_path = checkout_root / path.removeprefix("./")

        return "/" + str(absolute_path.resolve())

    def add_directories(self, cmd: List[str], directories: List[str]) -> List[str]:
        """Add canonical allowed directories to command line arguments.

        Claude CLI evaluates its write sandbox against canonical filesystem
        paths.  Passing a relative directory such as ``.cafe`` works for
        reads, but can reject writes from a nested worktree because the tool
        resolves the target path before comparing it to ``--add-dir``.

        Args:
            cmd: Current command line arguments
            directories: List of directories

        Returns:
            Updated command line arguments
        """
        for directory in directories:
            cmd.extend(["--add-dir", str(Path(directory).expanduser().resolve())])
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
                if isinstance(data, dict) and "session_id" in data:
                    return data["session_id"]
            except json.JSONDecodeError:
                continue
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
            matches=lambda record: record.get("type") == "system"
            and record.get("subtype") == "init",
            session_field="session_id",
            session_id=session_id,
            event_id=event_id,
        )

    def create_session(self) -> str:
        """Claude sessions are created by the real prompt execution."""
        return ""
