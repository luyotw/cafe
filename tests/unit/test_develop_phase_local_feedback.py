"""Tests for DevelopPhase local PR feedback reading (issue45)."""

from pathlib import Path
from unittest.mock import MagicMock, patch, call
from datetime import datetime

import pytest
import yaml

from cafe.phases.develop_phase import DevelopPhase
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

    # Create plan directory and file
    plan_dir = issue_dir / "plan"
    plan_dir.mkdir()
    plan_file = plan_dir / "plan_001.md"
    plan_file.write_text("# Test Plan\nImplementation guide")

    # Create config.yaml with pr.auto_create = false
    config_file = issue_dir / "config.yaml"
    config_data = {
        "base_branch": "main",
        "feature_branch": "test-issue",
        "pr": {"auto_create": False},
    }
    with open(config_file, "w") as f:
        yaml.dump(config_data, f)

    return issue_dir


@pytest.fixture
def mock_git_ops():
    """Create mock GitOperations."""
    with patch("cafe.phases.develop_phase.GitOperations") as MockGitOps:
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


class TestDevelopPhaseLocalFeedback:
    """Test DevelopPhase local PR feedback reading."""

    def test_reads_latest_pr_file(
        self,
        temp_issue_dir,
        mock_git_ops,
        mock_agent_manager,
        mock_permission_handler,
        mock_github_ops,
        monkeypatch,
    ):
        """測試讀取最新 pr_XXX.md 檔案（當有多個版本時）"""
        monkeypatch.chdir(temp_issue_dir.parent.parent.parent)

        # Create multiple pr files
        pr_dir = temp_issue_dir / "pr"
        pr_dir.mkdir()
        (pr_dir / "pr_001.md").write_text("First feedback")
        (pr_dir / "pr_002.md").write_text("Second feedback")
        (pr_dir / "pr_003.md").write_text("Latest feedback")

        with patch.object(DevelopPhase, "_get_issue_dir", return_value=temp_issue_dir):
            develop_phase = DevelopPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                spec_file=str(temp_issue_dir / "spec" / "spec_001.md"),
                plan_file=str(temp_issue_dir / "plan" / "plan_001.md"),
                workflow_mode=WorkflowMode.LOCAL,
                interactive=False,
            )

            # Call _load_local_pr_feedback
            feedback = develop_phase._load_local_pr_feedback()

            assert feedback == "Latest feedback"

    def test_returns_none_when_no_pr_files(
        self,
        temp_issue_dir,
        mock_git_ops,
        mock_agent_manager,
        mock_permission_handler,
        mock_github_ops,
        monkeypatch,
    ):
        """測試當沒有 pr_XXX.md 檔案時返回 None"""
        monkeypatch.chdir(temp_issue_dir.parent.parent.parent)

        with patch.object(DevelopPhase, "_get_issue_dir", return_value=temp_issue_dir):
            develop_phase = DevelopPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                spec_file=str(temp_issue_dir / "spec" / "spec_001.md"),
                plan_file=str(temp_issue_dir / "plan" / "plan_001.md"),
                workflow_mode=WorkflowMode.LOCAL,
                interactive=False,
            )

            feedback = develop_phase._load_local_pr_feedback()

            assert feedback is None

    def test_returns_none_when_pr_dir_not_exists(
        self,
        temp_issue_dir,
        mock_git_ops,
        mock_agent_manager,
        mock_permission_handler,
        mock_github_ops,
        monkeypatch,
    ):
        """測試當 pr 目錄不存在時返回 None"""
        monkeypatch.chdir(temp_issue_dir.parent.parent.parent)

        with patch.object(DevelopPhase, "_get_issue_dir", return_value=temp_issue_dir):
            develop_phase = DevelopPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                spec_file=str(temp_issue_dir / "spec" / "spec_001.md"),
                plan_file=str(temp_issue_dir / "plan" / "plan_001.md"),
                workflow_mode=WorkflowMode.LOCAL,
                interactive=False,
            )

            feedback = develop_phase._load_local_pr_feedback()

            assert feedback is None

    def test_uses_local_feedback_when_pr_auto_create_is_false(
        self,
        temp_issue_dir,
        mock_git_ops,
        mock_agent_manager,
        mock_permission_handler,
        mock_github_ops,
        monkeypatch,
    ):
        """測試 pr.auto_create: false 時使用本地建議整合至 prompt"""
        monkeypatch.chdir(temp_issue_dir.parent.parent.parent)

        # Create pr file
        pr_dir = temp_issue_dir / "pr"
        pr_dir.mkdir()
        (pr_dir / "pr_001.md").write_text("Local PR feedback")

        with patch.object(DevelopPhase, "_get_issue_dir", return_value=temp_issue_dir):
            develop_phase = DevelopPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                spec_file=str(temp_issue_dir / "spec" / "spec_001.md"),
                plan_file=str(temp_issue_dir / "plan" / "plan_001.md"),
                workflow_mode=WorkflowMode.LOCAL,
                interactive=False,
            )

            # Test _generate_prompt contains local feedback
            prompt = develop_phase._generate_prompt(user_input="Test")
            assert "PR Feedback (Local)" in prompt
            assert "Local PR feedback" in prompt

    def test_skips_local_feedback_when_pr_auto_create_is_true(
        self,
        temp_issue_dir,
        mock_git_ops,
        mock_agent_manager,
        mock_permission_handler,
        mock_github_ops,
        monkeypatch,
    ):
        """測試 pr.auto_create: true 時不使用本地建議"""
        monkeypatch.chdir(temp_issue_dir.parent.parent.parent)

        # Update config to pr.auto_create: true
        config_file = temp_issue_dir / "config.yaml"
        config_data = {
            "base_branch": "main",
            "feature_branch": "test-issue",
            "pr": {"auto_create": True},
        }
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        # Create pr file (should be ignored)
        pr_dir = temp_issue_dir / "pr"
        pr_dir.mkdir()
        (pr_dir / "pr_001.md").write_text("Local PR feedback (should be ignored)")

        with patch.object(DevelopPhase, "_get_issue_dir", return_value=temp_issue_dir):
            develop_phase = DevelopPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                spec_file=str(temp_issue_dir / "spec" / "spec_001.md"),
                plan_file=str(temp_issue_dir / "plan" / "plan_001.md"),
                workflow_mode=WorkflowMode.LOCAL,
                interactive=False,
            )

            # Test _generate_prompt does NOT contain local feedback
            prompt = develop_phase._generate_prompt(user_input="Test")
            assert "Local PR feedback (should be ignored)" not in prompt
            assert "PR Feedback (Local)" not in prompt
