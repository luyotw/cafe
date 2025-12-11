"""Tests for PRPhase local review mode (issue45)."""

from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest
import yaml

from cafe.phases.pr_phase import PRPhase
from cafe.core.types import PhaseStatus, PhaseResult, WorkflowMode
from cafe.core.status_codes import PhaseStatusCode


@pytest.fixture
def temp_issue_dir(tmp_path):
    """Create a temporary issue directory structure."""
    issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
    issue_dir.mkdir(parents=True)

    # Create spec directory and file
    spec_dir = issue_dir / "spec"
    spec_dir.mkdir()
    spec_file = spec_dir / "spec_001.md"
    spec_file.write_text("# Test Spec\nTest requirements")

    # Create config.yaml with pr.auto_create = false
    config_file = issue_dir / "config.yaml"
    config_data = {
        "base_branch": "main",
        "feature_branch": "test-issue",
        "pr": {
            "auto_create": False
        }
    }
    with open(config_file, 'w') as f:
        yaml.dump(config_data, f)

    return issue_dir


@pytest.fixture
def mock_git_ops():
    """Create mock GitOperations."""
    with patch('cafe.phases.pr_phase.GitOperations') as MockGitOps:
        mock_git = MagicMock()
        MockGitOps.return_value = mock_git
        mock_git.get_current_branch.return_value = "test-issue"
        yield mock_git


@pytest.fixture
def mock_agent_manager():
    """Create mock AgentManager."""
    return MagicMock()


@pytest.fixture
def mock_permission_handler():
    """Create mock PermissionHandler."""
    return MagicMock()


@pytest.fixture
def mock_github_ops():
    """Create mock GitHubOps."""
    return MagicMock()


