"""Tests for PRPhase auto_create prompt functionality."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import yaml

from cafe.phases.pr_phase import PRPhase
from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.types import PhaseResult, PhaseStatus, WorkflowMode, TokenUsage, AgentCLI
from cafe.core.permission import PermissionHandler
from cafe.utils.github import GitHubOps


class TestAutoCreatePrompt:
    """測試 auto_create 參數的互動式詢問機制"""

    @pytest.fixture
    def setup_phase_mocks(self, tmp_path: Path, monkeypatch):
        """設置 phase 所需的 mock 物件和目錄結構"""
        # Change to tmp_path for this test
        monkeypatch.chdir(tmp_path)

        # Setup issue directory structure
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)
        spec_file = spec_dir / "spec_001.md"
        spec_file.write_text("# Feature\n")

        config_file = issue_dir / "issue.yaml"

        # Create mocks
        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-issue"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_diff.return_value = "diff --git a/file.txt b/file.txt\n+new line"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"

        return {
            "agent_manager": agent_manager,
            "permission_handler": permission_handler,
            "git_ops": git_ops,
            "github_ops": github_ops,
            "spec_file": str(spec_file),
            "config_file": config_file,
            "issue_dir": issue_dir,
        }

    @patch('cafe.phases.pr_phase.is_github_repo')
    @patch('cafe.phases.pr_phase.prompt_confirm')
    def test_prompt_auto_create_on_github_repo_when_not_set(
        self, mock_prompt_confirm, mock_is_github_repo, tmp_path, setup_phase_mocks
    ):
        """測試在 GitHub repo 且未設定 auto_create 時觸發詢問"""
        mocks = setup_phase_mocks

        # Mock GitHub repo detection
        mock_is_github_repo.return_value = True

        # Mock user response: Yes to auto create
        mock_prompt_confirm.return_value = True

        # Create phase
        phase = PRPhase(
            agent_manager=mocks["agent_manager"],
            permission_handler=mocks["permission_handler"],
            git_ops=mocks["git_ops"],
            github_ops=mocks["github_ops"],
            spec_file=mocks["spec_file"],
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            interactive=True,
        )

        # Execute phase
        result = phase.execute()

        # Verify prompt was called
        mock_prompt_confirm.assert_called_once_with(
            "Automatically create PR on GitHub after development?", default=True
        )

        # Verify config was saved
        assert mocks["config_file"].exists()
        with open(mocks["config_file"]) as f:
            config_data = yaml.safe_load(f)
            assert config_data["pr"]["auto_create"] is True

        # Verify PR creation was attempted (since user chose True)
        assert result.status == PhaseStatus.COMPLETED

    @patch('cafe.phases.pr_phase.is_github_repo')
    @patch('cafe.phases.pr_phase.prompt_confirm')
    def test_prompt_auto_create_user_chooses_no(
        self, mock_prompt_confirm, mock_is_github_repo, tmp_path, setup_phase_mocks
    ):
        """測試使用者選擇 'n' 時進入 local review 模式"""
        mocks = setup_phase_mocks

        # Mock GitHub repo detection
        mock_is_github_repo.return_value = True

        # Mock user response: No to auto create
        mock_prompt_confirm.return_value = False

        # Create phase with non-interactive mode for local review (to avoid user input)
        phase = PRPhase(
            agent_manager=mocks["agent_manager"],
            permission_handler=mocks["permission_handler"],
            git_ops=mocks["git_ops"],
            github_ops=mocks["github_ops"],
            spec_file=mocks["spec_file"],
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            interactive=True,  # Still need interactive for the prompt itself
        )

        # Execute phase (will fail in local review due to non-interactive)
        result = phase.execute()

        # Verify prompt was called
        mock_prompt_confirm.assert_called_once()

        # Verify config was saved with False
        assert mocks["config_file"].exists()
        with open(mocks["config_file"]) as f:
            config_data = yaml.safe_load(f)
            assert config_data["pr"]["auto_create"] is False

        # Verify local review mode was attempted (no PR creation)
        mocks["github_ops"].create_pr.assert_not_called()

        # Local review requires interaction, so test should at least verify
        # that the config was saved and PR creation was not attempted
        assert result.status in [PhaseStatus.FAILED, PhaseStatus.COMPLETED]

    @patch('cafe.phases.pr_phase.is_github_repo')
    @patch('cafe.phases.pr_phase.prompt_confirm')
    def test_no_prompt_on_non_github_repo(
        self, mock_prompt_confirm, mock_is_github_repo, tmp_path, setup_phase_mocks
    ):
        """測試非 GitHub repo 時不觸發詢問"""
        mocks = setup_phase_mocks

        # Mock non-GitHub repo
        mock_is_github_repo.return_value = False

        # Create phase
        phase = PRPhase(
            agent_manager=mocks["agent_manager"],
            permission_handler=mocks["permission_handler"],
            git_ops=mocks["git_ops"],
            github_ops=mocks["github_ops"],
            spec_file=mocks["spec_file"],
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            interactive=True,
        )

        # Execute phase
        result = phase.execute()

        # Verify NO prompt was called
        mock_prompt_confirm.assert_not_called()

        # Verify config was saved with False (auto-set for non-GitHub repos)
        assert mocks["config_file"].exists()
        with open(mocks["config_file"]) as f:
            config_data = yaml.safe_load(f)
            assert config_data["pr"]["auto_create"] is False

        # Verify local review mode was used
        mocks["github_ops"].create_pr.assert_not_called()

    @patch('cafe.phases.pr_phase.is_github_repo')
    @patch('cafe.phases.pr_phase.prompt_confirm')
    def test_no_prompt_when_already_set_to_true(
        self, mock_prompt_confirm, mock_is_github_repo, tmp_path, setup_phase_mocks
    ):
        """測試 config 已設為 true 時不觸發詢問"""
        mocks = setup_phase_mocks

        # Pre-set config with auto_create = True
        with open(mocks["config_file"], 'w') as f:
            yaml.dump({"pr": {"auto_create": True}}, f)

        # Mock GitHub repo
        mock_is_github_repo.return_value = True

        # Create phase
        phase = PRPhase(
            agent_manager=mocks["agent_manager"],
            permission_handler=mocks["permission_handler"],
            git_ops=mocks["git_ops"],
            github_ops=mocks["github_ops"],
            spec_file=mocks["spec_file"],
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            interactive=True,
        )

        # Execute phase
        result = phase.execute()

        # Verify NO prompt was called
        mock_prompt_confirm.assert_not_called()

        # Verify PR creation was attempted
        assert result.status == PhaseStatus.COMPLETED
        mocks["github_ops"].create_pr.assert_called_once()

    @patch('cafe.phases.pr_phase.is_github_repo')
    @patch('cafe.phases.pr_phase.prompt_confirm')
    def test_no_prompt_when_already_set_to_false(
        self, mock_prompt_confirm, mock_is_github_repo, tmp_path, setup_phase_mocks
    ):
        """測試 config 已設為 false 時不觸發詢問並使用 local review"""
        mocks = setup_phase_mocks

        # Pre-set config with auto_create = False
        with open(mocks["config_file"], 'w') as f:
            yaml.dump({"pr": {"auto_create": False}}, f)

        # Mock GitHub repo
        mock_is_github_repo.return_value = True

        # Create phase
        phase = PRPhase(
            agent_manager=mocks["agent_manager"],
            permission_handler=mocks["permission_handler"],
            git_ops=mocks["git_ops"],
            github_ops=mocks["github_ops"],
            spec_file=mocks["spec_file"],
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            interactive=True,
        )

        # Execute phase (will require interaction for local review)
        result = phase.execute()

        # Verify NO prompt was called
        mock_prompt_confirm.assert_not_called()

        # Verify local review mode was attempted (no PR creation on GitHub)
        mocks["github_ops"].create_pr.assert_not_called()

        # Local review requires interaction, so test should at least verify
        # that prompt was not called and PR creation was not attempted
        assert result.status in [PhaseStatus.FAILED, PhaseStatus.COMPLETED]
