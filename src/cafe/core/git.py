"""Git operations for CAFE."""

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass(frozen=True)
class BranchHealth:
    """Result of checking whether the current Git branch context is safe for issue detection."""

    is_healthy: bool
    branch_name: Optional[str] = None
    reason: Optional[str] = None  # detached_head | in_progress | git_error


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

    @classmethod
    def is_repository(cls, repo_path: str = ".") -> bool:
        """Return whether ``repo_path`` is inside a Git work tree."""
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=Path(repo_path),
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"

    @classmethod
    def initialize_repository(
        cls,
        repo_path: str = ".",
        *,
        initial_branch: str = "main",
    ) -> "GitOperations":
        """Initialize a local repository with a baseline commit.

        The baseline is intentionally empty. Existing project files remain
        visible as uncommitted changes so CAFE can review them before the first
        feature commit instead of silently adding possible secrets.
        """
        path = Path(repo_path)
        try:
            subprocess.run(
                ["git", "init", "-b", initial_branch],
                cwd=path,
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise GitError(f"Git initialization failed: {exc.stderr}") from exc

        operations = cls(repo_path)
        operations.run_git("config", "--local", "cafe.bootstrap-pending", "true")
        operations.complete_repository_initialization()
        return operations

    def has_commits(self) -> bool:
        """Return whether the repository has a valid ``HEAD`` commit."""
        try:
            self.run_git("rev-parse", "--verify", "HEAD")
        except GitError:
            return False
        return True

    def is_bootstrap_pending(self) -> bool:
        """Return whether a CAFE-owned repository bootstrap is incomplete."""
        try:
            marker = self.run_git(
                "config", "--local", "--get", "cafe.bootstrap-pending"
            )
        except GitError:
            return False
        return marker.lower() == "true"

    def complete_repository_initialization(self) -> None:
        """Complete or resume CAFE's empty baseline commit."""
        if self.has_commits():
            return

        # A new non-developer setup often has no Git identity yet. Fill only
        # missing values and keep the fallback local to this repository.
        for key, fallback in (
            ("user.name", "CAFE"),
            ("user.email", "cafe@local.invalid"),
        ):
            try:
                configured_value = self.run_git("config", "--get", key)
            except GitError:
                configured_value = ""
            if not configured_value.strip():
                self.run_git("config", "--local", key, fallback)

        # A synthetic safety baseline must not run repository/global hooks,
        # which may publish or perform other external actions. It must also
        # leave any user-staged files untouched.
        with tempfile.TemporaryDirectory(prefix="cafe-empty-hooks-") as hooks_path:
            self.run_git(
                "-c",
                f"core.hooksPath={hooks_path}",
                "commit",
                "--allow-empty",
                "--only",
                "--no-gpg-sign",
                "-m",
                "Initialize repository",
            )

    def uses_placeholder_identity(self) -> bool:
        """Return whether this repository uses CAFE's local author fallback."""
        local_values = []
        for key in ("user.name", "user.email"):
            try:
                local_values.append(
                    self.run_git("config", "--local", "--get", key)
                )
            except GitError:
                continue

        return "CAFE" in local_values or "cafe@local.invalid" in local_values

    def requires_bootstrap_checkout(self) -> bool:
        """Return whether CAFE's initial untracked files still need this checkout.

        The repository-local marker survives a stopped or retried ``prepare``
        command. Worktrees become safe once every initial file is either
        committed or deliberately excluded by Git ignore rules.
        """
        if not self.is_bootstrap_pending():
            return False

        untracked_files = self.run_git("ls-files", "--others", "--exclude-standard")
        uncommitted_additions = self.run_git(
            "diff", "HEAD", "--name-only", "--diff-filter=A"
        )
        if untracked_files or uncommitted_additions:
            return True

        self.run_git("config", "--local", "--unset", "cafe.bootstrap-pending")
        return False

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

    def _git_dir_path(self) -> Path:
        """Resolve the repository .git directory (handles worktrees)."""
        git_dir = Path(self.run_git("rev-parse", "--git-dir"))
        if not git_dir.is_absolute():
            git_dir = (self.repo_path / git_dir).resolve()
        return git_dir

    def has_in_progress_operation(self) -> bool:
        """Return True when rebase, merge, cherry-pick, revert, or bisect is in progress."""
        git_dir = self._git_dir_path()
        markers = (
            "rebase-merge",
            "rebase-apply",
            "MERGE_HEAD",
            "CHERRY_PICK_HEAD",
            "REVERT_HEAD",
            "BISECT_LOG",
        )
        return any((git_dir / name).exists() for name in markers)

    def get_branch_health(self) -> BranchHealth:
        """Check whether branch-based issue detection is safe to use."""
        try:
            branch = self.get_current_branch()
        except GitError:
            return BranchHealth(is_healthy=False, reason="git_error")
        if not branch:
            return BranchHealth(is_healthy=False, reason="detached_head")
        if self.has_in_progress_operation():
            return BranchHealth(
                is_healthy=False,
                branch_name=branch,
                reason="in_progress",
            )
        return BranchHealth(is_healthy=True, branch_name=branch)

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

    def has_tracked_or_staged_changes(self) -> bool:
        """Return whether tracked or staged files differ, ignoring untracked files."""
        return bool(self.run_git("status", "--porcelain", "--untracked-files=no"))

    def has_staged_changes(self) -> bool:
        """Check if there are staged (index) changes ready to commit.

        Unlike :meth:`has_uncommitted_changes`, this ignores untracked and
        unstaged files, so it correctly reports whether a commit would
        actually produce a change. Used to guard against committing an empty
        squash merge.

        Returns:
            True if the index differs from HEAD
        """
        try:
            self.run_git("diff", "--cached", "--quiet")
            return False
        except GitError:
            # Non-zero exit means there are staged differences.
            return True

    def delete_branch(self, branch_name: str, force: bool = False) -> None:
        """Delete a local branch.

        Args:
            branch_name: Name of branch to delete
            force: Use force delete (-D) instead of safe delete (-d).
                Required for squash-merged branches, which Git still
                considers "not merged" because no merge commit points at them.

        Raises:
            GitError: If branch deletion fails
        """
        flag = "-D" if force else "-d"
        self.run_git("branch", flag, branch_name)

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

    def merge_squash(self, branch_name: str) -> None:
        """Squash-merge a branch into the current branch (stages only, no commit).

        Args:
            branch_name: Name of the branch to squash-merge

        Raises:
            GitError: If merge fails
        """
        self.run_git("merge", "--squash", branch_name)

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

    def get_default_base_branch(self) -> str:
        """Get the default base branch for PR creation and diffs.

        Prefers ``develop`` when the remote has a develop branch, falling
        back to the repo's main branch otherwise.  This matches the typical
        Git-Flow convention where feature branches target develop.

        Returns:
            Default base branch name
        """
        try:
            self.run_git("rev-parse", "--verify", "origin/develop")
            return "develop"
        except GitError:
            return self.get_main_branch()

    def get_commits_between(self, base: str, head: str) -> str:
        """Get commit list between two refs.

        Args:
            base: Base ref
            head: Head ref

        Returns:
            Commit list (one-line format)
        """
        return self.run_git("log", "--oneline", f"{base}..{head}")

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        """Return whether ``ancestor`` is contained in ``descendant`` history."""
        try:
            self.run_git("merge-base", "--is-ancestor", ancestor, descendant)
        except GitError:
            return False
        return True

    def ensure_remote_base_ancestor(
        self,
        base_branch: str,
        head_ref: str,
        *,
        remote: str = "origin",
    ) -> str:
        """Fetch a PR base and require the candidate history to contain it.

        A local base may be ahead of its remote counterpart. A behind or
        diverged base is unsafe because the eventual PR range would differ
        from the range CAFE reviewed.
        """
        remote_ref = f"{remote}/{base_branch}"
        self.run_git(
            "fetch",
            "--no-tags",
            remote,
            f"+refs/heads/{base_branch}:refs/remotes/{remote}/{base_branch}",
        )
        if not self.is_ancestor(remote_ref, head_ref):
            raise GitError(
                f"Remote base {remote_ref} is not contained in {head_ref}. "
                f"Merge or rebase {remote_ref} into {head_ref}, resolve any conflicts, "
                "then retry."
            )
        return remote_ref

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
            # No upstream branch - check if remote branch exists
            try:
                branch_name = self.get_current_branch()
                # Check if remote branch exists
                self.run_git("rev-parse", "--verify", f"origin/{branch_name}")
                # Remote exists but no tracking - compare with remote
                output = self.run_git("log", f"origin/{branch_name}..HEAD", "--oneline")
                return bool(output.strip())
            except GitError:
                # Remote branch doesn't exist - any local commits count as unpushed
                output = self.run_git("rev-list", "--count", "HEAD")
                return int(output.strip()) > 0
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
        range_spec = "@{u}..HEAD"

        if not self.has_upstream_branch():
            # No upstream branch - check if remote branch exists
            try:
                branch_name = self.get_current_branch()
                self.run_git("rev-parse", "--verify", f"origin/{branch_name}")
                # Remote exists but no tracking - compare with remote
                range_spec = f"origin/{branch_name}..HEAD"
            except GitError:
                # Remote branch doesn't exist - return empty list (caller should handle first push differently)
                return []

        try:
            # Format: <hash>|<iso-timestamp>|<subject>
            output = self.run_git("log", range_spec, "--format=%H|%aI|%s")
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
