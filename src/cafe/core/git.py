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
        """建立 Git worktree.

        Args:
            path: Worktree 路徑
            branch_name: 新分支名稱
            base_branch: 基礎分支名稱

        Raises:
            GitError: 如果建立失敗
        """
        # 確保父目錄存在
        worktree_path = Path(path)
        worktree_path.parent.mkdir(parents=True, exist_ok=True)

        # 建立 worktree 並建立新分支
        self.run_git("worktree", "add", "-b", branch_name, path, base_branch)

    def remove_worktree(self, path: str) -> None:
        """移除 Git worktree.

        Args:
            path: Worktree 路徑

        Raises:
            GitError: 如果移除失敗
        """
        self.run_git("worktree", "remove", path)

    def worktree_exists(self, path: str) -> bool:
        """檢查 worktree 是否存在.

        Args:
            path: Worktree 路徑

        Returns:
            True 如果 worktree 存在
        """
        worktrees = self.list_worktrees()
        abs_path = str(Path(path).resolve())
        return any(wt["path"] == abs_path for wt in worktrees)

    def list_worktrees(self) -> List[dict]:
        """列出所有 worktrees.

        Returns:
            Worktree 清單，每個 worktree 包含 path 和 branch
        """
        try:
            # git worktree list --porcelain 格式：
            # worktree /path/to/worktree
            # HEAD <commit-hash>
            # branch refs/heads/branch-name
            # (空行)
            output = self.run_git("worktree", "list", "--porcelain")
            if not output:
                return []

            worktrees = []
            current_worktree = {}

            for line in output.split("\n"):
                line = line.strip()
                if not line:
                    # 空行表示一個 worktree 結束
                    if current_worktree:
                        worktrees.append(current_worktree)
                        current_worktree = {}
                elif line.startswith("worktree "):
                    current_worktree["path"] = line[9:]  # 去掉 "worktree " 前綴
                elif line.startswith("branch "):
                    # refs/heads/branch-name -> branch-name
                    branch_ref = line[7:]  # 去掉 "branch " 前綴
                    current_worktree["branch"] = branch_ref.replace("refs/heads/", "")
                elif line.startswith("detached"):
                    current_worktree["branch"] = "HEAD"

            # 加入最後一個 worktree
            if current_worktree:
                worktrees.append(current_worktree)

            return worktrees
        except GitError:
            return []
