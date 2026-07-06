"""Tests for cafe close --squash (local review squash merge)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from cafe.ui.cli import app
from cafe.ui.commands.lifecycle import _resolve_squash_message

runner = CliRunner()


@pytest.fixture
def temp_repo_dir(tmp_path, monkeypatch):
    """Create a temporary repository directory."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".cafe").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def mock_git_ops():
    """Mock GitOperations with squash-related defaults."""
    with patch("cafe.ui.cli.GitOperations") as MockGitOps:
        mock = MagicMock()
        MockGitOps.return_value = mock
        mock.get_current_branch.return_value = "test-issue"
        mock.checkout_branch.return_value = None
        mock.pull.return_value = None
        mock.merge.return_value = None
        mock.merge_squash.return_value = None
        mock.commit.return_value = None
        mock.delete_branch.return_value = None
        # By default squash staged something to commit.
        mock.has_staged_changes.return_value = True
        yield mock


@pytest.fixture
def mock_github_ops_no_pr():
    """Mock GitHubOps to return no PR."""
    with patch("cafe.ui.cli.GitHubOps") as MockGitHubOps:
        mock = MagicMock()
        MockGitHubOps.return_value = mock
        mock.get_pr_for_branch.return_value = None
        yield mock


@pytest.fixture(autouse=True)
def cleanup_archive(temp_repo_dir):
    yield
    import shutil

    project_path = str(temp_repo_dir.resolve()).lstrip("/").replace("/", "-")
    archive_base = Path.home() / ".cafe" / "projects" / project_path
    if archive_base.exists():
        shutil.rmtree(archive_base)


def _write_issue(temp_repo_dir, *, auto_create=False, name="test-issue", worktree_path=None):
    issue_dir = temp_repo_dir / ".cafe" / "issues" / name
    issue_dir.mkdir(parents=True)
    config = {
        "base_branch": "main",
        "feature_branch": name,
        "pr": {"auto_create": auto_create},
    }
    if worktree_path is not None:
        config["worktree_path"] = str(worktree_path)
    with open(issue_dir / "issue.yaml", "w", encoding="utf-8") as f:
        yaml.dump(config, f)
    return issue_dir


# --------------------------------------------------------------------------- #
# _resolve_squash_message helper
# --------------------------------------------------------------------------- #


class TestResolveSquashMessage:
    """Unit tests for the squash commit message resolver."""

    def test_override_wins(self, tmp_path):
        """有 --message 覆寫時直接使用，忽略 PR title 與 issue name"""
        issue_dir = tmp_path / "issue"
        pr_iter = issue_dir / "pr" / "iteration_001"
        pr_iter.mkdir(parents=True)
        (pr_iter / "output.md").write_text("# PR Title\n\nbody", encoding="utf-8")

        result = _resolve_squash_message(issue_dir, "my-issue", "Custom message")
        assert result == "Custom message"

    def test_pr_title_used_when_no_override(self, tmp_path):
        """無覆寫時取 PR artifact 的 H1 標題"""
        issue_dir = tmp_path / "issue"
        pr_iter = issue_dir / "pr" / "iteration_001"
        pr_iter.mkdir(parents=True)
        (pr_iter / "output.md").write_text(
            "# Add awesome feature\n\n## Summary\nstuff", encoding="utf-8"
        )

        result = _resolve_squash_message(issue_dir, "my-issue", None)
        assert result == "Add awesome feature"

    def test_fallback_to_issue_name(self, tmp_path):
        """找不到 PR artifact 時 fallback 到 issue_name"""
        issue_dir = tmp_path / "issue"
        issue_dir.mkdir(parents=True)

        result = _resolve_squash_message(issue_dir, "my-issue", None)
        assert result == "my-issue"

    def test_fallback_when_no_h1_in_output(self, tmp_path):
        """output.md 沒有 H1 時 fallback 到 issue_name"""
        issue_dir = tmp_path / "issue"
        pr_iter = issue_dir / "pr" / "iteration_001"
        pr_iter.mkdir(parents=True)
        (pr_iter / "output.md").write_text("no heading here\njust text", encoding="utf-8")

        result = _resolve_squash_message(issue_dir, "my-issue", None)
        assert result == "my-issue"

    def test_picks_highest_iteration(self, tmp_path):
        """多個 iteration 時取編號最大的那個的標題"""
        issue_dir = tmp_path / "issue"
        for n, title in [(1, "First title"), (2, "Second title"), (10, "Tenth title")]:
            pr_iter = issue_dir / "pr" / f"iteration_{n:03d}"
            pr_iter.mkdir(parents=True)
            (pr_iter / "output.md").write_text(f"# {title}\n", encoding="utf-8")

        result = _resolve_squash_message(issue_dir, "my-issue", None)
        assert result == "Tenth title"

    def test_empty_override_falls_through(self, tmp_path):
        """空白 override 視同未提供，改用 issue_name"""
        issue_dir = tmp_path / "issue"
        issue_dir.mkdir(parents=True)

        result = _resolve_squash_message(issue_dir, "my-issue", "   ")
        assert result == "my-issue"