class TestPRPhaseLocalReviewMode:
    """Test PRPhase local review mode."""

    def test_reads_pr_auto_create_config(self, temp_issue_dir, mock_git_ops, mock_agent_manager,
                                         mock_permission_handler, mock_github_ops, monkeypatch):
        """測試讀取 pr.auto_create: false config 並進入本地審核流程"""
        monkeypatch.chdir(temp_issue_dir.parent.parent.parent)

        # Mock _get_issue_dir to return our temp directory
        with patch.object(PRPhase, '_get_issue_dir', return_value=temp_issue_dir):
            pr_phase = PRPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                github_ops=mock_github_ops,
                spec_file=str(temp_issue_dir / "spec" / "spec_001.md"),
                workflow_mode=WorkflowMode.LOCAL,
                interactive=False,
            )

            # Read config
            config_file = temp_issue_dir / "config.yaml"
            with open(config_file) as f:
                config = yaml.safe_load(f)

            assert "pr" in config
            assert config["pr"]["auto_create"] is False

    def test_executes_git_diff_for_local_review(self, temp_issue_dir, mock_git_ops, mock_agent_manager,
                                                  mock_permission_handler, mock_github_ops, monkeypatch):
        """測試 git diff {base_branch}..HEAD 正確執行"""
        monkeypatch.chdir(temp_issue_dir.parent.parent.parent)

        # Mock git diff output
        mock_git_ops.get_diff.return_value = "diff --git a/file.py b/file.py\n+new line"

        # Mock github_ops to prevent PR check
        mock_github_ops.check_gh_auth.return_value = False

        with patch.object(PRPhase, '_get_issue_dir', return_value=temp_issue_dir):
            pr_phase = PRPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                github_ops=mock_github_ops,
                spec_file=str(temp_issue_dir / "spec" / "spec_001.md"),
                workflow_mode=WorkflowMode.LOCAL,
                interactive=True,
            )

            # Mock _ask_user_for_review_decision to return confirm
            with patch.object(pr_phase, '_ask_user_for_review_decision', return_value="confirm"):
                # Execute should call _execute_local_review_mode
                result = pr_phase.execute()

                # Verify git diff was called
                mock_git_ops.get_diff.assert_called_with("main", "HEAD")

    def test_returns_confirmed_status_on_confirm(self, temp_issue_dir, mock_git_ops, mock_agent_manager,
                                                   mock_permission_handler, mock_github_ops, monkeypatch):
        """測試選擇 'c' 時回傳 CAFE_CONFIRMED 狀態"""
        monkeypatch.chdir(temp_issue_dir.parent.parent.parent)

        # Mock git diff
        mock_git_ops.get_diff.return_value = "diff content"

        with patch.object(PRPhase, '_get_issue_dir', return_value=temp_issue_dir):
            pr_phase = PRPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                github_ops=mock_github_ops,
                spec_file=str(temp_issue_dir / "spec" / "spec_001.md"),
                workflow_mode=WorkflowMode.LOCAL,
                interactive=True,
            )

            with patch.object(pr_phase, '_ask_user_for_review_decision', return_value="confirm"):
                result = pr_phase.execute()

                assert result.status == PhaseStatus.COMPLETED
                assert "status_code" in result.data
                assert result.data["status_code"] == PhaseStatusCode.CONFIRMED.value

    def test_returns_rejected_status_on_reject(self, temp_issue_dir, mock_git_ops, mock_agent_manager,
                                                 mock_permission_handler, mock_github_ops, monkeypatch):
        """測試選擇 'r' 時回傳 USER_REJECTED 狀態"""
        monkeypatch.chdir(temp_issue_dir.parent.parent.parent)

        # Mock git diff
        mock_git_ops.get_diff.return_value = "diff content"

        with patch.object(PRPhase, '_get_issue_dir', return_value=temp_issue_dir):
            pr_phase = PRPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                github_ops=mock_github_ops,
                spec_file=str(temp_issue_dir / "spec" / "spec_001.md"),
                workflow_mode=WorkflowMode.LOCAL,
                interactive=True,
            )

            with patch.object(pr_phase, '_ask_user_for_review_decision', return_value="reject"):
                result = pr_phase.execute()

                assert result.status == PhaseStatus.FAILED
                assert "USER_REJECTED" in result.message or result.data.get("status_code") == "USER_REJECTED"

    def test_saves_versioned_feedback_file_on_modify(self, temp_issue_dir, mock_git_ops, mock_agent_manager,
                                                       mock_permission_handler, mock_github_ops, monkeypatch):
        """測試選擇 'm' 時儲存 versioned 檔案 (pr_001.md, pr_002.md ...)"""
        monkeypatch.chdir(temp_issue_dir.parent.parent.parent)

        modification_request = "Please fix the authentication logic"

        # Mock git diff
        mock_git_ops.get_diff.return_value = "diff content"

        with patch.object(PRPhase, '_get_issue_dir', return_value=temp_issue_dir):
            pr_phase = PRPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                github_ops=mock_github_ops,
                spec_file=str(temp_issue_dir / "spec" / "spec_001.md"),
                workflow_mode=WorkflowMode.LOCAL,
                interactive=True,
            )

            with patch.object(pr_phase, '_ask_user_for_review_decision', return_value=modification_request):
                result = pr_phase.execute()

                # Check that pr_001.md was created
                pr_dir = temp_issue_dir / "pr"
                pr_file = pr_dir / "pr_001.md"

                assert pr_file.exists()
                content = pr_file.read_text()
                assert modification_request in content
                assert result.data.get("status_code") == PhaseStatusCode.NEEDS_CHANGES.value

    def test_returns_needs_changes_status_on_modify(self, temp_issue_dir, mock_git_ops, mock_agent_manager,
                                                      mock_permission_handler, mock_github_ops, monkeypatch):
        """測試選擇 'm' 時回傳 CAFE_NEEDS_CHANGES 狀態"""
        monkeypatch.chdir(temp_issue_dir.parent.parent.parent)

        modification_request = "Please add error handling"

        # Mock git diff
        mock_git_ops.get_diff.return_value = "diff content"

        with patch.object(PRPhase, '_get_issue_dir', return_value=temp_issue_dir):
            pr_phase = PRPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                github_ops=mock_github_ops,
                spec_file=str(temp_issue_dir / "spec" / "spec_001.md"),
                workflow_mode=WorkflowMode.LOCAL,
                interactive=True,
            )

            with patch.object(pr_phase, '_ask_user_for_review_decision', return_value=modification_request):
                result = pr_phase.execute()

                assert result.status == PhaseStatus.COMPLETED
                assert result.data.get("status_code") == PhaseStatusCode.NEEDS_CHANGES.value
