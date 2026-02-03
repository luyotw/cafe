"""GitHub operations using gh CLI."""

import json
import re
import subprocess
from pathlib import Path
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

    @staticmethod
    def extract_image_urls(body: str) -> List[str]:
        """從 Markdown 內容中解析圖片 URL

        支援標準 Markdown 圖片語法 ![alt](url) 和 GitHub user-attachments 格式

        Args:
            body: Markdown 內容

        Returns:
            圖片 URL 列表
        """
        # 標準圖片格式：![alt](url) 且 URL 帶有常見圖片副檔名
        standard_pattern = re.compile(
            r'!\[[^\]]*\]\((https?://[^)]+\.(?:png|jpg|jpeg|gif|webp|svg)(?:\?[^)]*)?)\)',
            re.IGNORECASE
        )
        # GitHub user-attachments 格式（不帶副檔名）
        github_assets_pattern = re.compile(
            r'!\[[^\]]*\]\((https://github\.com/user-attachments/assets/[^)]+)\)',
            re.IGNORECASE
        )

        urls = []
        # 解析標準格式
        urls.extend(standard_pattern.findall(body))
        # 解析 GitHub assets 格式
        urls.extend(github_assets_pattern.findall(body))

        return urls

    def download_issue_images(self, image_urls: List[str], save_dir: Path) -> List[Path]:
        """下載 issue 中的圖片到指定目錄

        使用 curl 下載圖片，檔名格式為 image_001.ext, image_002.ext 等

        Args:
            image_urls: 圖片 URL 列表
            save_dir: 儲存目錄路徑

        Returns:
            成功下載的圖片路徑列表
        """
        if not image_urls:
            return []

        # 建立目錄
        save_dir.mkdir(parents=True, exist_ok=True)

        saved_paths = []
        for idx, url in enumerate(image_urls, start=1):
            # 解析副檔名
            # 移除 query string 後提取副檔名
            url_path = url.split('?')[0]
            # 嘗試從 URL 中提取副檔名
            match = re.search(r'\.([a-zA-Z]+)$', url_path)
            if match:
                ext = match.group(1).lower()
            else:
                # 無副檔名預設為 png（通常是 GitHub assets）
                ext = "png"

            # 建立檔名
            filename = f"image_{idx:03d}.{ext}"
            save_path = save_dir / filename

            # 使用 curl 下載
            try:
                result = subprocess.run(
                    ["curl", "-L", "-o", str(save_path), url],
                    capture_output=True,
                    text=True,
                    check=False,
                )

                if result.returncode == 0:
                    saved_paths.append(save_path)
                # 下載失敗時繼續處理其他圖片，不拋出異常

            except (FileNotFoundError, subprocess.CalledProcessError):
                # curl 不存在或執行失敗，繼續處理其他圖片
                continue

        return saved_paths

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

    def update_issue(self, issue_id: str, title: Optional[str] = None, body: Optional[str] = None) -> None:
        """Update an existing GitHub issue.

        Args:
            issue_id: Issue number or ID
            title: New issue title (optional)
            body: New issue body/description (optional)

        Raises:
            GitHubError: If failed to update issue
        """
        try:
            cmd = ["gh", "issue", "edit", issue_id]

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
            raise GitHubError(f"Failed to update issue {issue_id}: {e.stderr}") from e


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
    comment_type: str = "review"  # "review", "timeline", or "review_body"
    review_id: Optional[str] = None  # ID of the parent review (for grouping)


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
    """Format PR comments into a prompt-friendly string with separate sections for review and timeline comments.

    Args:
        comments: List of PRComment objects (should be unresolved)

    Returns:
        Formatted string for inclusion in agent prompt, with comment IDs for tracking
    """
    if not comments:
        return ""

    # Separate comments by type
    review_comments = [c for c in comments if c.comment_type == "review"]
    timeline_comments = [c for c in comments if c.comment_type == "timeline"]
    review_body_comments = [c for c in comments if c.comment_type == "review_body"]

    # Collect review_ids that have a review body (so we can exclude their line comments from standalone section)
    review_ids_with_body = {c.review_id for c in review_body_comments if c.review_id}

    # Separate standalone review comments (not associated with a review body) from those that are
    standalone_review_comments = [c for c in review_comments if not c.review_id or c.review_id not in review_ids_with_body]

    lines = []
    comment_number = 1  # Sequential numbering across all sections

    # Format standalone review comments section (not grouped under a general review)
    if standalone_review_comments:
        count = len(standalone_review_comments)
        plural = "s" if count > 1 else ""
        lines.extend([
            "=" * 80,
            f"📝 PR Review Comments ({count} unresolved comment{plural})",
            "=" * 80,
            ""
        ])

        for comment in standalone_review_comments:
            lines.append(f"Comment #{comment_number} [ID: {comment.id}]")
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
            comment_number += 1

    # Format timeline comments section
    if timeline_comments:
        count = len(timeline_comments)
        plural = "s" if count > 1 else ""
        lines.extend([
            "=" * 80,
            f"💬 PR Discussion Comments ({count} comment{plural})",
            "=" * 80,
            ""
        ])

        for comment in timeline_comments:
            lines.append(f"Comment #{comment_number} [ID: {comment.id}]")
            lines.append(f"Author: {comment.author}")
            lines.append(f"Created: {comment.created_at}")
            lines.append("")
            lines.append(comment.body)
            lines.append("")
            lines.append("-" * 80)
            lines.append("")
            comment_number += 1

    # Format review body comments section (general review comments with their line comments)
    if review_body_comments:
        count = len(review_body_comments)
        plural = "s" if count > 1 else ""
        lines.extend([
            "=" * 80,
            f"📋 General Review Comments ({count} comment{plural})",
            "=" * 80,
            ""
        ])

        for review_body in review_body_comments:
            lines.append(f"Comment #{comment_number} [ID: {review_body.id}]")
            lines.append(f"Author: {review_body.author}")
            lines.append(f"Created: {review_body.created_at}")
            lines.append("")
            lines.append(review_body.body)
            lines.append("")

            # Find and display line-specific comments under this review
            related_comments = [c for c in review_comments if c.review_id == review_body.review_id]
            if related_comments:
                lines.append("**Related line-specific comments:**")
                lines.append("")
                for related in related_comments:
                    location = f"{related.path}"
                    if related.line:
                        location += f" (line {related.line})"
                    lines.append(f"  - {location}")
                    lines.append(f"    {related.body}")
                    lines.append("")

            lines.append("-" * 80)
            lines.append("")
            comment_number += 1

    return "\n".join(lines)


