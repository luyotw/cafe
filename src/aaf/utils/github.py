"""GitHub operations using gh CLI."""

import json
import re
import subprocess
from typing import Any, Dict, Optional


class GitHubError(Exception):
    """GitHub operation error."""

    pass


class GitHubOps:
    """GitHub operations wrapper for gh CLI."""

    def __init__(self) -> None:
        """Initialize GitHub operations.

        Raises:
            GitHubError: If gh CLI is not installed
        """
        # Check if gh CLI is installed on initialization
        self.check_gh_installed()

    def check_gh_installed(self) -> bool:
        """Check if gh CLI is installed.

        Returns:
            True if gh is installed, False otherwise
        """
        try:
            result = subprocess.run(
                ["gh", "--version"],
                capture_output=True,
                text=True,
                check=False,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False

    def get_issue(self, issue_id: str, include_comments: bool = False) -> Dict[str, Any]:
        """Get GitHub issue information.

        Args:
            issue_id: Issue ID or number
            include_comments: Include comments in response

        Returns:
            Issue data as dictionary

        Raises:
            GitHubError: If failed to get issue
        """
        try:
            fields = "number,title,body,state"
            if include_comments:
                fields += ",comments"

            result = subprocess.run(
                ["gh", "issue", "view", issue_id, "--json", fields],
                capture_output=True,
                text=True,
                check=True,
            )

            return json.loads(result.stdout)

        except subprocess.CalledProcessError as e:
            raise GitHubError(f"Failed to get issue {issue_id}: {e.stderr}") from e
        except json.JSONDecodeError as e:
            raise GitHubError(f"Failed to parse issue data: {e}") from e

    def create_pr(
        self,
        title: str,
        body: str,
        head: str,
        base: str = "main",
        issue_id: Optional[str] = None,
    ) -> str:
        """Create a GitHub Pull Request.

        Args:
            title: PR title
            body: PR body/description
            head: Head branch name
            base: Base branch name (default: main)
            issue_id: Optional issue ID to link

        Returns:
            PR URL

        Raises:
            GitHubError: If failed to create PR
        """
        try:
            cmd = [
                "gh",
                "pr",
                "create",
                "--title",
                title,
                "--body",
                body,
                "--head",
                head,
                "--base",
                base,
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
            )

            # Extract PR URL from output
            pr_url = result.stdout.strip()
            return pr_url

        except subprocess.CalledProcessError as e:
            raise GitHubError(f"Failed to create PR: {e.stderr}") from e

    def add_issue_comment(self, issue_id: str, comment: str) -> None:
        """Add a comment to a GitHub issue.

        Args:
            issue_id: Issue ID or number
            comment: Comment text

        Raises:
            GitHubError: If failed to add comment
        """
        try:
            subprocess.run(
                ["gh", "issue", "comment", issue_id, "--body", comment],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise GitHubError(f"Failed to add issue comment: {e.stderr}") from e

    def add_pr_comment(self, pr_id: str, comment: str) -> None:
        """Add a comment to a GitHub Pull Request.

        Args:
            pr_id: PR ID or number
            comment: Comment text

        Raises:
            GitHubError: If failed to add comment
        """
        try:
            subprocess.run(
                ["gh", "pr", "comment", pr_id, "--body", comment],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise GitHubError(f"Failed to add PR comment: {e.stderr}") from e

    def get_pr_status(self, pr_id: str) -> Dict[str, Any]:
        """Get GitHub Pull Request status.

        Args:
            pr_id: PR ID or number

        Returns:
            PR status data as dictionary

        Raises:
            GitHubError: If failed to get PR status
        """
        try:
            result = subprocess.run(
                ["gh", "pr", "view", pr_id, "--json", "number,state,mergeable,title"],
                capture_output=True,
                text=True,
                check=True,
            )

            return json.loads(result.stdout)

        except subprocess.CalledProcessError as e:
            raise GitHubError(f"Failed to get PR status: {e.stderr}") from e
        except json.JSONDecodeError as e:
            raise GitHubError(f"Failed to parse PR data: {e}") from e

    def extract_pr_number(self, pr_url_or_number: str) -> str:
        """Extract PR number from URL or return the number directly.

        Args:
            pr_url_or_number: PR URL or number

        Returns:
            PR number as string

        Raises:
            GitHubError: If invalid URL or number
        """
        # If it's already a number, return it
        if pr_url_or_number.isdigit():
            return pr_url_or_number

        # Try to extract from URL
        # Pattern: https://github.com/owner/repo/pull/123
        match = re.search(r"/pull/(\d+)", pr_url_or_number)
        if match:
            return match.group(1)

        raise GitHubError(f"Invalid PR URL or number: {pr_url_or_number}")
