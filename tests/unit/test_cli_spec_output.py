"""Tests for spec command output messages based on status codes.

測試 cafe spec 命令根據不同 status code 顯示正確下一步提示.
"""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from typer.testing import CliRunner

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
def mock_execute_alias():
    """Mock spec alias execution."""
    with patch("cafe.ui.cli._execute_single_step_alias") as mock:
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
    from tests.conftest import create_minimal_config

    # Create global config.yaml
    create_minimal_config(tmp_path)

    # Create issue directory and config
    issue_dir = tmp_path / ".cafe" / "issues" / "test-branch"
    issue_dir.mkdir(parents=True, exist_ok=True)

    config_file = issue_dir / "issue.yaml"
    config_file.write_text("issue_name: test-branch\nbase_branch: main\n")

    # Change to tmp_path
    monkeypatch.chdir(tmp_path)

    return tmp_path


class TestSpecCommandOutputWithReadyForReview:
    """測試 READY_FOR_REVIEW 狀態輸出訊息."""

    def test_ready_for_review_prompts_user_to_continue_with_make(
        self, runner, mock_git_ops, mock_execute_alias, mock_agent_manager, mock_permission_handler, setup_test_env
    ):
        """READY_FOR_REVIEW 狀態應提示使用者回到 workflow 主入口."""
        mock_execute_alias.return_value = {
            "iterations": 2,
            "status_code": "ready_for_review",
        }

        # Execute with interactive mode and user input
        result = runner.invoke(app, ["spec", "--interactive", "--user-input", "test input"])

        # Verify
        assert result.exit_code == 0
        assert "✅ Spec draft completed!" in result.stdout
        assert "Please review the spec, then continue with:" in result.stdout
        assert "cafe make" in result.stdout
        assert "Please review the spec and run:" not in result.stdout

    def test_ready_for_review_shows_saved_location(
        self, runner, mock_git_ops, mock_execute_alias, mock_agent_manager, mock_permission_handler, setup_test_env
    ):
        """READY_FOR_REVIEW 狀態應顯示 spec 儲存位置."""
        mock_execute_alias.return_value = {
            "iterations": 1,
            "status_code": "ready_for_review",
            "output_file": ".cafe/issues/test-branch/spec/iteration_001/output.md",
        }

        result = runner.invoke(app, ["spec", "--interactive", "--user-input", "test"])

        assert result.exit_code == 0
        assert "Saved to: .cafe/issues/test-branch/spec/iteration_001/output.md" in result.stdout

    def test_ready_for_review_uses_baton_pause_without_status_code(
        self, runner, mock_git_ops, mock_execute_alias, mock_agent_manager, mock_permission_handler, setup_test_env
    ):
        """confirm_output baton should render review-ready messaging without relying on status_code."""
        mock_execute_alias.return_value = {
            "iterations": 2,
            "handoff_owner": "user",
            "handoff_intent": "confirm_output",
        }

        result = runner.invoke(app, ["spec", "--interactive", "--user-input", "test input"])

        assert result.exit_code == 0
        assert "✅ Spec draft completed!" in result.stdout
        assert "Please review the spec, then continue with:" in result.stdout


class TestSpecCommandOutputWithConfirmed:
    """測試 CONFIRMED 狀態輸出訊息."""

    def test_confirmed_prompts_user_to_continue_workflow(
        self, runner, mock_git_ops, mock_execute_alias, mock_agent_manager, mock_permission_handler, setup_test_env
    ):
        """CONFIRMED 狀態應提示使用者回到 workflow 主入口."""
        mock_execute_alias.return_value = {
            "iterations": 3,
            "status_code": "confirmed",
        }

        # Execute
        result = runner.invoke(app, ["spec", "--interactive", "--user-input", "test"])

        # Verify
        assert result.exit_code == 0
        assert "✅ Spec clarification completed!" in result.stdout
        assert "Continue the workflow with:" in result.stdout
        assert "cafe make" in result.stdout
        assert "Please review the spec" not in result.stdout

    def test_confirmed_shows_iteration_count(
        self, runner, mock_git_ops, mock_execute_alias, mock_agent_manager, mock_permission_handler, setup_test_env
    ):
        """CONFIRMED 狀態應顯示 iteration 次數."""
        mock_execute_alias.return_value = {
            "iterations": 5,
            "status_code": "confirmed",
        }

        result = runner.invoke(app, ["spec", "--interactive", "--user-input", "test"])

        assert result.exit_code == 0
        assert "Iterations: 5" in result.stdout

    def test_confirmed_transition_uses_next_step_without_status_code(
        self, runner, mock_git_ops, mock_execute_alias, mock_agent_manager, mock_permission_handler, setup_test_env
    ):
        """next_step should drive completion messaging even when status_code is absent."""
        mock_execute_alias.return_value = {
            "iterations": 3,
            "next_step": "plan",
        }

        result = runner.invoke(app, ["spec", "--interactive", "--user-input", "test"])

        assert result.exit_code == 0
        assert "✅ Spec clarification completed!" in result.stdout
        assert "cafe make" in result.stdout