def parse_comment_processing_results(agent_response: str) -> dict:
    """Parse comment processing results from agent response.

    Extracts the "Comment Processing Summary" section from agent response and parses
    processed and skipped comments.

    Args:
        agent_response: The agent's response text

    Returns:
        Dictionary with structure:
        {
            "processed": [{"id": "123", "description": "Fixed issue"}],
            "skipped": [{"id": "456", "reason": "Not applicable"}]
        }
    """
    import re

    result = {
        "processed": [],
        "skipped": []
    }

    lines = agent_response.split('\n')
    current_section = None

    for line in lines:
        line = line.strip()

        # Detect section headers
        if "### Processed Comments" in line or "###Processed Comments" in line:
            current_section = "processed"
            continue
        elif "### Skipped Comments" in line or "###Skipped Comments" in line:
            current_section = "skipped"
            continue
        elif line.startswith("###") or line.startswith("##"):
            # New section that's not processed/skipped, exit parsing
            current_section = None
            continue

        # Parse comment lines in format: - [#ID] Description/Reason
        # Support multiple bullet formats: -, *, •, or no bullet
        if current_section and (line.startswith("-") or line.startswith("*") or line.startswith("•") or line.startswith("[")):
            # Extract ID and content using regex - flexible bullet pattern
            # Support both numeric IDs (123) and GitHub-style IDs (IC_kwDOQCpNoM7h2rLv)
            match = re.match(r'[-*•]?\s*\[#([^\]]+)\]\s*(.+)', line)
            if match:
                comment_id = match.group(1)
                content = match.group(2).strip()

                if current_section == "processed":
                    result["processed"].append({
                        "id": comment_id,
                        "description": content
                    })
                elif current_section == "skipped":
                    result["skipped"].append({
                        "id": comment_id,
                        "reason": content
                    })

    return result


