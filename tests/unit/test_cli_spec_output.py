"""Tests for spec command output messages based on status codes.

測試 cafe spec 命令根據不同的 status code 顯示正確的下一步提示。
"""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from typer.testing import CliRunner

from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import PhaseResult, PhaseStatus
from cafe.ui.cli import app


@pytest.fixture
def runner():
    """Create CLI test runner."""
    return CliRunner()


@pytest.fixture
def mock_git_ops():
    """Mock GitOperations."""
    with patch("cafe.ui.cli.GitOperations") as mock:
        mock_instance = Mock()
        mock_instance.get_current_branch.return_value = "test-branch"
        mock_instance.get_repo_root.return_value = Path.cwd()
        mock.return_value = mock_instance
        yield mock


@pytest.fixture
def mock_spec_phase():
    """Mock SpecPhase."""
    with patch("cafe.ui.cli.SpecPhase") as mock:
        yield mock


@pytest.fixture
def mock_agent_manager():
    """Mock AgentManager."""
    with patch("cafe.ui.cli.AgentManager") as mock:
        mock_instance = Mock()
        mock.return_value = mock_instance
        yield mock


@pytest.fixture
def mock_permission_handler():
    """Mock PermissionHandler."""
    with patch("cafe.ui.cli.PermissionHandler") as mock:
        mock_instance = Mock()
        mock.return_value = mock_instance
        yield mock


@pytest.fixture
def setup_test_env(tmp_path, monkeypatch):
    """Setup test environment with issue config."""
    # Create issue directory and config
    issue_dir = tmp_path / ".cafe" / "issues" / "test-branch"
    issue_dir.mkdir(parents=True, exist_ok=True)

    config_file = issue_dir / "config.yaml"
    config_file.write_text("issue_name: test-branch\nbase_branch: main\n")

    # Change to tmp_path
    monkeypatch.chdir(tmp_path)

    return tmp_path


class TestSpecCommandOutputWithReadyForReview:
    """測試 READY_FOR_REVIEW 狀態的輸出訊息。"""

    def test_ready_for_review_prompts_user_to_run_spec_again(
        self, runner, mock_git_ops, mock_spec_phase, mock_agent_manager, mock_permission_handler, setup_test_env
    ):
        """READY_FOR_REVIEW 狀態應提示使用者再次執行 cafe spec 確認。"""
        # Setup: Phase returns READY_FOR_REVIEW
        mock_phase_instance = Mock()
        mock_phase_instance.execute.return_value = PhaseResult(
            status=PhaseStatus.COMPLETED,
            message="Spec draft completed",
            data={
                "iterations": 2,
                "status_code": "CAFE_READY_FOR_REVIEW",
            },
        )
        mock_spec_phase.return_value = mock_phase_instance

        # Execute with interactive mode and user input
        result = runner.invoke(app, ["spec", "--interactive", "--user-input", "test input"])

        # Verify
        assert result.exit_code == 0
        assert "✅ Spec draft completed!" in result.stdout
        assert "Please review the spec and run:" in result.stdout
        assert "cafe spec" in result.stdout
        # Should NOT suggest cafe plan
        assert "cafe plan" not in result.stdout

    def test_ready_for_review_shows_saved_location(
        self, runner, mock_git_ops, mock_spec_phase, mock_agent_manager, mock_permission_handler, setup_test_env
    ):
        """READY_FOR_REVIEW 狀態應顯示 spec 儲存位置。"""
        mock_phase_instance = Mock()
        mock_phase_instance.execute.return_value = PhaseResult(
            status=PhaseStatus.COMPLETED,
            message="Spec draft completed",
            data={
                "iterations": 1,
                "status_code": "CAFE_READY_FOR_REVIEW",
            },
        )
        mock_spec_phase.return_value = mock_phase_instance

        result = runner.invoke(app, ["spec", "--interactive", "--user-input", "test"])

        assert result.exit_code == 0
        assert "Saved to: .cafe/issues/test-branch/spec" in result.stdout


class TestSpecCommandOutputWithConfirmed:
    """測試 CONFIRMED 狀態的輸出訊息。"""

    def test_confirmed_prompts_user_to_run_plan(
        self, runner, mock_git_ops, mock_spec_phase, mock_agent_manager, mock_permission_handler, setup_test_env
    ):
        """CONFIRMED 狀態應提示使用者執行 cafe plan。"""
        # Setup: Phase returns CONFIRMED
        mock_phase_instance = Mock()
        mock_phase_instance.execute.return_value = PhaseResult(
            status=PhaseStatus.COMPLETED,
            message="Spec completed",
            data={
                "iterations": 3,
                "status_code": "CAFE_CONFIRMED",
            },
        )
        mock_spec_phase.return_value = mock_phase_instance

        # Execute
        result = runner.invoke(app, ["spec", "--interactive", "--user-input", "test"])

        # Verify
        assert result.exit_code == 0
        assert "✅ Spec clarification completed!" in result.stdout
        assert "Next step:" in result.stdout
        assert "cafe plan" in result.stdout
        # Should NOT suggest running cafe spec again
        assert "Please review the spec" not in result.stdout

    def test_confirmed_shows_iteration_count(
        self, runner, mock_git_ops, mock_spec_phase, mock_agent_manager, mock_permission_handler, setup_test_env
    ):
        """CONFIRMED 狀態應顯示 iteration 次數。"""
        mock_phase_instance = Mock()
        mock_phase_instance.execute.return_value = PhaseResult(
            status=PhaseStatus.COMPLETED,
            message="Spec completed",
            data={
                "iterations": 5,
                "status_code": "CAFE_CONFIRMED",
            },
        )
        mock_spec_phase.return_value = mock_phase_instance

        result = runner.invoke(app, ["spec", "--interactive", "--user-input", "test"])

        assert result.exit_code == 0
        assert "Iterations: 5" in result.stdout


