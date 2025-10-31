"""Tests for CLI."""

import pytest
from pathlib import Path
from typer.testing import CliRunner
from unittest.mock import MagicMock, Mock, patch

from aaf.ui.cli import app, _setup_agents, _build_workflow
from aaf.core.types import AgentCLI, PhaseResult, PhaseStatus, WorkflowMode
from aaf.utils.config import ConfigManager


runner = CliRunner()


class TestSetupAgents:
    """Test agent setup functionality."""

    def test_setup_agents_with_default_config(self, tmp_path: Path) -> None:
        """測試使用預設設定建立 agents"""
        config_file = tmp_path / "config.yaml"
        config_manager = ConfigManager(str(config_file))

        agent_manager = _setup_agents(config_manager)

        # 驗證三個 agents 都已註冊
        assert "Roger" in agent_manager.agents
        assert "David" in agent_manager.agents
        assert "Richard" in agent_manager.agents

        # 驗證預設使用 claude
        assert agent_manager.agents["Roger"].config.cli == AgentCLI.CLAUDE
        assert agent_manager.agents["David"].config.cli == AgentCLI.CLAUDE
        assert agent_manager.agents["Richard"].config.cli == AgentCLI.CLAUDE

    def test_setup_agents_with_custom_config(self, tmp_path: Path) -> None:
        """測試使用自訂設定建立 agents"""
        config_file = tmp_path / "config.yaml"
        config_manager = ConfigManager(str(config_file))

        # 設定自訂 agent 設定（使用 dict 結構而非預設的 list）
        custom_config = {
            "agents": {
                "pm": {"name": "CustomPM", "cli": "gemini"},
                "developer": {"name": "CustomDev", "cli": "claude"},
                "reviewer": {"name": "Richard", "cli": "cursor-agent"},
            }
        }
        config_manager.save_config(custom_config)

        agent_manager = _setup_agents(config_manager)

        # 驗證自訂設定
        assert "CustomPM" in agent_manager.agents
        assert "CustomDev" in agent_manager.agents
        assert agent_manager.agents["CustomPM"].config.cli == AgentCLI.GEMINI
        assert agent_manager.agents["Richard"].config.cli == AgentCLI.CURSOR


class TestBuildWorkflow:
    """Test workflow building functionality."""

    @patch("aaf.ui.cli.SpecPhase")
    @patch("aaf.ui.cli.PlanPhase")
    @patch("aaf.ui.cli.ImplementationPhase")
    @patch("aaf.ui.cli.ReviewPhase")
    @patch("aaf.ui.cli.PRPhase")
    def test_build_workflow_creates_all_phases(
        self,
        mock_pr: Mock,
        mock_review: Mock,
        mock_impl: Mock,
        mock_plan: Mock,
        mock_req: Mock,
        tmp_path: Path,
    ) -> None:
        """測試建立 workflow 會初始化所有 5 個 phases"""
        config_file = tmp_path / "config.yaml"
        config_manager = ConfigManager(str(config_file))
        agent_manager = _setup_agents(config_manager)
        permission_handler = MagicMock()

        workflow = _build_workflow(
            mode=WorkflowMode.LOCAL,
            spec_file="req.md",
            issue_id=None,
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            config_manager=config_manager,
        )

        # 驗證所有 phase 都被建立
        mock_req.assert_called_once()
        mock_plan.assert_called_once()
        mock_impl.assert_called_once()
        mock_review.assert_called_once()
        mock_pr.assert_called_once()

        # 驗證 workflow 有 5 個 phases
        assert len(workflow.phases) == 5

    @patch("aaf.ui.cli.SpecPhase")
    def test_build_workflow_passes_correct_mode(
        self,
        mock_req: Mock,
        tmp_path: Path,
    ) -> None:
        """測試 workflow 正確傳遞 workflow mode"""
        config_file = tmp_path / "config.yaml"
        config_manager = ConfigManager(str(config_file))
        agent_manager = _setup_agents(config_manager)
        permission_handler = MagicMock()

        _build_workflow(
            mode=WorkflowMode.GITHUB,
            spec_file="req.md",
            issue_id="123",
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            config_manager=config_manager,
        )

        # 驗證 SpecPhase 收到正確的 mode
        call_kwargs = mock_req.call_args.kwargs
        assert call_kwargs["workflow_mode"] == WorkflowMode.GITHUB
        assert call_kwargs["issue_id"] == "123"