class TestSpecCommandOutputWithNeedClarification:
    """測試 NEED_CLARIFICATION 狀態輸出訊息."""

    def test_need_clarification_prompts_to_continue(
        self, runner, mock_git_ops, mock_execute_alias, mock_agent_manager, mock_permission_handler, setup_test_env
    ):
        """NEED_CLARIFICATION 狀態應提示使用者回到 workflow 主入口."""
        mock_execute_alias.return_value = {
            "iterations": 1,
            "status_code": "need_clarification",
        }

        result = runner.invoke(app, ["spec", "--interactive", "--user-input", "test"])

        assert result.exit_code == 0
        assert "💬 Agent needs clarification" in result.stdout
        assert "Add clarification and continue with:" in result.stdout
        assert "cafe make" in result.stdout


class TestSpecCommandOutputComparison:
    """比較測試：確保不同狀態輸出訊息確實不同."""

    def test_ready_for_review_and_confirmed_have_different_messages(
        self, runner, mock_git_ops, mock_execute_alias, mock_agent_manager, mock_permission_handler, setup_test_env
    ):
        """確認 READY_FOR_REVIEW and CONFIRMED 訊息確實不同."""
        # Test READY_FOR_REVIEW
        mock_execute_alias.return_value = {"iterations": 1, "status_code": "ready_for_review"}

        result_ready = runner.invoke(app, ["spec", "--interactive", "--user-input", "test"])

        # Test CONFIRMED
        mock_execute_alias.return_value = {"iterations": 1, "status_code": "confirmed"}

        result_confirmed = runner.invoke(app, ["spec", "--interactive", "--user-input", "test"])

        # Verify they have different messages
        assert "Spec draft completed!" in result_ready.stdout
        assert "cafe make" in result_ready.stdout

        assert "Spec clarification completed!" in result_confirmed.stdout
        assert "cafe make" in result_confirmed.stdout
        assert "Please review the spec" not in result_confirmed.stdout


class TestSpecCommandLegacyNotice:
    def test_spec_command_prints_legacy_wrapper_notice(
        self, runner, mock_git_ops, mock_execute_alias, mock_agent_manager, mock_permission_handler, setup_test_env
    ):
        mock_execute_alias.return_value = {
            "iterations": 1,
            "status_code": "confirmed",
        }

        result = runner.invoke(app, ["spec", "--interactive", "--user-input", "test"])

        assert result.exit_code == 0
        output = result.stdout.replace("\n", "")
        assert "Legacy workflow alias:" in output
        assert "cafe spec" in output
        assert "cafe workflow" in output
        assert "--start-step spec" in output
        assert "cafe make --user-input" in result.stdout
        assert "being retired" not in result.stdout

    @pytest.mark.parametrize(
        "command,step,preferred_fragment,invoke_args",
        [
            ("plan", "plan", "cafe make", ["plan", "--no-interactive", "--template", "default"]),
            ("develop", "develop", "cafe make", ["develop", "--no-interactive"]),
        ],
    )
    def test_hidden_step_commands_share_alias_notice(
        self,
        runner,
        mock_git_ops,
        mock_execute_alias,
        mock_agent_manager,
        mock_permission_handler,
        setup_test_env,
        command,
        step,
        preferred_fragment,
        invoke_args,
    ):
        def _fake_latest_versioned_file(phase_name: str, issue_name: str) -> Path:
            return Path(f".cafe/issues/{issue_name}/{phase_name}/iteration_001/output.md")

        mock_execute_alias.return_value = {
            "iterations": 1,
            "status_code": "confirmed",
            "next_step": "review",
        }

        with patch(
            "cafe.ui.commands.phases_legacy._get_latest_versioned_file",
            side_effect=_fake_latest_versioned_file,
        ), patch("cafe.ui.cli.is_branch_initialized", return_value=True), patch(
            "cafe.ui.cli.select_template", return_value="default"
        ), patch("cafe.templates.manager.TemplateManager"), patch(
            "cafe.ui.cli._run_iterative_alias_step",
            return_value={"iterations": 1, "status_code": "confirmed"},
        ):
            result = runner.invoke(app, invoke_args)

        assert result.exit_code == 0
        output = result.stdout.replace("\n", "")
        assert "Legacy workflow alias:" in output
        assert f"cafe {command}" in output
        assert "cafe workflow" in output
        assert f"--start-step {step}" in output
        assert preferred_fragment in result.stdout
        assert "being retired" not in result.stdout
