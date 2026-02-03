"""測試圖片 URL 解析與下載功能"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, call
from cafe.utils.github import GitHubOps, GitHubError


class TestExtractImageUrls:
    """測試 extract_image_urls 靜態方法"""

    def test_解析標準Markdown圖片格式(self) -> None:
        """測試解析標準 Markdown 圖片語法 ![alt](url)"""
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

    def test_解析GitHub_user_attachments格式(self) -> None:
        """測試解析 GitHub user-attachments 格式（不帶副檔名）"""
        body = """
        ![image](https://github.com/user-attachments/assets/abc123-def456)
        """
        urls = GitHubOps.extract_image_urls(body)
        assert len(urls) == 1
        assert "https://github.com/user-attachments/assets/abc123-def456" in urls

    def test_無圖片時回傳空列表(self) -> None:
        """測試當 Markdown 中無圖片時回傳空列表"""
        body = "Just some text without images"
        urls = GitHubOps.extract_image_urls(body)
        assert urls == []

    def test_混合內容解析(self) -> None:
        """測試混合標準格式與 GitHub assets 格式"""
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

    def test_支援多種圖片副檔名(self) -> None:
        """測試支援 png, jpg, jpeg, gif, webp, svg 等副檔名"""
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

    def test_忽略非圖片連結(self) -> None:
        """測試忽略非圖片的 Markdown 連結"""
        body = """
        [Link to docs](https://example.com/docs.html)
        ![image](https://example.com/image.png)
        [Another link](https://example.com/page)
        """
        urls = GitHubOps.extract_image_urls(body)
        assert len(urls) == 1
        assert urls[0] == "https://example.com/image.png"


class TestDownloadIssueImages:
    """測試 download_issue_images 方法"""

    @patch("subprocess.run")
    def test_成功下載單張圖片(self, mock_run: Mock, tmp_path: Path) -> None:
        """測試成功下載單張圖片"""
        mock_run.return_value = Mock(returncode=0)

        gh_ops = GitHubOps()
        image_urls = ["https://example.com/image.png"]
        save_dir = tmp_path / "images"

        saved_paths = gh_ops.download_issue_images(image_urls, save_dir)

        assert len(saved_paths) == 1
        assert saved_paths[0].parent == save_dir
        assert saved_paths[0].name == "image_001.png"
        assert save_dir.exists()

        # 驗證 curl 被正確呼叫（第二次呼叫，第一次是 gh --version）
        assert mock_run.call_count == 2
        curl_call = mock_run.call_args_list[1]
        call_args = curl_call[0][0]
        assert "curl" in call_args
        assert "-L" in call_args
        assert "-o" in call_args
        assert "https://example.com/image.png" in call_args

    @patch("subprocess.run")
    def test_成功下載多張圖片(self, mock_run: Mock, tmp_path: Path) -> None:
        """測試成功下載多張圖片並正確命名"""
        mock_run.return_value = Mock(returncode=0)

        gh_ops = GitHubOps()
        image_urls = [
            "https://example.com/image1.jpg",
            "https://example.com/image2.png",
            "https://github.com/user-attachments/assets/abc123",
        ]
        save_dir = tmp_path / "images"

        saved_paths = gh_ops.download_issue_images(image_urls, save_dir)

        assert len(saved_paths) == 3
        assert saved_paths[0].name == "image_001.jpg"
        assert saved_paths[1].name == "image_002.png"
        assert saved_paths[2].name == "image_003.png"  # 無副檔名預設為 png
        # 1 次 gh --version + 3 次 curl
        assert mock_run.call_count == 4

    @patch("subprocess.run")
    def test_下載失敗繼續處理其他圖片(self, mock_run: Mock, tmp_path: Path) -> None:
        """測試某張圖片下載失敗時，繼續處理其他圖片"""
        # gh --version + 第一張成功，第二張失敗，第三張成功
        mock_run.side_effect = [
            Mock(returncode=0),  # gh --version
            Mock(returncode=0),  # curl 1
            Mock(returncode=1, stderr="Failed to download"),  # curl 2 失敗
            Mock(returncode=0),  # curl 3
        ]

        gh_ops = GitHubOps()
        image_urls = [
            "https://example.com/ok1.png",
            "https://example.com/fail.png",
            "https://example.com/ok2.png",
        ]
        save_dir = tmp_path / "images"

        saved_paths = gh_ops.download_issue_images(image_urls, save_dir)

        # 只回傳成功下載的路徑
        assert len(saved_paths) == 2
        assert saved_paths[0].name == "image_001.png"
        assert saved_paths[1].name == "image_003.png"

    @patch("subprocess.run")
    def test_自動建立目錄(self, mock_run: Mock, tmp_path: Path) -> None:
        """測試當目錄不存在時自動建立"""
        mock_run.return_value = Mock(returncode=0)

        gh_ops = GitHubOps()
        image_urls = ["https://example.com/image.png"]
        save_dir = tmp_path / "non" / "existent" / "path"

        assert not save_dir.exists()

        saved_paths = gh_ops.download_issue_images(image_urls, save_dir)

        assert save_dir.exists()
        assert len(saved_paths) == 1

    @patch("subprocess.run")
    def test_空列表時回傳空列表(self, mock_run: Mock, tmp_path: Path) -> None:
        """測試傳入空的 URL 列表時回傳空列表"""
        mock_run.return_value = Mock(returncode=0)

        gh_ops = GitHubOps()
        image_urls = []
        save_dir = tmp_path / "images"

        saved_paths = gh_ops.download_issue_images(image_urls, save_dir)

        assert saved_paths == []
        # 只有 gh --version 被呼叫，沒有 curl
        assert mock_run.call_count == 1

    @patch("subprocess.run")
    def test_解析副檔名(self, mock_run: Mock, tmp_path: Path) -> None:
        """測試正確解析不同格式的圖片副檔名"""
        mock_run.return_value = Mock(returncode=0)

        gh_ops = GitHubOps()
        image_urls = [
            "https://example.com/photo.JPG",  # 大寫
            "https://example.com/image.png?size=large",  # 帶參數
            "https://github.com/user-attachments/assets/xyz",  # 無副檔名
        ]
        save_dir = tmp_path / "images"

        saved_paths = gh_ops.download_issue_images(image_urls, save_dir)

        assert saved_paths[0].name == "image_001.jpg"  # 轉小寫
        assert saved_paths[1].name == "image_002.png"
        assert saved_paths[2].name == "image_003.png"  # 預設 png