def get_pr_timeline_comments(pr_number: int) -> List[PRComment]:
    """Get timeline comments from a Pull Request.

    Timeline comments are general discussion comments not tied to specific code lines.

    Args:
        pr_number: PR number

    Returns:
        List of PRComment objects with comment_type="timeline"

    Raises:
        ValueError: If PR not found or invalid
        GitHubError: If failed to get PR comments
    """
    try:
        # Use gh pr view to fetch timeline comments
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "comments"],
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            if "not found" in result.stderr.lower() or "could not resolve" in result.stderr.lower():
                raise ValueError(f"PR #{pr_number} not found")
            raise GitHubError(f"Failed to get PR timeline comments: {result.stderr}")

        pr_data = json.loads(result.stdout)
        timeline_comments_data = pr_data.get("comments", [])

        comments = []
        for comment_data in timeline_comments_data:
            # Use id field (always present in GitHub API)
            comment_id = comment_data.get("id", "")
            author_data = comment_data.get("author", {})
            author = author_data.get("login", "unknown") if author_data else "unknown"

            comments.append(PRComment(
                id=comment_id,
                body=comment_data.get("body", ""),
                author=author,
                created_at=comment_data.get("createdAt", ""),
                comment_type="timeline",
            ))

        return comments

    except json.JSONDecodeError as e:
        raise GitHubError(f"Failed to parse PR timeline comments: {e}") from e
    except Exception as e:
        if isinstance(e, (ValueError, GitHubError)):
            raise
        raise GitHubError(f"Unexpected error getting PR timeline comments: {e}") from e


