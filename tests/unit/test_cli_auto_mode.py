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
    """測試 --auto 模式中變數作用域問題"""

    def test_spec_auto_uses_correct_variable_name(self, temp_repo_dir, mock_git_ops, prepared_issue):
        """測試 spec auto 模式使用正確變數名稱（issue_name 而非 current_branch）"""
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
    """測試 --auto 模式不會覆寫 prepare 配置"""

    def test_spec_phase_preserves_issue_config(self, temp_repo_dir, mock_git_ops, prepared_issue):
        """測試 spec phase 不會覆寫 issue config（例如 worktree_path）"""
        # This test uses real SpecPhase to ensure config preservation works
        from cafe.phases.spec_phase import SpecPhase
        from cafe.core.types import SpecRigor
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
            agent_manager=mock_agent_manager,
            permission_handler=mock_permission,
            git_ops=mock_git_ops,
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
        assert final_config["spec"]["rigor"] == "medium"

    def test_plan_phase_preserves_issue_config(self, temp_repo_dir, mock_git_ops, prepared_issue):
        """測試 plan phase 不會覆寫 issue config"""
        # Add worktree_path to config
        config_file = prepared_issue / "issue.yaml"
        with open(config_file, 'r') as f:
            config_data = yaml.safe_load(f)
        
        config_data["worktree_path"] = "/some/worktree/path"
        
        with open(config_file, 'w') as f:
            yaml.dump(config_data, f)
        
        # Create spec file in new structure (required for plan)
        spec_iter_dir = prepared_issue / "spec" / "iteration_001"
        spec_iter_dir.mkdir(parents=True, exist_ok=True)
        spec_file = spec_iter_dir / "output.md"
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


class TestAutoModeErrorHandling:
    """Test error suppression in auto mode to prevent redundant error messages."""

    def test_handle_phase_exception_suppresses_output_in_auto_mode(self):
        """Test that _handle_phase_exception suppresses standard error output in auto mode."""
        from cafe.ui.cli import _handle_phase_exception
        import typer

        # Mock console to capture output
        with patch('cafe.ui.cli.console') as mock_console:
            # In auto mode with non-critical, non-programming error, should exit without printing
            # Use RuntimeError instead of ValueError (ValueError is a programming error)
            with pytest.raises(typer.Exit):
                _handle_phase_exception(RuntimeError("test error"), "spec", auto=True)

            # Verify no error message was printed for non-critical, non-programming errors
            error_prints = [
                call for call in mock_console.print.call_args_list
                if "Error" in str(call) or "error" in str(call)
            ]
            assert len(error_prints) == 0, "Error message was printed in auto mode"

    def test_handle_phase_exception_shows_programming_errors_in_auto_mode(self):
        """Test that _handle_phase_exception shows programming errors even in auto mode."""
        from cafe.ui.cli import _handle_phase_exception
        import typer

        # Mock console to capture output
        with patch('cafe.ui.cli.console') as mock_console:
            # Programming errors (AttributeError, ValueError, TypeError, KeyError) should be shown
            with pytest.raises(typer.Exit):
                _handle_phase_exception(AttributeError("Phase must have 'phase_dir' attribute"), "spec", auto=True)

            # Verify error message was printed
            calls_str = str(mock_console.print.call_args_list)
            assert "Error" in calls_str and "AttributeError" in calls_str, \
                "Programming error was not shown in auto mode"

    def test_handle_phase_exception_shows_critical_errors_in_auto_mode(self):
        """Test that _handle_phase_exception still shows critical errors in auto mode."""
        from cafe.ui.cli import _handle_phase_exception
        from cafe.core.types import CriticalPhaseError
        import typer

        # Mock console to capture output
        with patch('cafe.ui.cli.console') as mock_console:
            critical_error = CriticalPhaseError(
                message="Rate limit reached",
                error_type="rate_limit",
                phase_name="spec"
            )

            with pytest.raises(typer.Exit):
                _handle_phase_exception(critical_error, "spec", auto=True)

            # Verify critical error message was printed
            calls_str = str(mock_console.print.call_args_list)
            assert "Critical error" in calls_str or "rate limit" in calls_str.lower(), \
                "Critical error message was suppressed in auto mode"

    def test_handle_phase_exception_shows_errors_in_non_auto_mode(self):
        """Test that _handle_phase_exception shows all errors when not in auto mode."""
        from cafe.ui.cli import _handle_phase_exception
        import typer

        # Mock console to capture output
        with patch('cafe.ui.cli.console') as mock_console:
            with pytest.raises(typer.Exit):
                _handle_phase_exception(ValueError("test error"), "spec", auto=False)

            # Verify error message was printed
            calls_str = str(mock_console.print.call_args_list)
            assert "Error" in calls_str, "Error message was not printed in non-auto mode"

    def test_execute_next_phase_auto_does_not_print_redundant_errors(self):
        """Test that _execute_next_phase_auto doesn't print error messages (already printed by phase)."""
        from cafe.ui.cli import _execute_next_phase_auto
        import typer

        with patch('cafe.ui.cli.subprocess.run') as mock_subprocess, \
             patch('cafe.ui.cli.console') as mock_console:
            # Simulate phase command failure
            mock_subprocess.return_value = MagicMock(returncode=1)

            with pytest.raises(typer.Exit):
                _execute_next_phase_auto("spec", "test-issue")

            # Verify no error message like "spec phase failed" was printed
            # Only the "Auto mode: executing..." message should be there
            error_messages = [
                call for call in mock_console.print.call_args_list
                if "failed" in str(call).lower()
            ]
            assert len(error_messages) == 0, \
                "Redundant error message was printed by _execute_next_phase_auto"


