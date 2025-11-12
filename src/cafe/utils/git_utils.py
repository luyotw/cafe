"""Git utility functions."""

import os
import subprocess
import tempfile
from typing import Tuple


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