# --------------------------------------------------------------------------- #
# close --squash integration (mocked git)
# --------------------------------------------------------------------------- #


class TestCloseSquash:
    """Integration tests for `cafe close --squash`."""

    def test_squash_merge_commits_and_force_deletes(
        self, temp_repo_dir, mock_git_ops, mock_github_ops_no_pr
    ):
        """squash 模式: merge_squash + commit(PR title) + force delete"""
        issue_dir = _write_issue(temp_repo_dir, auto_create=False)
        pr_iter = issue_dir / "pr" / "iteration_002"
        pr_iter.mkdir(parents=True)
        (pr_iter / "output.md").write_text("# Squash this work\n\nbody", encoding="utf-8")

        result = runner.invoke(app, ["close", "--squash"])

        assert result.exit_code == 0
        mock_git_ops.merge_squash.assert_called_once_with("test-issue")
        mock_git_ops.merge.assert_not_called()
        mock_git_ops.commit.assert_called_once_with("Squash this work")
        # Squash-merged branch must be force-deleted.
        mock_git_ops.delete_branch.assert_called_once_with("test-issue", force=True)

    def test_squash_uses_message_override(
        self, temp_repo_dir, mock_git_ops, mock_github_ops_no_pr
    ):
        """squash 模式: -m 覆寫 commit 訊息"""
        _write_issue(temp_repo_dir, auto_create=False)

        result = runner.invoke(app, ["close", "--squash", "-m", "My custom commit"])

        assert result.exit_code == 0
        mock_git_ops.commit.assert_called_once_with("My custom commit")

    def test_squash_empty_diff_skips_commit(
        self, temp_repo_dir, mock_git_ops, mock_github_ops_no_pr
    ):
        """空 diff guard: merge --squash 沒 stage 任何東西時跳過 commit"""
        _write_issue(temp_repo_dir, auto_create=False)
        # Nothing staged after squash merge.
        mock_git_ops.has_staged_changes.return_value = False

        result = runner.invoke(app, ["close", "--squash"])

        assert result.exit_code == 0
        mock_git_ops.merge_squash.assert_called_once_with("test-issue")
        mock_git_ops.commit.assert_not_called()
        assert "no merge needed" in result.stdout.lower()

    def test_squash_overrides_pr_mode(
        self, temp_repo_dir, mock_git_ops, mock_github_ops_no_pr
    ):
        """pr.auto_create: true 但明確 --squash → 仍在本地 squash-merge（優先於 PR 模式），不 pull。"""
        _write_issue(temp_repo_dir, auto_create=True)

        result = runner.invoke(app, ["close", "--squash"])

        assert result.exit_code == 0
        assert "--squash is ignored" not in result.stdout
        mock_git_ops.merge_squash.assert_called_once_with("test-issue")
        mock_git_ops.pull.assert_not_called()
        # squash 後分支非 fast-forward，需 force delete。
        mock_git_ops.delete_branch.assert_called_once_with("test-issue", force=True)

    def test_non_squash_local_review_unchanged(
        self, temp_repo_dir, mock_git_ops, mock_github_ops_no_pr
    ):
        """非 squash 本地審查模式維持普通 merge 與 -d 刪除"""
        _write_issue(temp_repo_dir, auto_create=False)

        result = runner.invoke(app, ["close"])

        assert result.exit_code == 0
        mock_git_ops.merge.assert_called_once_with("test-issue")
        mock_git_ops.merge_squash.assert_not_called()
        mock_git_ops.delete_branch.assert_called_once_with("test-issue")

    def test_squash_worktree_mode_reads_worktree_pr_title(
        self, temp_repo_dir, mock_git_ops, mock_github_ops_no_pr
    ):
        """worktree 模式 squash: 從 worktree 內 issue 目錄讀 PR title"""
        (temp_repo_dir / ".git").mkdir()
        worktree_path = temp_repo_dir / "worktrees" / "wt-issue"
        worktree_issue_dir = worktree_path / ".cafe" / "issues" / "wt-issue"
        worktree_issue_dir.mkdir(parents=True)
        # PR title only exists inside the worktree at merge time.
        pr_iter = worktree_issue_dir / "pr" / "iteration_001"
        pr_iter.mkdir(parents=True)
        (pr_iter / "output.md").write_text("# Worktree squash title\n", encoding="utf-8")

        # Repo-root issue config (read first to learn worktree_path).
        _write_issue(temp_repo_dir, auto_create=False, name="wt-issue", worktree_path=worktree_path)

        mock_git_ops.get_current_branch.return_value = "wt-issue"

        result = runner.invoke(app, ["close", "--squash"])

        assert result.exit_code == 0
        mock_git_ops.merge_squash.assert_called_once_with("wt-issue")
        mock_git_ops.commit.assert_called_once_with("Worktree squash title")
        mock_git_ops.delete_branch.assert_called_once_with("wt-issue", force=True)
