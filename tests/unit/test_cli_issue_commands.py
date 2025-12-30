"""Tests for CLI issue management commands (ls, rm)."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open
from click.exceptions import Exit
import yaml

from cafe.ui.commands.issue_commands import list_issues, remove_issue


@pytest.fixture
def mock_console():
    """Create a mock console."""
    with patch("cafe.ui.commands.issue_commands.console") as mock:
        yield mock


@pytest.fixture
def temp_issues_dir(tmp_path):
    """Create temporary issues directory with test issues."""
    issues_dir = tmp_path / ".cafe" / "issues"
    issues_dir.mkdir(parents=True)

    # Create test issues
    issue1 = issues_dir / "issue1"
    issue1.mkdir()
    (issue1 / "spec").mkdir()
    (issue1 / "plan").mkdir()
    (issue1 / "issue.yaml").write_text(yaml.dump({"worktree_path": "/path/to/worktree1"}))

    issue2 = issues_dir / "issue2"
    issue2.mkdir()
    (issue2 / "spec").mkdir()
    (issue2 / "issue.yaml").write_text(yaml.dump({"worktree_path": "-"}))

    issue3 = issues_dir / "test-123"
    issue3.mkdir()

    return tmp_path


class TestListIssues:
    """Test list_issues command."""

    def test_list_issues_no_directory(self, mock_console, tmp_path, monkeypatch):
        """Test listing issues when .cafe/issues doesn't exist."""
        monkeypatch.chdir(tmp_path)

        list_issues()

        mock_console.print.assert_any_call("[yellow]No issues directory found[/yellow]")
        mock_console.print.assert_any_call("Run 'cafe prepare' to create your first issue")

    def test_list_issues_empty_directory(self, mock_console, tmp_path, monkeypatch):
        """Test listing issues when .cafe/issues is empty."""
        issues_dir = tmp_path / ".cafe" / "issues"
        issues_dir.mkdir(parents=True)
        monkeypatch.chdir(tmp_path)

        list_issues()

        mock_console.print.assert_any_call("[yellow]No issues found[/yellow]")

    def test_list_issues_shows_table(self, mock_console, temp_issues_dir, monkeypatch):
        """Test that list_issues displays a table of issues."""
        monkeypatch.chdir(temp_issues_dir)

        with patch("cafe.ui.commands.issue_commands.Table") as mock_table:
            mock_table_instance = MagicMock()
            mock_table.return_value = mock_table_instance

            list_issues()

            # Verify table was created
            mock_table.assert_called_once()
            assert mock_table_instance.add_row.called
            mock_console.print.assert_any_call(mock_table_instance)

    def test_list_issues_shows_phases(self, temp_issues_dir, monkeypatch):
        """Test that list_issues shows which phases exist for each issue."""
        monkeypatch.chdir(temp_issues_dir)

        with patch("cafe.ui.commands.issue_commands.Table") as mock_table:
            mock_table_instance = MagicMock()
            mock_table.return_value = mock_table_instance

            list_issues()

            # Should have called add_row for each issue
            assert mock_table_instance.add_row.call_count == 3

    def test_list_issues_shows_worktree_path(self, temp_issues_dir, monkeypatch):
        """Test that list_issues shows worktree path from issue.yaml."""
        monkeypatch.chdir(temp_issues_dir)

        with patch("cafe.ui.commands.issue_commands.Table") as mock_table:
            mock_table_instance = MagicMock()
            mock_table.return_value = mock_table_instance

            list_issues()

            # Verify that add_row was called with worktree paths
            calls = mock_table_instance.add_row.call_args_list
            assert any("/path/to/worktree1" in str(call) for call in calls)


