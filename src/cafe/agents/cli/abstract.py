"""Abstract base class defining the common interface for all CLI tools."""

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional, Sequence, Tuple

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

    @property
    def event_driver_conforming(self) -> bool:
        """Whether this adapter has verified event-driver evidence parsing."""
        return False

    def build_event_driver_command(
        self,
        prompt: str,
        allowed_tools: Optional[List[str]] = None,
        allowed_directories: Optional[List[str]] = None,
    ) -> List[str]:
        """Build a provider command for callback-only structured observation."""
        return self.build_command(prompt, allowed_tools, allowed_directories)

    def extract_event_driver_session(
        self, records: Sequence[Mapping[str, Any]]
    ) -> Optional[str]:
        """Return a provider-created session only from adapter-verified evidence."""
        return None

    def accepts_event_driver_callback(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        session_id: str,
    ) -> bool:
        """Recognize durable callback acceptance for one exact resumed session."""
        return False

    def _verified_event_driver_session(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        matches: Callable[[Mapping[str, Any]], bool],
        field: str,
    ) -> Optional[str]:
        """Extract one non-conflicting session from exact provider record shapes."""
        session_ids: set[str] = set()
        for record in records:
            if not isinstance(record, Mapping) or not matches(record):
                continue
            model = record.get("model")
            if model is not None and model != self.config.model:
                return None
            session_id = record.get(field)
            if not isinstance(session_id, str) or not session_id.strip():
                return None
            session_ids.add(session_id.strip())
        return next(iter(session_ids)) if len(session_ids) == 1 else None

    def prepare_project_workspace(self, project_root: Path) -> None:
        """Prepare CLI-specific project workspace before execution."""
        return None

    def build_environment(self) -> dict[str, str]:
        """Build process environment for this CLI."""
        return dict(os.environ)

    def build_interactive_command(self, initial_prompt: Optional[str] = None) -> List[str]:
        """Build the command used for a user-owned interactive chat session.

        Interactive chat intentionally omits the non-interactive execution flags
        added by :meth:`build_command`, while preserving each CLI's resume/model
        conventions in one strategy-layer contract.
        """
        cli = self.config.cli.value
        command = [cli]

        if cli == "codex" and self.config.model:
            command.extend(["--model", self.config.model])

        if self.config.session_id:
            if cli == "codex":
                command.extend(["resume", self.config.session_id])
            else:
                command.extend(["--resume", self.config.session_id])

        if self.config.model and cli in {"claude", "copilot", "gemini"}:
            command.extend(["--model", self.config.model])

        if initial_prompt and cli in {"codex", "claude"}:
            command.append(initial_prompt)

        return command
