"""Permission handling for tool usage."""

import re
from typing import Any, Dict, List, Optional

from cafe.core.types import PermissionAction, PermissionRequest


class PermissionDeniedError(Exception):
    """Permission denied error."""

    pass


class PermissionHandler:
    """Handles permission requests for tool usage."""

    def __init__(
        self,
        allowed_tools: Optional[List[str]] = None,
        auto_approve_read: bool = False,
        auto_approve_safe_git: bool = False,
    ) -> None:
        """Initialize permission handler.

        Args:
            allowed_tools: List of allowed tool patterns (e.g., ["Read(*)", "Bash(git:*)"])
            auto_approve_read: Auto-approve read operations
            auto_approve_safe_git: Auto-approve safe git commands
        """
        self.allowed_tools = allowed_tools or []
        self.auto_approve_read = auto_approve_read
        self.auto_approve_safe_git = auto_approve_safe_git
        self.history: List[Dict[str, Any]] = []

    def request_permission(self, request: PermissionRequest) -> PermissionAction:
        """Request permission for tool usage.

        Args:
            request: Permission request

        Returns:
            User's action (AUTHORIZE, DIALOG, or SKIP)
        """
        # Check if tool is in allowed list
        if self.is_tool_allowed(request.tool_name, request.tool_input):
            action = PermissionAction.AUTHORIZE
            self._record_request(request, action)
            return action

        # Check auto-approve rules
        if self.auto_approve_read and request.tool_name == "Read":
            action = PermissionAction.AUTHORIZE
            self._record_request(request, action)
            return action

        if self.auto_approve_safe_git and self._is_safe_git_command(request):
            action = PermissionAction.AUTHORIZE
            self._record_request(request, action)
            return action

        # Prompt user
        action = self._prompt_user(request)
        self._record_request(request, action)
        return action

    def format_request(self, request: PermissionRequest) -> str:
        """Format permission request for display.

        Args:
            request: Permission request

        Returns:
            Formatted request string
        """
        lines = [f"Tool: {request.tool_name}"]

        # Format tool input
        for key, value in request.tool_input.items():
            lines.append(f"  {key}: {value}")

        return "\n".join(lines)

    def is_tool_allowed(self, tool_name: str, tool_input: Dict[str, Any]) -> bool:
        """Check if tool is allowed by patterns.

        Args:
            tool_name: Tool name
            tool_input: Tool input parameters

        Returns:
            True if tool is allowed
        """
        for pattern in self.allowed_tools:
            if self._match_pattern(pattern, tool_name, tool_input):
                return True
        return False

    def get_history(self) -> List[Dict[str, Any]]:
        """Get permission request history.

        Returns:
            List of permission requests with actions
        """
        return self.history.copy()

    def _match_pattern(
        self, pattern: str, tool_name: str, tool_input: Dict[str, Any]
    ) -> bool:
        """Match tool against pattern.

        Args:
            pattern: Pattern like "Read" or "Bash(git:*)" or "Bash(git status)"
            tool_name: Tool name
            tool_input: Tool input parameters

        Returns:
            True if matches
        """
        # Pattern: "ToolName" or "ToolName(*)" or "ToolName(command_pattern)"
        if "(" not in pattern:
            # Exact tool name match
            return pattern == tool_name

        # Parse pattern: ToolName(param_pattern)
        match = re.match(r"([^(]+)\(([^)]*)\)", pattern)
        if not match:
            return False

        pattern_tool, pattern_param = match.groups()

        # Check tool name
        if pattern_tool != tool_name:
            return False

        # Check parameter pattern
        if pattern_param == "*":
            # Match any parameters
            return True

        # Check command parameter for Bash tool
        if tool_name == "Bash" and "command" in tool_input:
            command = tool_input["command"]

            # Pattern like "git:*" means starts with "git "
            if pattern_param.endswith(":*"):
                prefix = pattern_param[:-2]
                return command.startswith(prefix + " ") or command == prefix

            # Exact command match
            return command == pattern_param

        return False

    def _is_safe_git_command(self, request: PermissionRequest) -> bool:
        """Check if this is a safe git command.

        Args:
            request: Permission request

        Returns:
            True if it's a safe git command
        """
        if request.tool_name != "Bash":
            return False

        command = request.tool_input.get("command", "")

        # Safe git commands (read-only or non-destructive)
        safe_commands = [
            "git status",
            "git log",
            "git diff",
            "git branch",
            "git show",
        ]

        return any(command.startswith(safe) for safe in safe_commands)

    def _prompt_user(self, request: PermissionRequest) -> PermissionAction:
        """Prompt user for permission.

        Args:
            request: Permission request

        Returns:
            User's action
        """
        # This will be implemented with actual user interaction
        # For now, this is a placeholder that will be mocked in tests
        raise NotImplementedError("User prompt not yet implemented")

    def _record_request(
        self, request: PermissionRequest, action: PermissionAction
    ) -> None:
        """Record permission request in history.

        Args:
            request: Permission request
            action: User's action
        """
        self.history.append({"request": request, "action": action})