class TestRemoveIssue:
    """Test remove_issue command."""

    def test_remove_issue_not_found(self, mock_console, tmp_path, monkeypatch):
        """Test removing a non-existent issue."""
        monkeypatch.chdir(tmp_path)

        with pytest.raises(Exit):
            remove_issue(issue_names=["nonexistent"], force=False)

        mock_console.print.assert_any_call("[red]Issue(s) not found: nonexistent[/red]")

    def test_remove_issue_with_confirmation(self, mock_console, temp_issues_dir, monkeypatch):
        """Test removing an issue with confirmation."""
        monkeypatch.chdir(temp_issues_dir)

        with patch("cafe.ui.commands.issue_commands.prompt_confirm") as mock_confirm:
            with patch("cafe.ui.commands.issue_commands.shutil.rmtree") as mock_rmtree:
                mock_confirm.return_value = True

                remove_issue(issue_names=["issue1"], force=False)

                mock_confirm.assert_called_once()
                mock_rmtree.assert_called_once()
                mock_console.print.assert_any_call("[green]✓[/green] Issue 'issue1' deleted successfully")

    def test_remove_issue_cancelled(self, mock_console, temp_issues_dir, monkeypatch):
        """Test removing an issue when user cancels."""
        monkeypatch.chdir(temp_issues_dir)

        with patch("cafe.ui.commands.issue_commands.prompt_confirm") as mock_confirm:
            mock_confirm.return_value = False

            with pytest.raises(Exit):
                remove_issue(issue_names=["issue1"], force=False)

            mock_console.print.assert_any_call("[dim]Cancelled[/dim]")

    def test_remove_issue_with_force(self, mock_console, temp_issues_dir, monkeypatch):
        """Test removing an issue with --force flag."""
        monkeypatch.chdir(temp_issues_dir)

        with patch("cafe.ui.commands.issue_commands.shutil.rmtree") as mock_rmtree:
            remove_issue(issue_names=["issue1"], force=True)

            # Should not ask for confirmation
            mock_rmtree.assert_called_once()
            mock_console.print.assert_any_call("[green]✓[/green] Issue 'issue1' deleted successfully")

    def test_remove_issue_multiple(self, mock_console, temp_issues_dir, monkeypatch):
        """Test removing multiple issues."""
        monkeypatch.chdir(temp_issues_dir)

        with patch("cafe.ui.commands.issue_commands.shutil.rmtree") as mock_rmtree:
            remove_issue(issue_names=["issue1", "issue2"], force=True)

            assert mock_rmtree.call_count == 2
            mock_console.print.assert_any_call("\n[green]2/2 issue(s) deleted successfully[/green]")

    def test_remove_issue_with_wildcard(self, mock_console, temp_issues_dir, monkeypatch):
        """Test removing issues with wildcard pattern."""
        monkeypatch.chdir(temp_issues_dir)

        with patch("cafe.ui.commands.issue_commands.shutil.rmtree") as mock_rmtree:
            remove_issue(issue_names=["test-*"], force=True)

            # Should match test-123
            mock_rmtree.assert_called_once()

    def test_remove_issue_wildcard_no_matches(self, mock_console, temp_issues_dir, monkeypatch):
        """Test removing issues with wildcard that doesn't match anything."""
        monkeypatch.chdir(temp_issues_dir)

        with pytest.raises(Exit):
            remove_issue(issue_names=["nomatch-*"], force=False)

        mock_console.print.assert_any_call("[red]No issues matched the given patterns[/red]")

    def test_remove_issue_keyboard_interrupt(self, mock_console, temp_issues_dir, monkeypatch):
        """Test handling KeyboardInterrupt during confirmation."""
        monkeypatch.chdir(temp_issues_dir)

        with patch("cafe.ui.commands.issue_commands.prompt_confirm") as mock_confirm:
            mock_confirm.side_effect = KeyboardInterrupt()

            with pytest.raises(Exit):
                remove_issue(issue_names=["issue1"], force=False)

            mock_console.print.assert_any_call("\n[dim]Cancelled[/dim]")

    def test_remove_issue_deletion_error(self, mock_console, temp_issues_dir, monkeypatch):
        """Test handling errors during deletion."""
        monkeypatch.chdir(temp_issues_dir)

        with patch("cafe.ui.commands.issue_commands.shutil.rmtree") as mock_rmtree:
            mock_rmtree.side_effect = Exception("Permission denied")

            with pytest.raises(Exit):
                remove_issue(issue_names=["issue1"], force=True)

            mock_console.print.assert_any_call("[red]✗[/red] Failed to delete issue 'issue1': Permission denied")
