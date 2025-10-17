"""Git operations for AAF."""

import subprocess
from pathlib import Path
from typing import List, Optional


class GitError(Exception):
    """Git operation error."""

    pass


class GitOperations:
    """Handles all git operations."""

    def __init__(self, repo_path: str = ".") -> None:
        """Initialize git operations.

        Args:
            repo_path: Path to git repository
        """
        self.repo_path = Path(repo_path)

    def run_git(self, *args: str) -> str:
        """Run a git command.

        Args:
            *args: Git command arguments

        Returns:
            Command output

        Raises:
            GitError: If command fails
        """
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            raise GitError(f"Git command failed: {e.stderr}") from e

    def get_current_branch(self) -> str:
        """Get current branch name.

        Returns:
            Current branch name
        """
        return self.run_git("branch", "--show-current")

    def create_branch(self, branch_name: str) -> None:
        """Create and checkout a new branch.

        Args:
            branch_name: Name of the branch to create
        """
        self.run_git("checkout", "-b", branch_name)

    def checkout_branch(self, branch_name: str) -> None:
        """Checkout an existing branch.

        Args:
            branch_name: Name of the branch to checkout
        """
        self.run_git("checkout", branch_name)

    def branch_exists(self, branch_name: str) -> bool:
        """Check if a branch exists.

        Args:
            branch_name: Name of the branch

        Returns:
            True if branch exists
        """
        try:
            self.run_git("show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}")
            return True
        except GitError:
            return False

    def get_diff(self, base: str = "main", head: str = "HEAD") -> str:
        """Get diff between two refs.

        Args:
            base: Base ref (default: main)
            head: Head ref (default: HEAD)

        Returns:
            Diff output
        """
        return self.run_git("diff", base, head)

    def commit(self, message: str) -> None:
        """Create a commit.

        Args:
            message: Commit message
        """
        self.run_git("commit", "-m", message)

    def push(self, branch_name: str, set_upstream: bool = True) -> None:
        """Push branch to remote.

        Args:
            branch_name: Branch to push
            set_upstream: Set upstream tracking
        """
        args = ["push"]
        if set_upstream:
            args.extend(["-u", "origin"])
        args.append(branch_name)
        self.run_git(*args)

    def get_status(self) -> str:
        """Get git status.

        Returns:
            Status output
        """
        return self.run_git("status", "--porcelain")

    def has_uncommitted_changes(self) -> bool:
        """Check if there are uncommitted changes.

        Returns:
            True if there are uncommitted changes
        """
        return bool(self.get_status())

    def get_main_branch(self) -> str:
        """Get the main branch name (main or master).

        Returns:
            Main branch name
        """
        try:
            # Try to get default branch from remote
            output = self.run_git(
                "symbolic-ref", "refs/remotes/origin/HEAD", "--short"
            )
            # Output format: "origin/main" -> "main"
            return output.split("/")[-1]
        except GitError:
            # Fallback to "main"
            return "main"

    def get_commits_between(self, base: str, head: str) -> str:
        """Get commit list between two refs.

        Args:
            base: Base ref
            head: Head ref

        Returns:
            Commit list (one-line format)
        """
        return self.run_git("log", "--oneline", f"{base}..{head}")