class TestRunCommand:
    """Test run command."""

    def test_run_local_mode_success(self, tmp_path: Path) -> None:
        """測試 local mode 成功執行"""
        # 建立測試檔案
        req_file = tmp_path / "spec.md"
        req_file.write_text("Test requirements")
        config_file = tmp_path / "config.yaml"

        with patch("aaf.ui.cli._build_workflow") as mock_build:
            # Mock workflow execution
            mock_workflow = MagicMock()
            mock_workflow.execute.return_value = [
                PhaseResult(status=PhaseStatus.COMPLETED, message="Phase 1 done"),
                PhaseResult(status=PhaseStatus.COMPLETED, message="Phase 2 done"),
            ]
            mock_build.return_value = mock_workflow

            result = runner.invoke(
                app,
                [
                    "run",
                    "--mode", "local",
                    "--spec", str(req_file),
                    "--config", str(config_file),
                ]
            )

            assert result.exit_code == 0
            assert "Starting AAF workflow" in result.stdout
            assert "Mode: local" in result.stdout

    def test_run_github_mode_without_issue_fails(self, tmp_path: Path) -> None:
        """測試 github mode 沒有 issue_id 會失敗"""
        config_file = tmp_path / "config.yaml"

        result = runner.invoke(
            app,
            [
                "run",
                "--mode", "github",
                "--config", str(config_file),
            ]
        )

        assert result.exit_code == 1
        assert "--issue is required for github mode" in result.stdout

    def test_run_local_mode_missing_requirements_file_fails(self, tmp_path: Path) -> None:
        """測試 local mode 缺少 spec 檔案會失敗"""
        config_file = tmp_path / "config.yaml"

        result = runner.invoke(
            app,
            [
                "run",
                "--mode", "local",
                "--spec", "nonexistent.md",
                "--config", str(config_file),
            ]
        )

        assert result.exit_code == 1
        assert "Spec file not found" in result.stdout

    def test_run_invalid_mode_fails(self, tmp_path: Path) -> None:
        """測試無效的 mode 會失敗"""
        config_file = tmp_path / "config.yaml"

        result = runner.invoke(
            app,
            [
                "run",
                "--mode", "invalid",
                "--config", str(config_file),
            ]
        )

        assert result.exit_code == 1
        assert "Invalid mode 'invalid'" in result.stdout

    def test_run_exits_with_error_on_failed_phase(self, tmp_path: Path) -> None:
        """測試當有 phase 失敗時會回傳錯誤碼"""
        req_file = tmp_path / "spec.md"
        req_file.write_text("Test requirements")
        config_file = tmp_path / "config.yaml"

        with patch("aaf.ui.cli._build_workflow") as mock_build:
            mock_workflow = MagicMock()
            mock_workflow.execute.return_value = [
                PhaseResult(status=PhaseStatus.COMPLETED, message="Done"),
                PhaseResult(status=PhaseStatus.FAILED, message="Error"),
            ]
            mock_build.return_value = mock_workflow

            result = runner.invoke(
                app,
                [
                    "run",
                    "--mode", "local",
                    "--spec", str(req_file),
                    "--config", str(config_file),
                ]
            )

            assert result.exit_code == 1
            assert "FAILED" in result.stdout


class TestVersionCommand:
    """Test version command."""

    def test_version_shows_version_number(self) -> None:
        """測試 version 指令顯示版本號"""
        result = runner.invoke(app, ["version"])

        assert result.exit_code == 0
        assert "0.1.0" in result.stdout


class TestConfigCommand:
    """Test config command."""

    def test_config_list_all(self, tmp_path: Path) -> None:
        """測試列出所有設定"""
        config_file = tmp_path / "config.yaml"
        config_manager = ConfigManager(str(tmp_path))
        config_manager.set("test.key", "value")  # set() already calls save_config()

        result = runner.invoke(
            app,
            ["config", "--list", "--config", str(config_file)]
        )

        assert result.exit_code == 0
        assert "test:" in result.stdout or "test" in result.stdout
        assert "value" in result.stdout

    def test_config_get_existing_key(self, tmp_path: Path) -> None:
        """測試取得存在的設定值"""
        config_file = tmp_path / "config.yaml"
        # Save custom config with dict structure for agents
        custom_config = {
            "agents": {
                "pm": {"name": "Roger"}
            }
        }
        config_manager = ConfigManager(str(tmp_path))
        config_manager.save_config(custom_config)

        result = runner.invoke(
            app,
            ["config", "agents.pm.name", "--config", str(config_file)]
        )

        assert result.exit_code == 0
        assert "Roger" in result.stdout

    def test_config_get_nonexistent_key(self, tmp_path: Path) -> None:
        """測試取得不存在的設定值"""
        config_file = tmp_path / "config.yaml"

        result = runner.invoke(
            app,
            ["config", "nonexistent.key", "--config", str(config_file)]
        )

        assert result.exit_code == 0
        assert "Key not found" in result.stdout

    def test_config_set_value(self, tmp_path: Path) -> None:
        """測試設定值"""
        config_file = tmp_path / "config.yaml"

        result = runner.invoke(
            app,
            ["config", "test.key", "test_value", "--config", str(config_file)]
        )

        assert result.exit_code == 0
        assert "Set test.key = test_value" in result.stdout

        # 驗證設定已儲存 (ConfigManager takes directory, not file)
        config_manager = ConfigManager(str(tmp_path))
        assert config_manager.get("test.key") == "test_value"

    def test_config_without_args_shows_help(self, tmp_path: Path) -> None:
        """測試沒有參數時顯示提示"""
        config_file = tmp_path / "config.yaml"

        result = runner.invoke(
            app,
            ["config", "--config", str(config_file)]
        )

        assert result.exit_code == 0
        assert "Use --list" in result.stdout


