"""Agent management for AAF."""

import json
import subprocess
from typing import Dict, List, Optional, Tuple

from aaf.agents.executor import AgentExecutor, AgentExecutionError
from aaf.core.session import SessionManager
from aaf.core.types import AgentConfig, TokenUsage


class AgentNotFoundError(Exception):
    """Agent not found error."""

    pass


class AgentManager:
    """Manages multiple AI agents and their sessions."""

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

    def register_agent(self, config: AgentConfig) -> None:
        """Register an agent with configuration.

        Args:
            config: Agent configuration
        """
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

    def execute(self, agent_name: str, prompt: str, allowed_tools: Optional[List[str]] = None) -> Tuple[str, TokenUsage]:
        """Execute prompt with specified agent.

        Args:
            agent_name: Name of agent to use
            prompt: Prompt to execute
            allowed_tools: List of allowed tools (using Claude naming convention)

        Returns:
            Tuple of (agent's response, token usage)

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
                response, token_usage = executor.execute(prompt, allowed_tools)
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

        return response, token_usage

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
