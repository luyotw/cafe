"""Git operations for CAFE."""

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
            Current branch name (empty string if detached HEAD)
        """
        return self.run_git("branch", "--show-current")

    def is_valid_branch(self) -> bool:
        """Check if currently on a valid branch (not detached HEAD).

        Returns:
            True if on a valid branch, False if detached HEAD
        """
        current_branch = self.get_current_branch()
        return bool(current_branch)

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

    def push(self, branch_name: str, set_upstream: bool = True, force: bool = False) -> None:
        """Push branch to remote.

        Args:
            branch_name: Branch to push
            set_upstream: Set upstream tracking
            force: Force push (use with caution)
        """
        args = ["push"]
        if force:
            args.append("--force")
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

    def delete_branch(self, branch_name: str) -> None:
        """Delete a local branch.

        Args:
            branch_name: Name of branch to delete

        Raises:
            GitError: If branch deletion fails
        """
        self.run_git("branch", "-d", branch_name)

    def pull(self) -> None:
        """Pull latest changes from remote.

        Raises:
            GitError: If pull fails
        """
        self.run_git("pull")

    def merge(self, branch_name: str) -> None:
        """Merge a branch into current branch.

        Args:
            branch_name: Name of the branch to merge

        Raises:
            GitError: If merge fails
        """
        self.run_git("merge", branch_name)

    def get_main_branch(self) -> str:
        """Get the main branch name (main or master).

        Returns:
            Main branch name
        """
        try:
            # Try to get default branch from remote
            output = self.run_git("symbolic-ref", "refs/remotes/origin/HEAD", "--short")
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

    def get_commits_since(self, timestamp: str) -> List[dict]:
        """Get commits since a given timestamp.

        Args:
            timestamp: ISO format timestamp (e.g., "2025-01-02T10:00:00+00:00")

        Returns:
            List of commit dictionaries with 'hash' and 'message' keys
        """
        try:
            # Use git log with --since parameter
            output = self.run_git("log", "--since", timestamp, "--format=%H %s")
            if not output:
                return []

            commits = []
            for line in output.split("\n"):
                if line.strip():
                    parts = line.split(" ", 1)
                    commits.append(
                        {"hash": parts[0], "message": parts[1] if len(parts) > 1 else ""}
                    )
            return commits
        except GitError:
            return []

    def create_worktree(self, path: str, branch_name: str, base_branch: str) -> None:
        """Create Git worktree.

        Args:
            path: Worktree path
            branch_name: New branch name
            base_branch: Base branch name

        Raises:
            GitError: If creation fails
        """
        # Ensure parent directory exists
        worktree_path = Path(path)
        worktree_path.parent.mkdir(parents=True, exist_ok=True)

        # Create worktree and create new branch
        self.run_git("worktree", "add", "-b", branch_name, path, base_branch)

    def remove_worktree(self, path: str) -> None:
        """Remove Git worktree.

        Args:
            path: Worktree path

        Raises:
            GitError: If removal fails
        """
        self.run_git("worktree", "remove", path)

    def worktree_exists(self, path: str) -> bool:
        """Check if worktree exists.

        Args:
            path: Worktree path

        Returns:
            True if worktree exists
        """
        worktrees = self.list_worktrees()
        abs_path = str(Path(path).resolve())
        return any(wt["path"] == abs_path for wt in worktrees)

    def list_worktrees(self) -> List[dict]:
        """List all worktrees.

        Returns:
            Worktree list, each worktree contains path and branch
        """
        try:
            # git worktree list --porcelain format:
            # worktree /path/to/worktree
            # HEAD <commit-hash>
            # branch refs/heads/branch-name
            # (empty line)
            output = self.run_git("worktree", "list", "--porcelain")
            if not output:
                return []

            worktrees = []
            current_worktree = {}

            for line in output.split("\n"):
                line = line.strip()
                if not line:
                    # Empty line indicates worktree end
                    if current_worktree:
                        worktrees.append(current_worktree)
                        current_worktree = {}
                elif line.startswith("worktree "):
                    current_worktree["path"] = line[9:]  # Remove "worktree " prefix
                elif line.startswith("branch "):
                    # refs/heads/branch-name -> branch-name
                    branch_ref = line[7:]  # Remove "branch " prefix
                    current_worktree["branch"] = branch_ref.replace("refs/heads/", "")
                elif line.startswith("detached"):
                    current_worktree["branch"] = "HEAD"

            # Add the last worktree
            if current_worktree:
                worktrees.append(current_worktree)

            return worktrees
        except GitError:
            return []

    def has_upstream_branch(self) -> bool:
        """Check if current branch has upstream tracking.

        Returns:
            True if branch has upstream tracking, False otherwise
        """
        try:
            self.run_git("rev-parse", "--abbrev-ref", "@{u}")
            return True
        except (GitError, Exception):
            return False

    def has_unpushed_commits(self) -> bool:
        """Check if there are commits not pushed to upstream.

        Returns:
            True if there are unpushed commits, False otherwise
        """
        if not self.has_upstream_branch():
            return False
        try:
            output = self.run_git("log", "@{u}..HEAD", "--oneline")
            return bool(output.strip())
        except GitError:
            return False

    def get_unpushed_commits(self) -> List[dict]:
        """Get list of unpushed commits with timestamps.

        Returns:
            List of commit dictionaries with 'hash', 'timestamp', and 'message' keys.
            Timestamp is in ISO 8601 format.
        """
        if not self.has_upstream_branch():
            return []
        try:
            # Format: <hash>|<iso-timestamp>|<subject>
            output = self.run_git("log", "@{u}..HEAD", "--format=%H|%aI|%s")
            if not output:
                return []

            commits = []
            for line in output.split("\n"):
                if line.strip():
                    parts = line.split("|", 2)
                    if len(parts) == 3:
                        commits.append({
                            "hash": parts[0],
                            "timestamp": parts[1],  # ISO 8601 format
                            "message": parts[2],
                        })
            return commits
        except GitError:
            return []

    def get_latest_unpushed_commit_timestamp(self) -> Optional[str]:
        """Get timestamp of most recent unpushed commit.

        Returns:
            ISO 8601 timestamp string of latest unpushed commit, or None if no unpushed commits
        """
        unpushed_commits = self.get_unpushed_commits()
        if not unpushed_commits:
            return None
        return unpushed_commits[0]["timestamp"]
