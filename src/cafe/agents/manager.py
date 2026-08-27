"""Agent management for CAFE."""

import json
import os
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from cafe.agents.diagnostics import (
    build_failed_attempt,
    is_transient_same_cli_error,
    sanitize_error_excerpt,
)
from cafe.agents.executor import AgentExecutor, AgentExecutionError
from cafe.core.session import SessionManager
from cafe.core.session_continuation import (
    SessionContinuation,
    SessionContinuationPolicy,
)
from cafe.core.types import AgentCLI, AgentConfig, AgentResponse, CliEntry, TokenUsage


class AgentNotFoundError(Exception):
    """Agent not found error."""

    pass


class AgentManager:
    """Manages multiple AI agents and their sessions."""

    # Agent directory constants
    CAFE_DIR = ".cafe"
    AGENTS_DIR = "agents"
    FALLBACKABLE_ERROR_TYPES = ("rate_limit", "cli_not_found", "cli_unavailable", "model_not_found")
    SUPPORTS_COLD_TAKEOVER = True

    def __init__(
        self,
        session_manager: Optional[SessionManager] = None,
        issue_name: Optional[str] = None,
        stream_agent_output: bool = True,
    ) -> None:
        """Initialize agent manager.

        Args:
            session_manager: Session manager for handling agent sessions
            issue_name: Issue name for issue-specific sessions
            stream_agent_output: Whether executors print agent response narration
        """
        self.session_manager = session_manager or SessionManager()
        self.issue_name = issue_name
        self.stream_agent_output = stream_agent_output
        self.agents: Dict[str, AgentExecutor] = {}
        self.current_agent_name: Optional[str] = None
        self._total_token_usage = TokenUsage()
        self._last_model: Optional[str] = None  # Track latest model used
        self._last_cli: Optional[AgentCLI] = None
        self._last_session_id: Optional[str] = None
        self._failed_attempts: List[Dict[str, object]] = []
        self.show_prompt = False  # CLI can set this to True to show prompts
        self._use_mock = os.getenv("CAFE_MOCK_AGENTS", "").lower() in ("true", "1", "yes")

    def register_agent(self, config: AgentConfig) -> None:
        """Register an agent with configuration.

        Args:
            config: Agent configuration
        """
        # Check if we should use mock agent
        if self._use_mock:
            from cafe.agents.mock_executor import MockAgentExecutor

            # Get mock response from env var (default: ready_for_review with mock spec)
            mock_response = os.getenv(
                "CAFE_MOCK_RESPONSE",
                "ready_for_review\n\n# Mock Spec\n\nThis is a mock specification.",
            )
            self.agents[config.name] = MockAgentExecutor(config=config, response=mock_response)
            return

        # Load existing session for this agent+CLI combination (if any)
        # Use issue-specific session if issue_name is provided
        session_data = self.session_manager.load_session(config.name, config.cli, self.issue_name)
        session_id = session_data.session_id if session_data else None
        # Note: Don't create session here - let executor handle it on first use

        # Update config with session ID (may be None), preserve clis chain and legacy fields
        config_with_session = AgentConfig(
            name=config.name,
            cli=config.cli,
            session_id=session_id,
            model=config.model,
            clis=config.clis,
            backup_clis=config.backup_clis,
            models_config=config.models_config,
        )

        # Create executor
        executor = AgentExecutor(config_with_session)
        executor.stream_output = self.stream_agent_output
        self.agents[config.name] = executor

    def _load_active_cli_from_file(
        self,
        agent_name: str,
    ) -> Optional[tuple[AgentCLI, Optional[str], Optional[AgentCLI], Optional[tuple[str, ...]]]]:
        """Load the last successful CLI for this agent from active_clis.json."""
        if self.issue_name is None:
            return None
        active_file = Path(".cafe") / "issues" / self.issue_name / "active_clis.json"
        if not active_file.exists():
            return None

        try:
            raw = json.loads(active_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None

        record = raw.get(agent_name)
        if not isinstance(record, dict):
            return None

        raw_cli = record.get("cli")
        if not isinstance(raw_cli, str):
            return None

        try:
            cli = AgentCLI(raw_cli)
        except ValueError:
            return None

        model = record.get("model")
        model_value = model if isinstance(model, str) else None

        raw_primary = record.get("configured_primary")
        configured_primary: Optional[AgentCLI] = None
        if isinstance(raw_primary, str):
            try:
                configured_primary = AgentCLI(raw_primary)
            except ValueError:
                configured_primary = None

        raw_chain = record.get("chain")
        chain: Optional[tuple[str, ...]] = None
        if isinstance(raw_chain, list):
            chain_entries: list[str] = []
            for item in raw_chain:
                if isinstance(item, str) and item:
                    chain_entries.append(item)
                elif isinstance(item, dict):
                    cli_name = item.get("cli")
                    if not isinstance(cli_name, str) or not cli_name:
                        continue
                    model_value = item.get("model")
                    model_text = str(model_value).strip() if isinstance(model_value, str) else ""
                    chain_entries.append(f"{cli_name}:{model_text}")
            chain = tuple(chain_entries) if chain_entries else None

        return cli, (model if isinstance(model, str) else None), configured_primary, chain

    def _configured_chain_for_agent(self, config: AgentConfig) -> list[CliEntry]:
        """Return configured CLI chain entries for this agent."""
        if config.clis:
            return [
                CliEntry(
                    cli=entry.cli,
                    model=entry.model,
                    phase_models=dict(entry.phase_models),
                )
                for entry in config.clis
                if isinstance(entry, CliEntry)
            ]

        chain: list[CliEntry] = [
            CliEntry(cli=config.cli, model=config.model),
        ]
        for backup_cli in config.backup_clis:
            phase_models = {}
            raw_phase_models = config.models_config.get(backup_cli.value)
            if isinstance(raw_phase_models, dict):
                phase_models = dict(raw_phase_models)
            chain.append(CliEntry(cli=backup_cli, phase_models=phase_models))

        return chain

    @staticmethod
    def _normalize_chain(cli_chain: List[CliEntry]) -> list[CliEntry]:
        """Remove duplicate entries from a CLI chain while preserving order."""
        output: list[CliEntry] = []
        seen: set[AgentCLI] = set()
        for entry in cli_chain:
            if entry.cli in seen:
                continue
            seen.add(entry.cli)
            output.append(entry)
        return output

    def configured_primary_cli(self, config: AgentConfig) -> Optional[AgentCLI]:
        """Return the configured primary CLI before any sticky reorder."""
        chain = self._normalize_chain(self._configured_chain_for_agent(config))
        return chain[0].cli if chain else None

    def _resolve_execution_chain(
        self,
        config: AgentConfig,
        phase_name: Optional[str] = None,
    ) -> list[CliEntry]:
        """Build execution chain with fallback preference from last successful CLI."""
        chain = self._normalize_chain(self._configured_chain_for_agent(config))

        configured_chain = tuple(
            f"{entry.cli.value}:{entry.resolve_model(phase_name) or ''}" for entry in chain
        )

        last_success = self._load_active_cli_from_file(config.name)
        if not last_success:
            return chain

        preferred_cli, _, recorded_primary, recorded_chain = last_success
        if recorded_chain is not None and recorded_chain != configured_chain:
            return chain

        # Sticky reorder is a within-config fallback preference: keep using the
        # CLI that last succeeded so we don't thrash mid-issue. But an explicit
        # execution config change that changes the configured primary must win. If the
        # configured primary differs from what it was when this CLI was recorded
        # (or the record predates this field), treat the sticky record as stale.
        if chain and recorded_primary is not None and recorded_primary != chain[0].cli:
            return chain
        if chain and recorded_primary is None and preferred_cli != chain[0].cli:
            return chain

        cli_values = [entry.cli for entry in chain]
        if preferred_cli not in cli_values:
            return chain

        reordered = [entry for entry in chain if entry.cli != preferred_cli]
        preferred_entry = next((entry for entry in chain if entry.cli == preferred_cli), None)
        if preferred_entry is None:
            return chain

        reordered.insert(0, preferred_entry)
        return self._normalize_chain(reordered)

    def _session_id_for_cli(
        self,
        agent_name: str,
        cli: AgentCLI,
        phase_name: Optional[str] = None,
    ) -> Optional[str]:
        """Load a persisted session ID for agent+CLI, if available."""
        saved = self.session_manager.load_session(
            agent_name,
            cli,
            self.issue_name,
            phase_name,
        )
        if not saved:
            return None

        return saved.session_id

    def get_last_successful_cli_and_session(
        self,
        agent_name: str,
        phase_name: Optional[str] = None,
    ) -> tuple[Optional[AgentCLI], Optional[str]]:
        """Return last successful CLI and its session for this agent, if available."""
        try:
            config = self.get_agent(agent_name).config
        except AgentNotFoundError:
            return None, None

        active_info = self._load_active_cli_from_file(agent_name)
        if not active_info:
            return None, None

        active_cli = active_info[0]
        configured = [
            entry.cli for entry in self._normalize_chain(self._configured_chain_for_agent(config))
        ]
        if active_cli not in configured:
            return None, None

        return active_cli, self._session_id_for_cli(agent_name, active_cli, phase_name)

    def configured_execution_chain(self, agent_name: str) -> list[CliEntry]:
        """Return the canonical configured execution chain."""
        config = self.get_agent(agent_name).config
        return self._normalize_chain(self._configured_chain_for_agent(config))

    def get_execution_config(
        self,
        agent_name: str,
        phase_name: Optional[str] = None,
        continuation: Optional[SessionContinuation] = None,
    ) -> AgentConfig:
        """Return an AgentConfig adjusted for the effective CLI continuation target."""
        base = self.get_agent(agent_name).config
        continuation = continuation or SessionContinuation.auto()
        chain = self._resolve_execution_chain(base, phase_name=phase_name)
        if not chain:
            return self._base_config_for_continuation(base, continuation)

        if continuation.is_exact:
            exact_index = next(
                (index for index, entry in enumerate(chain) if entry.cli == continuation.cli),
                None,
            )
            if exact_index is None:
                continuation = SessionContinuation.new()
            elif exact_index:
                chain = [chain[exact_index], *chain[:exact_index], *chain[exact_index + 1 :]]

        primary = chain[0]
        if continuation.is_exact and primary.cli == continuation.cli:
            primary_session_id = continuation.session_id
        elif continuation.policy == SessionContinuationPolicy.AUTO:
            primary_session_id = self._session_id_for_cli(
                agent_name,
                primary.cli,
                phase_name,
            )
        else:
            primary_session_id = None
        primary_model = primary.resolve_model(phase_name)
        if primary_model is None:
            primary_model = primary.model

        return AgentConfig(
            name=base.name,
            cli=primary.cli,
            session_id=primary_session_id,
            model=primary_model,
            clis=chain,
            backup_clis=[entry.cli for entry in chain[1:]],
            models_config=base.models_config,
        )

    @staticmethod
    def _base_config_for_continuation(
        base: AgentConfig,
        continuation: SessionContinuation,
    ) -> AgentConfig:
        """Apply an explicit continuation policy without consulting persistence."""
        if continuation.policy == SessionContinuationPolicy.AUTO:
            return base
        if continuation.is_exact and continuation.cli == base.cli:
            session_id = continuation.session_id
        else:
            session_id = None
        return base.model_copy(update={"session_id": session_id})

    @staticmethod
    def _config_is_equivalent(a: AgentConfig, b: AgentConfig) -> bool:
        return (
            a.cli == b.cli
            and a.session_id == b.session_id
            and a.model == b.model
            and a.clis == b.clis
            and a.backup_clis == b.backup_clis
            and a.models_config == b.models_config
        )

    def get_agent(self, name: str) -> AgentExecutor:
        """Get agent executor by name.

        Args:
            name: Agent name

        Returns:
            Agent executor

        Raises:
            AgentNotFoundError: If agent not found
        """
        if name not in self.agents:
            raise AgentNotFoundError(f"Agent '{name}' not found")
        return self.agents[name]

    def switch_agent(self, name: str) -> None:
        """Switch to a different agent.

        Args:
            name: Agent name to switch to

        Raises:
            AgentNotFoundError: If agent not found
        """
        if name not in self.agents:
            raise AgentNotFoundError(f"Agent '{name}' not found")
        self.current_agent_name = name

    def get_current_agent(self) -> Optional[AgentExecutor]:
        """Get current active agent.

        Returns:
            Current agent executor, or None if no agent selected
        """
        if self.current_agent_name is None:
            return None
        return self.agents.get(self.current_agent_name)

    def execute(
        self,
        agent_name: str,
        prompt: str,
        allowed_tools: Optional[List[str]] = None,
        allowed_directories: Optional[List[str]] = None,
        streaming_output_file: Optional[str] = None,
        phase_name: Optional[str] = None,
        continuation: Optional[SessionContinuation] = None,
        backup_context_callback: Optional[Callable[[AgentExecutionError], str]] = None,
    ) -> Tuple[str, TokenUsage, List, Optional[List[str]], List[str], Optional[str]]:
        """Execute prompt with specified agent.

        Args:
            agent_name: Name of agent to use
            prompt: Prompt to execute
            allowed_tools: List of allowed tools (using Claude naming convention)
            allowed_directories: List of allowed directories
            streaming_output_file: Optional file path to write streaming output line-by-line
            phase_name: Current phase name for phase-specific model lookup in backup agents

        Returns:
            Tuple of (agent's response, token usage, permission denials, cli_command_args, streaming_log, model)

        Raises:
            AgentNotFoundError: If agent not found
            AgentExecutionError: If all agents (primary + backups) fail
        """
        self._failed_attempts = []
        base_executor = self.get_agent(agent_name)

        try:
            execution_config = self.get_execution_config(
                agent_name,
                phase_name=phase_name,
                continuation=continuation,
            )
        except Exception:
            execution_config = self._base_config_for_continuation(
                base_executor.config,
                continuation or SessionContinuation.auto(),
            )

        if not self._config_is_equivalent(base_executor.config, execution_config):
            executor = AgentExecutor(execution_config)
            executor.stream_output = self.stream_agent_output
            effective_continuation = continuation or SessionContinuation.auto()
            if effective_continuation.policy == SessionContinuationPolicy.AUTO:
                self.agents[agent_name] = executor
        else:
            executor = base_executor

        # Show prompt if enabled
        if self.show_prompt:
            print(f"\n{'=' * 80}")
            print(f"📝 Prompt for {agent_name}:")
            print(f"{'=' * 80}")
            print(prompt)
            print(f"{'=' * 80}\n")

        # Track if we've already retried for session conflict
        retried = False
        transient_retry_done = False
        primary_attempt = 1
        attempt_prompt = prompt

        while True:
            try:
                agent_response = executor.execute(
                    attempt_prompt,
                    allowed_tools,
                    allowed_directories,
                    streaming_output_file,
                )
                break  # Success, exit loop
            except AgentExecutionError as e:
                self._record_failed_attempt(
                    cli=executor.config.cli,
                    chain_role="primary",
                    attempt=primary_attempt,
                    error=e,
                )
                # Handle session conflict (only retry once)
                if (
                    hasattr(e, "error_type") and e.error_type == "SESSION_CONFLICT" and not retried
                ):
                    retried = True
                    # Clear session ID to force creation of new session on next execution
                    print(f"⚠️  Session conflict detected, will create new session on retry...")
                    executor.config.session_id = None
                    primary_attempt += 1

                    # Loop will retry (with no session ID, a new one will be created)
                elif is_transient_same_cli_error(e) and not transient_retry_done:
                    transient_retry_done = True
                    primary_attempt += 1
                    print(
                        f"⚠️  {executor.config.cli.value} connection closed unexpectedly, "
                        "retrying once..."
                    )
                elif hasattr(e, "error_type") and e.error_type in self.FALLBACKABLE_ERROR_TYPES:
                    # Try backup agents
                    agent_response = self._try_backup_agents(
                        primary_error=e,
                        primary_executor=executor,
                        prompt=prompt,
                        allowed_tools=allowed_tools,
                        allowed_directories=allowed_directories,
                        streaming_output_file=streaming_output_file,
                        phase_name=phase_name,
                        continuation=continuation,
                        backup_context_callback=backup_context_callback,
                    )
                    break  # Backup succeeded, exit loop
                else:
                    # Not a session conflict, or already retried - re-raise
                    raise

        # Extract response components
        response = agent_response.response
        token_usage = agent_response.token_usage
        permission_denials = agent_response.permission_denials
        cli_command_args = agent_response.cli_command_args
        streaming_log = agent_response.streaming_log
        model = agent_response.model
        actual_cli = agent_response.cli or executor.config.cli
        actual_session_id = agent_response.session_id
        if model is None and actual_cli == executor.config.cli:
            model = executor.config.model

        self._last_cli = actual_cli
        self._last_session_id = actual_session_id

        # Save session ID if it was created during execution
        if actual_session_id:
            self.session_manager.save_session(
                agent_name,
                actual_cli,
                actual_session_id,
                self.issue_name,
                phase_name,
            )

        # Accumulate token usage
        self._total_token_usage.input_tokens += token_usage.input_tokens
        self._total_token_usage.output_tokens += token_usage.output_tokens
        self._total_token_usage.cache_creation_input_tokens += (
            token_usage.cache_creation_input_tokens
        )
        self._total_token_usage.cache_write_input_tokens += token_usage.cache_write_input_tokens
        self._total_token_usage.cache_read_input_tokens += token_usage.cache_read_input_tokens
        self._total_token_usage.reasoning_output_tokens += token_usage.reasoning_output_tokens
        self._total_token_usage.total_cost_usd += token_usage.total_cost_usd
        if token_usage.turn_usages:
            self._total_token_usage.turn_usages.extend(token_usage.turn_usages)

        # For duration, accumulate the values
        if token_usage.duration_ms is not None:
            if self._total_token_usage.duration_ms is None:
                self._total_token_usage.duration_ms = token_usage.duration_ms
            else:
                self._total_token_usage.duration_ms += token_usage.duration_ms
        if token_usage.duration_api_ms is not None:
            if self._total_token_usage.duration_api_ms is None:
                self._total_token_usage.duration_api_ms = token_usage.duration_api_ms
            else:
                self._total_token_usage.duration_api_ms += token_usage.duration_api_ms

        return response, token_usage, permission_denials, cli_command_args, streaming_log, model

    def preview_cli_command_args(
        self,
        agent_name: str,
        prompt: str,
        allowed_tools: Optional[List[str]] = None,
        allowed_directories: Optional[List[str]] = None,
        phase_name: Optional[str] = None,
        continuation: Optional[SessionContinuation] = None,
    ) -> Optional[List[str]]:
        """Preview CLI command args before execution starts."""
        executor = AgentExecutor(
            self.get_execution_config(
                agent_name,
                phase_name=phase_name,
                continuation=continuation,
            )
        )
        return executor.preview_cli_command_args(prompt, allowed_tools, allowed_directories)

    def preview_cli_environment(
        self,
        agent_name: str,
        phase_name: Optional[str] = None,
        continuation: Optional[SessionContinuation] = None,
    ) -> Optional[dict[str, str]]:
        """Build the CLI environment that would be used for execution."""
        executor = AgentExecutor(
            self.get_execution_config(
                agent_name,
                phase_name=phase_name,
                continuation=continuation,
            )
        )
        return executor.preview_cli_environment()

    def _try_backup_agents(
        self,
        primary_error: "AgentExecutionError",
        primary_executor: AgentExecutor,
        prompt: str,
        allowed_tools: Optional[List[str]] = None,
        allowed_directories: Optional[List[str]] = None,
        streaming_output_file: Optional[str] = None,
        phase_name: Optional[str] = None,
        continuation: Optional[SessionContinuation] = None,
        backup_context_callback: Optional[Callable[[AgentExecutionError], str]] = None,
    ) -> "AgentResponse":
        """Try backup agents in order until one succeeds or all fail.

        Backup retry flow:
        1. If no backup CLIs are configured, re-raise the original error immediately
        2. Try each backup CLI in backup_clis in order
        3. If a backup CLI also hits a fallbackable runtime error, continue to the next
        4. If a backup CLI raises any other error, re-raise it immediately (do not continue)
        5. If all backups fail, raise an error listing all attempted CLIs

        phase_name is used to look up the model for the backup CLI from models_config,
        e.g. during the "develop" phase with gemini backup: models_config["gemini"]["develop"].

        Args:
            primary_error: Error raised by the primary agent
            primary_executor: Primary agent executor
            prompt: Prompt to execute
            allowed_tools: List of allowed tools
            allowed_directories: List of allowed directories
            streaming_output_file: File path for streaming output
            phase_name: Current phase name, used to look up model configuration

        Returns:
            AgentResponse: Response from the first successful backup agent

        Raises:
            AgentExecutionError: Raised when all agents (primary + backups) fail
        """
        config = primary_executor.config

        # Use clis chain when available; fall back to legacy backup_clis + models_config
        chain = config.clis
        if chain:
            fallback_entries = chain[1:]
        else:
            # Legacy path: reconstruct minimal CliEntry list from backup_clis + models_config
            from cafe.core.types import CliEntry

            fallback_entries = []
            for backup_cli in config.backup_clis:
                phase_models = {}
                if config.models_config:
                    raw = config.models_config.get(backup_cli.value, {})
                    phase_models = dict(raw) if isinstance(raw, dict) else {}
                fallback_entries.append(CliEntry(cli=backup_cli, phase_models=phase_models))

        if not fallback_entries:
            # No fallback entries configured, re-raise the original error
            raise primary_error

        primary_cli_name = config.cli.value
        print(
            f"❌ {primary_cli_name} failed ({self._fallback_reason(primary_error)}), trying backup agent..."
        )
        self._print_fallback_error_detail(primary_error)

        # Track tried CLIs to avoid duplicates
        tried_clis = {config.cli}
        failed_agents: List[str] = [f"{primary_cli_name} ({sanitize_error_excerpt(primary_error)})"]
        takeover_error = primary_error

        for entry in fallback_entries:
            if entry.cli in tried_clis:
                continue
            tried_clis.add(entry.cli)

            backup_model = entry.resolve_model(phase_name)
            print(f"Trying {entry.cli.value}...")

            # Every different-provider attempt is a cold takeover. Refresh the
            # durable runtime snapshot at this last responsible moment instead
            # of carrying a provider session or an earlier in-memory summary.
            backup_prompt = prompt
            if backup_context_callback is not None:
                try:
                    takeover_context = backup_context_callback(takeover_error)
                except Exception as exc:
                    failed_agents.append(
                        f"{entry.cli.value} (takeover context unavailable: {sanitize_error_excerpt(exc)})"
                    )
                    print(
                        f"❌ {entry.cli.value} takeover context unavailable; trying next agent..."
                    )
                    continue
                if takeover_context:
                    backup_prompt = (
                        f"{prompt}\n\nCold backup takeover context (fresh, provider-neutral):\n"
                        f"{takeover_context}"
                    )

            # Explicit workflow policies never continue a different fallback
            # session. AUTO preserves legacy sticky-session behavior.
            fallback_session_id = None
            if continuation is None or continuation.policy == SessionContinuationPolicy.AUTO:
                fallback_session_id = self._session_id_for_cli(
                    config.name,
                    entry.cli,
                    phase_name,
                )
            backup_config = AgentConfig(
                name=config.name,
                cli=entry.cli,
                model=backup_model,
                session_id=fallback_session_id,
            )
            backup_executor = AgentExecutor(backup_config)
            backup_executor.stream_output = self.stream_agent_output

            backup_attempt = 1
            transient_retry_done = False
            while True:
                try:
                    agent_response = backup_executor.execute(
                        backup_prompt,
                        allowed_tools,
                        allowed_directories,
                        streaming_output_file,
                    )
                    if agent_response.cli is None:
                        agent_response.cli = entry.cli
                    if agent_response.session_id is None:
                        agent_response.session_id = backup_executor.config.session_id
                    if agent_response.model is None:
                        agent_response.model = backup_model
                    print(f"✅ Successfully completed with {entry.cli.value}")
                    return agent_response
                except AgentExecutionError as backup_error:
                    self._record_failed_attempt(
                        cli=entry.cli,
                        chain_role="fallback",
                        attempt=backup_attempt,
                        error=backup_error,
                    )
                    if is_transient_same_cli_error(backup_error) and not transient_retry_done:
                        transient_retry_done = True
                        backup_attempt += 1
                        print(
                            f"⚠️  {entry.cli.value} connection closed unexpectedly, retrying once..."
                        )
                        continue
                    if (
                        hasattr(backup_error, "error_type")
                        and backup_error.error_type in self.FALLBACKABLE_ERROR_TYPES
                    ):
                        failed_agents.append(
                            f"{entry.cli.value} ({sanitize_error_excerpt(backup_error)})"
                        )
                        print(
                            f"❌ {entry.cli.value} failed ({self._fallback_reason(backup_error)}), trying next agent..."
                        )
                        self._print_fallback_error_detail(backup_error)
                        takeover_error = backup_error
                        break
                    raise

        # All agents (primary + all fallbacks) failed, compose error message
        tried_list = ", ".join(failed_agents)
        raise AgentExecutionError(
            f"All agents failed. Tried: {tried_list}. "
            f"Please wait for transient failures to clear or add more backup agents.",
            error_type=getattr(primary_error, "error_type", None) or "agent_unavailable",
            display_message=(
                f"All agents failed. Tried: {tried_list}. "
                "Please wait for transient failures to clear or add more backup agents."
            ),
        )

    def _record_failed_attempt(
        self,
        *,
        cli: AgentCLI,
        chain_role: str,
        attempt: int,
        error: AgentExecutionError,
    ) -> None:
        """Append one safe diagnostic record for the current execute call."""
        self._failed_attempts.append(
            build_failed_attempt(
                cli=cli,
                chain_role=chain_role,
                attempt=attempt,
                error=error,
            )
        )

    def get_failed_attempts(self) -> List[Dict[str, object]]:
        """Return a defensive copy of this execution's failed CLI attempts."""
        return [dict(attempt) for attempt in self._failed_attempts]

    @staticmethod
    def _fallback_reason(error: AgentExecutionError) -> str:
        error_type = getattr(error, "error_type", None)
        if error_type == "rate_limit":
            return "rate limit"
        if error_type == "cli_not_found":
            return "CLI not found"
        if error_type == "cli_unavailable":
            return "CLI unavailable"
        if error_type == "model_not_found":
            return "model unavailable"
        return str(error)

    @staticmethod
    def _print_fallback_error_detail(error: AgentExecutionError) -> None:
        detail = str(error).strip()
        if not detail:
            return

        print("   Original error:")
        for line in detail.splitlines():
            print(f"   {line}")

    def execute_current(self, prompt: str) -> str:
        """Execute prompt with current agent.

        Args:
            prompt: Prompt to execute

        Returns:
            Agent's response

        Raises:
            AgentNotFoundError: If no current agent selected
        """
        current = self.get_current_agent()
        if current is None:
            raise AgentNotFoundError("No current agent selected")

        response, token_usage = current.execute(prompt)

        # Save session ID if it was created during execution
        if current.config.session_id and self.current_agent_name:
            self.session_manager.save_session(
                self.current_agent_name,
                current.config.cli,
                current.config.session_id,
                self.issue_name,
            )

        # Accumulate token usage
        self._total_token_usage.input_tokens += token_usage.input_tokens
        self._total_token_usage.output_tokens += token_usage.output_tokens
        self._total_token_usage.cache_creation_input_tokens += (
            token_usage.cache_creation_input_tokens
        )
        self._total_token_usage.cache_write_input_tokens += token_usage.cache_write_input_tokens
        self._total_token_usage.cache_read_input_tokens += token_usage.cache_read_input_tokens
        self._total_token_usage.reasoning_output_tokens += token_usage.reasoning_output_tokens
        self._total_token_usage.total_cost_usd += token_usage.total_cost_usd

        # For duration, accumulate the values
        if token_usage.duration_ms is not None:
            if self._total_token_usage.duration_ms is None:
                self._total_token_usage.duration_ms = token_usage.duration_ms
            else:
                self._total_token_usage.duration_ms += token_usage.duration_ms
        if token_usage.duration_api_ms is not None:
            if self._total_token_usage.duration_api_ms is None:
                self._total_token_usage.duration_api_ms = token_usage.duration_api_ms
            else:
                self._total_token_usage.duration_api_ms += token_usage.duration_api_ms

        return response

    def get_total_token_usage(self) -> TokenUsage:
        """Get total accumulated token usage across all agent executions.

        Returns:
            Total token usage statistics
        """
        return self._total_token_usage

    def get_last_model(self) -> Optional[str]:
        """Get the last model name used.

        Returns:
            Last model name, or None if no model has been used yet
        """
        return self._last_model

    def get_last_cli(self) -> Optional[AgentCLI]:
        """Get the actual CLI that produced the last response."""
        return self._last_cli

    def get_last_session_id(self) -> Optional[str]:
        """Get the actual session id from the last response, if any."""
        return self._last_session_id

    def delete_session(self, agent_name: str) -> None:
        """Delete session for an agent.

        Args:
            agent_name: Agent name
        """
        # Get the agent's CLI type
        executor = self.get_agent(agent_name)
        self.session_manager.delete_session(agent_name, executor.config.cli, self.issue_name)

    def list_agents(self) -> List[str]:
        """List all registered agent names.

        Returns:
            List of agent names
        """
        return list(self.agents.keys())

    def has_agent(self, name: str) -> bool:
        """Check if agent exists.

        Args:
            name: Agent name

        Returns:
            True if agent exists
        """
        return name in self.agents

    def get_agent_config(self, name: str) -> AgentConfig:
        """Get agent configuration.

        Args:
            name: Agent name

        Returns:
            Agent configuration

        Raises:
            AgentNotFoundError: If agent not found
        """
        executor = self.get_agent(name)
        return executor.config

    def _create_claude_session(self) -> str:
        """Create a new Claude session by calling Claude CLI.

        Returns:
            Session ID from Claude CLI

        Raises:
            RuntimeError: If session creation fails
        """
        cmd = ["claude", "-p", "Say 'hi'", "--output-format", "json"]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to create Claude session: {result.stderr}")

        try:
            response = json.loads(result.stdout)
            session_id = response.get("session_id")
            if not session_id:
                raise RuntimeError("No session_id in Claude CLI response")
            return session_id
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Failed to parse Claude CLI response: {e}") from e

    @staticmethod
    def _agent_file_prompt_path(
        source: str, resolved_path: Path, agent_name: str, role: str
    ) -> str:
        if source == "builtin":
            return str(Path("src/cafe/data/agents") / role / f"{agent_name}.md")
        return str(resolved_path)

    @classmethod
    def get_agent_file_path(cls, agent_name: str, role: str, cafe_dir: str = None) -> str:
        """Get the path to agent md file (for use in prompts).

        Searches in order: local .cafe/agents/ first, then ~/.cafe/agents/,
        then falls back to src/cafe/data/agents/.

        Args:
            agent_name: Agent name (e.g. "Roger", "David", "Richard", "John")
            role: Agent role directory name (e.g. "pm", "developer", "reviewer")
            cafe_dir: CAFE config directory path (deprecated, not used)

        Returns:
            str: Agent file path
        """
        from cafe.catalogs.resolver import CatalogKind, CatalogResolver

        project_root = Path(cafe_dir).parent if cafe_dir else None
        resolver = CatalogResolver(project_root=project_root)
        entry = resolver.resolve(CatalogKind.AGENT, f"{role}/{agent_name}")
        return cls._agent_file_prompt_path(entry.source, entry.path, agent_name, role)

    @classmethod
    def read_agent_file(
        cls, agent_name: str, role: str, cafe_dir: str = None
    ) -> tuple[str, str]:
        """Resolve and read an agent definition under one shared catalog lock."""
        from cafe.catalogs.resolver import CatalogKind, CatalogResolver, global_catalog_lock

        project_root = Path(cafe_dir).parent if cafe_dir else None
        resolver = CatalogResolver(project_root=project_root)
        with global_catalog_lock(resolver.global_root):
            path = (
                cls.get_agent_file_path(agent_name, role, cafe_dir)
                if cafe_dir is not None
                else cls.get_agent_file_path(agent_name, role)
            )
            content_path = Path(path)
            builtin_prompt_path = Path("src/cafe/data/agents") / role / f"{agent_name}.md"
            if content_path == builtin_prompt_path:
                content_path = resolver.candidate_path(
                    CatalogKind.AGENT,
                    f"{role}/{agent_name}",
                    resolver.builtin_root / "agents",
                )
            try:
                content = content_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                content = ""
            return path, content
