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
    """取得 Git repository 的根目錄。

    支援一般 repository 和 worktree 環境。
    從給定目錄開始向上搜尋，直到找到 .git 目錄或檔案。
    如果是 worktree（.git 是檔案），則解析 gitdir 找到主 repo。

    Args:
        cwd: 起始目錄（default: 當前目錄）

    Returns:
        Repository 根目錄的 Path 物件

    Raises:
        ValueError: 如果不在 Git repository 中

    Example:
        >>> repo_root = get_repo_root()
        >>> print(repo_root)  # /Users/me/projects/my-repo
    """
    if cwd is None:
        cwd = Path.cwd()
    else:
        cwd = Path(cwd)

    # 向上搜尋 .git
    current = cwd.resolve()
    while current != current.parent:
        git_path = current / ".git"
        if git_path.exists():
            # 找到 .git，檢查是目錄還是檔案
            if git_path.is_dir():
                # 一般 repo：返回此目錄
                return current
            elif git_path.is_file():
                # Worktree：解析 .git 檔案找到主 repo
                gitdir_content = git_path.read_text().strip()
                if gitdir_content.startswith("gitdir: "):
                    # 格式：gitdir: /path/to/main-repo/.git/worktrees/branch-name
                    gitdir = Path(gitdir_content[8:])  # 移除 "gitdir: " 前綴
                    # 主 repo 的 .git 目錄在 ../../（從 worktrees/branch-name 向上兩層）
                    main_git_dir = gitdir.parent.parent
                    # 主 repo root 是 .git 的父目錄
                    main_repo_root = main_git_dir.parent
                    return main_repo_root
                else:
                    raise ValueError(f"Invalid .git file format: {git_path}")
        current = current.parent

    # 沒有找到 .git
    raise ValueError(f"Not in a Git repository: {cwd}")


def to_git_ignore_path(file_path: Union[str, Path], repo_root: Union[str, Path]) -> str:
    """將絕對路徑轉換為 git ignore 格式的相對路徑。

    Git ignore 格式：以 / 開頭的相對於 repo root 的路徑。
    例如：/.cafe/issues/issue26/spec/spec_001.md

    Args:
        file_path: 檔案的絕對路徑
        repo_root: Repository 根目錄的絕對路徑

    Returns:
        Git ignore 格式的路徑字串（以 / 開頭）

    Raises:
        ValueError: 如果 file_path 不在 repo_root 下

    Example:
        >>> path = to_git_ignore_path(
        ...     "/Users/me/repo/.cafe/issues/x/spec.md",
        ...     "/Users/me/repo"
        ... )
        >>> print(path)  # /.cafe/issues/x/spec.md
    """
    file_path = Path(file_path).resolve()
    repo_root = Path(repo_root).resolve()

    # 檢查 file_path 是否在 repo_root 下
    try:
        relative_path = file_path.relative_to(repo_root)
    except ValueError:
        raise ValueError(f"File path {file_path} is not under repository root {repo_root}")

    # 轉換為 git ignore 格式（以 / 開頭）
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

    git_path = cwd / ".git"

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
