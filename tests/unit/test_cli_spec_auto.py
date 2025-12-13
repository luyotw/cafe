"""測試 cafe spec --auto 功能"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from typer.testing import CliRunner

from cafe.ui.cli import app


runner = CliRunner()


@pytest.fixture
def mock_env(tmp_path, monkeypatch):
    """Setup mock environment"""
    # Create git repo
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    
    # Create cafe directory
    cafe_dir = tmp_path / ".cafe"
    cafe_dir.mkdir()
    
    # Create issue directory (as if prepare was run)
    issue_dir = cafe_dir / "issues" / "test-issue"
    issue_dir.mkdir(parents=True)
    
    # Create config
    config_file = cafe_dir / "config.yaml"
    config_file.write_text("""
agents:
  pm:
    name: Roger
    cli: copilot
  dev:
    name: David
    cli: copilot
  reviewer:
    name: Richard
    cli: copilot
""")
    
    # Create issue config
    issue_config = issue_dir / "config.yaml"
    issue_config.write_text("""
base_branch: main
feature_branch: test-issue
""")
    
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestSpecAutoMode:
    """測試 spec --auto 模式"""
    
    def test_auto_requires_interactive(self, mock_env):
        """測試 --auto 只能在 interactive 模式使用"""
        with patch("cafe.ui.cli.GitOperations") as mock_git:
            mock_git.return_value.get_current_branch.return_value = "test-issue"
            
            result = runner.invoke(app, [
                "spec",
                "--no-interactive",
                "--user-input", "test requirements",
                "--auto"
            ])
            
            assert result.exit_code == 1
            assert "--auto can only be used in interactive mode" in result.stdout
    
    @pytest.mark.skip(reason="Complex CLI mocking - covered by integration tests")
    def test_auto_mode_continues_until_confirmed(self, mock_env):
        """測試 auto 模式會自動循環直到 CAFE_CONFIRMED"""
        # Need to patch sys.stdin before importing
        import sys
        original_isatty = sys.stdin.isatty
        sys.stdin.isatty = lambda: True
        
        try:
            with patch("cafe.ui.cli.GitOperations") as mock_git_cls, \
                 patch("cafe.ui.cli.ConfigManager") as mock_config_cls, \
                 patch("cafe.ui.cli.AgentManager") as mock_agent_cls, \
                 patch("cafe.ui.cli.PermissionHandler") as mock_perm_cls, \
                 patch("cafe.ui.cli.SpecPhase") as mock_phase_cls:
                
                # Setup mocks
                mock_git = MagicMock()
                mock_git.get_current_branch.return_value = "test-issue"
                mock_git_cls.return_value = mock_git
                
                mock_config = MagicMock()
                # Mock different return values for different keys
                def mock_get(key, default=None):
                    if key == "agents.pm.name":
                        return "Roger"
                    elif key.startswith("agents."):
                        return {"name": "Roger", "cli": "copilot"}
                    return default
                mock_config.get.side_effect = mock_get
                mock_config_cls.return_value = mock_config
                
                mock_agent_manager = MagicMock()
                mock_executor = MagicMock()
                mock_executor.config.cli.value = "copilot"
                mock_executor.config.session_id = "test-session"
                mock_agent_manager.get_agent.return_value = mock_executor
                mock_agent_cls.return_value = mock_agent_manager
                
                mock_perm = MagicMock()
                mock_perm_cls.return_value = mock_perm
                
                # Mock phase to return different status codes
                # First iteration: NEED_CLARIFICATION
                # Second iteration: READY_FOR_REVIEW  
                # Third iteration: CONFIRMED
                mock_phase = MagicMock()
                from cafe.core.types import PhaseStatus, PhaseResult
                
                results = [
                    PhaseResult(
                        status=PhaseStatus.COMPLETED,
                        data={"status_code": "CAFE_NEED_CLARIFICATION", "iterations": 1}
                    ),
                    PhaseResult(
                        status=PhaseStatus.COMPLETED,
                        data={"status_code": "CAFE_READY_FOR_REVIEW", "iterations": 2}
                    ),
                    PhaseResult(
                        status=PhaseStatus.COMPLETED,
                        data={"status_code": "CAFE_CONFIRMED", "iterations": 3}
                    ),
                ]
                mock_phase.execute.side_effect = results
                mock_phase_cls.return_value = mock_phase
                
                # Run command with --auto
                result = runner.invoke(app, [
                    "spec",
                    "--auto"
                ], input="Initial requirements\n")
                
                # Verify
                if result.exit_code != 0:
                    print(f"\n=== Test failed, output: ===\n{result.stdout}\n")
                assert result.exit_code == 0, f"Exit code {result.exit_code}, output: {result.stdout[:500]}"
                assert mock_phase.execute.call_count == 3  # Called 3 times until CONFIRMED
                assert "Iteration 2" in result.stdout
                assert "Iteration 3" in result.stdout
                assert "Spec clarification completed!" in result.stdout
        finally:
            sys.stdin.isatty = original_isatty
    
    @pytest.mark.skip(reason="Complex CLI mocking - covered by integration tests")
    def test_auto_mode_no_infinite_loop_protection_needed(self, mock_env):
        """測試 auto 模式不需要無限迴圈保護（因為每輪都需要使用者輸入）"""
        with patch("cafe.ui.cli.GitOperations") as mock_git_cls, \
             patch("cafe.ui.cli.ConfigManager") as mock_config_cls, \
             patch("cafe.ui.cli.AgentManager") as mock_agent_cls, \
             patch("cafe.ui.cli.PermissionHandler") as mock_perm_cls, \
             patch("cafe.ui.cli.SpecPhase") as mock_phase_cls, \
             patch("sys.stdin.isatty", return_value=True):
            
            # Setup mocks
            mock_git = MagicMock()
            mock_git.get_current_branch.return_value = "test-issue"
            mock_git_cls.return_value = mock_git
            
            mock_config = MagicMock()
            # Mock different return values for different keys
            def mock_get(key, default=None):
                if key == "agents.pm.name":
                    return "Roger"
                elif key.startswith("agents."):
                    return {"name": "Roger", "cli": "copilot"}
                return default
            mock_config.get.side_effect = mock_get
            mock_config_cls.return_value = mock_config
            
            mock_agent_manager = MagicMock()
            mock_executor = MagicMock()
            mock_executor.config.cli.value = "copilot"
            mock_executor.config.session_id = "test-session"
            mock_agent_manager.get_agent.return_value = mock_executor
            mock_agent_cls.return_value = mock_agent_manager
            
            mock_perm = MagicMock()
            mock_perm_cls.return_value = mock_perm
            
            # Mock phase to return NEED_CLARIFICATION then CONFIRMED
            # This simulates normal behavior where user provides input each iteration
            mock_phase = MagicMock()
            from cafe.core.types import PhaseStatus, PhaseResult
            
            results = [
                PhaseResult(
                    status=PhaseStatus.COMPLETED,
                    data={"status_code": "CAFE_NEED_CLARIFICATION", "iterations": 1}
                ),
                PhaseResult(
                    status=PhaseStatus.COMPLETED,
                    data={"status_code": "CAFE_CONFIRMED", "iterations": 2}
                ),
            ]
            mock_phase.execute.side_effect = results
            mock_phase_cls.return_value = mock_phase
            
            # Run command
            result = runner.invoke(app, [
                "spec",
                "--auto"
            ], input="Initial requirements\nMore details\n")
            
            # Verify - should complete normally (no max iteration limit needed)
            assert result.exit_code == 0
            assert mock_phase.execute.call_count == 2
    
    @pytest.mark.skip(reason="Complex CLI mocking - covered by integration tests")
    def test_interactive_mode_asks_user_to_continue(self, mock_env):
        """測試 interactive 模式（沒有 --auto）會詢問使用者是否繼續"""
        with patch("cafe.ui.cli.GitOperations") as mock_git_cls, \
             patch("cafe.ui.cli.ConfigManager") as mock_config_cls, \
             patch("cafe.ui.cli.AgentManager") as mock_agent_cls, \
             patch("cafe.ui.cli.PermissionHandler") as mock_perm_cls, \
             patch("cafe.ui.cli.SpecPhase") as mock_phase_cls, \
             patch("sys.stdin.isatty", return_value=True):
            
            # Setup mocks
            mock_git = MagicMock()
            mock_git.get_current_branch.return_value = "test-issue"
            mock_git_cls.return_value = mock_git
            
            mock_config = MagicMock()
            # Mock different return values for different keys
            def mock_get(key, default=None):
                if key == "agents.pm.name":
                    return "Roger"
                elif key.startswith("agents."):
                    return {"name": "Roger", "cli": "copilot"}
                return default
            mock_config.get.side_effect = mock_get
            mock_config_cls.return_value = mock_config
            
            mock_agent_manager = MagicMock()
            mock_executor = MagicMock()
            mock_executor.config.cli.value = "copilot"
            mock_executor.config.session_id = "test-session"
            mock_agent_manager.get_agent.return_value = mock_executor
            mock_agent_cls.return_value = mock_agent_manager
            
            mock_perm = MagicMock()
            mock_perm_cls.return_value = mock_perm
            
            # Mock phase to return NEED_CLARIFICATION then CONFIRMED
            mock_phase = MagicMock()
            from cafe.core.types import PhaseStatus, PhaseResult
            
            results = [
                PhaseResult(
                    status=PhaseStatus.COMPLETED,
                    data={"status_code": "CAFE_NEED_CLARIFICATION", "iterations": 1}
                ),
                PhaseResult(
                    status=PhaseStatus.COMPLETED,
                    data={"status_code": "CAFE_CONFIRMED", "iterations": 2}
                ),
            ]
            mock_phase.execute.side_effect = results
            mock_phase_cls.return_value = mock_phase
            
            # Run command WITHOUT --auto, user answers "yes" to continue
            result = runner.invoke(app, [
                "spec"
            ], input="Initial requirements\ny\n")  # y = yes to continue
            
            # Verify - should ask and continue
            assert mock_phase.execute.call_count == 2
            assert "Continue to next iteration?" in result.stdout
            assert "Spec clarification completed!" in result.stdout
    
    @pytest.mark.skip(reason="Complex CLI mocking - covered by integration tests")
    def test_non_auto_mode_runs_once(self, mock_env):
        """測試沒有 --auto 時只執行一次"""
        with patch("cafe.ui.cli.GitOperations") as mock_git_cls, \
             patch("cafe.ui.cli.ConfigManager") as mock_config_cls, \
             patch("cafe.ui.cli.AgentManager") as mock_agent_cls, \
             patch("cafe.ui.cli.PermissionHandler") as mock_perm_cls, \
             patch("cafe.ui.cli.SpecPhase") as mock_phase_cls:
            
            # Setup mocks
            mock_git = MagicMock()
            mock_git.get_current_branch.return_value = "test-issue"
            mock_git_cls.return_value = mock_git
            
            mock_config = MagicMock()
            # Mock different return values for different keys
            def mock_get(key, default=None):
                if key == "agents.pm.name":
                    return "Roger"
                elif key.startswith("agents."):
                    return {"name": "Roger", "cli": "copilot"}
                return default
            mock_config.get.side_effect = mock_get
            mock_config_cls.return_value = mock_config
            
            mock_agent_manager = MagicMock()
            mock_executor = MagicMock()
            mock_executor.config.cli.value = "copilot"
            mock_executor.config.session_id = "test-session"
            mock_agent_manager.get_agent.return_value = mock_executor
            mock_agent_cls.return_value = mock_agent_manager
            
            mock_perm = MagicMock()
            mock_perm_cls.return_value = mock_perm
            
            # Mock phase to return NEED_CLARIFICATION
            mock_phase = MagicMock()
            from cafe.core.types import PhaseStatus, PhaseResult
            
            mock_phase.execute.return_value = PhaseResult(
                status=PhaseStatus.COMPLETED,
                data={"status_code": "CAFE_NEED_CLARIFICATION", "iterations": 1}
            )
            mock_phase_cls.return_value = mock_phase
            
            # Run command WITHOUT --auto
            result = runner.invoke(app, [
                "spec",
                "--no-interactive",
                "--user-input", "test requirements"
            ])
            
            # Verify - should only run once
            assert mock_phase.execute.call_count == 1
            assert "To continue, run:" in result.stdout
