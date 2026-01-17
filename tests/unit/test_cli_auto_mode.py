import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import yaml
import pytest
import typer

from cafe.ui.cli import app
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import PhaseResult, PhaseStatus

@pytest.fixture
def temp_repo_dir(tmp_path):
    """建立一個暫時的 Git 倉庫環境"""
    (tmp_path / ".cafe").mkdir()
    (tmp_path / ".cafe" / "issues").mkdir()
    with open(tmp_path / ".cafe" / "config.yaml", 'w') as f:
        yaml.dump({
            "agents": {
                "pm": "Roger",
                "developer": "Nick",
                "reviewer": "Richard"
            },
            "auto": {
                "max_review_iterations": 5
            }
        }, f)
    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(old_cwd)

@pytest.fixture
def mock_git_ops():
    """Mock GitOperations"""
    with patch('cafe.ui.cli.GitOperations') as MockGit:
        mock_instance = MockGit.return_value
        mock_instance.get_current_branch.return_value = "test-issue"
        mock_instance.branch_exists.return_value = True
        mock_instance.is_valid_branch.return_value = True
        mock_instance.has_uncommitted_changes.return_value = False
        yield mock_instance

@pytest.fixture
def prepared_issue(temp_repo_dir):
    """建立一個已經 prepare 過的 issue"""
    issue_name = "test-issue"
    issue_dir = temp_repo_dir / ".cafe" / "issues" / issue_name
    issue_dir.mkdir(parents=True)
    config_data = {
        "base_branch": "main",
        "feature_branch": issue_name,
        "auto": {"max_review_iterations": 5},
        "spec": {"input_method": "manual", "rigor": "medium", "template": "auto"},
        "plan": {"template": "auto"}
    }
    with open(issue_dir / "issue.yaml", 'w') as f:
        yaml.dump(config_data, f)
    return issue_dir

@pytest.fixture
def force_interactive(monkeypatch):
    """強制 CLI 進入互動模式，方便測試 --auto"""
    monkeypatch.setenv("CAFE_FORCE_INTERACTIVE", "1")

