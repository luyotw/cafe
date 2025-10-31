"""Session management for AI agents."""

import json
from pathlib import Path
from typing import Optional

from aaf.core.types import AgentConfig


class SessionManager:
    """Manages agent sessions and their persistence."""

    def __init__(self, sessions_dir: str = ".aaf/sessions") -> None:
        """Initialize session manager.

        Args:
            sessions_dir: Directory to store session files
        """
        self.sessions_dir = Path(sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def get_session_file(self, agent_name: str, issue_name: Optional[str] = None) -> Path:
        """Get the session file path for an agent.

        Args:
            agent_name: Name of the agent
            issue_name: Name of the issue (for issue-specific sessions)

        Returns:
            Path to the session file
        """
        if issue_name:
            return self.sessions_dir / f"{issue_name}_{agent_name}_session_id"
        return self.sessions_dir / f"{agent_name}_session_id"

    def load_session(self, agent_name: str, issue_name: Optional[str] = None) -> Optional[str]:
        """Load existing session ID for an agent.

        Args:
            agent_name: Name of the agent
            issue_name: Name of the issue (for issue-specific sessions)

        Returns:
            Session ID if exists, None otherwise
        """
        session_file = self.get_session_file(agent_name, issue_name)
        if session_file.exists():
            return session_file.read_text().strip()
        return None

    def save_session(self, agent_name: str, session_id: str, issue_name: Optional[str] = None) -> None:
        """Save session ID for an agent.

        Args:
            agent_name: Name of the agent
            session_id: Session ID to save
            issue_name: Name of the issue (for issue-specific sessions)
        """
        session_file = self.get_session_file(agent_name, issue_name)
        session_file.write_text(session_id)

    def delete_session(self, agent_name: str, issue_name: Optional[str] = None) -> None:
        """Delete session for an agent.

        Args:
            agent_name: Name of the agent
            issue_name: Name of the issue (for issue-specific sessions)
        """
        session_file = self.get_session_file(agent_name, issue_name)
        if session_file.exists():
            session_file.unlink()

    def init_or_resume_session(self, agent_config: AgentConfig) -> str:
        """Initialize new session or resume existing one.

        Args:
            agent_config: Agent configuration

        Returns:
            Session ID (existing or newly created)
        """
        # Try to load existing session
        existing_session = self.load_session(agent_config.name)
        if existing_session:
            return existing_session

        # Create new session (placeholder - actual implementation depends on agent tool)
        # This would call the actual agent API to create a session
        session_id = self._create_new_session(agent_config)
        self.save_session(agent_config.name, session_id)
        return session_id

    def _create_new_session(self, agent_config: AgentConfig) -> str:
        """Create a new session (to be implemented per agent type).

        Args:
            agent_config: Agent configuration

        Returns:
            New session ID
        """
        # Placeholder - actual implementation in agent-specific classes
        raise NotImplementedError("Session creation must be implemented by agent executor")
