"""Test image URL extraction and download functionality"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, call
from cafe.utils.github import GitHubOps, GitHubError


class TestExtractImageUrls:
    """Test extract_image_urls static method"""

    def test_parse_standard_markdown_image_format(self) -> None:
        """Test parsing standard Markdown image syntax ![alt](url)"""
        body = """
        Here is a screenshot:
        ![screenshot](https://example.com/image.png)
        And another one:
        ![demo](https://example.com/demo.jpg)
        """
        urls = GitHubOps.extract_image_urls(body)
        assert len(urls) == 2
        assert "https://example.com/image.png" in urls
        assert "https://example.com/demo.jpg" in urls

    def test_parse_github_user_attachments_format(self) -> None:
        """Test parsing GitHub user-attachments format (no file extension)"""
        body = """
        ![image](https://github.com/user-attachments/assets/abc123-def456)
        """
        urls = GitHubOps.extract_image_urls(body)
        assert len(urls) == 1
        assert "https://github.com/user-attachments/assets/abc123-def456" in urls

    def test_returns_empty_list_when_no_images(self) -> None:
        """Test returns empty list when Markdown has no images"""
        body = "Just some text without images"
        urls = GitHubOps.extract_image_urls(body)
        assert urls == []

    def test_parse_mixed_content(self) -> None:
        """Test parsing mixed standard format and GitHub assets format"""
        body = """
        # Title
        ![standard](https://example.com/image.png)
        Some text
        ![github-asset](https://github.com/user-attachments/assets/xyz789)
        ![another](https://test.com/photo.jpeg?size=large)
        """
        urls = GitHubOps.extract_image_urls(body)
        assert len(urls) == 3
        assert "https://example.com/image.png" in urls
        assert "https://github.com/user-attachments/assets/xyz789" in urls
        assert "https://test.com/photo.jpeg?size=large" in urls

    def test_supports_multiple_image_extensions(self) -> None:
        """Test support for png, jpg, jpeg, gif, webp, svg extensions"""
        body = """
        ![png](https://example.com/1.png)
        ![jpg](https://example.com/2.jpg)
        ![jpeg](https://example.com/3.jpeg)
        ![gif](https://example.com/4.gif)
        ![webp](https://example.com/5.webp)
        ![svg](https://example.com/6.svg)
        """
        urls = GitHubOps.extract_image_urls(body)
        assert len(urls) == 6

    def test_ignores_non_image_links(self) -> None:
        """Test ignores non-image Markdown links"""
        body = """
        [Link to docs](https://example.com/docs.html)
        ![image](https://example.com/image.png)
        [Another link](https://example.com/page)
        """
        urls = GitHubOps.extract_image_urls(body)
        assert len(urls) == 1
        assert urls[0] == "https://example.com/image.png"


class TestDownloadIssueImages:
    """Test download_issue_images method"""

    @patch("subprocess.run")
    def test_download_single_image_success(self, mock_run: Mock, tmp_path: Path) -> None:
        """Test successfully downloading a single image"""
        mock_run.return_value = Mock(returncode=0, stdout=b"fake image data")

        gh_ops = GitHubOps()
        image_urls = ["https://example.com/image.png"]
        save_dir = tmp_path / "images"

        saved_paths = gh_ops.download_issue_images(image_urls, save_dir)

        assert len(saved_paths) == 1
        assert saved_paths[0].parent == save_dir
        assert saved_paths[0].name == "image.png"
        assert save_dir.exists()

        # Verify gh api was called correctly (second call, first is gh --version)
        assert mock_run.call_count == 2
        gh_call = mock_run.call_args_list[1]
        call_args = gh_call[0][0]
        assert "gh" in call_args
        assert "api" in call_args
        assert "https://example.com/image.png" in call_args

    @patch("subprocess.run")
    def test_download_multiple_images_success(self, mock_run: Mock, tmp_path: Path) -> None:
        """Test successfully downloading multiple images with correct naming"""
        mock_run.return_value = Mock(returncode=0, stdout=b"fake image data")

        gh_ops = GitHubOps()
        image_urls = [
            "https://example.com/image1.jpg",
            "https://example.com/image2.png",
            "https://github.com/user-attachments/assets/abc123",
        ]
        save_dir = tmp_path / "images"

        saved_paths = gh_ops.download_issue_images(image_urls, save_dir)

        assert len(saved_paths) == 3
        assert saved_paths[0].name == "image1.jpg"
        assert saved_paths[1].name == "image2.png"
        assert saved_paths[2].name == "abc123.png"  # Default to png for no extension
        # 1 gh --version + 3 gh api calls
        assert mock_run.call_count == 4

    @patch("subprocess.run")
    def test_continues_on_download_failure(self, mock_run: Mock, tmp_path: Path) -> None:
        """Test continues processing other images when one fails"""
        # gh --version + first success, second fails, third success
        mock_run.side_effect = [
            Mock(returncode=0),  # gh --version
            Mock(returncode=0, stdout=b"fake image 1"),  # gh api 1
            Mock(returncode=1, stderr="Failed to download"),  # gh api 2 fails
            Mock(returncode=0, stdout=b"fake image 3"),  # gh api 3
        ]

        gh_ops = GitHubOps()
        image_urls = [
            "https://example.com/ok1.png",
            "https://example.com/fail.png",
            "https://example.com/ok2.png",
        ]
        save_dir = tmp_path / "images"

        saved_paths = gh_ops.download_issue_images(image_urls, save_dir)

        # Only returns successfully downloaded paths
        assert len(saved_paths) == 2
        assert saved_paths[0].name == "ok1.png"
        assert saved_paths[1].name == "ok2.png"

    @patch("subprocess.run")
    def test_creates_directory_automatically(self, mock_run: Mock, tmp_path: Path) -> None:
        """Test automatically creates directory when it doesn't exist"""
        mock_run.return_value = Mock(returncode=0, stdout=b"fake image data")

        gh_ops = GitHubOps()
        image_urls = ["https://example.com/image.png"]
        save_dir = tmp_path / "non" / "existent" / "path"

        assert not save_dir.exists()

        saved_paths = gh_ops.download_issue_images(image_urls, save_dir)

        assert save_dir.exists()
        assert len(saved_paths) == 1

    @patch("subprocess.run")
    def test_returns_empty_list_for_empty_input(self, mock_run: Mock, tmp_path: Path) -> None:
        """Test returns empty list when given empty URL list"""
        mock_run.return_value = Mock(returncode=0)

        gh_ops = GitHubOps()
        image_urls = []
        save_dir = tmp_path / "images"

        saved_paths = gh_ops.download_issue_images(image_urls, save_dir)

        assert saved_paths == []
        # Only gh --version was called, no gh api
        assert mock_run.call_count == 1

    @patch("subprocess.run")
    def test_parses_file_extensions(self, mock_run: Mock, tmp_path: Path) -> None:
        """Test correctly parses different image file extensions"""
        mock_run.return_value = Mock(returncode=0, stdout=b"fake image data")

        gh_ops = GitHubOps()
        image_urls = [
            "https://example.com/photo.JPG",  # uppercase
            "https://example.com/image.png?size=large",  # with query params
            "https://github.com/user-attachments/assets/xyz",  # no extension
        ]
        save_dir = tmp_path / "images"

        saved_paths = gh_ops.download_issue_images(image_urls, save_dir)

        assert saved_paths[0].name == "photo.JPG"  # preserves original filename
        assert saved_paths[1].name == "image.png"
        assert saved_paths[2].name == "xyz.png"  # defaults to png
