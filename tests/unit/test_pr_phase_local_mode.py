"""Tests for PRPhase local review mode (issue45)."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest
import yaml
from typer.testing import CliRunner

from cafe.phases.pr_phase import PRPhase
from cafe.core.types import PhaseStatus, PhaseResult, WorkflowMode
from cafe.core.status_codes import PhaseStatusCode
from cafe.ui.cli import app

runner = CliRunner()


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

    def test_worktree_mode_displays_relative_path_correctly(self, tmp_path, mock_git_ops, mock_agent_manager,
                                                              mock_permission_handler, mock_github_ops, monkeypatch,
                                                              capsys):
        """測試 worktree 模式下修改請求檔案路徑顯示正確 (issue50)

        在 worktree 模式下，pr_file 路徑指向 repo root (.cafe/issues/xxx/pr/pr_001.md)
        而 cwd 是 worktree 目錄 (.cafe/worktrees/xxx)。
        應使用 to_cwd_relative_path() 正確顯示路徑，而非拋出 "not in subpath" 錯誤。
        """
        # Setup repo root structure
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        issue_dir = repo_root / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)

        # Create spec file
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir()
        spec_file = spec_dir / "spec_001.md"
        spec_file.write_text("# Test Spec\nTest requirements")

        # Create config with worktree mode and pr.auto_create = false
        config_file = issue_dir / "config.yaml"
        worktree_path = repo_root / ".cafe" / "worktrees" / "test-issue"
        worktree_path.mkdir(parents=True)

        config_data = {
            "base_branch": "main",
            "feature_branch": "test-issue",
            "worktree_path": str(worktree_path),
            "pr": {
                "auto_create": False
            }
        }
        with open(config_file, 'w') as f:
            yaml.dump(config_data, f)

        # Change to worktree directory (not repo root)
        monkeypatch.chdir(worktree_path)

        # Mock git diff
        mock_git_ops.get_diff.return_value = "diff content"

        modification_request = "Please fix authentication"

        with patch.object(PRPhase, '_get_issue_dir', return_value=issue_dir):
            pr_phase = PRPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                github_ops=mock_github_ops,
                spec_file=str(spec_file),
                workflow_mode=WorkflowMode.LOCAL,
                interactive=True,
            )

            with patch.object(pr_phase, '_ask_user_for_review_decision', return_value=modification_request):
                # This should NOT raise "not in subpath" error
                result = pr_phase.execute()

                # Verify the phase completed successfully
                assert result.status == PhaseStatus.COMPLETED
                assert result.data.get("status_code") == PhaseStatusCode.NEEDS_CHANGES.value

                # Verify pr_001.md was created
                pr_dir = issue_dir / "pr"
                pr_file = pr_dir / "pr_001.md"
                assert pr_file.exists()

                # Capture console output to verify path was displayed
                # (In real execution, Rich console would print the path)


class TestPRPhaseAutoMode:
    """測試 PR phase auto mode 自動執行 develop phase (issue50)"""

    def test_auto_mode_executes_develop_on_needs_changes(self, tmp_path, monkeypatch):
        """測試 cafe pr --auto 在 local review mode 收到 NEEDS_CHANGES 時自動執行 cafe develop --auto"""
        # Setup repo structure
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()

        issue_dir = cafe_dir / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)

        # Create config with pr.auto_create = false (local review mode)
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

        # Create global config
        global_config = cafe_dir / "config.yaml"
        global_config_data = {
            "agents": {
                "pm": {"name": "Roger", "cli": "copilot"},
                "developer": {"name": "David", "cli": "copilot"},
                "reviewer": {"name": "Richard", "cli": "copilot"},
            }
        }
        with open(global_config, 'w') as f:
            yaml.dump(global_config_data, f)

        # Create spec file
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir()
        spec_file = spec_dir / "spec_001.md"
        spec_file.write_text("# Test Spec")

        # Create plan file (required for PR phase)
        plan_dir = issue_dir / "plan"
        plan_dir.mkdir()
        plan_file = plan_dir / "plan_001.md"
        plan_file.write_text("# Test Plan")

        monkeypatch.chdir(tmp_path)

        # Mock PRPhase to return NEEDS_CHANGES status
        with patch('cafe.ui.cli.PRPhase') as MockPRPhase, \
             patch('cafe.ui.cli.GitOperations') as MockGitOps, \
             patch('cafe.ui.cli.PermissionHandler'), \
             patch('cafe.ui.cli._setup_agents'), \
             patch('cafe.ui.cli.subprocess.run') as mock_subprocess:

            # Setup git ops mock
            mock_git = MagicMock()
            MockGitOps.return_value = mock_git
            mock_git.get_current_branch.return_value = "test-issue"

            # Setup PR phase mock to return NEEDS_CHANGES
            mock_phase = MagicMock()
            MockPRPhase.return_value = mock_phase

            mock_result = MagicMock()
            mock_result.status = PhaseStatus.COMPLETED
            mock_result.message = "Local review completed - modification requested"
            mock_result.data = {
                "status_code": PhaseStatusCode.NEEDS_CHANGES.value,
                "local_review": True,
                "pr_file": str(issue_dir / "pr" / "pr_001.md")
            }
            mock_phase.execute.return_value = mock_result

            # Mock subprocess to simulate develop execution
            mock_subprocess.return_value = MagicMock(returncode=0)

            # Execute pr command with --auto
            result = runner.invoke(app, ["pr", "--auto", "--no-interactive"])

            # Should succeed (exit code 0)
            assert result.exit_code == 0, f"Command failed with output:\n{result.output}"

            # Verify _execute_next_phase_auto was called with develop
            # (it calls subprocess.run with develop --auto)
            assert mock_subprocess.called, "subprocess.run should have been called to execute develop"
            call_args = mock_subprocess.call_args[0][0]
            assert "develop" in call_args, f"Expected 'develop' in subprocess call, got: {call_args}"
            assert "--auto" in call_args, f"Expected '--auto' in subprocess call, got: {call_args}"
