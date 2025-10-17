"""Agent management for AAF."""

from typing import Dict, List, Optional

from aaf.agents.executor import AgentExecutor
from aaf.core.session import SessionManager
from aaf.core.types import AgentConfig


class AgentNotFoundError(Exception):
    """Agent not found error."""

    pass


class AgentManager:
    """Manages multiple AI agents and their sessions."""

    def __init__(self, session_manager: Optional[SessionManager] = None) -> None:
        """Initialize agent manager.

        Args:
            session_manager: Session manager for handling agent sessions
        """
        self.session_manager = session_manager or SessionManager()
        self.agents: Dict[str, AgentExecutor] = {}
        self.current_agent_name: Optional[str] = None

    def register_agent(self, config: AgentConfig) -> None:
        """Register an agent with configuration.

        Args:
            config: Agent configuration
        """
        # Load existing session for this agent
        session_id = self.session_manager.load_session(config.name)
        if not session_id:
            # Generate a simple session ID if none exists
            # Actual session creation happens when agent is first executed
            import uuid
            session_id = f"{config.name}-{uuid.uuid4().hex[:8]}"
            self.session_manager.save_session(config.name, session_id)

        # Update config with session ID
        config_with_session = AgentConfig(
            name=config.name,
            tool=config.tool,
            session_id=session_id,
            allowed_tools=config.allowed_tools,
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

    def execute(self, agent_name: str, prompt: str) -> str:
        """Execute prompt with specified agent.

        Args:
            agent_name: Name of agent to use
            prompt: Prompt to execute

        Returns:
            Agent's response

        Raises:
            AgentNotFoundError: If agent not found
        """
        executor = self.get_agent(agent_name)
        return executor.execute(prompt)

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
        return current.execute(prompt)

    def delete_session(self, agent_name: str) -> None:
        """Delete session for an agent.

        Args:
            agent_name: Agent name
        """
        self.session_manager.delete_session(agent_name)

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
