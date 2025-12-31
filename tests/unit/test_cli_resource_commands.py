"""Tests for CLI resource management commands (template, agent)."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open
from click.exceptions import Exit
import tempfile
import os

from cafe.ui.commands.resource_commands import (
    template,
    agent_ls,
    agent_rm,
    agent_create,
    agent_edit,
)


@pytest.fixture
def mock_console():
    """Create a mock console."""
    with patch("cafe.ui.commands.resource_commands.console") as mock:
        yield mock


@pytest.fixture
def mock_template_manager():
    """Create a mock TemplateManager."""
    with patch("cafe.ui.commands.resource_commands.TemplateManager") as mock:
        instance = MagicMock()
        mock.return_value = instance
        yield instance


@pytest.fixture
def temp_agents_dir(tmp_path):
    """Create temporary agents directory with test agents."""
    agents_dir = tmp_path / ".cafe" / "agents"

    # Create PM agents
    pm_dir = agents_dir / "pm"
    pm_dir.mkdir(parents=True)
    (pm_dir / "Roger.md").write_text("---\nname: Roger\ndescription: PM agent\n---\nContent")

    # Create Developer agents
    dev_dir = agents_dir / "developer"
    dev_dir.mkdir(parents=True)
    (dev_dir / "David.md").write_text("---\nname: David\ndescription: Dev agent\n---\nContent")

    return tmp_path


class TestTemplate:
    """Test template command."""

    def test_template_add_success(self, mock_template_manager, mock_console, tmp_path):
        """Test adding a template successfully."""
        source_file = tmp_path / "template.md"
        source_file.write_text("Template content")

        template(action="add", source=str(source_file), name="my-template", config_file=".cafe/config.yaml")

        mock_template_manager.add_template.assert_called_once_with(str(source_file), "my-template")
        mock_console.print.assert_called_with("[green]✅ Template 'my-template' added successfully[/green]")

    def test_template_add_missing_args(self, mock_template_manager, mock_console):
        """Test adding template without required arguments."""
        with pytest.raises(Exit):
            template(action="add", source=None, name="my-template", config_file=".cafe/config.yaml")

        mock_console.print.assert_any_call("[red]Error: 'add' action requires both source file path and template name[/red]")

    def test_template_ls_with_templates(self, mock_template_manager, mock_console):
        """Test listing templates."""
        mock_template_manager.list_templates.return_value = ["template1", "template2"]

        template(action="ls", source=None, name=None, config_file=".cafe/config.yaml")

        mock_console.print.assert_any_call("[bold]Available templates:[/bold]")
        mock_console.print.assert_any_call("  • template1")
        mock_console.print.assert_any_call("  • template2")

    def test_template_ls_no_templates(self, mock_template_manager, mock_console):
        """Test listing templates when none exist."""
        mock_template_manager.list_templates.return_value = []

        template(action="ls", source=None, name=None, config_file=".cafe/config.yaml")

        mock_console.print.assert_called_with("[dim]No templates found[/dim]")

    def test_template_rm_success(self, mock_template_manager, mock_console):
        """Test removing a template."""
        template(action="rm", source=None, name="my-template", config_file=".cafe/config.yaml")

        mock_template_manager.remove_template.assert_called_once_with("my-template")
        mock_console.print.assert_called_with("[green]✅ Template 'my-template' removed successfully[/green]")

    def test_template_rm_missing_name(self, mock_template_manager, mock_console):
        """Test removing template without name."""
        with pytest.raises(Exit):
            template(action="rm", source=None, name=None, config_file=".cafe/config.yaml")

        mock_console.print.assert_any_call("[red]Error: 'rm' action requires template name[/red]")

    def test_template_unknown_action(self, mock_template_manager, mock_console):
        """Test template with unknown action."""
        with pytest.raises(Exit):
            template(action="unknown", source=None, name=None, config_file=".cafe/config.yaml")

        mock_console.print.assert_any_call("[red]Error: Unknown action 'unknown'[/red]")


class TestAgentLs:
    """Test agent ls command."""

    def test_agent_ls_no_agents_dir(self, mock_console, tmp_path, monkeypatch):
        """Test listing agents when directory doesn't exist."""
        monkeypatch.chdir(tmp_path)

        agent_ls()

        mock_console.print.assert_called_with("[yellow]No agents found.[/yellow]")

    def test_agent_ls_with_agents(self, mock_console, temp_agents_dir, monkeypatch):
        """Test listing agents."""
        monkeypatch.chdir(temp_agents_dir)

        with patch("cafe.ui.commands.resource_commands.Table") as mock_table:
            mock_table_instance = MagicMock()
            mock_table.return_value = mock_table_instance

            agent_ls()

            mock_table.assert_called_once()
            assert mock_table_instance.add_row.call_count >= 2
            mock_console.print.assert_any_call(mock_table_instance)

    def test_agent_ls_empty_roles(self, mock_console, tmp_path, monkeypatch):
        """Test listing agents when role directories exist but are empty."""
        agents_dir = tmp_path / ".cafe" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "pm").mkdir()
        (agents_dir / "developer").mkdir()
        (agents_dir / "reviewer").mkdir()

        monkeypatch.chdir(tmp_path)

        agent_ls()

        mock_console.print.assert_called_with("[yellow]No agents found.[/yellow]")


