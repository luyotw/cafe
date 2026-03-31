"""Git utility functions."""

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple, Union


def rewrite_commit_message(commit_sha: str, new_message: str, base_branch: str = "main") -> Tuple[bool, str]:
    """Rewrite a single commit message using git rebase.
    
    Args:
        commit_sha: Commit SHA to modify (short or long format)
        new_message: New commit message
        base_branch: Base branch (default: main)
        
    Returns:
        (success, message) tuple
        
    Example:
        success, msg = rewrite_commit_message("abc123", "fix: update logic")
        if success:
            print("Commit message updated!")
    """
    try:
        # Create temp file for new message
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write(new_message)
            message_file = f.name
        
        try:
            # Build exec command
            exec_cmd = f"""
if test $(git rev-parse HEAD) = {commit_sha} || test $(git rev-parse HEAD) = $(git rev-parse {commit_sha}); then
  git commit --amend -F {message_file} --allow-empty --no-edit;
fi
"""
            
            # Run rebase
            result = subprocess.run(
                ['git', 'rebase', '--onto', base_branch, base_branch, 'HEAD', '--exec', exec_cmd],
                capture_output=True,
                text=True,
                check=False,
            )
            
            if result.returncode == 0:
                return True, f"Rewrote commit {commit_sha[:7]}"
            else:
                subprocess.run(['git', 'rebase', '--abort'], capture_output=True)
                return False, f"Rebase failed: {result.stderr}"
                
        finally:
            os.unlink(message_file)
            
    except Exception as e:
        return False, f"Error: {e}"


def is_branch_initialized(branch_name: str, repo_path: Optional[Union[str, Path]] = None) -> bool:
    """Check if a branch has been initialized via cafe prepare.

    Args:
        branch_name: Name of the branch to check
        repo_path: Repository path (default: current directory)

    Returns:
        True if .cafe/issues/<branch-name>/ directory exists, False otherwise

    Example:
        >>> if is_branch_initialized("feature/new-login"):
        ...     print("Branch is initialized")
        ... else:
        ...     print("Run 'cafe prepare' first")
    """
    if repo_path is None:
        repo_path = Path.cwd()
    else:
        repo_path = Path(repo_path)

    issue_dir = repo_path / ".cafe" / "issues" / branch_name
    return issue_dir.exists() and issue_dir.is_dir()


def get_repo_root(cwd: Optional[Path] = None) -> Path:
    """Get the root directory of a Git repository.

    Supports both regular repository and worktree environments.
    Searches upward from the given directory until finding a .git directory or file.
    If it's a worktree (.git is a file), parses gitdir to find the main repo.

    Respects GIT_CEILING_DIRECTORIES environment variable to prevent searching beyond specified boundaries.
    This is critical for preventing tests from polluting the real repository.

    Args:
        cwd: Starting directory (default: current directory)

    Returns:
        Path object of the repository root directory

    Raises:
        ValueError: If not in a Git repository, or search was blocked by ceiling

    Example:
        >>> repo_root = get_repo_root()
        >>> print(repo_root)  # /Users/me/projects/my-repo
    """
    import os

    if cwd is None:
        cwd = Path.cwd()
    else:
        cwd = Path(cwd)

    # Parse GIT_CEILING_DIRECTORIES (may contain multiple paths, colon-separated)
    ceiling_dirs: set[Path] = set()
    ceiling_env = os.environ.get("GIT_CEILING_DIRECTORIES")
    if ceiling_env:
        for ceiling_str in ceiling_env.split(":"):
            if ceiling_str:  # Ignore empty strings
                ceiling_dirs.add(Path(ceiling_str).resolve())

    # Search upward for .git
    current = cwd.resolve()
    while current != current.parent:
        # Check if reached ceiling (before checking for .git)
        # If current directory is ceiling, stop searching
        if current in ceiling_dirs:
            raise ValueError(
                f"Not in a Git repository: {cwd} "
                f"(stopped at ceiling: {current})"
            )

        git_path = current / ".git"
        if git_path.exists():
            # Found .git, check if it's a directory or file
            if git_path.is_dir():
                # Regular repo: return this directory
                return current
            elif git_path.is_file():
                # Worktree: parse .git file to find main repo
                gitdir_content = git_path.read_text().strip()
                if gitdir_content.startswith("gitdir: "):
                    # Format: gitdir: /path/to/main-repo/.git/worktrees/branch-name
                    gitdir = Path(gitdir_content[8:])  # Remove "gitdir: " prefix
                    # Main repo's .git directory is ../../ (up two levels from worktrees/branch-name)
                    main_git_dir = gitdir.parent.parent
                    # Main repo root is parent of .git
                    main_repo_root = main_git_dir.parent

                    # Critical fix: Check if main repo root is outside ceiling
                    # Ceiling semantics: Only allow searching inside ceiling directory
                    # If worktree points to main repo not inside ceiling, should raise error
                    if ceiling_dirs:
                        # Check if main_repo_root is inside any ceiling
                        is_under_any_ceiling = False
                        for ceiling in ceiling_dirs:
                            try:
                                rel = main_repo_root.relative_to(ceiling)
                                # main_repo_root is under ceiling, allow
                                is_under_any_ceiling = True
                                break
                            except ValueError as e:
                                # main_repo_root is not under this ceiling, continue checking other ceilings
                                continue

                        if not is_under_any_ceiling:
                            # main_repo_root is not inside any ceiling, raise error
                            raise ValueError(
                                f"Not in a Git repository: {cwd} "
                                f"(worktree points to repo outside ceiling: {main_repo_root})"
                            )

                    return main_repo_root
                else:
                    raise ValueError(f"Invalid .git file format: {git_path}")

        # Before moving up to parent directory, check if parent is outside ceiling
        # Ceiling semantics: Only allow searching inside ceiling directory
        parent = current.parent
        if ceiling_dirs:
            # Check if parent is inside any ceiling
            is_under_any_ceiling = False
            for ceiling in ceiling_dirs:
                try:
                    parent.relative_to(ceiling)
                    # parent is under ceiling, allow continuing upward search
                    is_under_any_ceiling = True
                    break
                except ValueError:
                    # parent is not under this ceiling, continue checking other ceilings
                    continue

            if not is_under_any_ceiling:
                # parent is not inside any ceiling, stop searching
                raise ValueError(
                    f"Not in a Git repository: {cwd} (stopped at ceiling boundary)"
                )

        current = parent

    # Did not find .git
    raise ValueError(f"Not in a Git repository: {cwd}")