class TestAutoModeVariableScope:
    def test_spec_auto_uses_correct_variable_name(self, temp_repo_dir, mock_git_ops, prepared_issue, force_interactive):
        """測試 spec --auto 使用正確的變數名稱"""
        with patch('cafe.ui.cli.SpecPhase') as MockSpecPhase, \
             patch('cafe.ui.cli.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            mock_phase = MagicMock()
            MockSpecPhase.return_value = mock_phase
            mock_result_ready = MagicMock()
            mock_result_ready.status.value = "completed"
            mock_result_ready.data = {
                "status_code": "CAFE_READY_FOR_REVIEW",
                "iterations": 1,
                "spec_file": str(prepared_issue / "spec" / "iteration_001" / "output.md")
            }
            mock_result_confirmed = MagicMock()
            mock_result_confirmed.status.value = "completed"
            mock_result_confirmed.data = {
                "status_code": "CAFE_CONFIRMED",
                "iterations": 2,
                "spec_file": str(prepared_issue / "spec" / "iteration_001" / "output.md")
            }
            mock_phase.execute.side_effect = [mock_result_ready, mock_result_confirmed]
            from typer.testing import CliRunner
            runner = CliRunner()
            with patch('cafe.ui.cli.PermissionHandler'), \
                 patch('cafe.ui.cli._setup_agents'), \
                 patch('cafe.ui.cli.is_branch_initialized', return_value=True):
                result = runner.invoke(app, ["spec", "--auto"])
                assert result.exit_code == 0, f"Output: {result.output}"
                assert "✅ Spec clarification completed!" in result.output
                # 驗證是否有呼叫下一階段 (plan)
                assert mock_run.called
                args = mock_run.call_args[0][0]
                assert "plan" in args
                assert "--auto" in args

class TestAutoModeConfigPreservation:
    def test_spec_phase_preserves_issue_config(self, temp_repo_dir, mock_git_ops, prepared_issue):
        """測試 spec phase 不會覆寫 issue config 中的其他欄位"""
        config_file = prepared_issue / "issue.yaml"
        with open(config_file, 'r') as f:
            config_data = yaml.safe_load(f)
        config_data["worktree_path"] = "/some/worktree/path"
        with open(config_file, 'w') as f:
            yaml.dump(config_data, f)
        from cafe.phases.spec_phase import SpecPhase
        from cafe.agents.manager import AgentManager
        from cafe.core.permission import PermissionHandler
        phase = SpecPhase(
            agent_manager=MagicMock(spec=AgentManager),
            permission_handler=MagicMock(spec=PermissionHandler),
            git_ops=mock_git_ops,
        )
        phase._save_issue_config()
        with open(config_file, 'r') as f:
            final_config = yaml.safe_load(f)
        assert "worktree_path" in final_config
        assert final_config["worktree_path"] == "/some/worktree/path"
        assert final_config["base_branch"] == "main"
        assert final_config["feature_branch"] == "test-issue"
        assert final_config["auto"]["max_review_iterations"] == 5
        assert final_config["spec"]["rigor"] == "medium"

    def test_plan_phase_preserves_issue_config(self, temp_repo_dir, mock_git_ops, prepared_issue, force_interactive):
        """測試 plan phase 不會覆寫 issue config"""
        config_file = prepared_issue / "issue.yaml"
        with open(config_file, 'r') as f:
            config_data = yaml.safe_load(f)
        config_data["worktree_path"] = "/some/worktree/path"
        with open(config_file, 'w') as f:
            yaml.dump(config_data, f)
        spec_iter_dir = prepared_issue / "spec" / "iteration_001"
        spec_iter_dir.mkdir(parents=True, exist_ok=True)
        (spec_iter_dir / "output.md").write_text("# Test Spec")
        with patch('cafe.ui.cli.PlanPhase') as MockPlanPhase:
            mock_phase = MagicMock()
            MockPlanPhase.return_value = mock_phase
            mock_result = MagicMock()
            mock_result.status.value = "completed"
            mock_result.data = {"status_code": "CAFE_CONFIRMED", "iterations": 2}
            mock_phase.execute.return_value = mock_result
            with patch('cafe.ui.cli.PermissionHandler'), \
                 patch('cafe.ui.cli._setup_agents'), \
                 patch('cafe.ui.cli.typer.confirm', return_value=True), \
                 patch('cafe.ui.cli.is_branch_initialized', return_value=True):
                from typer.testing import CliRunner
                runner = CliRunner()
                result = runner.invoke(app, ["plan"])
                assert result.exit_code == 0, f"Output: {result.output}"
                with open(config_file, 'r') as f:
                    final_config = yaml.safe_load(f)
                assert final_config["worktree_path"] == "/some/worktree/path"
                assert final_config["spec"]["rigor"] == "medium"

class TestAutoModeErrorHandling:
    def test_handle_phase_exception_suppresses_output_in_auto_mode(self, temp_repo_dir, mock_git_ops, prepared_issue, force_interactive):
        """測試在 auto mode 下隱藏非必要的錯誤訊息（不應該印出 Normal error）"""
        with patch('cafe.ui.cli.SpecPhase') as MockSpecPhase:
            mock_phase = MagicMock()
            MockSpecPhase.return_value = mock_phase
            mock_phase.execute.side_effect = Exception("Normal error")
            from typer.testing import CliRunner
            runner = CliRunner()
            with patch('cafe.ui.cli.PermissionHandler'), \
                 patch('cafe.ui.cli._setup_agents'), \
                 patch('cafe.ui.cli.is_branch_initialized', return_value=True):
                result = runner.invoke(app, ["spec", "--auto"])
                assert result.exit_code != 0
                assert "Traceback" not in result.output
                # 在 auto 模式下，非程式錯誤會被靜音，只回傳 exit(1)
                assert "Normal error" not in result.output

    def test_handle_phase_exception_shows_programming_errors_in_auto_mode(self, temp_repo_dir, mock_git_ops, prepared_issue, force_interactive):
        """測試在 auto mode 下仍然顯示程式碼錯誤"""
        with patch('cafe.ui.cli.SpecPhase') as MockSpecPhase:
            mock_phase = MagicMock()
            MockSpecPhase.return_value = mock_phase
            mock_phase.execute.side_effect = NameError("name 'undefined_var' is not defined")
            from typer.testing import CliRunner
            runner = CliRunner()
            with patch('cafe.ui.cli.PermissionHandler'), \
                 patch('cafe.ui.cli._setup_agents'), \
                 patch('cafe.ui.cli.is_branch_initialized', return_value=True):
                result = runner.invoke(app, ["spec", "--auto"])
                assert result.exit_code != 0
                assert "undefined_var" in result.output

    def test_handle_phase_exception_shows_critical_errors_in_auto_mode(self, temp_repo_dir, mock_git_ops, prepared_issue, force_interactive):
        """測試在 auto mode 下仍然顯示 CriticalPhaseError"""
        from cafe.core.types import CriticalPhaseError
        with patch('cafe.ui.cli.SpecPhase') as MockSpecPhase:
            mock_phase = MagicMock()
            MockSpecPhase.return_value = mock_phase
            mock_phase.execute.side_effect = CriticalPhaseError(
                message="Something critical", error_type="rate_limit", phase_name="SpecPhase"
            )
            from typer.testing import CliRunner
            runner = CliRunner()
            with patch('cafe.ui.cli.PermissionHandler'), \
                 patch('cafe.ui.cli._setup_agents'), \
                 patch('cafe.ui.cli.is_branch_initialized', return_value=True):
                result = runner.invoke(app, ["spec", "--auto"])
                assert result.exit_code != 0
                # _handle_phase_exception 對 rate_limit 會印出特定訊息
                assert "API rate limit reached" in result.output

    def test_handle_phase_exception_shows_errors_in_non_auto_mode(self, temp_repo_dir, mock_git_ops, prepared_issue, force_interactive):
        """測試在非 auto mode 下仍然顯示所有錯誤訊息"""
        spec_iter_dir = prepared_issue / "spec" / "iteration_001"
        spec_iter_dir.mkdir(parents=True, exist_ok=True)
        (spec_iter_dir / "output.md").write_text("# Test Spec")
        with patch('cafe.ui.cli.SpecPhase') as MockSpecPhase:
            mock_phase = MagicMock()
            MockSpecPhase.return_value = mock_phase
            mock_phase.execute.side_effect = Exception("Any error")
            from typer.testing import CliRunner
            runner = CliRunner()
            with patch('cafe.ui.cli.PermissionHandler'), \
                 patch('cafe.ui.cli._setup_agents'), \
                 patch('cafe.ui.cli.is_branch_initialized', return_value=True):
                result = runner.invoke(app, ["spec"])
                assert result.exit_code != 0
                assert "Any error" in result.output

    def test_execute_next_phase_auto_does_not_print_redundant_errors(self, temp_repo_dir, mock_git_ops, prepared_issue, force_interactive):
        """測試 _execute_next_phase_auto 不會重覆列印錯誤訊息"""
        with patch('cafe.ui.cli.SpecPhase') as MockSpecPhase:
            mock_phase = MagicMock()
            MockSpecPhase.return_value = mock_phase
            mock_result = MagicMock()
            mock_result.status.value = "failed"
            mock_result.message = "Execution failed"
            mock_phase.execute.return_value = mock_result
            from typer.testing import CliRunner
            runner = CliRunner()
            with patch('cafe.ui.cli.PermissionHandler'), \
                 patch('cafe.ui.cli._setup_agents'), \
                 patch('cafe.ui.cli.console.print') as mock_print, \
                 patch('cafe.ui.cli.is_branch_initialized', return_value=True):
                result = runner.invoke(app, ["spec", "--auto"])
                assert result.exit_code != 0
                print_calls = [args[0] for args, kwargs in mock_print.call_args_list if len(args) > 0 and isinstance(args[0], str)]
                failed_msg_count = sum(1 for call in print_calls if "Execution failed" in call)
                assert failed_msg_count <= 1

class TestReviewMaxIterationsConfig:
    def test_review_reads_max_iterations_from_config_not_issue_yaml(self, temp_repo_dir, mock_git_ops, prepared_issue):
        """測試 review phase 是讀取全域 config 的 max_iterations"""
        config_file = prepared_issue / "issue.yaml"
        with open(config_file, 'r') as f:
            config_data = yaml.safe_load(f)
        config_data["auto"] = {"max_review_iterations": 999}
        with open(config_file, 'w') as f:
            yaml.dump(config_data, f)
        with open(temp_repo_dir / ".cafe" / "config.yaml", 'w') as f:
            yaml.dump({"auto": {"max_review_iterations": 2}}, f)
        spec_iter_dir = prepared_issue / "spec" / "iteration_001"
        spec_iter_dir.mkdir(parents=True, exist_ok=True)
        (spec_iter_dir / "output.md").write_text("# Test Spec")
        plan_iter_dir = prepared_issue / "plan" / "iteration_001"
        plan_iter_dir.mkdir(parents=True, exist_ok=True)
        (plan_iter_dir / "output.md").write_text("# Test Plan")
        with patch('cafe.ui.cli.ReviewPhase') as MockReviewPhase:
            mock_phase = MagicMock()
            MockReviewPhase.return_value = mock_phase
            mock_result = MagicMock()
            mock_result.status.value = "completed"
            mock_result.data = {"status_code": "CAFE_CONFIRMED"}
            mock_phase.execute.return_value = mock_result
            from typer.testing import CliRunner
            runner = CliRunner()
            with patch('cafe.ui.cli.PermissionHandler'), \
                 patch('cafe.ui.cli._setup_agents'), \
                 patch('cafe.ui.cli.is_branch_initialized', return_value=True):
                result = runner.invoke(app, ["review", "--auto"])
                assert MockReviewPhase.called, f"Output: {result.output}"

    def test_review_respects_config_limit_when_exceeded(self, temp_repo_dir, mock_git_ops, prepared_issue):
        """測試 review phase 達到上限後停止"""
        with open(temp_repo_dir / ".cafe" / "config.yaml", 'w') as f:
            yaml.dump({"auto": {"max_review_iterations": 1}}, f)
        spec_iter_dir = prepared_issue / "spec" / "iteration_001"
        spec_iter_dir.mkdir(parents=True, exist_ok=True)
        (spec_iter_dir / "output.md").write_text("# Test Spec")
        plan_iter_dir = prepared_issue / "plan" / "iteration_001"
        plan_iter_dir.mkdir(parents=True, exist_ok=True)
        (plan_iter_dir / "output.md").write_text("# Test Plan")
        review_iter_dir = prepared_issue / "review" / "iteration_001"
        review_iter_dir.mkdir(parents=True, exist_ok=True)
        (review_iter_dir / "output.md").write_text("# Test Review")
        with patch('cafe.ui.cli.ReviewPhase') as MockReviewPhase:
            mock_phase = MagicMock()
            MockReviewPhase.return_value = mock_phase
            mock_result = MagicMock()
            mock_result.status.value = "completed"
            mock_result.data = {"status_code": "CAFE_NEED_CHANGES"}
            mock_phase.execute.return_value = mock_result
            from typer.testing import CliRunner
            runner = CliRunner()
            with patch('cafe.ui.cli.PermissionHandler'), \
                 patch('cafe.ui.cli._setup_agents'), \
                 patch('cafe.ui.cli.is_branch_initialized', return_value=True):
                result = runner.invoke(app, ["review", "--auto"])
                assert "Review loop limit reached" in result.output
                assert mock_phase.execute.call_count == 1