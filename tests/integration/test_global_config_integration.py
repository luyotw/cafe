"""Integration tests for global config feature.

Tests the complete workflow of agent and template management using global directory:
- create -> list -> edit -> delete workflow
- cross-project reuse scenarios
- name conflict handling
"""

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from typer.testing import CliRunner

from cafe.ui.cli import app


@pytest.fixture
def runner():
    """Create CLI runner."""
    return CliRunner()


@pytest.fixture
def fake_home(tmp_path):
    """Create a fake home directory for testing."""
    home = tmp_path / "home"
    home.mkdir()
    return home


class TestAgentWorkflow:
    """Test complete agent workflow: create -> list -> edit -> delete."""

    def test_agent_complete_workflow(self, runner, fake_home):
        """Test creating, listing, editing, and deleting an agent."""
        # Mock Path.home() to use fake home directory
        with patch("cafe.utils.config.Path.home", return_value=fake_home):
            # Step 1: Create an agent
            with patch("cafe.ui.cli.prompt_list", return_value="developer"), \
                 patch("cafe.ui.cli.prompt_text", side_effect=["TestAgent", "A test agent"]), \
                 patch("subprocess.run") as mock_run:

                def write_agent_content(cmd, **kwargs):
                    # Simulate editor writing content
                    if len(cmd) >= 2:
                        temp_file = cmd[1]
                        with open(temp_file, "r") as f:
                            content = f.read()
                        with open(temp_file, "w") as f:
                            f.write(content + "\nTest agent content\n")
                    return Mock(returncode=0)

                mock_run.side_effect = write_agent_content
                result = runner.invoke(app, ["role", "create"])

            assert result.exit_code == 0
            assert "created successfully" in result.stdout

            # Verify agent file exists in global directory
            agent_file = fake_home / ".cafe" / "roles" / "developer" / "TestAgent.md"
            assert agent_file.exists()

            # Step 2: List agents - verify the created agent appears
            result = runner.invoke(app, ["role", "ls"])
            assert result.exit_code == 0
            assert "TestAgent" in result.stdout

            # Step 3: Edit the agent
            with patch("cafe.ui.cli.prompt_list", side_effect=["developer", "TestAgent.md"]), \
                 patch("subprocess.run") as mock_run:
                mock_run.return_value = Mock(returncode=0)
                result = runner.invoke(app, ["role", "edit"])

            assert result.exit_code == 0
            assert "updated successfully" in result.stdout or "Updated" in result.stdout

            # Step 4: Delete the agent
            with patch("cafe.ui.cli.prompt_list", side_effect=["developer", "TestAgent.md"]), \
                 patch("cafe.ui.cli.prompt_confirm", return_value=True):
                result = runner.invoke(app, ["role", "rm"])

            assert result.exit_code == 0
            assert "deleted successfully" in result.stdout
            assert not agent_file.exists()


class TestTemplateWorkflow:
    """Test complete template workflow: create -> list -> edit -> delete."""

    def test_template_complete_workflow(self, runner, fake_home, tmp_path):
        """Test creating, listing, editing, and deleting a template."""
        # Create a source template file
        source_file = tmp_path / "test_template.md"
        source_file.write_text("# Test Template\nContent here")

        # Mock Path.home() to use fake home directory
        with patch("cafe.utils.config.Path.home", return_value=fake_home):
            # Step 1: Add a template
            result = runner.invoke(app, [
                "template", "add",
                "--source-file", str(source_file),
                "--name", "test-template",
                "--type", "plan",
            ])

            assert result.exit_code == 0
            assert "added successfully" in result.stdout.lower() or "✅" in result.stdout

            # Verify template file exists in global directory
            template_file = fake_home / ".cafe" / "templates" / "plan" / "test-template.md"
            assert template_file.exists()

            # Step 2: List templates - verify the added template appears
            result = runner.invoke(app, ["template", "ls", "--type", "plan"])
            assert result.exit_code == 0
            assert "test-template" in result.stdout

            # Step 3: Edit the template
            with patch("cafe.ui.cli.subprocess.run") as mock_run:
                mock_run.return_value = Mock(returncode=0)
                result = runner.invoke(app, [
                    "template", "edit",
                    "--name", "test-template",
                    "--type", "plan",
                ])

            assert result.exit_code == 0

            # Step 4: Delete the template
            result = runner.invoke(app, [
                "template", "rm",
                "--name", "test-template",
                "--type", "plan",
                "--force",
            ])

            assert result.exit_code == 0
            assert not template_file.exists()