def get_git_dir(cwd: Optional[Path] = None) -> Path:
    """Get the active Git metadata directory for the current repo or worktree.

    Args:
        cwd: Starting directory (default: current directory)

    Returns:
        Path to the Git metadata directory:
        - regular repo: <repo>/.git
        - worktree: <main-repo>/.git/worktrees/<worktree-name>

    Raises:
        ValueError: If not in a Git repository or .git file format is invalid
    """
    if cwd is None:
        cwd = Path.cwd()
    else:
        cwd = Path(cwd)

    current = cwd.resolve()
    while current != current.parent:
        git_path = current / ".git"
        if git_path.exists():
            if git_path.is_dir():
                return git_path
            if git_path.is_file():
                gitdir_content = git_path.read_text().strip()
                if gitdir_content.startswith("gitdir: "):
                    return Path(gitdir_content[8:])
                raise ValueError(f"Invalid .git file format: {git_path}")
        current = current.parent

    raise ValueError(f"Not in a Git repository: {cwd}")


def get_git_toplevel(cwd: Optional[Path] = None) -> Path:
    """Get the current Git worktree/repository top-level directory.

    Unlike get_repo_root(), this returns the active checkout root. In a worktree,
    that is the worktree directory itself rather than the main repository root.

    Args:
        cwd: Starting directory (default: current directory)

    Returns:
        Path to the current git top-level directory

    Raises:
        ValueError: If not in a Git repository
    """
    if cwd is None:
        cwd = Path.cwd()
    else:
        cwd = Path(cwd)

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise ValueError(f"Not in a Git repository: {cwd}") from e

    return Path(result.stdout.strip())


def to_relative_path(file_path: Union[str, Path], repo_root: Union[str, Path]) -> str:
    """Convert absolute path to plain relative path.

    Plain relative path: path relative to repo root without / prefix.
    Example: .cafe/issues/issue26/spec/spec_001.md

    Args:
        file_path: File's absolute path
        repo_root: Repository root directory's absolute path

    Returns:
        Plain relative path string (without / prefix)

    Raises:
        ValueError: If file_path is not under repo_root

    Example:
        >>> path = to_relative_path(
        ...     "/Users/me/repo/.cafe/issues/x/spec.md",
        ...     "/Users/me/repo"
        ... )
        >>> print(path)  # .cafe/issues/x/spec.md
    """
    file_path = Path(file_path).resolve()
    repo_root = Path(repo_root).resolve()

    # Check if file_path is under repo_root
    try:
        relative_path = file_path.relative_to(repo_root)
    except ValueError:
        raise ValueError(f"File path {file_path} is not under repository root {repo_root}")

    # Return plain relative path (without / prefix)
    return str(relative_path)


