"""
Agent management utilities for CAFE."""

import os
import shutil
from pathlib import Path
from typing import List, Optional

import yaml


class AgentManager:
    """Manage agents."""

    def __init__(self, config_dir: str = ".cafe", issue_name: Optional[str] = None):
        """Initialize agent manager.

        Args:
            config_dir: CAFE configuration directory name.
            issue_name: Optional issue name for session management.
        """
        # Note: Agents are stored in the HOME directory's .cafe config,
        # not the project's .cafe directory.
        self.agent_dir = Path.home() / config_dir / "agents"
        self.agent_dir.mkdir(parents=True, exist_ok=True)
        self.issue_name = issue_name  # Used by other parts of the application

    def list_agents(self) -> List[str]:
        """List all available agents.

        Returns:
            List of agent names (relative paths from the agent directory)
        """
        if not self.agent_dir.exists():
            return []

        agents = []
        for path in self.agent_dir.rglob("*.md"):
            if ".git" in path.parts:
                continue
            relative_path = path.relative_to(self.agent_dir)
            agents.append(str(relative_path))

        return sorted(agents)

    def list_agents_by_role(self, role: str) -> List[str]:
        """List all available agents for a specific role.

        Args:
            role: The role directory (e.g., 'pm', 'developer').

        Returns:
            List of agent names (file names without extension).
        """
        role_dir = self.agent_dir / role
        if not role_dir.exists() or not role_dir.is_dir():
            return []

        return sorted([p.stem for p in role_dir.glob("*.md")])

    def create_agent(self, role: str, name: str, description: str, conduct: str) -> Path:
        """Create a new agent file.

        Args:
            role: The role of the agent (e.g., 'pm').
            name: The name of the agent (e.g., 'Michael').
            description: A short description of the agent.
            conduct: The main content describing the agent's code of conduct.

        Returns:
            The path to the newly created agent file.

        Raises:
            FileExistsError: If an agent with the same name already exists for that role.
        """
        role_dir = self.agent_dir / role
        role_dir.mkdir(exist_ok=True)

        agent_path = role_dir / f"{name}.md"

        if agent_path.exists():
            raise FileExistsError(f"Agent '{name}' already exists in role '{role}'.")

        frontmatter = {
            "name": name,
            "description": description,
        }
        content = f"---\n{yaml.dump(frontmatter)}---\n\n{conduct}\n"

        agent_path.write_text(content, encoding="utf-8")
        return agent_path

    def get_agent_path(self, role: str, name: str) -> Optional[Path]:
        """Get the full path to an agent's file.

        Args:
            role: The role of the agent.
            name: The name of the agent (without extension).

        Returns:
            The Path object for the agent file, or None if not found.
        """
        agent_file = self.agent_dir / role / f"{name}.md"
        return agent_file if agent_file.exists() else None

    def remove_agent(self, agent_name: str) -> None:
        """Remove an agent.

        Args:
            agent_name: Name of the agent to remove (relative path)

        Raises:
            FileNotFoundError: If agent doesn't exist
            PermissionError: If trying to delete outside the agent directory
        """
        agent_path = (self.agent_dir / agent_name).resolve()

        # Security check: Ensure the path is within the agent_dir
        if self.agent_dir.resolve() not in agent_path.parents:
            raise PermissionError("Cannot remove files outside the agent directory.")

        if not agent_path.exists() or not agent_path.is_file():
            raise FileNotFoundError(f"Agent not found: {agent_name}")

        agent_path.unlink()

    def agent_exists(self, agent_name: str) -> bool:
        """Check if an agent exists.

        Args:
            agent_name: Name of the agent (relative path)

        Returns:
            True if agent exists, False otherwise
        """
        return (self.agent_dir / agent_name).exists()
