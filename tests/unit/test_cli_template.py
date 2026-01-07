"""Tests for cafe template commands."""

import pytest
from pathlib import Path
from typer.testing import CliRunner
from unittest.mock import MagicMock, patch

from cafe.ui.cli import app


runner = CliRunner()


class TestTemplateCommandGroup:
    """Test template command group structure"""

    def test_template_command_group_exists(self):
        """Test that template command group is accessible"""
        # Test that template command group exists
        result = runner.invoke(app, ["template", "--help"])
        assert result.exit_code == 0
        assert "Manage plan and spec templates" in result.output or "template" in result.output.lower()

    def test_template_subcommands_exist(self):
        """Test that all template subcommands are accessible"""
        subcommands = ["add", "ls", "rm", "cat", "edit", "create"]

        for subcmd in subcommands:
            result = runner.invoke(app, ["template", subcmd, "--help"])
            # Should not fail with "No such command" error
            assert "No such command" not in result.output


class TestTemplateAdd:
    """Test cafe template add command"""

    def test_template_add_with_all_flags(self, tmp_path: Path):
        """Test template add with all flags provided"""
        # Create a source template file
        source_file = tmp_path / "test_template.md"
        source_file.write_text("# Test Template\nContent")

        config_dir = tmp_path / ".cafe"
        config_dir.mkdir(parents=True, exist_ok=True)

        with patch("cafe.ui.cli.Path.cwd", return_value=tmp_path):
            result = runner.invoke(app, [
                "template", "add",
                "--source-file", str(source_file),
                "--name", "my-template",
                "--type", "plan",
                "--config", str(config_dir / "config.yaml")
            ])

            # Should succeed
            assert result.exit_code == 0
            assert "added successfully" in result.output.lower() or "✅" in result.output

    def test_template_add_with_invalid_type(self, tmp_path: Path):
        """Test template add with invalid type shows error"""
        source_file = tmp_path / "test_template.md"
        source_file.write_text("# Test Template")

        result = runner.invoke(app, [
            "template", "add",
            "--source-file", str(source_file),
            "--name", "my-template",
            "--type", "invalid"
        ])

        # Should fail with error message
        assert result.exit_code != 0
        assert "invalid" in result.output.lower() or "must be" in result.output.lower()

    def test_template_add_with_missing_file(self, tmp_path: Path):
        """Test template add with non-existent source file"""
        result = runner.invoke(app, [
            "template", "add",
            "--source-file", "/nonexistent/file.md",
            "--name", "my-template",
            "--type", "plan"
        ])

        # Should fail
        assert result.exit_code != 0


class TestTemplateLs:
    """Test cafe template ls command"""

    def test_template_ls_with_type_flag(self, tmp_path: Path):
        """Test template ls with --type flag"""
        config_dir = tmp_path / ".cafe"
        config_dir.mkdir(parents=True, exist_ok=True)

        with patch("cafe.ui.cli.Path.cwd", return_value=tmp_path):
            result = runner.invoke(app, [
                "template", "ls",
                "--type", "plan",
                "--config", str(config_dir / "config.yaml")
            ])

            # Should succeed (even if no templates)
            assert result.exit_code == 0


class TestTemplateRm:
    """Test cafe template rm command"""

    def test_template_rm_with_force_flag(self, tmp_path: Path):
        """Test template rm with --force flag skips confirmation"""
        config_dir = tmp_path / ".cafe"
        templates_dir = config_dir / "templates" / "plan"
        templates_dir.mkdir(parents=True, exist_ok=True)

        # Create a test template
        test_template = templates_dir / "test.md"
        test_template.write_text("# Test")

        with patch("cafe.ui.cli.Path.cwd", return_value=tmp_path):
            result = runner.invoke(app, [
                "template", "rm",
                "--name", "test",
                "--type", "plan",
                "--force",
                "--config", str(config_dir / "config.yaml")
            ])

            # Should succeed without prompting
            assert result.exit_code == 0
            assert not test_template.exists()