def to_cwd_relative_path(file_path: Union[str, Path]) -> str:
    """Convert file path to path relative to current working directory.

    This function supports worktree environments because it uses the current working directory
    as the base, rather than the repository root. In a worktree, the current working directory
    is the worktree directory.

    Args:
        file_path: File path (can be absolute or relative)

    Returns:
        Relative path string relative to current working directory (with ./ prefix)

    Raises:
        ValueError: If file_path is not under current working directory

    Example:
        In normal repo:
        >>> # cwd = /Users/me/repo
        >>> path = to_cwd_relative_path("/Users/me/repo/.cafe/issues/x/spec.md")
        >>> print(path)  # ./.cafe/issues/x/spec.md

        In worktree:
        >>> # cwd = /Users/me/repo/.cafe/worktrees/issue33
        >>> path = to_cwd_relative_path(
        ...     "/Users/me/repo/.cafe/worktrees/issue33/.cafe/issues/issue33/spec.md"
        ... )
        >>> print(path)  # ./.cafe/issues/issue33/spec.md
    """
    import os

    file_path = Path(file_path)
    if not file_path.is_absolute():
        file_path = file_path.resolve()

    cwd = Path(os.getcwd()).resolve()

    try:
        relative_path = file_path.relative_to(cwd)
        return f"./{relative_path}"
    except ValueError:
        raise ValueError(f"File path {file_path} is not under current working directory {cwd}")


def to_git_ignore_path(file_path: Union[str, Path], repo_root: Union[str, Path]) -> str:
    """Convert absolute path to git ignore format relative path.

    Git ignore format: path relative to repo root with / prefix.
    Example: /.cafe/issues/issue26/spec/spec_001.md

    Args:
        file_path: File's absolute path
        repo_root: Repository root directory's absolute path

    Returns:
        Git ignore format path string (with / prefix)

    Raises:
        ValueError: If file_path is not under repo_root

    Example:
        >>> path = to_git_ignore_path(
        ...     "/Users/me/repo/.cafe/issues/x/spec.md",
        ...     "/Users/me/repo"
        ... )
        >>> print(path)  # /.cafe/issues/x/spec.md
    """
    file_path = Path(file_path).resolve()
    repo_root = Path(repo_root).resolve()

    # Check if file_path is under repo_root
    try:
        relative_path = file_path.relative_to(repo_root)
    except ValueError:
        raise ValueError(f"File path {file_path} is not under repository root {repo_root}")

    # Convert to git ignore format (with / prefix)
    return "/" + str(relative_path)


def get_github_repo_name(cwd: Optional[Path] = None) -> str:
    """Get GitHub repository name from .git/config.

    Args:
        cwd: Working directory (default: current directory)

    Returns:
        Repository name in "owner/repo" format

    Raises:
        FileNotFoundError: If .git/config not found
        ValueError: If remote origin not found or invalid URL

    Example:
        >>> repo = get_github_repo_name()
        >>> print(repo)  # "anthropics/cli-agent-flow-engine"
    """
    if cwd is None:
        cwd = Path.cwd()
    else:
        cwd = Path(cwd)

    # Try to find git repository root directory using git command
    # This works even from subdirectories
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True
        )
        repo_root = Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Fallback: assume cwd is the repo root (for tests that only create .git/config)
        repo_root = cwd

    git_path = repo_root / ".git"

    # Handle worktree: .git is a file pointing to the real git directory
    if git_path.is_file():
        # Read the gitdir path from .git file
        gitdir_content = git_path.read_text().strip()
        # Format: "gitdir: /path/to/main/repo/.git/worktrees/branch-name"
        if gitdir_content.startswith("gitdir: "):
            gitdir = Path(gitdir_content[8:])  # Remove "gitdir: " prefix
            # The main repo's config is at ../../config (up from worktrees/branch-name)
            config_file = gitdir.parent.parent / "config"
        else:
            raise ValueError(f"Invalid .git file format in {cwd}")
    else:
        # Normal repository: .git is a directory
        config_file = git_path / "config"

    if not config_file.exists():
        raise FileNotFoundError(f".git/config not found (looking at {config_file})")

    config_content = config_file.read_text()

    # Find remote "origin" URL
    # Match patterns like:
    # [remote "origin"]
    #     url = https://github.com/owner/repo.git
    # or:
    #     url = git@github.com:owner/repo.git
    match = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*(.+)', config_content, re.MULTILINE)
    if not match:
        raise ValueError("No remote 'origin' found in .git/config")

    remote_url = match.group(1).strip()

    # Extract owner/repo from URL
    # HTTPS: https://github.com/owner/repo.git
    # SSH: git@github.com:owner/repo.git
    https_match = re.match(r'https://github\.com/([^/]+)/([^/\.]+)', remote_url)
    ssh_match = re.match(r'git@github\.com:([^/]+)/([^/\.]+)', remote_url)

    if https_match:
        owner, repo = https_match.groups()
        return f"{owner}/{repo}"
    elif ssh_match:
        owner, repo = ssh_match.groups()
        return f"{owner}/{repo}"
    else:
        raise ValueError(f"Invalid GitHub URL: {remote_url}")


def is_github_repo(cwd: Optional[Path] = None) -> bool:
    """Check if the repository has a GitHub remote.

    Args:
        cwd: Working directory (default: current directory)

    Returns:
        True if a github.com remote is found, False otherwise.
    """
    try:
        get_github_repo_name(cwd=cwd)
        return True
    except (FileNotFoundError, ValueError):
        return False