class TestSpecCommandOutputWithNeedClarification:
    """測試 NEED_CLARIFICATION 狀態的輸出訊息。"""

    def test_need_clarification_prompts_to_continue(
        self, runner, mock_git_ops, mock_spec_phase, mock_agent_manager, mock_permission_handler, setup_test_env
    ):
        """NEED_CLARIFICATION 狀態應提示使用者繼續執行 cafe spec。"""
        mock_phase_instance = Mock()
        mock_phase_instance.execute.return_value = PhaseResult(
            status=PhaseStatus.COMPLETED,
            message="Need clarification",
            data={
                "iterations": 1,
                "status_code": "CAFE_NEED_CLARIFICATION",
            },
        )
        mock_spec_phase.return_value = mock_phase_instance

        result = runner.invoke(app, ["spec", "--interactive", "--user-input", "test"])

        assert result.exit_code == 0
        assert "💬 Agent needs clarification" in result.stdout
        assert "To continue, run:" in result.stdout
        assert "cafe spec" in result.stdout


class TestSpecCommandOutputWithRejected:
    """測試 REJECTED 狀態的輸出訊息。"""

    def test_rejected_shows_error_message(
        self, runner, mock_git_ops, mock_spec_phase, mock_agent_manager, mock_permission_handler, setup_test_env
    ):
        """REJECTED 狀態應顯示錯誤訊息且不提示下一步。"""
        mock_phase_instance = Mock()
        mock_phase_instance.execute.return_value = PhaseResult(
            status=PhaseStatus.COMPLETED,
            message="Spec rejected",
            data={
                "iterations": 2,
                "status_code": "CAFE_REJECTED",
            },
        )
        mock_spec_phase.return_value = mock_phase_instance

        result = runner.invoke(app, ["spec", "--interactive", "--user-input", "test"])

        assert result.exit_code == 0
        assert "❌ Spec rejected by agent" in result.stdout
        # Should NOT suggest next steps
        assert "Next step" not in result.stdout
        assert "cafe plan" not in result.stdout


class TestSpecCommandOutputComparison:
    """比較測試：確保不同狀態的輸出訊息確實不同。"""

    def test_ready_for_review_and_confirmed_have_different_messages(
        self, runner, mock_git_ops, mock_spec_phase, mock_agent_manager, mock_permission_handler, setup_test_env
    ):
        """確認 READY_FOR_REVIEW 和 CONFIRMED 的訊息確實不同。"""
        # Test READY_FOR_REVIEW
        mock_phase_instance = Mock()
        mock_phase_instance.execute.return_value = PhaseResult(
            status=PhaseStatus.COMPLETED,
            message="Draft",
            data={"iterations": 1, "status_code": "CAFE_READY_FOR_REVIEW"},
        )
        mock_spec_phase.return_value = mock_phase_instance

        result_ready = runner.invoke(app, ["spec", "--interactive", "--user-input", "test"])

        # Test CONFIRMED
        mock_phase_instance.execute.return_value = PhaseResult(
            status=PhaseStatus.COMPLETED,
            message="Confirmed",
            data={"iterations": 1, "status_code": "CAFE_CONFIRMED"},
        )

        result_confirmed = runner.invoke(app, ["spec", "--interactive", "--user-input", "test"])

        # Verify they have different messages
        assert "Spec draft completed!" in result_ready.stdout
        assert "cafe spec" in result_ready.stdout
        assert "cafe plan" not in result_ready.stdout

        assert "Spec clarification completed!" in result_confirmed.stdout
        assert "cafe plan" in result_confirmed.stdout
        assert "Please review the spec" not in result_confirmed.stdout


