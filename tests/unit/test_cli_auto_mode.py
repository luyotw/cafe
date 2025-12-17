"""Tests for --auto mode phase chaining."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest
import yaml
from typer.testing import CliRunner

from cafe.ui.cli import app

runner = CliRunner()


@pytest.fixture
def temp_repo_dir(tmp_path):
    """Create a temporary git repository directory."""
    cafe_dir = tmp_path / ".cafe"
    cafe_dir.mkdir(parents=True)
    
    # Create config with default auto settings
    config_file = cafe_dir / "config.yaml"
    config_data = {
        "agents": {
            "pm": {"name": "Roger", "cli": "copilot"},
            "developer": {"name": "David", "cli": "copilot"},
            "reviewer": {"name": "Richard", "cli": "copilot"},
        },
        "auto": {
            "max_review_iterations": 5,
        },
        "defaults": {
            "workflow_mode": "local",
            "interactive": True,
        },
    }
    with open(config_file, 'w') as f:
        yaml.dump(config_data, f)
    
    return tmp_path


@pytest.fixture(autouse=True)
def change_test_dir(tmp_path, monkeypatch):
    """Automatically change to tmp_path for all tests."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def mock_git_ops():
    """Create a mock GitOperations instance."""
    with patch('cafe.ui.cli.GitOperations') as MockGitOperations:
        mock_git = MagicMock()
        MockGitOperations.return_value = mock_git
        mock_git.get_current_branch.return_value = "test-issue"
        mock_git.has_uncommitted_changes.return_value = False
        yield mock_git


@pytest.fixture
def prepared_issue(temp_repo_dir):
    """Create a prepared issue with config."""
    issue_dir = temp_repo_dir / ".cafe" / "issues" / "test-issue"
    issue_dir.mkdir(parents=True, exist_ok=True)
    
    # Create spec directory
    spec_dir = issue_dir / "spec"
    spec_dir.mkdir(exist_ok=True)
    
    # Create issue config
    config_file = issue_dir / "issue.yaml"
    config_data = {
        "base_branch": "main",
        "feature_branch": "test-issue",
        "auto": {
            "max_review_iterations": 5,
        },
    }
    with open(config_file, 'w') as f:
        yaml.dump(config_data, f)
    
    return issue_dir


class TestAutoModeVariableScope:
    """測試 --auto 模式中的變數作用域問題"""

    def test_spec_auto_uses_correct_variable_name(self, temp_repo_dir, mock_git_ops, prepared_issue):
        """測試 spec auto 模式使用正確的變數名稱（issue_name 而非 current_branch）"""
        # Directly test the auto chaining logic by calling _execute_next_phase_auto
        from cafe.ui.cli import _execute_next_phase_auto
        
        # Mock subprocess to prevent actual execution
        with patch('cafe.ui.cli.subprocess.run') as mock_subprocess:
            mock_subprocess.return_value = MagicMock(returncode=0)
            
            # This should use issue_name, not current_branch (which doesn't exist)
            # If it fails with "name 'current_branch' is not defined", test fails
            try:
                _execute_next_phase_auto("plan", "test-issue")
                # Should have called subprocess
                assert mock_subprocess.called
                call_args = mock_subprocess.call_args[0][0]
                assert "plan" in call_args
                assert "--auto" in call_args
            except NameError as e:
                pytest.fail(f"Variable name error: {e}")


class TestAutoModeConfigPreservation:
    """測試 --auto 模式不會覆寫 prepare 的配置"""

    def test_spec_phase_preserves_issue_config(self, temp_repo_dir, mock_git_ops, prepared_issue):
        """測試 spec phase 不會覆寫 issue config（例如 worktree_path）"""
        # This test uses real SpecPhase to ensure config preservation works
        from cafe.phases.spec_phase import SpecPhase
        from cafe.core.types import SpecRigor, WorkflowMode
        from cafe.core.permission import PermissionHandler
        
        # Add worktree_path to config
        config_file = prepared_issue / "issue.yaml"
        with open(config_file, 'r') as f:
            config_data = yaml.safe_load(f)
        
        config_data["worktree_path"] = "/some/worktree/path"
        
        with open(config_file, 'w') as f:
            yaml.dump(config_data, f)
        
        # Create mock dependencies
        mock_agent_manager = MagicMock()
        mock_permission = PermissionHandler()
        
        # Create real SpecPhase instance
        phase = SpecPhase(
            issue_name="test-issue",
            rigor=SpecRigor.MEDIUM,
            issue_id=None,
            agent_manager=mock_agent_manager,
            permission_handler=mock_permission,
            git_ops=mock_git_ops,
            workflow_mode=WorkflowMode.LOCAL,
        )
        
        # Save config (this is what happens during execution)
        phase._save_issue_config()
        
        # Check config still has all fields
        with open(config_file, 'r') as f:
            final_config = yaml.safe_load(f)
        
        assert "worktree_path" in final_config, f"worktree_path was removed from config. Config: {final_config}"
        assert final_config["worktree_path"] == "/some/worktree/path"
        assert final_config["base_branch"] == "main"
        assert final_config["feature_branch"] == "test-issue"
        assert final_config["auto"]["max_review_iterations"] == 5
        assert final_config["rigor"] == "medium"

    def test_plan_phase_preserves_issue_config(self, temp_repo_dir, mock_git_ops, prepared_issue):
        """測試 plan phase 不會覆寫 issue config"""
        # Add worktree_path to config
        config_file = prepared_issue / "issue.yaml"
        with open(config_file, 'r') as f:
            config_data = yaml.safe_load(f)
        
        config_data["worktree_path"] = "/some/worktree/path"
        
        with open(config_file, 'w') as f:
            yaml.dump(config_data, f)
        
        # Create spec file (required for plan)
        spec_file = prepared_issue / "spec" / "spec_001.md"
        spec_file.write_text("# Test Spec")
        
        # Mock plan phase
        with patch('cafe.ui.cli.PlanPhase') as MockPlanPhase:
            mock_phase = MagicMock()
            MockPlanPhase.return_value = mock_phase
            
            mock_result = MagicMock()
            mock_result.status.value = "completed"
            mock_result.data = {
                "status_code": "CAFE_CONFIRMED",
                "iterations": 2,
            }
            mock_phase.execute.return_value = mock_result
            
            with patch('cafe.ui.cli.PermissionHandler'), \
                 patch('cafe.ui.cli._setup_agents'), \
                 patch('cafe.ui.cli.select_template', return_value="default"):
                
                # Execute plan
                result = runner.invoke(app, ["plan", "--no-interactive"])
                
                assert result.exit_code == 0
                
                # Check config still has worktree_path
                with open(config_file, 'r') as f:
                    final_config = yaml.safe_load(f)
                
                assert "worktree_path" in final_config, "worktree_path was removed from config"
                assert final_config["worktree_path"] == "/some/worktree/path"
