"""Tests for ls and rm CLI commands."""

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from cafe.ui.cli import app

runner = CliRunner()


@pytest.fixture
def temp_issues_dir(tmp_path, monkeypatch):
    """Create a temporary issues directory."""
    issues_dir = tmp_path / ".cafe" / "issues"
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

    def test_ls_with_worktree_path(self, temp_issues_dir):
        """Test ls displays worktree path when available."""
        import yaml

        # Create test issue with worktree_path
        issue = temp_issues_dir / "test-issue-wt"
        issue.mkdir()
        (issue / "spec").mkdir()

        # Create issue.yaml with worktree_path
        config = {
            "worktree_path": ".cafe/worktrees/test-issue-wt",
            "base_branch": "main",
            "feature_branch": "test-issue-wt",
        }
        config_file = issue / "issue.yaml"
        with open(config_file, "w") as f:
            yaml.dump(config, f)

        result = runner.invoke(app, ["ls"])

        assert result.exit_code == 0
        assert "test-issue-wt" in result.stdout
        assert ".cafe/worktrees" in result.stdout

    def test_ls_without_worktree_path(self, temp_issues_dir):
        """Test ls displays '-' when worktree_path not available."""
        import yaml

        # Create test issue without worktree_path
        issue = temp_issues_dir / "test-issue-no-wt"
        issue.mkdir()
        (issue / "spec").mkdir()

        # Create issue.yaml without worktree_path
        config = {
            "base_branch": "main",
            "feature_branch": "test-issue-no-wt",
        }
        config_file = issue / "issue.yaml"
        with open(config_file, "w") as f:
            yaml.dump(config, f)

        result = runner.invoke(app, ["ls"])

        assert result.exit_code == 0
        assert "test-issue-no-wt" in result.stdout
        # Should display '-' for missing worktree_path
        assert "-" in result.stdout

    def test_ls_mixed_issues(self, temp_issues_dir):
        """Test ls with mixed issues (with and without worktree)."""
        import yaml

        # Create issue with worktree
        issue1 = temp_issues_dir / "issue-with-wt"
        issue1.mkdir()
        (issue1 / "spec").mkdir()
        config1 = {"worktree_path": ".cafe/worktrees/issue-with-wt"}
        with open(issue1 / "issue.yaml", "w") as f:
            yaml.dump(config1, f)

        # Create issue without worktree
        issue2 = temp_issues_dir / "issue-without-wt"
        issue2.mkdir()
        (issue2 / "plan").mkdir()
        config2 = {"base_branch": "main"}
        with open(issue2 / "issue.yaml", "w") as f:
            yaml.dump(config2, f)

        # Create issue without issue.yaml
        issue3 = temp_issues_dir / "issue-no-config"
        issue3.mkdir()
        (issue3 / "develop").mkdir()

        result = runner.invoke(app, ["ls"])

        assert result.exit_code == 0
        assert "issue-with-wt" in result.stdout

    def test_ls_reads_phases_from_worktree_location(self, tmp_path, monkeypatch):
        """測試從 repo root 執行 cafe ls 時，應該從 worktree 目錄讀取 phases 資訊"""
        import yaml

        # Setup: Create repo structure at repo root
        monkeypatch.chdir(tmp_path)

        # Create .cafe/issues at repo root
        repo_issues_dir = tmp_path / ".cafe" / "issues"
        repo_issues_dir.mkdir(parents=True)

        # Create issue27 at repo root with worktree_path config
        issue27_root = repo_issues_dir / "issue27"
        issue27_root.mkdir()

        # Create issue.yaml with worktree_path
        config = {
            "worktree_path": ".cafe/worktrees/issue27",
            "base_branch": "develop",
            "feature_branch": "issue27"
        }
        with open(issue27_root / "issue.yaml", "w") as f:
            yaml.dump(config, f)

        # Create worktree directory with actual issue data
        worktree_dir = tmp_path / ".cafe" / "worktrees" / "issue27"
        worktree_dir.mkdir(parents=True)

        # Create .cafe/issues/issue27 in worktree with phases
        worktree_issue_dir = worktree_dir / ".cafe" / "issues" / "issue27"
        worktree_issue_dir.mkdir(parents=True)

        # Add spec and plan phases in worktree
        (worktree_issue_dir / "spec").mkdir()
        (worktree_issue_dir / "plan").mkdir()

        # Execute: Run cafe ls from repo root
        result = runner.invoke(app, ["ls"])

        # Verify: Should show phases from worktree location
        assert result.exit_code == 0
        assert "issue27" in result.stdout
        # Should show "spec, plan" from worktree, not "empty"
        assert "spec, plan" in result.stdout
        # Should NOT show "empty"
        lines = [line for line in result.stdout.split('\n') if 'issue27' in line]
        assert len(lines) > 0
        for line in lines:
            assert "empty" not in line
        assert ".cafe/worktrees/issue27" in result.stdout

    def test_ls_displays_custom_playbook_step_directories(self, temp_issues_dir):
        """UT-009: issue display derives phases from the configured playbook."""
        issue = temp_issues_dir / "release-issue"
        issue.mkdir()
        (issue / "issue.yaml").write_text("playbook: release-flow\n", encoding="utf-8")
        (issue / "repair").mkdir()
        (issue / "release").mkdir()
        playbook_dir = temp_issues_dir.parent / "playbooks"
        playbook_dir.mkdir()
        (playbook_dir / "release-flow.yaml").write_text(
            """
playbook:
  id: release-flow
steps:
  repair: {skill: cafe-develop, role: developer, on: {await_agent: release}}
  release: {skill: cafe-pr, role: developer, on: {await_agent: _done}}
""".strip(),
            encoding="utf-8",
        )

        result = runner.invoke(app, ["ls"])

        assert result.exit_code == 0
        assert "repair, release" in result.stdout


