"""Test PR comment image extraction and integration"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from cafe.utils.github import GitHubOps, PRComment
from cafe.phases.pr_phase import PRPhase


class TestPRCommentImageExtraction:
    """Test image URL extraction from PR comment bodies"""

    def test_extract_image_from_single_pr_comment(self) -> None:
        """Test extracting image URL from a single PR comment body"""
        comment = PRComment(
            id="123",
            body="Look at this bug:\n![screenshot](https://example.com/bug.png)",
            author="reviewer",
            created_at="2024-01-01T00:00:00Z",
        )

        urls = GitHubOps.extract_image_urls(comment.body)
        assert len(urls) == 1
        assert "https://example.com/bug.png" in urls

    def test_extract_multiple_images_from_pr_comment(self) -> None:
        """Test extracting multiple image URLs from a single PR comment"""
        comment = PRComment(
            id="456",
            body="""
            Here are the issues:
            ![before](https://example.com/before.png)
            After the fix:
            ![after](https://example.com/after.jpg)
            """,
            author="reviewer",
            created_at="2024-01-01T00:00:00Z",
        )

        urls = GitHubOps.extract_image_urls(comment.body)
        assert len(urls) == 2
        assert "https://example.com/before.png" in urls
        assert "https://example.com/after.jpg" in urls

    def test_extract_images_from_multiple_comments(self) -> None:
        """Test collecting image URLs from multiple PR comments"""
        comments = [
            PRComment(
                id="1",
                body="![img1](https://example.com/img1.png)",
                author="user1",
                created_at="2024-01-01T00:00:00Z",
            ),
            PRComment(
                id="2",
                body="![img2](https://example.com/img2.jpg)",
                author="user2",
                created_at="2024-01-01T00:00:00Z",
            ),
            PRComment(
                id="3",
                body="No images here",
                author="user3",
                created_at="2024-01-01T00:00:00Z",
            ),
        ]

        all_urls = []
        for comment in comments:
            urls = GitHubOps.extract_image_urls(comment.body)
            all_urls.extend(urls)

        assert len(all_urls) == 2
        assert "https://example.com/img1.png" in all_urls
        assert "https://example.com/img2.jpg" in all_urls

    def test_deduplicate_same_image_url(self) -> None:
        """Test deduplication when same image URL appears multiple times"""
        comments = [
            PRComment(
                id="1",
                body="![screenshot](https://example.com/same.png)",
                author="user1",
                created_at="2024-01-01T00:00:00Z",
            ),
            PRComment(
                id="2",
                body="Same issue here: ![screenshot](https://example.com/same.png)",
                author="user2",
                created_at="2024-01-01T00:00:00Z",
            ),
        ]

        all_urls = []
        for comment in comments:
            urls = GitHubOps.extract_image_urls(comment.body)
            all_urls.extend(urls)

        # Deduplicate using set
        unique_urls = list(set(all_urls))

        assert len(unique_urls) == 1
        assert "https://example.com/same.png" in unique_urls

    def test_extract_github_user_attachments_from_pr_comment(self) -> None:
        """Test extracting GitHub user-attachments format from PR comment"""
        comment = PRComment(
            id="789",
            body="![image](https://github.com/user-attachments/assets/abc123-def456)",
            author="reviewer",
            created_at="2024-01-01T00:00:00Z",
        )

        urls = GitHubOps.extract_image_urls(comment.body)
        assert len(urls) == 1
        assert "https://github.com/user-attachments/assets/abc123-def456" in urls

    def test_no_images_in_pr_comment(self) -> None:
        """Test PR comment with no images returns empty list"""
        comment = PRComment(
            id="999",
            body="Just a regular text comment without any images",
            author="reviewer",
            created_at="2024-01-01T00:00:00Z",
        )

        urls = GitHubOps.extract_image_urls(comment.body)
        assert urls == []
