"""Agent management for CAFE."""

import json
import os
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from cafe.agents.executor import AgentExecutor, AgentExecutionError
from cafe.core.session import SessionManager
from cafe.core.types import AgentConfig, PermissionDenial, TokenUsage


class AgentNotFoundError(Exception):
    """Agent not found error."""

    pass


class AgentManager:
    """Manages multiple AI agents and their sessions."""

    # Agent directory constants
    CAFE_DIR = ".cafe"
    AGENTS_DIR = "agents"

    def __init__(self, session_manager: Optional[SessionManager] = None, issue_name: Optional[str] = None) -> None:
        """Initialize agent manager.

        Args:
            session_manager: Session manager for handling agent sessions
            issue_name: Issue name for issue-specific sessions
        """
        self.session_manager = session_manager or SessionManager()
        self.issue_name = issue_name
        self.agents: Dict[str, AgentExecutor] = {}
        self.current_agent_name: Optional[str] = None
        self._total_token_usage = TokenUsage()
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
            
            # Get mock response from env var (default: CAFE_READY_FOR_REVIEW with mock spec)
            mock_response = os.getenv("CAFE_MOCK_RESPONSE", "CAFE_READY_FOR_REVIEW\n\n# Mock Spec\n\nThis is a mock specification.")
            self.agents[config.name] = MockAgentExecutor(
                config=config,
                response=mock_response
            )
            return
        
        # Load existing session for this agent+CLI combination (if any)
        # Use issue-specific session if issue_name is provided
        session_data = self.session_manager.load_session(config.name, config.cli, self.issue_name)
        session_id = session_data.session_id if session_data else None
        # Note: Don't create session here - let executor handle it on first use

        # Update config with session ID (may be None)
        config_with_session = AgentConfig(
            name=config.name,
            cli=config.cli,
            session_id=session_id,
            model=config.model,
        )

        # Create executor
        executor = AgentExecutor(config_with_session)
        self.agents[config.name] = executor

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
        streaming_output_file: Optional[str] = None
    ) -> Tuple[str, TokenUsage, List, Optional[List[str]], List[str]]:
        """Execute prompt with specified agent.

        Args:
            agent_name: Name of agent to use
            prompt: Prompt to execute
            allowed_tools: List of allowed tools (using Claude naming convention)
            allowed_directories: List of allowed directories
            streaming_output_file: Optional file path to write streaming output line-by-line

        Returns:
            Tuple of (agent's response, token usage, permission denials, cli_command_args, streaming_log)

        Raises:
            AgentNotFoundError: If agent not found
        """
        executor = self.get_agent(agent_name)

        # Show prompt if enabled
        if self.show_prompt:
            print(f"\n{'='*80}")
            print(f"📝 Prompt for {agent_name}:")
            print(f"{'='*80}")
            print(prompt)
            print(f"{'='*80}\n")

        # Track if we've already retried for session conflict
        retried = False

        while True:
            try:
                agent_response = executor.execute(prompt, allowed_tools, allowed_directories, streaming_output_file)
                break  # Success, exit loop
            except AgentExecutionError as e:
                # Handle session conflict (only retry once)
                if hasattr(e, 'error_type') and e.error_type == "SESSION_CONFLICT" and not retried:
                    retried = True
                    # Clear session ID to force creation of new session on next execution
                    print(f"⚠️  Session conflict detected, will create new session on retry...")
                    executor.config.session_id = None

                    # Loop will retry (with no session ID, a new one will be created)
                else:
                    # Not a session conflict, or already retried - re-raise
                    raise

        # Extract response components
        response = agent_response.response
        token_usage = agent_response.token_usage
        permission_denials = agent_response.permission_denials
        cli_command_args = agent_response.cli_command_args
        streaming_log = agent_response.streaming_log

        # Save session ID if it was created during execution
        if executor.config.session_id:
            self.session_manager.save_session(
                agent_name, executor.config.cli, executor.config.session_id, self.issue_name
            )

        # Accumulate token usage
        self._total_token_usage.input_tokens += token_usage.input_tokens
        self._total_token_usage.output_tokens += token_usage.output_tokens
        self._total_token_usage.cache_creation_input_tokens += token_usage.cache_creation_input_tokens
        self._total_token_usage.cache_read_input_tokens += token_usage.cache_read_input_tokens
        self._total_token_usage.total_cost_usd += token_usage.total_cost_usd

        # For model and duration, use the latest value (not accumulate)
        if token_usage.model:
            self._total_token_usage.model = token_usage.model
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

        return response, token_usage, permission_denials, cli_command_args, streaming_log

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
                self.current_agent_name, current.config.cli, current.config.session_id, self.issue_name
            )

        # Accumulate token usage
        self._total_token_usage.input_tokens += token_usage.input_tokens
        self._total_token_usage.output_tokens += token_usage.output_tokens
        self._total_token_usage.cache_creation_input_tokens += token_usage.cache_creation_input_tokens
        self._total_token_usage.cache_read_input_tokens += token_usage.cache_read_input_tokens
        self._total_token_usage.total_cost_usd += token_usage.total_cost_usd

        # For model and duration, use the latest value (not accumulate)
        if token_usage.model:
            self._total_token_usage.model = token_usage.model
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
            raise RuntimeError(
                f"Failed to create Claude session: {result.stderr}"
            )

        try:
            response = json.loads(result.stdout)
            session_id = response.get("session_id")
            if not session_id:
                raise RuntimeError("No session_id in Claude CLI response")
            return session_id
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"Failed to parse Claude CLI response: {e}"
            ) from e

    @classmethod
    def get_agent_file_path(cls, agent_name: str, role: str, cafe_dir: str = None) -> str:
        """Get the path to agent md file (for use in prompts).

        Args:
            agent_name: Agent name (e.g. "Roger", "David", "Richard", "John")
            role: Agent role directory name (e.g. "pm", "developer", "reviewer")
            cafe_dir: CAFE config directory path (deprecated, not used)

        Returns:
            str: Agent file path (tries ~/.cafe/agents first, then src/cafe/data/agents)

        Examples:
            >>> AgentManager.get_agent_file_path("Roger", "pm")
            'src/cafe/data/agents/pm/Roger.md'
            >>> AgentManager.get_agent_file_path("David", "developer")
            'src/cafe/data/agents/developer/David.md'
            >>> AgentManager.get_agent_file_path("John", "developer")
            'src/cafe/data/agents/developer/John.md'
        """
        from pathlib import Path

        # Try ~/.cafe/agents first
        home_path = Path.home() / ".cafe" / "agents" / role / f"{agent_name}.md"
        if home_path.exists():
            return str(home_path)

        # Fall back to src/cafe/data/agents
        return f"src/cafe/data/agents/{role}/{agent_name}.md"