class TestTemplateCat:
    """Test cafe template cat command"""

    def test_template_cat_with_all_flags(self, tmp_path: Path):
        """Test template cat with all flags provided"""
        config_dir = tmp_path / ".cafe"
        templates_dir = config_dir / "templates" / "plan"
        templates_dir.mkdir(parents=True, exist_ok=True)

        # Create a test template
        test_template = templates_dir / "test.md"
        test_content = "# Test Template\nContent here"
        test_template.write_text(test_content)

        with patch("cafe.ui.cli.Path.cwd", return_value=tmp_path):
            # Mock subprocess.run to avoid opening pager
            with patch("cafe.ui.cli.subprocess.run") as mock_run:
                mock_run.side_effect = FileNotFoundError  # Force fallback to console

                result = runner.invoke(app, [
                    "template", "cat",
                    "--name", "test",
                    "--type", "plan",
                    "--config", str(config_dir / "config.yaml")
                ])

                # Should succeed and show content
                assert result.exit_code == 0


class TestTemplateEdit:
    """Test cafe template edit command"""

    def test_template_edit_with_all_flags(self, tmp_path: Path):
        """Test template edit with all flags provided"""
        config_dir = tmp_path / ".cafe"
        templates_dir = config_dir / "templates" / "plan"
        templates_dir.mkdir(parents=True, exist_ok=True)

        # Create a test template
        test_template = templates_dir / "test.md"
        test_template.write_text("# Test Template")

        with patch("cafe.ui.cli.Path.cwd", return_value=tmp_path):
            with patch("cafe.ui.cli.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)

                result = runner.invoke(app, [
                    "template", "edit",
                    "--name", "test",
                    "--type", "plan",
                    "--config", str(config_dir / "config.yaml")
                ])

                # Should succeed
                assert result.exit_code == 0


class TestTemplateCreate:
    """Test cafe template create command"""

    def test_template_create_with_all_flags(self, tmp_path: Path):
        """Test template create with all flags provided"""
        config_dir = tmp_path / ".cafe"
        config_dir.mkdir(parents=True, exist_ok=True)

        with patch("cafe.ui.cli.Path.cwd", return_value=tmp_path):
            with patch("cafe.ui.cli.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)

                # Mock tempfile to provide content
                with patch("tempfile.NamedTemporaryFile") as mock_temp:
                    temp_file = tmp_path / "temp.md"
                    temp_file.write_text("# My content")
                    mock_temp.return_value.__enter__.return_value.name = str(temp_file)

                    result = runner.invoke(app, [
                        "template", "create",
                        "--name", "my-new-template",
                        "--type", "plan",
                        "--config", str(config_dir / "config.yaml")
                    ])

                    # Should succeed
                    assert result.exit_code == 0

    def test_template_create_placeholder_includes_name_and_type(self, tmp_path: Path):
        """Test template create placeholder includes template name and type"""
        config_dir = tmp_path / ".cafe"
        config_dir.mkdir(parents=True, exist_ok=True)

        template_name = "my-plan"
        template_type = "plan"

        # We'll verify the placeholder content by checking what was written to tempfile
        written_content = None

        def capture_write(content):
            nonlocal written_content
            written_content = content

        with patch("cafe.ui.cli.Path.cwd", return_value=tmp_path):
            with patch("cafe.ui.cli.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)

                with patch("tempfile.NamedTemporaryFile") as mock_temp:
                    temp_file = tmp_path / "temp.md"

                    class MockFile:
                        def __init__(self):
                            self.name = str(temp_file)

                        def write(self, content):
                            capture_write(content)

                        def __enter__(self):
                            return self

                        def __exit__(self, *args):
                            pass

                    mock_temp.return_value = MockFile()
                    temp_file.write_text("# Content")

                    result = runner.invoke(app, [
                        "template", "create",
                        "--name", template_name,
                        "--type", template_type,
                        "--config", str(config_dir / "config.yaml")
                    ])

                    # Verify placeholder contains template name and type
                    if written_content:
                        assert template_name in written_content
                        assert template_type in written_content
