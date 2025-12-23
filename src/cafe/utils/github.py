"""GitHub operations using gh CLI."""

import json
import re
import subprocess
from typing import Any, Dict, Optional, List
from pydantic import BaseModel


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

    def check_gh_auth(self) -> bool:
        """Check if gh CLI is authenticated

        Returns:
            True if authenticated, False otherwise

        Raises:
            GitHubError: If gh CLI is not installed or check failed
        """
        if not self.check_gh_installed():
            raise GitHubError(
                "gh CLI is not installed. Please install it from: https://github.com/cli/cli"
            )

        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            check=False,
        )
        # gh auth status returns 0 if authenticated, 1 if not
        return result.returncode == 0

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
        head: Optional[str] = None,
        base: Optional[str] = None,
        draft: bool = False,
    ) -> str:
        """Create a GitHub Pull Request.

        Args:
            title: PR title
            body: PR body/description
            head: Head branch name (optional, uses current branch if not specified)
            base: Base branch name (optional, uses default branch if not specified)
            draft: Create as draft PR (default: False)

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
            ]

            if head:
                cmd.extend(["--head", head])
            if base:
                cmd.extend(["--base", base])
            if draft:
                cmd.append("--draft")

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

    def extract_issue_number(self, issue_url_or_number: str) -> str:
        """Extract issue number from URL or return the number directly.

        Args:
            issue_url_or_number: Issue URL or number

        Returns:
            Issue number as string

        Raises:
            GitHubError: If invalid URL or number
        """
        # If it's already a number, return it
        if issue_url_or_number.isdigit():
            return issue_url_or_number

        # Try to extract from URL
        # Pattern: https://github.com/owner/repo/issues/123
        match = re.search(r"/issues/(\d+)", issue_url_or_number)
        if match:
            return match.group(1)

        raise GitHubError(f"Invalid issue URL or number: {issue_url_or_number}")

    def get_pr_for_branch(self, branch: str) -> Optional[Dict[str, Any]]:
        """Check if a PR exists for the given branch.

        Args:
            branch: Branch name

        Returns:
            PR data as dictionary if exists, None otherwise.
            Dictionary contains: number, url, title, body, state, isDraft

        Raises:
            GitHubError: If failed to check PR
        """
        try:
            result = subprocess.run(
                ["gh", "pr", "list", "--head", branch, "--json", "number,url,title,body,state,isDraft"],
                capture_output=True,
                text=True,
                check=True,
            )

            prs = json.loads(result.stdout)
            # Return the first PR if exists
            return prs[0] if prs else None

        except subprocess.CalledProcessError as e:
            raise GitHubError(f"Failed to check PR for branch {branch}: {e.stderr}") from e
        except json.JSONDecodeError as e:
            raise GitHubError(f"Failed to parse PR data: {e}") from e

    def update_pr(self, pr_number: str, title: Optional[str] = None, body: Optional[str] = None) -> None:
        """Update an existing Pull Request.

        Args:
            pr_number: PR number
            title: New PR title (optional)
            body: New PR body (optional)

        Raises:
            GitHubError: If failed to update PR
        """
        try:
            cmd = ["gh", "pr", "edit", pr_number]

            if title is not None:
                cmd.extend(["--title", title])
            if body is not None:
                cmd.extend(["--body", body])

            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
            )

        except subprocess.CalledProcessError as e:
            raise GitHubError(f"Failed to update PR {pr_number}: {e.stderr}") from e


# PR Comments utilities

class PRComment(BaseModel):
    """PR review comment model."""

    id: str
    body: str
    author: str
    created_at: str
    path: Optional[str] = None
    line: Optional[int] = None
    is_resolved: bool = False


def get_pr_comments(pr_number: int) -> List[PRComment]:
    """Get all review comments from a Pull Request with accurate resolved status.

    Uses GitHub GraphQL API to fetch review threads which include isResolved status.

    Args:
        pr_number: PR number

    Returns:
        List of PRComment objects with accurate is_resolved status

    Raises:
        ValueError: If PR not found or invalid
        GitHubError: If failed to get PR comments
    """
    try:
        # Get repo info first
        repo_result = subprocess.run(
            ["gh", "repo", "view", "--json", "owner,name"],
            capture_output=True,
            text=True,
            check=False,
        )

        if repo_result.returncode != 0:
            raise GitHubError(f"Failed to get repo info: {repo_result.stderr}")

        repo_data = json.loads(repo_result.stdout)
        owner = repo_data["owner"]["login"]
        repo_name = repo_data["name"]

        # Use GraphQL API to get review threads with resolved status
        graphql_query = """
        query($owner: String!, $name: String!, $number: Int!) {
          repository(owner: $owner, name: $name) {
            pullRequest(number: $number) {
              reviewThreads(first: 100) {
                nodes {
                  isResolved
                  comments(first: 50) {
                    nodes {
                      id
                      databaseId
                      body
                      author {
                        login
                      }
                      createdAt
                      path
                      line
                    }
                  }
                }
              }
            }
          }
        }
        """

        # Execute GraphQL query using gh api graphql
        result = subprocess.run(
            ["gh", "api", "graphql",
             "-f", f"query={graphql_query}",
             "-f", f"owner={owner}",
             "-f", f"name={repo_name}",
             "-F", f"number={pr_number}"],
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            if "not found" in result.stderr.lower() or "could not resolve" in result.stderr.lower():
                raise ValueError(f"PR #{pr_number} not found")
            raise GitHubError(f"Failed to get PR comments: {result.stderr}")

        response_data = json.loads(result.stdout)

        # Check for GraphQL errors
        if "errors" in response_data:
            error_messages = [e.get("message", str(e)) for e in response_data["errors"]]
            raise GitHubError(f"GraphQL errors: {', '.join(error_messages)}")

        # Extract review threads
        pr_data = response_data.get("data", {}).get("repository", {}).get("pullRequest")
        if not pr_data:
            raise ValueError(f"PR #{pr_number} not found")

        review_threads = pr_data.get("reviewThreads", {}).get("nodes", [])

        comments = []
        for thread in review_threads:
            is_resolved = thread.get("isResolved", False)
            thread_comments = thread.get("comments", {}).get("nodes", [])

            for comment in thread_comments:
                # Use databaseId (integer) for compatibility with existing code
                comment_id = str(comment.get("databaseId", ""))

                comments.append(PRComment(
                    id=comment_id,
                    body=comment.get("body", ""),
                    author=comment.get("author", {}).get("login", "unknown") if comment.get("author") else "unknown",
                    created_at=comment.get("createdAt", ""),
                    path=comment.get("path"),
                    line=comment.get("line"),
                    is_resolved=is_resolved,
                ))

        return comments

    except json.JSONDecodeError as e:
        raise GitHubError(f"Failed to parse PR comments: {e}") from e
    except Exception as e:
        if isinstance(e, (ValueError, GitHubError)):
            raise
        raise GitHubError(f"Unexpected error getting PR comments: {e}") from e


def filter_unresolved_comments(comments: List[PRComment]) -> List[PRComment]:
    """Filter out resolved comments, keeping only unresolved ones.

    Args:
        comments: List of PRComment objects

    Returns:
        List of unresolved PRComment objects
    """
    return [c for c in comments if not c.is_resolved]


def format_comments_for_prompt(comments: List[PRComment]) -> str:
    """Format PR comments into a prompt-friendly string.

    Args:
        comments: List of PRComment objects (should be unresolved)

    Returns:
        Formatted string for inclusion in agent prompt
    """
    if not comments:
        return ""

    count = len(comments)
    plural = "s" if count > 1 else ""
    
    lines = [
        "=" * 80,
        f"📝 PR Review Comments ({count} unresolved comment{plural})",
        "=" * 80,
        ""
    ]

    for i, comment in enumerate(comments, 1):
        lines.append(f"Comment #{i}")
        lines.append(f"Author: {comment.author}")
        if comment.path:
            location = f"{comment.path}"
            if comment.line:
                location += f" (line {comment.line})"
            lines.append(f"Location: {location}")
        lines.append(f"Created: {comment.created_at}")
        lines.append("")
        lines.append(comment.body)
        lines.append("")
        lines.append("-" * 80)
        lines.append("")

    return "\n".join(lines)
