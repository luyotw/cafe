"""Shared fixtures and helpers for integration tests."""

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Optional
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
def prepared_repo_factory(tmp_path, monkeypatch):
    """Factory fixture that creates isolated git repositories for integration tests.

    This fixture simulates 'cafe prepare' behavior:
    - Creates a clean git repository in a temporary directory
    - Initializes git config (user.name, user.email)
    - Creates initial commit on main branch
    - Creates and checks out the issue branch
    - Creates .cafe/issues/{issue_name}/ directory structure
    - Changes working directory to the repo using monkeypatch.chdir()

    Usage:
        def test_something(prepared_repo_factory):
            repo_path = prepared_repo_factory("my-issue")
            # Now working directory is repo_path
            # .cafe/issues/my-issue/ exists
            # Currently on 'my-issue' branch

    Returns:
        Callable that takes issue_name and returns Path to the repository
    """
    def _create_prepared_repo(issue_name: str) -> Path:
        """Create and prepare a git repository for the given issue.

        Args:
            issue_name: Name of the issue (will be used as branch name)

        Returns:
            Path to the repository root
        """
        # Use tmp_path for this specific test
        repo_path = tmp_path / issue_name
        repo_path.mkdir(parents=True, exist_ok=True)

        # 🔥 關鍵修復：設置 GIT_CEILING_DIRECTORIES 阻止向上搜尋
        monkeypatch.setenv('GIT_CEILING_DIRECTORIES', str(tmp_path.parent))

        # 清理其他 Git 環境變數
        for var in ['GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE']:
            monkeypatch.delenv(var, raising=False)

        # Change to repo directory
        monkeypatch.chdir(repo_path)

        # Initialize git repo using GitOperations
        git = GitOperations(repo_path)
        git.run_git("init")
        git.run_git("config", "user.email", "test@example.com")
        git.run_git("config", "user.name", "Test User")

        # Create initial commit on main branch
        readme = repo_path / "README.md"
        readme.write_text("# Test Project")
        git.run_git("add", ".")
        git.commit("Initial commit")

        # Create and checkout issue branch
        git.run_git("checkout", "-b", issue_name)

        # Create .cafe/issues/{issue_name}/ directory structure
        issue_dir = repo_path / ".cafe" / "issues" / issue_name
        (issue_dir / "spec").mkdir(parents=True, exist_ok=True)
        (issue_dir / "plan").mkdir(parents=True, exist_ok=True)
        (issue_dir / "review").mkdir(parents=True, exist_ok=True)
        (issue_dir / "review" / "history").mkdir(parents=True, exist_ok=True)

        return repo_path

    return _create_prepared_repo


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
    # 🔥 關鍵修復：設置 GIT_CEILING_DIRECTORIES 阻止向上搜尋
    monkeypatch.setenv('GIT_CEILING_DIRECTORIES', str(tmp_path.parent))

    # 清理其他 Git 環境變數
    for var in ['GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE']:
        monkeypatch.delenv(var, raising=False)

    monkeypatch.chdir(tmp_path)

    # Initialize git repo using GitOperations
    git = GitOperations(tmp_path)
    git.run_git("init")
    git.run_git("config", "user.email", "test@example.com")
    git.run_git("config", "user.name", "Test User")

    # Create initial commit
    readme = tmp_path / "README.md"
    readme.write_text("# Test Project")
    git.run_git("add", ".")
    git.commit("Initial commit")

    # Create and checkout test-issue branch
    git.run_git("checkout", "-b", "test-issue")

    return tmp_path


@contextmanager
def _git_env_isolation(tmp_path: Path, monkeypatch: Optional[pytest.MonkeyPatch] = None):
    """Context manager for Git environment variable isolation.

    Args:
        tmp_path: Temporary directory path
        monkeypatch: Optional pytest monkeypatch fixture. If provided, uses it.
                    Otherwise, uses os.environ directly (less safe but works).
    """
    # 保存原始環境變數值
    original_env = {}
    git_vars = ['GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_CEILING_DIRECTORIES']
    
    for var in git_vars:
        original_env[var] = os.environ.get(var)

    try:
        # 設置 GIT_CEILING_DIRECTORIES 阻止向上搜尋
        ceiling_dir = str(tmp_path.parent)
        if monkeypatch:
            monkeypatch.setenv('GIT_CEILING_DIRECTORIES', ceiling_dir)
            # 清理其他 Git 環境變數
            for var in ['GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE']:
                monkeypatch.delenv(var, raising=False)
        else:
            os.environ['GIT_CEILING_DIRECTORIES'] = ceiling_dir
            # 清理其他 Git 環境變數
            for var in ['GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE']:
                os.environ.pop(var, None)
        
        yield
    finally:
        # 恢復原始環境變數
        if monkeypatch:
            for var, value in original_env.items():
                if value is None:
                    monkeypatch.delenv(var, raising=False)
                else:
                    monkeypatch.setenv(var, value)
        else:
            for var, value in original_env.items():
                if value is None:
                    os.environ.pop(var, None)
                else:
                    os.environ[var] = value


def init_git_repo_for_issue(
    tmp_path: Path, 
    issue_name: str, 
    monkeypatch: Optional[pytest.MonkeyPatch] = None
) -> None:
    """Initialize git repo in tmp_path and checkout issue branch.

    Helper function for e2e tests that need to run CLI commands in a git repo.

    Args:
        tmp_path: Temporary directory path
        issue_name: Issue name to use as branch name
        monkeypatch: Optional pytest monkeypatch fixture for environment isolation.
                    If provided, uses it for better isolation. Otherwise uses os.environ.
    """
    with _git_env_isolation(tmp_path, monkeypatch):
        git = GitOperations(tmp_path)

        if (tmp_path / ".git").exists():
            # Git repo already exists, just checkout to issue branch
            try:
                git.run_git("checkout", "-B", issue_name)
            except:
                pass  # Don't fail if branch doesn't exist
            return

        # Initialize git repo using GitOperations
        git.run_git("init")
        git.run_git("config", "user.email", "test@example.com")
        git.run_git("config", "user.name", "Test User")

        # Create initial commit
        readme = tmp_path / "README.md"
        readme.write_text("# Test Project")
        git.run_git("add", ".")
        git.commit("Initial commit")

        # Create and checkout issue branch
        git.run_git("checkout", "-b", issue_name)