class TestAgentRm:
    """Test agent rm command."""

    def test_agent_rm_success(self, mock_console, temp_agents_dir, monkeypatch):
        """Test removing an agent."""
        monkeypatch.chdir(temp_agents_dir)

        with patch("cafe.ui.commands.resource_commands.prompt_list") as mock_list:
            with patch("cafe.ui.commands.resource_commands.prompt_confirm") as mock_confirm:
                mock_list.side_effect = ["pm", "Roger.md"]
                mock_confirm.return_value = True

                agent_rm()

                assert not (temp_agents_dir / ".cafe" / "agents" / "pm" / "Roger.md").exists()
                mock_console.print.assert_any_call("[green]✓[/green] Agent 'pm/Roger.md' deleted successfully")

    def test_agent_rm_cancelled(self, mock_console, temp_agents_dir, monkeypatch):
        """Test removing an agent when user cancels."""
        monkeypatch.chdir(temp_agents_dir)

        with patch("cafe.ui.commands.resource_commands.prompt_list") as mock_list:
            with patch("cafe.ui.commands.resource_commands.prompt_confirm") as mock_confirm:
                mock_list.side_effect = ["pm", "Roger.md"]
                mock_confirm.return_value = False

                with pytest.raises(Exit):
                    agent_rm()

                mock_console.print.assert_called_with("[dim]Cancelled[/dim]")

    def test_agent_rm_keyboard_interrupt(self, mock_console, temp_agents_dir, monkeypatch):
        """Test removing an agent with keyboard interrupt."""
        monkeypatch.chdir(temp_agents_dir)

        with patch("cafe.ui.commands.resource_commands.prompt_list") as mock_list:
            mock_list.side_effect = KeyboardInterrupt()

            with pytest.raises(Exit):
                agent_rm()

            mock_console.print.assert_called_with("\n[dim]Cancelled[/dim]")


class TestAgentCreate:
    """Test agent create command."""

    def test_agent_create_success(self, mock_console, temp_agents_dir, monkeypatch):
        """Test creating a new agent."""
        monkeypatch.chdir(temp_agents_dir)

        with patch("cafe.ui.commands.resource_commands.prompt_list") as mock_list:
            with patch("cafe.ui.commands.resource_commands.prompt_text") as mock_text:
                with patch("cafe.ui.commands.resource_commands.subprocess.run") as mock_run:
                    with patch("cafe.ui.commands.resource_commands.tempfile.NamedTemporaryFile") as mock_temp:
                        mock_list.return_value = "pm"
                        mock_text.side_effect = ["TestAgent", "Test description"]

                        # Mock temp file
                        temp_file = MagicMock()
                        temp_file.name = "/tmp/test.md"
                        temp_file.__enter__ = MagicMock(return_value=temp_file)
                        temp_file.__exit__ = MagicMock(return_value=False)
                        mock_temp.return_value = temp_file

                        # Mock reading the edited content
                        with patch("builtins.open", mock_open(read_data="---\nname: TestAgent\ndescription: Test description\n---\nContent")):
                            with patch("os.unlink"):
                                agent_create()

                        agent_file = temp_agents_dir / ".cafe" / "agents" / "pm" / "TestAgent.md"
                        assert agent_file.exists()

    def test_agent_create_empty_name(self, mock_console, temp_agents_dir, monkeypatch):
        """Test creating agent with empty name."""
        monkeypatch.chdir(temp_agents_dir)

        with patch("cafe.ui.commands.resource_commands.prompt_list") as mock_list:
            with patch("cafe.ui.commands.resource_commands.prompt_text") as mock_text:
                mock_list.return_value = "pm"
                mock_text.return_value = "   "  # Whitespace only

                with pytest.raises(Exit):
                    agent_create()

                mock_console.print.assert_called_with("[red]Error: Agent name cannot be empty[/red]")


class TestAgentEdit:
    """Test agent edit command."""

    def test_agent_edit_success(self, mock_console, temp_agents_dir, monkeypatch):
        """Test editing an agent."""
        monkeypatch.chdir(temp_agents_dir)

        with patch("cafe.ui.commands.resource_commands.prompt_list") as mock_list:
            with patch("cafe.ui.commands.resource_commands.subprocess.run") as mock_run:
                mock_list.side_effect = ["pm", "Roger.md"]

                agent_edit()

                mock_run.assert_called_once()
                mock_console.print.assert_any_call("[green]✓[/green] Agent updated successfully: .cafe/agents/pm/Roger.md")

    def test_agent_edit_no_agents_in_role(self, mock_console, temp_agents_dir, monkeypatch):
        """Test editing agent when no agents exist in role."""
        monkeypatch.chdir(temp_agents_dir)

        # Remove all agents from reviewer role
        with patch("cafe.ui.commands.resource_commands.prompt_list") as mock_list:
            mock_list.return_value = "reviewer"

            with pytest.raises(Exit):
                agent_edit()

            mock_console.print.assert_called_with("[red]No agents found in role 'reviewer'[/red]")

    def test_agent_edit_keyboard_interrupt(self, mock_console, temp_agents_dir, monkeypatch):
        """Test editing agent with keyboard interrupt."""
        monkeypatch.chdir(temp_agents_dir)

        with patch("cafe.ui.commands.resource_commands.prompt_list") as mock_list:
            mock_list.side_effect = KeyboardInterrupt()

            with pytest.raises(Exit):
                agent_edit()

            mock_console.print.assert_called_with("\n[dim]Cancelled[/dim]")
