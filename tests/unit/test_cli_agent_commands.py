"Tests for agent ls, rm, create, and edit CLI commands."

import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from typer.testing import CliRunner

from cafe.ui.cli import app

runner = CliRunner()


@pytest.fixture
def temp_agent_dir(tmp_path, monkeypatch):
    """
    Create a temporary directory to simulate the home directory containing
    the .cafe/agents directory.
    """
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    
    # Patch Path.home() to return our temporary home directory
    monkeypatch.setattr(Path, "home", lambda: home_dir)

    agents_dir = home_dir / ".cafe" / "agents"
    agents_dir.mkdir(parents=True)

    return agents_dir


class TestAgentCommand:
    """Test `cafe agent` subcommands."""

    def test_agent_ls_no_agents(self, temp_agent_dir):
        """Test `agent ls` when no agents exist."""
        result = runner.invoke(app, ["agent", "ls"])
        assert result.exit_code == 0
        assert "No agents found" in result.stdout

    def test_agent_ls_with_agents(self, temp_agent_dir):
        """Test `agent ls` with multiple agents in different roles."""
        (temp_agent_dir / "pm").mkdir()
        (temp_agent_dir / "pm" / "Roger.md").write_text("PM Roger")
        (temp_agent_dir / "developer").mkdir()
        (temp_agent_dir / "developer" / "David.md").write_text("Dev David")

        result = runner.invoke(app, ["agent", "ls"])

        assert result.exit_code == 0
        assert "developer/" in result.stdout
        assert "David.md" in result.stdout
        assert "pm/" in result.stdout
        assert "Roger.md" in result.stdout

    def test_agent_rm_nonexistent(self, temp_agent_dir):
        """Test `agent rm` with a non-existent agent."""
        result = runner.invoke(app, ["agent", "rm", "developer/nonexistent.md"])
        assert result.exit_code == 1
        assert "Error: Agent 'developer/nonexistent.md' not found" in result.stdout

    def test_agent_rm_success(self, temp_agent_dir):
        """Test successful removal of an agent."""
        agent_path = temp_agent_dir / "pm" / "ToBeDeleted.md"
        agent_path.parent.mkdir()
        agent_path.write_text("delete me")
        
        assert agent_path.exists()
        result = runner.invoke(app, ["agent", "rm", "pm/ToBeDeleted.md"], input="y\n")
        
        assert result.exit_code == 0
        assert "removed successfully" in result.stdout
        assert not agent_path.exists()

    @patch("inquirer.prompt")
    def test_agent_create_success(self, mock_prompt, temp_agent_dir):
        """Test successful creation of a new agent."""
        mock_prompt.return_value = {
            "role": "developer",
            "name": "Michael",
            "description": "A senior Go developer",
            "conduct": "Writes clean code.",
        }

        result = runner.invoke(app, ["agent", "create"])
        
        assert result.exit_code == 0
        assert "Agent 'Michael' created successfully" in result.stdout

        new_agent_path = temp_agent_dir / "developer" / "Michael.md"
        assert new_agent_path.exists()
        content = new_agent_path.read_text()
        assert "name: Michael" in content
        assert "description: A senior Go developer" in content
        assert "Writes clean code." in content

    @patch("inquirer.prompt")
    def test_agent_create_already_exists(self, mock_prompt, temp_agent_dir):
        """Test `agent create` when the agent file already exists."""
        # Pre-create the agent file
        agent_path = temp_agent_dir / "developer" / "Michael.md"
        agent_path.parent.mkdir(exist_ok=True)
        agent_path.write_text("Original content")
        
        mock_prompt.return_value = {
            "role": "developer",
            "name": "Michael",
            "description": "A new description",
            "conduct": "New conduct",
        }

        result = runner.invoke(app, ["agent", "create"])
        
        assert result.exit_code == 1
        assert "Error: Agent 'Michael' already exists in role 'developer'" in result.stdout
        # Ensure the original file was not overwritten
        assert agent_path.read_text() == "Original content"

    @patch("subprocess.run")
    @patch("inquirer.prompt")
    def test_agent_edit_success(self, mock_prompt, mock_subprocess_run, temp_agent_dir):
        """Test successful editing of an agent."""
        # Create a dummy agent to edit
        agent_path = temp_agent_dir / "reviewer" / "Richard.md"
        agent_path.parent.mkdir()
        agent_path.write_text("Original content")

        # Mock interactive prompts
        mock_prompt.side_effect = [
            {"role": "reviewer"},
            {"agent_name": "Richard"},
        ]

        # Mock the editor call
        mock_subprocess_run.return_value = MagicMock(returncode=0)
        editor = os.environ.get("EDITOR", "vim")

        result = runner.invoke(app, ["agent", "edit"])

        assert result.exit_code == 0
        assert "Agent 'reviewer/Richard.md' updated successfully." in result.stdout
        mock_subprocess_run.assert_called_once_with([editor, str(agent_path)], check=True)

    @patch("inquirer.prompt")
    def test_agent_edit_no_agents_in_role(self, mock_prompt, temp_agent_dir):
        """Test `agent edit` when the selected role has no agents."""
        mock_prompt.return_value = {"role": "pm"}

        result = runner.invoke(app, ["agent", "edit"])
        
        assert result.exit_code == 0
        assert "No agents found for role 'pm'" in result.stdout