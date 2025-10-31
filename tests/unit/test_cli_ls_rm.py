"""Tests for ls and rm CLI commands."""

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from aaf.ui.cli import app

runner = CliRunner()


@pytest.fixture
def temp_issues_dir(tmp_path, monkeypatch):
    """Create a temporary issues directory."""
    issues_dir = tmp_path / ".aaf" / "issues"
    issues_dir.mkdir(parents=True)

    # Change to temp directory
    monkeypatch.chdir(tmp_path)

    return issues_dir


class TestLsCommand:
    """Test ls command."""

    def test_ls_no_issues_dir(self, tmp_path, monkeypatch):
        """Test ls when no issues directory exists."""
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["ls"])

        assert result.exit_code == 0
        assert "No issues directory found" in result.stdout

    def test_ls_empty_issues(self, temp_issues_dir):
        """Test ls with empty issues directory."""
        result = runner.invoke(app, ["ls"])

        assert result.exit_code == 0
        assert "No issues found" in result.stdout

    def test_ls_with_issues(self, temp_issues_dir):
        """Test ls with multiple issues."""
        # Create test issues
        issue1 = temp_issues_dir / "test-issue-1"
        issue1.mkdir()
        (issue1 / "spec").mkdir()

        issue2 = temp_issues_dir / "test-issue-2"
        issue2.mkdir()
        (issue2 / "spec").mkdir()
        (issue2 / "analysis").mkdir()

        result = runner.invoke(app, ["ls"])

        assert result.exit_code == 0
        assert "test-issue-1" in result.stdout
        assert "test-issue-2" in result.stdout
        assert "spec" in result.stdout
        assert "Total: 2 issue(s)" in result.stdout


class TestRmCommand:
    """Test rm command."""

    def test_rm_nonexistent_issue(self, temp_issues_dir):
        """Test rm with non-existent issue."""
        result = runner.invoke(app, ["rm", "nonexistent"])

        assert result.exit_code == 1
        assert "not found" in result.stdout

    def test_rm_with_confirmation(self, temp_issues_dir):
        """Test rm with confirmation prompt."""
        # Create test issue
        issue = temp_issues_dir / "test-issue"
        issue.mkdir()

        # Confirm deletion
        result = runner.invoke(app, ["rm", "test-issue"], input="y\n")

        assert result.exit_code == 0
        assert "deleted successfully" in result.stdout
        assert not issue.exists()

    def test_rm_cancelled(self, temp_issues_dir):
        """Test rm when user cancels."""
        # Create test issue
        issue = temp_issues_dir / "test-issue"
        issue.mkdir()

        # Cancel deletion
        result = runner.invoke(app, ["rm", "test-issue"], input="n\n")

        assert result.exit_code == 0
        assert "Cancelled" in result.stdout
        assert issue.exists()

    def test_rm_force(self, temp_issues_dir):
        """Test rm with --force flag."""
        # Create test issue
        issue = temp_issues_dir / "test-issue"
        issue.mkdir()

        result = runner.invoke(app, ["rm", "--force", "test-issue"])

        assert result.exit_code == 0
        assert "deleted successfully" in result.stdout
        assert not issue.exists()