def get_pr_review_body_comments(pr_number: int) -> List[PRComment]:
    """Get review body comments from a Pull Request.

    Review body comments are summary comments written at the top when submitting
    a review, which have a body but are not bound to a specific line of code.

    Args:
        pr_number: PR number

    Returns:
        List of PRComment objects with comment_type="review_body"

    Raises:
        ValueError: If PR not found or invalid
        GitHubError: If failed to get PR review body comments
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

        # Use GraphQL API to get reviews with body field and their comments
        graphql_query = """
        query($owner: String!, $name: String!, $number: Int!) {
          repository(owner: $owner, name: $name) {
            pullRequest(number: $number) {
              reviews(first: 100) {
                nodes {
                  id
                  databaseId
                  body
                  author {
                    login
                  }
                  createdAt
                  state
                  comments(first: 100) {
                    nodes {
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
            raise GitHubError(f"Failed to get PR review body comments: {result.stderr}")

        response_data = json.loads(result.stdout)

        # Check for GraphQL errors
        if "errors" in response_data:
            error_messages = [e.get("message", str(e)) for e in response_data["errors"]]
            raise GitHubError(f"GraphQL errors: {', '.join(error_messages)}")

        # Extract reviews
        pr_data = response_data.get("data", {}).get("repository", {}).get("pullRequest")
        if not pr_data:
            raise ValueError(f"PR #{pr_number} not found")

        reviews = pr_data.get("reviews", {}).get("nodes", [])

        comments = []
        for review in reviews:
            # Only include reviews with non-empty body
            body = review.get("body", "").strip()
            if not body:
                continue

            # Use databaseId (integer) for compatibility with existing code
            review_id = str(review.get("databaseId", ""))

            # Add review body comment
            comments.append(PRComment(
                id=review_id,
                body=body,
                author=review.get("author", {}).get("login", "unknown") if review.get("author") else "unknown",
                created_at=review.get("createdAt", ""),
                comment_type="review_body",
                review_id=review_id,
            ))

            # Add line-specific comments under this review
            review_comments = review.get("comments", {}).get("nodes", [])
            for comment in review_comments:
                comment_id = str(comment.get("databaseId", ""))
                comments.append(PRComment(
                    id=comment_id,
                    body=comment.get("body", ""),
                    author=comment.get("author", {}).get("login", "unknown") if comment.get("author") else "unknown",
                    created_at=comment.get("createdAt", ""),
                    path=comment.get("path"),
                    line=comment.get("line"),
                    comment_type="review",
                    review_id=review_id,
                ))

        return comments

    except json.JSONDecodeError as e:
        raise GitHubError(f"Failed to parse PR review body comments: {e}") from e
    except Exception as e:
        if isinstance(e, (ValueError, GitHubError)):
            raise
        raise GitHubError(f"Unexpected error getting PR review body comments: {e}") from e


def get_all_pr_comments(pr_number: int) -> List[PRComment]:
    """Get all PR comments (review comments, timeline comments, and review body comments).

    Combines review comments from code review threads, general timeline comments,
    and review body comments (summary comments from reviews).
    Continues gracefully if one type fails, as long as at least one type succeeds.

    Args:
        pr_number: PR number

    Returns:
        List of PRComment objects (review, timeline, and review_body types)

    Raises:
        ValueError: If PR not found or invalid
        GitHubError: If failed to get PR comments
    """
    comments = []
    errors = []

    # Get review comments (code review comments on specific lines)
    try:
        review_comments = get_pr_comments(pr_number)
        comments.extend(review_comments)
    except (ValueError, GitHubError) as e:
        # If review comments fail, log but continue to get timeline comments
        errors.append(f"Failed to get review comments: {e}")

    # Get timeline comments (general discussion comments)
    try:
        timeline_comments = get_pr_timeline_comments(pr_number)
        comments.extend(timeline_comments)
    except (ValueError, GitHubError) as e:
        # If timeline comments fail, log but continue
        errors.append(f"Failed to get timeline comments: {e}")

    # Get review body comments (summary comments from reviews)
    try:
        review_body_comments = get_pr_review_body_comments(pr_number)
        comments.extend(review_body_comments)
    except (ValueError, GitHubError) as e:
        # If review body comments fail, log but continue
        errors.append(f"Failed to get review body comments: {e}")

    # If all failed, raise error
    if not comments:
        error_msg = "; ".join(errors)
        raise GitHubError(f"Failed to get any comments for PR #{pr_number}: {error_msg}")

    # Deduplicate comments by ID, preferring those with review_id (from get_pr_review_body_comments)
    # because they have the review context
    comments_by_id = {}
    for comment in comments:
        if comment.id in comments_by_id:
            # Keep the one with review_id if available
            if comment.review_id and not comments_by_id[comment.id].review_id:
                comments_by_id[comment.id] = comment
        else:
            comments_by_id[comment.id] = comment

    return list(comments_by_id.values())


def get_processed_comment_ids_from_history(phase_dir: "Path") -> set:
    """Get all processed and skipped comment IDs from previous iterations' history.

    Loads context.json from all iteration_XXX directories and collects comment IDs
    from pr_comments_processed and pr_comments_skipped fields.

    Args:
        phase_dir: Path to the phase directory (e.g., .cafe/issues/issue123/develop)

    Returns:
        Set of comment IDs that have been processed or skipped in previous iterations
    """
    from pathlib import Path
    import json

    processed_ids = set()

    if not phase_dir.exists():
        return processed_ids

    # Find all iteration_XXX directories
    for item in phase_dir.iterdir():
        if not item.is_dir() or not item.name.startswith("iteration_"):
            continue

        context_file = item / "context.json"
        if not context_file.exists():
            continue

        try:
            with open(context_file, "r", encoding="utf-8") as f:
                context_data = json.load(f)

            # Collect IDs from pr_comments_processed
            processed = context_data.get("pr_comments_processed", [])
            for comment in processed:
                if "id" in comment:
                    processed_ids.add(comment["id"])

            # Collect IDs from pr_comments_skipped
            skipped = context_data.get("pr_comments_skipped", [])
            for comment in skipped:
                if "id" in comment:
                    processed_ids.add(comment["id"])

        except (json.JSONDecodeError, KeyError, TypeError):
            # Skip files with invalid JSON or unexpected structure
            continue

    return processed_ids
