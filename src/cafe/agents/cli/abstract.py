"""Abstract base class defining the common interface for all CLI tools."""

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional, Sequence, Tuple

from cafe.agents.stream_activity import StreamActivity
from cafe.agents.transport_types import _has_evidence_conflict, _validated_evidence_scalar
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

    def create_stream_activity(self, cmd: List[str]) -> StreamActivity | None:
        """Supply optional native activity for this invocation; stdout is default.

        The executor owns resource cleanup, idle policy and completion checks.
        An adapter owns native setup, session verification and metadata filtering.
        """
        return None

    def project_native_review(self, command: List[str]) -> List[str]:
        if self.config.native_review_configuration is not None:
            raise ValueError("selected provider lacks a verified read-only native review projection")
        return command

    def native_review_observations(self, output_lines: List[str], *, observed_at=None) -> List[dict]:
        return []

    def prepare_response_accounting(self, command, environment):
        """Optionally project provider counters to this physical invocation.

        Capture any native baseline before launch. The returned observer receives
        parsed usage and stdout records, including partial results on failure.
        """
        return None

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
        event_id: str,
    ) -> bool:
        """Recognize durable callback acceptance for one exact resumed session."""
        return False

    def _verified_event_driver_acceptance(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        session_matches: Callable[[Mapping[str, Any]], bool],
        acceptance_matches: Callable[[Mapping[str, Any]], bool],
        session_field: str,
        session_id: str,
        event_id: str,
    ) -> bool:
        """Verify a provider turn acknowledgement after exact-session evidence."""
        if not event_id.strip():
            return False
        if self.conversation_evidence(records).failure_code:
            return False
        if any(record.get("event_id") not in (None, event_id)
               or record.get("delivery_id") not in (None, event_id) for record in records):
            return False
        observed = self._verified_event_driver_session(
            records,
            matches=session_matches,
            field=session_field,
        )
        if observed != session_id:
            return False

        session_observed = False
        for record in records:
            if not isinstance(record, Mapping):
                continue
            if session_matches(record):
                session_observed = True
                continue
            if session_observed and acceptance_matches(record):
                return True
        return False

    def _event_driver_record_contains_text(self, value: Any, expected: str) -> bool:
        """Recognize an exact event token inside a provider-owned user record."""
        if isinstance(value, str):
            return expected in value
        if isinstance(value, Mapping):
            return any(
                self._event_driver_record_contains_text(item, expected)
                for item in value.values()
            )
        if isinstance(value, Sequence):
            return any(
                self._event_driver_record_contains_text(item, expected) for item in value
            )
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
            if _has_evidence_conflict((self.config.model, model)):
                return None
            try:
                session_id = _validated_evidence_scalar(record.get(field), strip=True)
            except ValueError:
                return None
            session_ids.add(session_id)
        return next(iter(session_ids)) if len(session_ids) == 1 else None

    conversation_operations = frozenset()
    conversation_session_operations = frozenset()
    conversation_model_operations = frozenset()
    conversation_usage_operations = frozenset()
    conversation_acceptance_operations = frozenset()

    def conversation_capabilities(self, operation):
        """Adapters explicitly admit operations and evidence formats separately."""
        from cafe.agents.transport_types import TransportCapabilities

        supported = operation in self.conversation_operations
        return TransportCapabilities(
            supported=supported,
            session=supported and operation in self.conversation_session_operations,
            model=supported and operation in self.conversation_model_operations,
            usage=supported and operation in self.conversation_usage_operations,
            acceptance=supported and operation in self.conversation_acceptance_operations,
        )

    def conversation_evidence(self, records):
        """Summarize authoritative provider identity records without retaining them."""
        from cafe.agents.transport_types import TransportResult

        identities = set()
        models = set()
        invalid = False
        for record in records:
            for model in self.conversation_reported_models(record):
                try:
                    models.add(_validated_evidence_scalar(model))
                except ValueError:
                    invalid = True
            if not self.conversation_identity_record(record):
                continue
            try:
                identities.add(
                    _validated_evidence_scalar(
                        record.get(self.conversation_session_field), strip=True
                    )
                )
            except ValueError:
                invalid = True
            model = record.get("model")
            if model is not None:
                try:
                    models.add(_validated_evidence_scalar(model))
                except ValueError:
                    invalid = True
        failure = None
        if invalid:
            failure = "invalid_evidence"
        elif _has_evidence_conflict(identities):
            failure = "conflicting_session_evidence"
        elif _has_evidence_conflict(
            (*models, self.config.model if models and self.config.model else None)
        ):
            failure = "model_mismatch"
        return TransportResult(
            observed_session_id=next(iter(identities)) if len(identities) == 1 and not invalid else None,
            reported_model=next(iter(models)) if len(models) == 1 else None,
            failure_code=failure,
        )

    def conversation_reported_models(self, record):
        if self.conversation_identity_record(record) and record.get("model") is not None:
            return [record["model"]]
        return []

    conversation_session_field = "session_id"

    def conversation_identity_record(self, record):
        """Adapters opt in to exact identity record shapes."""
        return False

    read_only_operations: frozenset[str] = frozenset()

    def require_read_only(self, operation: str) -> None:
        """Admit only an integrated native parameter path; no backend probe."""
        if operation not in self.read_only_operations:
            raise ValueError(
                f"Read-only chat is unsupported for {self.config.cli.value}/{operation}"
            )

    def apply_read_only(self, command: List[str], operation: str) -> List[str]:
        """Project native options, leaving ordinary construction unchanged."""
        self.require_read_only(operation)
        raise NotImplementedError("Integrated read-only operation has no projection")

    def prepare_project_workspace(self, project_root: Path) -> None:
        """Prepare CLI-specific project workspace before execution."""
        return None

    def build_environment(self) -> dict[str, str]:
        """Build process environment for this CLI."""
        return dict(os.environ)

    def supports_initial_context(self) -> bool:
        """Whether native interactive launch can carry an initial context."""
        return self.config.cli.value in {"codex", "claude"}

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

    def prepare_interactive_accounting(self, command, environment):
        """Optional native evidence reader; never capture the user's terminal.

        An absent reader means unsupported accounting, not verified zero usage.
        """

        return command, None
