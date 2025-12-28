"""Tests for PR command CLI interactions."""

from pathlib import Path
from unittest.mock import MagicMock, patch, call
import subprocess

import pytest
import yaml
from typer.testing import CliRunner

from cafe.ui.cli import app
from cafe.core.types import PhaseResult, PhaseStatus

runner = CliRunner()


@pytest.fixture
def temp_repo_dir(tmp_path):
    """Create a temporary git repository directory."""
    cafe_dir = tmp_path / ".cafe"
    cafe_dir.mkdir(parents=True)
    
    # Create config
    config_file = cafe_dir / "config.yaml"
    config_data = {
        "agents": {
            "pm": {"name": "Roger", "cli": "copilot"},
            "developer": {"name": "David", "cli": "copilot"},
            "reviewer": {"name": "Richard", "cli": "copilot"},
        },
        "defaults": {
            "workflow_mode": "github",
            "interactive": True,
        },
    }
    with open(config_file, 'w') as f:
        yaml.dump(config_data, f)
    
    # Create issue directory with spec and plan files
    issue_dir = cafe_dir / "issues" / "test-issue"
    issue_dir.mkdir(parents=True)
    
    spec_dir = issue_dir / "spec" / "iteration_001"
    spec_dir.mkdir(parents=True)
    (spec_dir / "output.md").write_text("Test spec")

    plan_dir = issue_dir / "plan" / "iteration_001"
    plan_dir.mkdir(parents=True)
    (plan_dir / "output.md").write_text("Test plan")
    
    return tmp_path


@pytest.fixture(autouse=True)
def change_test_dir(tmp_path, monkeypatch):
    """Automatically change to tmp_path for all tests."""
    monkeypatch.chdir(tmp_path)


