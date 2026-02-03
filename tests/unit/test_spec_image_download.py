"""測試 spec phase 圖片下載功能"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, Mock

from cafe.phases.spec_phase import SpecPhase
from cafe.core.types import PhaseResult, PhaseStatus


@pytest.fixture
def mock_dependencies():
    """建立 SpecPhase 所需的 mock dependencies"""
    mock_agent_manager = MagicMock()
    mock_permission_handler = MagicMock()
    mock_git_ops = MagicMock()
    mock_git_ops.get_current_branch.return_value = "test-branch"

    return {
        "agent_manager": mock_agent_manager,
        "permission_handler": mock_permission_handler,
        "git_ops": mock_git_ops,
    }


@pytest.fixture
def spec_phase(tmp_path, mock_dependencies):
    """建立測試用的 SpecPhase instance"""
    phase = SpecPhase(
        agent_manager=mock_dependencies["agent_manager"],
        permission_handler=mock_dependencies["permission_handler"],
        git_ops=mock_dependencies["git_ops"],
        pm_agent="Roger",
        interactive=False,
        issue_name="test-issue",
    )

    # 設定 phase directory 和 issue directory
    phase.issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
    phase.phase_dir = phase.issue_dir / "spec"
    phase.phase_dir.mkdir(parents=True, exist_ok=True)

    # 設定 iteration
    phase.iteration = 1
    iteration_dir = phase.phase_dir / "iteration_001"
    iteration_dir.mkdir(parents=True, exist_ok=True)
    phase.spec_file = str(iteration_dir / "output.md")

    return phase


class TestFetchGitHubIssueWithImages:
    """測試 _fetch_github_issue 下載圖片功能"""

    @patch("cafe.phases.spec_phase.get_github_repo_name")
    @patch("cafe.phases.spec_phase.GitHubOps")
    @patch("cafe.phases.spec_phase.fetch_github_issue")
    def test_下載圖片成功(self, mock_fetch, mock_gh_ops_cls, mock_get_repo, spec_phase):
        """測試成功下載圖片"""
        # Setup
        mock_get_repo.return_value = "test/repo"
        mock_fetch.return_value = (
            "# Test Issue\n\nWith image ![img](https://example.com/image.png)",
            ["https://example.com/image.png"]
        )

        mock_gh_ops = MagicMock()
        mock_gh_ops_cls.return_value = mock_gh_ops

        saved_path = spec_phase.phase_dir / "images" / "image_001.png"
        mock_gh_ops.download_issue_images.return_value = [saved_path]

        # Execute
        result = spec_phase._fetch_github_issue(123)

        # Verify
        assert result is None  # Success
        mock_gh_ops.download_issue_images.assert_called_once()
        call_args = mock_gh_ops.download_issue_images.call_args
        assert call_args[0][0] == ["https://example.com/image.png"]
        assert call_args[0][1] == spec_phase.phase_dir / "images"

    @patch("cafe.phases.spec_phase.get_github_repo_name")
    @patch("cafe.phases.spec_phase.GitHubOps")
    @patch("cafe.phases.spec_phase.fetch_github_issue")
    def test_無圖片時不下載(self, mock_fetch, mock_gh_ops_cls, mock_get_repo, spec_phase):
        """測試無圖片時不呼叫下載"""
        # Setup
        mock_get_repo.return_value = "test/repo"
        mock_fetch.return_value = (
            "# Test Issue\n\nNo images here",
            []
        )

        mock_gh_ops = MagicMock()
        mock_gh_ops_cls.return_value = mock_gh_ops

        # Execute
        result = spec_phase._fetch_github_issue(123)

        # Verify
        assert result is None  # Success
        mock_gh_ops.download_issue_images.assert_not_called()

    @patch("cafe.phases.spec_phase.get_github_repo_name")
    @patch("cafe.phases.spec_phase.GitHubOps")
    @patch("cafe.phases.spec_phase.fetch_github_issue")
    def test_部分圖片下載失敗(self, mock_fetch, mock_gh_ops_cls, mock_get_repo, spec_phase):
        """測試部分圖片下載失敗時繼續處理"""
        # Setup
        mock_get_repo.return_value = "test/repo"
        mock_fetch.return_value = (
            "# Test Issue\n\nWith images",
            ["https://example.com/img1.png", "https://example.com/img2.png", "https://example.com/img3.png"]
        )

        mock_gh_ops = MagicMock()
        mock_gh_ops_cls.return_value = mock_gh_ops

        # 只有 2 張成功
        saved_paths = [
            spec_phase.phase_dir / "images" / "image_001.png",
            spec_phase.phase_dir / "images" / "image_003.png"
        ]
        mock_gh_ops.download_issue_images.return_value = saved_paths

        # Execute
        result = spec_phase._fetch_github_issue(123)

        # Verify - 不應該失敗
        assert result is None

    @patch("cafe.phases.spec_phase.get_github_repo_name")
    @patch("cafe.phases.spec_phase.GitHubOps")
    @patch("cafe.phases.spec_phase.fetch_github_issue")
    def test_圖片下載異常不影響主流程(self, mock_fetch, mock_gh_ops_cls, mock_get_repo, spec_phase):
        """測試圖片下載拋出異常時不中斷主流程"""
        # Setup
        mock_get_repo.return_value = "test/repo"
        mock_fetch.return_value = (
            "# Test Issue\n\nWith image",
            ["https://example.com/image.png"]
        )

        mock_gh_ops = MagicMock()
        mock_gh_ops_cls.return_value = mock_gh_ops
        mock_gh_ops.download_issue_images.side_effect = Exception("Network error")

        # Execute
        result = spec_phase._fetch_github_issue(123)

        # Verify - 應該成功，只是警告圖片下載失敗
        assert result is None  # Success
        # Spec file 應該仍然被寫入
        assert Path(spec_phase.spec_file).exists()
