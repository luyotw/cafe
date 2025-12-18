"""Tests for GitHub utilities (issue fetching)."""

import pytest
import subprocess
from unittest.mock import Mock, patch
from pathlib import Path

from cafe.utils.github import GitHubOps, GitHubError
from cafe.utils.git_utils import get_github_repo_name


class TestGetGitHubRepoName:
    """Test get_github_repo_name from .git/config."""

    def test_get_repo_name_from_https_remote(self, tmp_path: Path) -> None:
        """測試從 HTTPS remote URL 提取 repo name"""
        # Create .git/config
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        config_file = git_dir / "config"
        config_file.write_text("""[core]
        repositoryformatversion = 0
[remote "origin"]
        url = https://github.com/owner/repo.git
        fetch = +refs/heads/*:refs/remotes/origin/*
""")

        repo_name = get_github_repo_name(cwd=tmp_path)

        assert repo_name == "owner/repo"

    def test_get_repo_name_from_ssh_remote(self, tmp_path: Path) -> None:
        """測試從 SSH remote URL 提取 repo name"""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        config_file = git_dir / "config"
        config_file.write_text("""[core]
        repositoryformatversion = 0
[remote "origin"]
        url = git@github.com:owner/repo.git
        fetch = +refs/heads/*:refs/remotes/origin/*
""")

        repo_name = get_github_repo_name(cwd=tmp_path)

        assert repo_name == "owner/repo"

    def test_get_repo_name_no_git_config(self, tmp_path: Path) -> None:
        """測試沒有 .git/config 檔案時拋出錯誤"""
        with pytest.raises(FileNotFoundError, match=".git/config not found"):
            get_github_repo_name(cwd=tmp_path)

    def test_get_repo_name_no_remote_origin(self, tmp_path: Path) -> None:
        """測試 .git/config 沒有 remote origin 時拋出錯誤"""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        config_file = git_dir / "config"
        config_file.write_text("""[core]
        repositoryformatversion = 0
""")

        with pytest.raises(ValueError, match="No remote 'origin' found"):
            get_github_repo_name(cwd=tmp_path)

    def test_get_repo_name_invalid_url(self, tmp_path: Path) -> None:
        """測試無效 GitHub URL 格式時拋出錯誤"""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        config_file = git_dir / "config"
        config_file.write_text("""[core]
        repositoryformatversion = 0
[remote "origin"]
        url = https://invalid.com/repo
""")

        with pytest.raises(ValueError, match="Invalid GitHub URL"):
            get_github_repo_name(cwd=tmp_path)


class TestPostIssueComment:
    """Test posting comment to GitHub issue."""

    @patch("subprocess.run")
    def test_post_comment_success(self, mock_run: Mock) -> None:
        """測試成功貼回 comment 到 GitHub issue"""
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

        gh_ops = GitHubOps()
        # Should not raise
        gh_ops.add_issue_comment("123", "Spec completed:\n\n# Requirement")

        # Verify gh CLI was called correctly
        call_args = mock_run.call_args[0][0]
        assert "gh" in call_args
        assert "issue" in call_args
        assert "comment" in call_args
        assert "123" in call_args
        assert "--body" in call_args

    @patch("subprocess.run")
    def test_post_comment_issue_not_found(self, mock_run: Mock) -> None:
        """測試 issue 不存在時拋出錯誤"""
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1,
            cmd=["gh", "issue", "comment", "999"],
            stderr="issue not found",
        )

        gh_ops = GitHubOps()

        with pytest.raises(GitHubError, match="Failed to add issue comment"):
            gh_ops.add_issue_comment("999", "Test comment")

    @patch("subprocess.run")
    def test_post_comment_network_error(self, mock_run: Mock) -> None:
        """測試網路錯誤時拋出錯誤"""
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1,
            cmd=["gh", "issue", "comment", "123"],
            stderr="network error",
        )

        gh_ops = GitHubOps()

        with pytest.raises(GitHubError, match="Failed to add issue comment"):
            gh_ops.add_issue_comment("123", "Test")
