"""Shared fixtures and helpers for integration tests."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cafe.core.git import GitOperations


@pytest.fixture
def mock_git_ops():
    """Create mock GitOperations for integration tests."""
    git_ops = MagicMock(spec=GitOperations)
    git_ops.get_current_branch.return_value = "test-issue"
    git_ops.branch_exists.return_value = True
    return git_ops


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    """Create a git repository in tmp_path with initial commit and issue branch.

    This fixture initializes a git repo, configures it, creates an initial commit,
    and checks out a branch named after the issue (default: 'test-issue').

    Usage:
        def test_something(git_repo):
            # git_repo is the tmp_path with initialized git
            issue_name = git_repo.name  # 'test-issue'
    """
    monkeypatch.chdir(tmp_path)

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        capture_output=True,
        check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        capture_output=True,
        check=True
    )

    # Create initial commit
    readme = tmp_path / "README.md"
    readme.write_text("# Test Project")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=tmp_path,
        capture_output=True,
        check=True
    )

    # Create and checkout test-issue branch
    subprocess.run(
        ["git", "checkout", "-b", "test-issue"],
        cwd=tmp_path,
        capture_output=True,
        check=True
    )

    return tmp_path


def init_git_repo_for_issue(tmp_path: Path, issue_name: str) -> None:
    """Initialize git repo in tmp_path and checkout issue branch.

    Helper function for e2e tests that need to run CLI commands in a git repo.

    Args:
        tmp_path: Temporary directory path
        issue_name: Issue name to use as branch name
    """
    if (tmp_path / ".git").exists():
        return

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        capture_output=True,
        check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        capture_output=True,
        check=True
    )

    # Create initial commit
    readme = tmp_path / "README.md"
    readme.write_text("# Test Project")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=tmp_path,
        capture_output=True,
        check=True
    )

    # Create and checkout issue branch
    subprocess.run(
        ["git", "checkout", "-b", issue_name],
        cwd=tmp_path,
        capture_output=True,
        check=True
    )