class TestPRCommandGitHubMode:
    """測試 PR 指令在 GitHub 模式下行為."""

    @patch("subprocess.run")
    @patch("cafe.ui.cli.GitHubOps")
    @patch("cafe.ui.cli.PermissionHandler")
    @patch("cafe.ui.cli.AgentManager")
    @patch("cafe.ui.cli.ConfigManager")
    @patch("cafe.ui.cli.GitOperations")
    @patch("cafe.ui.cli.PRPhase")
    def test_auto_opens_pr_diff_in_browser_on_success(
        self,
        mock_pr_phase_cls,
        mock_git_cls,
        mock_config_cls,
        mock_agent_cls,
        mock_perm_cls,
        mock_github_ops_cls,
        mock_subprocess_run,
        temp_repo_dir,
    ):
        """測試 PR 成功創建後自動執行 gh pr diff --web."""
        # Setup mocks
        mock_git = MagicMock()
        mock_git.get_current_branch.return_value = "test-issue"
        mock_git_cls.return_value = mock_git
        
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "David", "cli": "copilot"}
        mock_config_cls.return_value = mock_config
        
        mock_agent_manager = MagicMock()
        mock_agent_cls.return_value = mock_agent_manager
        
        mock_perm = MagicMock()
        mock_perm_cls.return_value = mock_perm
        
        mock_github_ops = MagicMock()
        mock_github_ops_cls.return_value = mock_github_ops
        
        # Mock successful PR creation
        mock_pr_phase = MagicMock()
        pr_url = "https://github.com/user/repo/pull/123"
        mock_pr_phase.execute.return_value = PhaseResult(
            status=PhaseStatus.COMPLETED,
            message="Pull Request #123 created successfully",
            data={
                "pr_url": pr_url,
                "pr_number": 123,
            }
        )
        mock_pr_phase_cls.return_value = mock_pr_phase
        
        # Run command
        result = runner.invoke(app, ["pr"])
        
        # Verify subprocess.run was called with gh pr diff --web
        # Note: subprocess.run might be called multiple times (e.g., for gh --version check)
        # so we check that it was called with the expected arguments at least once
        calls = [call for call in mock_subprocess_run.call_args_list 
                 if call[0][0] == ["gh", "pr", "diff", "--web"]]
        assert len(calls) == 1, f"Expected exactly 1 call to 'gh pr diff --web', got {len(calls)}"
        assert calls[0] == (
            (["gh", "pr", "diff", "--web"],),
            {"capture_output": True, "timeout": 5}
        )
        
        # Verify output contains PR URL and next steps
        assert result.exit_code == 0
        assert pr_url in result.stdout
        assert "Next steps:" in result.stdout
        assert "gh pr diff --web" in result.stdout

    @patch("subprocess.run")
    @patch("cafe.ui.cli.GitHubOps")
    @patch("cafe.ui.cli.PermissionHandler")
    @patch("cafe.ui.cli.AgentManager")
    @patch("cafe.ui.cli.ConfigManager")
    @patch("cafe.ui.cli.GitOperations")
    @patch("cafe.ui.cli.PRPhase")
    def test_silently_ignores_subprocess_errors(
        self,
        mock_pr_phase_cls,
        mock_git_cls,
        mock_config_cls,
        mock_agent_cls,
        mock_perm_cls,
        mock_github_ops_cls,
        mock_subprocess_run,
        temp_repo_dir,
    ):
        """測試當 subprocess.run 失敗時靜默忽略錯誤."""
        # Setup mocks
        mock_git = MagicMock()
        mock_git.get_current_branch.return_value = "test-issue"
        mock_git_cls.return_value = mock_git
        
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "David", "cli": "copilot"}
        mock_config_cls.return_value = mock_config
        
        mock_agent_manager = MagicMock()
        mock_agent_cls.return_value = mock_agent_manager
        
        mock_perm = MagicMock()
        mock_perm_cls.return_value = mock_perm
        
        mock_github_ops = MagicMock()
        mock_github_ops_cls.return_value = mock_github_ops
        
        # Mock successful PR creation
        mock_pr_phase = MagicMock()
        pr_url = "https://github.com/user/repo/pull/123"
        mock_pr_phase.execute.return_value = PhaseResult(
            status=PhaseStatus.COMPLETED,
            message="Pull Request #123 created successfully",
            data={
                "pr_url": pr_url,
                "pr_number": 123,
            }
        )
        mock_pr_phase_cls.return_value = mock_pr_phase
        
        # Make subprocess.run raise an exception only for gh pr diff --web
        def subprocess_side_effect(*args, **kwargs):
            if args[0] == ["gh", "pr", "diff", "--web"]:
                raise subprocess.TimeoutExpired(
                    cmd=["gh", "pr", "diff", "--web"],
                    timeout=5
                )
            return MagicMock(returncode=0)
        
        mock_subprocess_run.side_effect = subprocess_side_effect
        
        # Run command - should not fail despite subprocess error
        result = runner.invoke(app, ["pr"])
        
        # Verify command still succeeds
        assert result.exit_code == 0
        assert pr_url in result.stdout
        
        # Verify subprocess was called with gh pr diff --web
        calls = [call for call in mock_subprocess_run.call_args_list 
                 if call[0][0] == ["gh", "pr", "diff", "--web"]]
        assert len(calls) >= 1, "Expected subprocess to be called with gh pr diff --web"

    @patch("subprocess.run")
    @patch("cafe.ui.cli.GitHubOps")
    @patch("cafe.ui.cli.PermissionHandler")
    @patch("cafe.ui.cli.AgentManager")
    @patch("cafe.ui.cli.ConfigManager")
    @patch("cafe.ui.cli.GitOperations")
    @patch("cafe.ui.cli.PRPhase")
    def test_does_not_call_subprocess_on_pr_failure(
        self,
        mock_pr_phase_cls,
        mock_git_cls,
        mock_config_cls,
        mock_agent_cls,
        mock_perm_cls,
        mock_github_ops_cls,
        mock_subprocess_run,
        temp_repo_dir,
    ):
        """測試當 PR 創建失敗時不執行 subprocess."""
        # Setup mocks
        mock_git = MagicMock()
        mock_git.get_current_branch.return_value = "test-issue"
        mock_git_cls.return_value = mock_git
        
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "David", "cli": "copilot"}
        mock_config_cls.return_value = mock_config
        
        mock_agent_manager = MagicMock()
        mock_agent_cls.return_value = mock_agent_manager
        
        mock_perm = MagicMock()
        mock_perm_cls.return_value = mock_perm
        
        mock_github_ops = MagicMock()
        mock_github_ops_cls.return_value = mock_github_ops
        
        # Mock failed PR creation
        mock_pr_phase = MagicMock()
        mock_pr_phase.execute.return_value = PhaseResult(
            status=PhaseStatus.FAILED,
            message="PR creation failed",
            data={}
        )
        mock_pr_phase_cls.return_value = mock_pr_phase
        
        # Run command
        result = runner.invoke(app, ["pr"])
        
        # Verify subprocess was NOT called with gh pr diff --web
        # (it may be called for gh --version check, which is OK)
        calls = [call for call in mock_subprocess_run.call_args_list 
                 if call[0][0] == ["gh", "pr", "diff", "--web"]]
        assert len(calls) == 0, f"Expected no calls to 'gh pr diff --web', but found {len(calls)}"
        
        # Verify command failed
        assert result.exit_code == 1
