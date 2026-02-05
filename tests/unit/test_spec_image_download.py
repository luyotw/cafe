"""Test spec phase image download functionality"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, Mock

from cafe.phases.spec_phase import SpecPhase
from cafe.core.types import PhaseResult, PhaseStatus


@pytest.fixture
def mock_dependencies():
    """Create mock dependencies for SpecPhase"""
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
    """Create a SpecPhase instance for testing"""
    phase = SpecPhase(
        agent_manager=mock_dependencies["agent_manager"],
        permission_handler=mock_dependencies["permission_handler"],
        git_ops=mock_dependencies["git_ops"],
        pm_agent="Roger",
        interactive=False,
        issue_name="test-issue",
    )

    # Set up phase directory and issue directory
    phase.issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
    phase.phase_dir = phase.issue_dir / "spec"
    phase.phase_dir.mkdir(parents=True, exist_ok=True)

    # Set up iteration
    phase.iteration = 1
    iteration_dir = phase.phase_dir / "iteration_001"
    iteration_dir.mkdir(parents=True, exist_ok=True)
    phase.spec_file = str(iteration_dir / "output.md")

    return phase


class TestFetchGitHubIssueWithImages:
    """Test _fetch_github_issue image download functionality"""

    @patch("cafe.phases.spec_phase.get_github_repo_name")
    @patch("cafe.phases.spec_phase.GitHubOps")
    @patch("cafe.phases.spec_phase.fetch_github_issue")
    def test_download_images_success(self, mock_fetch, mock_gh_ops_cls, mock_get_repo, spec_phase):
        """Test successfully downloading images"""
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
    def test_no_download_when_no_images(self, mock_fetch, mock_gh_ops_cls, mock_get_repo, spec_phase):
        """Test no download call when there are no images"""
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
    def test_partial_download_failure(self, mock_fetch, mock_gh_ops_cls, mock_get_repo, spec_phase):
        """Test continues processing when some images fail to download"""
        # Setup
        mock_get_repo.return_value = "test/repo"
        mock_fetch.return_value = (
            "# Test Issue\n\nWith images",
            ["https://example.com/img1.png", "https://example.com/img2.png", "https://example.com/img3.png"]
        )

        mock_gh_ops = MagicMock()
        mock_gh_ops_cls.return_value = mock_gh_ops

        # Only 2 succeeded
        saved_paths = [
            spec_phase.phase_dir / "images" / "image_001.png",
            spec_phase.phase_dir / "images" / "image_003.png"
        ]
        mock_gh_ops.download_issue_images.return_value = saved_paths

        # Execute
        result = spec_phase._fetch_github_issue(123)

        # Verify - should not fail
        assert result is None

    @patch("cafe.phases.spec_phase.get_github_repo_name")
    @patch("cafe.phases.spec_phase.GitHubOps")
    @patch("cafe.phases.spec_phase.fetch_github_issue")
    def test_download_exception_does_not_interrupt_main_flow(self, mock_fetch, mock_gh_ops_cls, mock_get_repo, spec_phase):
        """Test image download exception does not interrupt main flow"""
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

        # Verify - should succeed, just warn about image download failure
        assert result is None  # Success
        # Spec file should still be written
        assert Path(spec_phase.spec_file).exists()