class TestReviewMaxIterationsConfig:
    """測試 review phase 從 config.yaml 讀取 max_review_iterations，而非 issue.yaml"""

    def test_review_reads_max_iterations_from_config_not_issue_yaml(self, temp_repo_dir, mock_git_ops, prepared_issue):
        """測試 review phase 從 config.yaml 讀取 max_review_iterations"""
        from cafe.ui.cli import review
        from typer.testing import CliRunner
        from cafe.ui.cli import app

        runner = CliRunner()

        # Setup: Create spec and plan files
        spec_iter_dir = prepared_issue / "spec" / "iteration_001"
        spec_iter_dir.mkdir(parents=True, exist_ok=True)
        spec_file = spec_iter_dir / "output.md"
        spec_file.write_text("# Test Spec")

        plan_iter_dir = prepared_issue / "plan" / "iteration_001"
        plan_iter_dir.mkdir(parents=True, exist_ok=True)
        plan_file = plan_iter_dir / "output.md"
        plan_file.write_text("# Test Plan")

        # Create 8 review iterations
        review_dir = prepared_issue / "review"
        review_dir.mkdir(exist_ok=True)
        for i in range(1, 9):
            review_iter_dir = review_dir / f"iteration_{i:03d}"
            review_iter_dir.mkdir(exist_ok=True)
            review_output = review_iter_dir / "output.md"
            review_output.write_text(f"# Review {i}")

        # Set issue.yaml to have max_review_iterations: 5 (should be IGNORED)
        issue_config_file = prepared_issue / "issue.yaml"
        with open(issue_config_file, 'r') as f:
            issue_config = yaml.safe_load(f)

        issue_config["auto"] = {"max_review_iterations": 5}

        with open(issue_config_file, 'w') as f:
            yaml.dump(issue_config, f)

        # Set config.yaml to have max_review_iterations: 10 (should be USED)
        config_file = temp_repo_dir / ".cafe" / "config.yaml"
        with open(config_file, 'r') as f:
            config_data = yaml.safe_load(f)

        config_data["auto"] = {"max_review_iterations": 10}

        with open(config_file, 'w') as f:
            yaml.dump(config_data, f)

        # Mock ReviewPhase to succeed
        with patch('cafe.ui.cli.ReviewPhase') as MockReviewPhase, \
             patch('cafe.ui.cli.PermissionHandler'), \
             patch('cafe.ui.cli._setup_agents'), \
             patch('cafe.ui.cli._execute_next_phase_auto') as mock_next_phase:

            mock_phase = MagicMock()
            MockReviewPhase.return_value = mock_phase

            mock_result = MagicMock()
            mock_result.status.value = "completed"
            mock_result.data = {"status_code": "CAFE_NEEDS_CHANGES"}
            mock_phase.execute.return_value = mock_result

            # Execute review with --auto
            result = runner.invoke(app, ["review", "--auto", "--no-interactive"])

            # Should NOT hit the limit (8 < 10), so should call next phase
            assert mock_next_phase.called, "Should continue to next phase when under limit"

            # Verify it didn't print the limit warning
            assert "Review loop limit reached" not in result.stdout

    def test_review_respects_config_limit_when_exceeded(self, temp_repo_dir, mock_git_ops, prepared_issue):
        """測試 review phase 達到 config.yaml 設定的上限時會停止"""
        from cafe.ui.cli import review
        from typer.testing import CliRunner
        from cafe.ui.cli import app

        runner = CliRunner()

        # Setup: Create spec and plan files
        spec_iter_dir = prepared_issue / "spec" / "iteration_001"
        spec_iter_dir.mkdir(parents=True, exist_ok=True)
        spec_file = spec_iter_dir / "output.md"
        spec_file.write_text("# Test Spec")

        plan_iter_dir = prepared_issue / "plan" / "iteration_001"
        plan_iter_dir.mkdir(parents=True, exist_ok=True)
        plan_file = plan_iter_dir / "output.md"
        plan_file.write_text("# Test Plan")

        # Create 10 review iterations (equal to limit)
        review_dir = prepared_issue / "review"
        review_dir.mkdir(exist_ok=True)
        for i in range(1, 11):
            review_iter_dir = review_dir / f"iteration_{i:03d}"
            review_iter_dir.mkdir(exist_ok=True)
            review_output = review_iter_dir / "output.md"
            review_output.write_text(f"# Review {i}")

        # Set config.yaml to have max_review_iterations: 10
        config_file = temp_repo_dir / ".cafe" / "config.yaml"
        with open(config_file, 'r') as f:
            config_data = yaml.safe_load(f)

        config_data["auto"] = {"max_review_iterations": 10}

        with open(config_file, 'w') as f:
            yaml.dump(config_data, f)

        # Mock ReviewPhase to succeed
        with patch('cafe.ui.cli.ReviewPhase') as MockReviewPhase, \
             patch('cafe.ui.cli.PermissionHandler'), \
             patch('cafe.ui.cli._setup_agents'), \
             patch('cafe.ui.cli._execute_next_phase_auto') as mock_next_phase:

            mock_phase = MagicMock()
            MockReviewPhase.return_value = mock_phase

            mock_result = MagicMock()
            mock_result.status.value = "completed"
            mock_result.data = {"status_code": "CAFE_NEEDS_CHANGES"}
            mock_phase.execute.return_value = mock_result

            # Execute review with --auto
            result = runner.invoke(app, ["review", "--auto", "--no-interactive"])

            # Should hit the limit (10 >= 10), so should NOT call next phase
            assert not mock_next_phase.called, "Should NOT continue to next phase when limit reached"

            # Verify it printed the limit warning
            assert "Review loop limit reached (10 times)" in result.stdout
