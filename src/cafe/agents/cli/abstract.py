"""Abstract base class defining the common interface for all CLI tools."""

import os
from abc import ABC, abstractmethod
from typing import List, Optional, Tuple

from cafe.core.types import AgentConfig, PermissionDenial, TokenUsage


class AbstractCLI(ABC):
    """Abstract base class for CLI tools.

    All CLI tools (Claude, Gemini, Cursor, Copilot) must inherit from this class
    and implement all abstract methods.
    """

    def __init__(self, config: AgentConfig) -> None:
        """Initialize CLI strategy.

        Args:
            config: Agent configuration
        """
        self.config = config

    @abstractmethod
    def build_command(
        self,
        prompt: str,
        allowed_tools: Optional[List[str]] = None,
        allowed_directories: Optional[List[str]] = None,
    ) -> List[str]:
        """Build CLI command line arguments.

        Args:
            prompt: Prompt text
            allowed_tools: List of allowed tools
            allowed_directories: List of allowed directories

        Returns:
            Complete list of command line arguments
        """
        pass

    @abstractmethod
    def parse_response(
        self,
        output_lines: List[str],
        streaming_log: Optional[List[str]] = None,
    ) -> Tuple[str, TokenUsage, List[PermissionDenial]]:
        """Parse CLI output.

        Args:
            output_lines: List of CLI output lines
            streaming_log: Streaming output log (optional)

        Returns:
            Tuple of (response, token_usage, permission_denials)
        """
        pass

    @abstractmethod
    def translate_allowed_tools(self, tools: List[str]) -> List[str]:
        """Translate tool names to this CLI's format.

        Args:
            tools: List of tool names (using internal convention format)

        Returns:
            List of translated tool names
        """
        pass

    @abstractmethod
    def add_directories(self, cmd: List[str], directories: List[str]) -> List[str]:
        """Add allowed directories to command line arguments.

        Args:
            cmd: Current command line arguments
            directories: List of directories

        Returns:
            Updated command line arguments
        """
        pass

    @abstractmethod
    def get_output_format(self) -> List[str]:
        """Get output format parameters for this CLI.

        Returns:
            Command line parameters for output format (e.g. ["--output-format", "stream-json"])
        """
        pass

    @abstractmethod
    def extract_session_id(self, output_lines: List[str]) -> Optional[str]:
        """Extract session ID from output.

        Args:
            output_lines: List of CLI output lines

        Returns:
            Session ID if found, None otherwise
        """
        pass

    def create_session(self) -> str:
        """Create a new session.

        This method is optional to implement. For CLIs that automatically create sessions
        (like Gemini, Cursor), use the default implementation (return empty string).
        For CLIs that need explicit session creation (like Claude), override this method.

        Returns:
            New session ID, or empty string if CLI automatically creates sessions

        Raises:
            AgentExecutionError: If session creation fails
        """
        # Default implementation: return empty string, indicating CLI will auto-create session
        return ""

    def build_environment(self) -> dict[str, str]:
        """Build process environment for this CLI."""
        return dict(os.environ)