class TestRmCommand:
    """Test rm command."""

    def test_rm_nonexistent_issue(self, temp_issues_dir):
        """Test rm with non-existent issue."""
        result = runner.invoke(app, ["rm", "nonexistent"])

        assert result.exit_code == 1
        assert "not found" in result.stdout

    @patch("cafe.ui.cli.prompt_confirm")
    def test_rm_with_confirmation(self, mock_prompt_confirm, temp_issues_dir):
        """Test rm with confirmation prompt."""
        # Create test issue
        issue = temp_issues_dir / "test-issue"
        issue.mkdir()

        # Mock user confirming deletion
        mock_prompt_confirm.return_value = True

        result = runner.invoke(app, ["rm", "test-issue"])

        assert result.exit_code == 0
        assert "Backup: none" in result.stdout
        assert "deleted successfully" in result.stdout
        assert not issue.exists()

    @patch("cafe.ui.cli.prompt_confirm")
    def test_rm_cancelled(self, mock_prompt_confirm, temp_issues_dir):
        """Test rm when user cancels."""
        # Create test issue
        issue = temp_issues_dir / "test-issue"
        issue.mkdir()

        # Mock user cancelling deletion
        mock_prompt_confirm.return_value = False

        result = runner.invoke(app, ["rm", "test-issue"])

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
        assert "Backup: none" in result.stdout
        assert "deleted successfully" in result.stdout
        assert not issue.exists()

    @patch("cafe.ui.cli.prompt_confirm")
    def test_rm_multiple_issues(self, mock_prompt_confirm, temp_issues_dir):
        """Test rm with multiple issues."""
        # Create test issues
        issue1 = temp_issues_dir / "test-issue-1"
        issue1.mkdir()
        issue2 = temp_issues_dir / "test-issue-2"
        issue2.mkdir()
        issue3 = temp_issues_dir / "test-issue-3"
        issue3.mkdir()

        # Mock user confirming deletion
        mock_prompt_confirm.return_value = True

        # Delete multiple issues with confirmation
        result = runner.invoke(app, ["rm", "test-issue-1", "test-issue-2", "test-issue-3"])

        assert result.exit_code == 0
        assert "About to delete 3 issue(s)" in result.stdout
        assert "test-issue-1" in result.stdout
        assert "test-issue-2" in result.stdout
        assert "test-issue-3" in result.stdout
        assert "3/3 issue(s) deleted successfully" in result.stdout
        assert not issue1.exists()
        assert not issue2.exists()
        assert not issue3.exists()

    def test_rm_multiple_issues_force(self, temp_issues_dir):
        """Test rm with multiple issues and --force flag."""
        # Create test issues
        issue1 = temp_issues_dir / "test-issue-1"
        issue1.mkdir()
        issue2 = temp_issues_dir / "test-issue-2"
        issue2.mkdir()

        result = runner.invoke(app, ["rm", "-f", "test-issue-1", "test-issue-2"])

        assert result.exit_code == 0
        assert "deleted successfully" in result.stdout
        assert "2/2 issue(s) deleted successfully" in result.stdout
        assert not issue1.exists()
        assert not issue2.exists()

    @patch("cafe.ui.cli.prompt_confirm")
    def test_rm_multiple_issues_some_missing(self, mock_prompt_confirm, temp_issues_dir):
        """Test rm with multiple issues where some don't exist."""
        # Create only one issue
        issue1 = temp_issues_dir / "test-issue-1"
        issue1.mkdir()

        # Mock user confirming deletion
        mock_prompt_confirm.return_value = True

        # Try to delete one existing and two non-existing
        result = runner.invoke(app, ["rm", "test-issue-1", "nonexistent-1", "nonexistent-2"])

        assert result.exit_code == 0  # Still succeeds because at least one was deleted
        assert "not found" in result.stdout
        assert "nonexistent-1" in result.stdout
        assert "nonexistent-2" in result.stdout
        assert "About to delete 1 issue(s)" in result.stdout
        assert not issue1.exists()

    def test_rm_multiple_issues_all_missing(self, temp_issues_dir):
        """Test rm with multiple issues where all don't exist."""
        result = runner.invoke(app, ["rm", "nonexistent-1", "nonexistent-2"])

        assert result.exit_code == 1
        assert "not found" in result.stdout
        assert "nonexistent-1" in result.stdout
        assert "nonexistent-2" in result.stdout

    @patch("cafe.ui.cli.prompt_confirm")
    def test_rm_multiple_issues_cancel(self, mock_prompt_confirm, temp_issues_dir):
        """Test rm with multiple issues when user cancels."""
        # Create test issues
        issue1 = temp_issues_dir / "test-issue-1"
        issue1.mkdir()
        issue2 = temp_issues_dir / "test-issue-2"
        issue2.mkdir()

        # Mock user cancelling deletion
        mock_prompt_confirm.return_value = False

        # Cancel deletion
        result = runner.invoke(app, ["rm", "test-issue-1", "test-issue-2"])

        assert result.exit_code == 0
        assert "Cancelled" in result.stdout
        assert issue1.exists()
        assert issue2.exists()

    def test_rm_worktree_issue_backs_up_and_removes_worktree(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        issue_name = "issue-wt"

        issue_dir = tmp_path / ".cafe" / "issues" / issue_name
        issue_dir.mkdir(parents=True, exist_ok=True)
        worktree_path = tmp_path / ".cafe" / "worktrees" / issue_name
        worktree_issue_dir = worktree_path / ".cafe" / "issues" / issue_name
        worktree_issue_dir.mkdir(parents=True, exist_ok=True)

        (worktree_issue_dir / "spec").mkdir(parents=True, exist_ok=True)
        (worktree_issue_dir / "spec" / "output.md").write_text("spec", encoding="utf-8")
        (issue_dir / "issue.yaml").write_text(
            yaml.safe_dump(
                {
                    "base_branch": "v02",
                    "feature_branch": issue_name,
                    "worktree_path": str(worktree_path),
                }
            ),
            encoding="utf-8",
        )

        result = runner.invoke(app, ["rm", "--force", issue_name])

        assert result.exit_code == 0
        assert "Backup:" in result.stdout
        assert not issue_dir.exists()
        assert not worktree_path.exists()

        project_path = str(tmp_path.resolve()).lstrip("/").replace("/", "-")
        archive_path = Path.home() / ".cafe" / "projects" / project_path / "archived" / issue_name
        assert archive_path.exists()
        assert (archive_path / "spec" / "output.md").read_text(encoding="utf-8") == "spec"

    def test_rm_worktree_issue_skips_backup_when_worktree_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        issue_name = "issue-wt-missing"

        issue_dir = tmp_path / ".cafe" / "issues" / issue_name
        issue_dir.mkdir(parents=True, exist_ok=True)
        worktree_path = tmp_path / ".cafe" / "worktrees" / issue_name
        (issue_dir / "issue.yaml").write_text(
            yaml.safe_dump(
                {
                    "base_branch": "v02",
                    "feature_branch": issue_name,
                    "worktree_path": str(worktree_path),
                }
            ),
            encoding="utf-8",
        )

        result = runner.invoke(app, ["rm", "--force", issue_name])

        assert result.exit_code == 0
        assert "Backup: none" in result.stdout
        assert not issue_dir.exists()
        assert "deleted successfully" in result.stdout