class TestSpecRigorConfigLoading:
    """測試 spec 命令從 config 載入 rigor 設定。"""

    @pytest.fixture
    def setup_env_with_rigor(self, tmp_path, monkeypatch):
        """Setup test environment with issue config containing rigor setting."""
        import yaml
        # Create issue directory and config
        issue_dir = tmp_path / ".cafe" / "issues" / "test-branch"
        issue_dir.mkdir(parents=True, exist_ok=True)

        config_file = issue_dir / "config.yaml"
        config_data = {
            "base_branch": "main",
            "feature_branch": "test-branch",
            "spec": {
                "rigor": "high"
            }
        }
        with open(config_file, 'w', encoding='utf-8') as f:
            yaml.dump(config_data, f)

        # Change to tmp_path
        monkeypatch.chdir(tmp_path)
        return tmp_path

    @pytest.fixture
    def setup_env_without_rigor(self, tmp_path, monkeypatch):
        """Setup test environment with issue config without rigor setting."""
        import yaml
        # Create issue directory and config
        issue_dir = tmp_path / ".cafe" / "issues" / "test-branch"
        issue_dir.mkdir(parents=True, exist_ok=True)

        config_file = issue_dir / "config.yaml"
        config_data = {
            "base_branch": "main",
            "feature_branch": "test-branch"
        }
        with open(config_file, 'w', encoding='utf-8') as f:
            yaml.dump(config_data, f)

        # Change to tmp_path
        monkeypatch.chdir(tmp_path)
        return tmp_path

    def test_loads_rigor_from_config(
        self, runner, mock_git_ops, mock_spec_phase, mock_agent_manager,
        mock_permission_handler, setup_env_with_rigor
    ):
        """應該從 issue config 載入 rigor 設定。"""
        from cafe.core.types import SpecRigor

        # Setup: Phase returns success
        mock_phase_instance = Mock()
        mock_phase_instance.execute.return_value = PhaseResult(
            status=PhaseStatus.COMPLETED,
            message="Spec confirmed",
            data={"iterations": 1, "status_code": "CAFE_CONFIRMED"},
        )
        mock_spec_phase.return_value = mock_phase_instance

        # Execute without --rigor flag
        result = runner.invoke(app, ["spec", "--interactive", "--user-input", "test"])

        # Verify: SpecPhase was created with rigor=high from config
        assert result.exit_code == 0
        args, kwargs = mock_spec_phase.call_args
        assert kwargs.get("rigor") == SpecRigor.HIGH

    def test_cli_flag_overrides_config_rigor(
        self, runner, mock_git_ops, mock_spec_phase, mock_agent_manager,
        mock_permission_handler, setup_env_with_rigor
    ):
        """CLI flag 應該覆蓋 config 中的 rigor 設定。"""
        from cafe.core.types import SpecRigor

        # Setup: Phase returns success
        mock_phase_instance = Mock()
        mock_phase_instance.execute.return_value = PhaseResult(
            status=PhaseStatus.COMPLETED,
            message="Spec confirmed",
            data={"iterations": 1, "status_code": "CAFE_CONFIRMED"},
        )
        mock_spec_phase.return_value = mock_phase_instance

        # Execute with --rigor low (config has high)
        result = runner.invoke(app, ["spec", "--rigor", "low", "--interactive", "--user-input", "test"])

        # Verify: SpecPhase was created with rigor=low from CLI flag
        assert result.exit_code == 0
        args, kwargs = mock_spec_phase.call_args
        assert kwargs.get("rigor") == SpecRigor.LOW

    def test_no_rigor_when_not_in_config_or_flag(
        self, runner, mock_git_ops, mock_spec_phase, mock_agent_manager,
        mock_permission_handler, setup_env_without_rigor
    ):
        """當 config 和 flag 都沒有 rigor 時，應該傳入 None。"""
        # Setup: Phase returns success
        mock_phase_instance = Mock()
        mock_phase_instance.execute.return_value = PhaseResult(
            status=PhaseStatus.COMPLETED,
            message="Spec confirmed",
            data={"iterations": 1, "status_code": "CAFE_CONFIRMED"},
        )
        mock_spec_phase.return_value = mock_phase_instance

        # Execute without --rigor flag and config has no rigor
        result = runner.invoke(app, ["spec", "--interactive", "--user-input", "test"])

        # Verify: SpecPhase was created with rigor=None
        assert result.exit_code == 0
        args, kwargs = mock_spec_phase.call_args
        assert kwargs.get("rigor") is None

    def test_displays_rigor_when_loaded_from_config(
        self, runner, mock_git_ops, mock_spec_phase, mock_agent_manager,
        mock_permission_handler, setup_env_with_rigor
    ):
        """應該顯示從 config 載入的 rigor 設定。"""
        # Setup: Phase returns success
        mock_phase_instance = Mock()
        mock_phase_instance.execute.return_value = PhaseResult(
            status=PhaseStatus.COMPLETED,
            message="Spec confirmed",
            data={"iterations": 1, "status_code": "CAFE_CONFIRMED"},
        )
        mock_spec_phase.return_value = mock_phase_instance

        # Execute
        result = runner.invoke(app, ["spec", "--interactive", "--user-input", "test"])

        # Verify: Output shows rigor level
        assert result.exit_code == 0
        assert "Rigor: high" in result.stdout