class TestPlanCommand:
    """Test plan command."""

    @patch("aaf.ui.cli.PlanPhase")
    def test_plan_local_mode_success(
        self,
        mock_plan_phase: Mock,
        tmp_path: Path,
    ) -> None:
        """測試 plan 指令 local mode 成功執行"""
        # Setup: Create spec file in the expected location
        issue_name = "test-issue"
        spec_file = tmp_path / ".aaf" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Spec\n\n## 開發指南\nGuide")
        config_file = tmp_path / "config.yaml"

        # Mock phase execution
        mock_phase_instance = MagicMock()
        mock_phase_instance.execute.return_value = PhaseResult(
            status=PhaseStatus.COMPLETED,
            message="Plan completed",
            data={"iterations": 2}
        )
        mock_plan_phase.return_value = mock_phase_instance

        # Execute (note: using tmp_path as cwd won't work for our test, so we'll use absolute path checking)
        # We need to temporarily change directory or mock Path.exists
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(
                app,
                ["plan", issue_name, "--config", str(config_file)]
            )
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 0
        assert "Implementation plan completed" in result.stdout
        assert "Iterations: 2" in result.stdout
        mock_plan_phase.assert_called_once()

    @patch("aaf.ui.cli.PlanPhase")
    def test_plan_github_mode_with_issue(
        self,
        mock_plan_phase: Mock,
        tmp_path: Path,
    ) -> None:
        """測試 plan 指令 github mode 使用 issue ID"""
        # Setup: GitHub mode still checks if spec file exists first
        issue_name = "test-issue"
        spec_file = tmp_path / ".aaf" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Spec\n\n## 開發指南\nGuide")
        config_file = tmp_path / "config.yaml"

        # Mock phase execution
        mock_phase_instance = MagicMock()
        mock_phase_instance.execute.return_value = PhaseResult(
            status=PhaseStatus.COMPLETED,
            message="Plan completed",
            data={"iterations": 1}
        )
        mock_plan_phase.return_value = mock_phase_instance

        # Execute
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(
                app,
                ["plan", issue_name, "-m", "github", "-i", "123", "--config", str(config_file)]
            )
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 0
        assert "GitHub Issue: #123" in result.stdout
        mock_plan_phase.assert_called_once()

    @patch("aaf.ui.cli.PlanPhase")
    def test_plan_fails_with_error(
        self,
        mock_plan_phase: Mock,
        tmp_path: Path,
    ) -> None:
        """測試 plan 指令執行失敗"""
        # Setup: Create spec file in the expected location
        issue_name = "test-issue"
        spec_file = tmp_path / ".aaf" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Spec")
        config_file = tmp_path / "config.yaml"

        # Mock phase execution failure
        mock_phase_instance = MagicMock()
        mock_phase_instance.execute.return_value = PhaseResult(
            status=PhaseStatus.FAILED,
            message="Missing dev guide"
        )
        mock_plan_phase.return_value = mock_phase_instance

        # Execute
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(
                app,
                ["plan", issue_name, "--config", str(config_file)]
            )
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 1
        assert "Plan phase failed" in result.stdout

    def test_plan_invalid_mode_fails(self, tmp_path: Path) -> None:
        """測試 plan 指令使用無效 mode"""
        issue_name = "test-issue"
        config_file = tmp_path / "config.yaml"

        result = runner.invoke(
            app,
            ["plan", issue_name, "-m", "invalid", "--config", str(config_file)]
        )

        assert result.exit_code == 1
        assert "Invalid mode" in result.stdout
