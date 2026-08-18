"""Session management for AI agents."""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from cafe.core.types import AgentCLI, AgentConfig, SessionData


class SessionManager:
    """Manages agent sessions and their persistence.

    Each issue has an independent session directory, and each agent+CLI combination has an independent session file.
    """

    def __init__(self, sessions_dir: str = ".cafe/sessions") -> None:
        """Initialize session manager.

        Args:
            sessions_dir: Directory to store session files
        """
        self.sessions_dir = Path(sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def get_session_file(
        self,
        agent_name: str,
        cli: AgentCLI,
        issue_name: Optional[str] = None,
        phase_name: Optional[str] = None,
    ) -> Path:
        """Get the session file path for an agent.

        Args:
            agent_name: Name of the agent
            cli: CLI type (claude, gemini, copilot, cursor)
            issue_name: Name of the issue (for issue-specific sessions)

        Returns:
            Path to the session file

        Examples:
            Without issue: .cafe/sessions/Roger_copilot.json
            With issue: .cafe/issues/myip/sessions/Roger_copilot.json
        """
        if issue_name:
            # Issue-specific sessions go under .cafe/issues/{issue_name}/sessions/
            issue_sessions_dir = Path(".cafe/issues") / issue_name / "sessions"
            issue_sessions_dir.mkdir(parents=True, exist_ok=True)
            phase_suffix = f"_{phase_name}" if phase_name else ""
            return issue_sessions_dir / f"{agent_name}_{cli.value}{phase_suffix}.json"

        # Global sessions (no issue) go under .cafe/sessions/
        return self.sessions_dir / f"{agent_name}_{cli.value}.json"

    def load_session(
        self,
        agent_name: str,
        cli: AgentCLI,
        issue_name: Optional[str] = None,
        phase_name: Optional[str] = None,
    ) -> Optional[SessionData]:
        """Load existing session data for an agent.

        Args:
            agent_name: Name of the agent
            cli: CLI type
            issue_name: Name of the issue (for issue-specific sessions)

        Returns:
            SessionData if exists, None otherwise
        """
        session_file = self.get_session_file(agent_name, cli, issue_name, phase_name)
        if not session_file.exists():
            return None

        try:
            with open(session_file, "r") as f:
                data = json.load(f)
                return SessionData(**data)
        except (json.JSONDecodeError, ValueError):
            # Invalid session file, return None
            return None

    def save_session(
        self,
        agent_name: str,
        cli: AgentCLI,
        session_id: str,
        issue_name: Optional[str] = None,
        phase_name: Optional[str] = None,
    ) -> None:
        """Save session data for an agent.

        Args:
            agent_name: Name of the agent
            cli: CLI type
            session_id: Session ID to save
            issue_name: Name of the issue (for issue-specific sessions)
        """
        session_file = self.get_session_file(agent_name, cli, issue_name, phase_name)

        # Load existing session to preserve created_at, or create new
        existing = self.load_session(agent_name, cli, issue_name, phase_name)
        now = datetime.now()

        session_data = SessionData(
            agent_name=agent_name,
            cli=cli,
            session_id=session_id,
            created_at=existing.created_at if existing else now,
            last_used_at=now,
            phase_name=phase_name,
        )

        with open(session_file, "w") as f:
            json.dump(session_data.model_dump(mode="json"), f, indent=2, default=str)

    def delete_session(
        self,
        agent_name: str,
        cli: AgentCLI,
        issue_name: Optional[str] = None,
        phase_name: Optional[str] = None,
    ) -> None:
        """Delete session for an agent.

        Args:
            agent_name: Name of the agent
            cli: CLI type
            issue_name: Name of the issue (for issue-specific sessions)
        """
        session_file = self.get_session_file(agent_name, cli, issue_name, phase_name)
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
