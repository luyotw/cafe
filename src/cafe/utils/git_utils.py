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

    config_file = cwd / ".git" / "config"
    if not config_file.exists():
        raise FileNotFoundError(f".git/config not found in {cwd}")

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