class TestCrossProjectReuse:
    """Test cross-project reuse scenarios."""

    def test_agents_reusable_across_projects(self, runner, fake_home, tmp_path):
        """Test that agents created in one project can be used in another."""
        # Create two project directories
        project1 = tmp_path / "project1"
        project2 = tmp_path / "project2"
        project1.mkdir()
        project2.mkdir()

        # Mock Path.home() to use fake home directory
        with patch("cafe.utils.config.Path.home", return_value=fake_home):
            # Create an agent while in project1
            with patch("cafe.ui.cli.prompt_list", return_value="developer"), \
                 patch("cafe.ui.cli.prompt_text", side_effect=["SharedAgent", "A shared agent"]), \
                 patch("subprocess.run") as mock_run:

                def write_content(cmd, **kwargs):
                    if len(cmd) >= 2:
                        with open(cmd[1], "r") as f:
                            content = f.read()
                        with open(cmd[1], "w") as f:
                            f.write(content + "\nShared content\n")
                    return Mock(returncode=0)

                mock_run.side_effect = write_content
                result = runner.invoke(app, ["role", "create"])

            assert result.exit_code == 0

            # Verify agent is visible from project2 (without changing to that directory)
            # The agent should be in global directory, so it's accessible from anywhere
            agent_file = fake_home / ".cafe" / "roles" / "developer" / "SharedAgent.md"
            assert agent_file.exists()

            # List agents - should show the agent regardless of current directory
            result = runner.invoke(app, ["role", "ls"])
            assert result.exit_code == 0
            assert "SharedAgent" in result.stdout

    def test_templates_reusable_across_projects(self, runner, fake_home, tmp_path):
        """Test that templates created in one project can be used in another."""
        source_file = tmp_path / "shared_template.md"
        source_file.write_text("# Shared Template\nContent")

        with patch("cafe.utils.config.Path.home", return_value=fake_home):
            # Add a template
            result = runner.invoke(app, [
                "template", "add",
                "--source-file", str(source_file),
                "--name", "shared-template",
                "--type", "spec",
            ])

            assert result.exit_code == 0

            # Verify template is in global directory
            template_file = fake_home / ".cafe" / "templates" / "spec" / "shared-template.md"
            assert template_file.exists()

            # List templates - should show the template
            result = runner.invoke(app, ["template", "ls", "--type", "spec"])
            assert result.exit_code == 0
            assert "shared-template" in result.stdout


class TestNameConflictHandling:
    """Test name conflict handling."""

    def test_agent_duplicate_name_error(self, runner, fake_home):
        """Test that creating an agent with duplicate name shows error."""
        with patch("cafe.utils.config.Path.home", return_value=fake_home):
            # Create first agent
            with patch("cafe.ui.cli.prompt_list", return_value="developer"), \
                 patch("cafe.ui.cli.prompt_text", side_effect=["DuplicateAgent", "First agent"]), \
                 patch("subprocess.run") as mock_run:

                def write_content(cmd, **kwargs):
                    if len(cmd) >= 2:
                        with open(cmd[1], "r") as f:
                            content = f.read()
                        with open(cmd[1], "w") as f:
                            f.write(content + "\nFirst content\n")
                    return Mock(returncode=0)

                mock_run.side_effect = write_content
                result = runner.invoke(app, ["role", "create"])

            assert result.exit_code == 0

            # Try to create another agent with the same name
            with patch("cafe.ui.cli.prompt_list", return_value="developer"), \
                 patch("cafe.ui.cli.prompt_text", side_effect=["DuplicateAgent", "Second agent"]):
                result = runner.invoke(app, ["role", "create"])

            assert result.exit_code == 1
            assert "already exists" in result.stdout
            assert "cafe role edit" in result.stdout

    def test_template_duplicate_name_error(self, runner, fake_home, tmp_path):
        """Test that adding a template with duplicate name shows error."""
        source_file1 = tmp_path / "template1.md"
        source_file1.write_text("# Template 1")

        source_file2 = tmp_path / "template2.md"
        source_file2.write_text("# Template 2")

        with patch("cafe.utils.config.Path.home", return_value=fake_home):
            # Add first template
            result = runner.invoke(app, [
                "template", "add",
                "--source-file", str(source_file1),
                "--name", "duplicate-template",
                "--type", "plan",
            ])

            assert result.exit_code == 0

            # Try to add another template with the same name
            result = runner.invoke(app, [
                "template", "add",
                "--source-file", str(source_file2),
                "--name", "duplicate-template",
                "--type", "plan",
            ])

            assert result.exit_code == 1
            assert "already exists" in result.stdout.lower() or "duplicate" in result.stdout.lower()


class TestSystemAndGlobalTemplateCoexistence:
    """Test that system and global templates can coexist."""

    def test_global_template_overrides_system_template(
        self, runner, fake_home, tmp_path, monkeypatch
    ):
        """Test that global template with same name as system template takes precedence."""
        # chdir 到沒有本地 .cafe 的目錄，確保本地路徑不干涉
        monkeypatch.chdir(tmp_path)

        # Create a global template with the same name as a system template (e.g., "default")
        source_file = tmp_path / "custom_default.md"
        custom_content = "# Custom Default Template\nThis overrides system default"
        source_file.write_text(custom_content)

        with patch("cafe.utils.config.Path.home", return_value=fake_home):
            # Add a template with the same name as a system template
            result = runner.invoke(app, [
                "template", "add",
                "--source-file", str(source_file),
                "--name", "default",  # Same name as system template
                "--type", "plan",
            ])

            assert result.exit_code == 0

            # List templates - should show "default" with custom source type
            result = runner.invoke(app, ["template", "ls", "--type", "plan"])
            assert result.exit_code == 0
            assert "default" in result.stdout

            # Verify that the global template file was created
            global_template = fake_home / ".cafe" / "templates" / "plan" / "default.md"
            assert global_template.exists()

            # Verify the content is from the global template, not system
            global_content = global_template.read_text()
            assert "Custom Default Template" in global_content
            assert "This overrides system default" in global_content

            # Use template cat to verify the global version is returned
            with patch("cafe.ui.cli.subprocess.run") as mock_run:
                mock_run.side_effect = FileNotFoundError  # Force fallback to console
                result = runner.invoke(app, [
                    "template", "cat",
                    "--name", "default",
                    "--type", "plan",
                ])

            assert result.exit_code == 0
            # The output should contain the custom content
            assert "Custom Default Template" in result.stdout or custom_content in result.stdout
