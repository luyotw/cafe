"""Tests for CLI."""

import pytest
import typer
from pathlib import Path
from typer.testing import CliRunner
from unittest.mock import MagicMock, Mock, patch

from cafe.ui.cli import app
from cafe.core.git import GitOperations
from cafe.utils.config import ConfigManager


runner = CliRunner()


@pytest.fixture
def mock_git_ops() -> MagicMock:
    """Create a mock GitOperations for testing."""
    git_ops = MagicMock(spec=GitOperations)
    git_ops.get_current_branch.return_value = "test-issue"
    return git_ops


def _create_minimal_config(tmp_path: Path):
    """Helper to create minimal config.yaml in tmp_path/.cafe/"""
    from tests.conftest import create_minimal_config
    create_minimal_config(tmp_path)


class TestVersionCommand:
    """Test version command."""

    def test_version_shows_version_number(self) -> None:
        """Test version command runs and shows a valid version string."""
        import re

        result = runner.invoke(app, ["version"])

        assert result.exit_code == 0
        assert re.search(r"CAFE version \d+\.\d+\.\d+", result.stdout)


class TestStatusCommand:
    """Test status command registration."""

    def test_status_command_shows_help(self) -> None:
        """Test `cafe status --help` renders command help."""
        result = runner.invoke(app, ["status", "--help"])

        assert result.exit_code == 0
        assert "current workflow state" in result.stdout
        assert "cafe status" in result.stdout


class TestConfigCommand:
    """Test config command."""

    def test_config_list_all(self, tmp_path: Path) -> None:
        """測試列出所有設定"""
        # Change to tmp_path directory first, then set config
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            # Create basic config first
            cafe_dir = tmp_path / ".cafe"
            cafe_dir.mkdir()
            (cafe_dir / "config.yaml").write_text("agents: {}")

            # Now ConfigManager will use .cafe in current directory
            config_manager = ConfigManager()
            config_manager.set("test.key", "value")  # set() already calls save_config()

            result = runner.invoke(app, ["config"])
        finally:
            os.chdir(old_cwd)

        assert result.exit_code == 0
        assert "test:" in result.stdout or "test" in result.stdout
        assert "value" in result.stdout

    def test_config_get_existing_key(self, tmp_path: Path) -> None:
        """測試取得存在設定值"""
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            custom_config = {
                "python_bin": "python3",
                "agents": {
                    "pm": {"name": "Roger"}
                },
            }
            config_manager = ConfigManager()
            config_manager.save_config(custom_config)

            result = runner.invoke(app, ["config", "get", "python_bin"])
        finally:
            os.chdir(old_cwd)

        assert result.exit_code == 0
        assert "python3" in result.stdout

    def test_config_get_nonexistent_key(self, tmp_path: Path) -> None:
        """測試取得不存在設定值"""
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            _create_minimal_config(tmp_path)
            result = runner.invoke(app, ["config", "get", "nonexistent.key"])
        finally:
            os.chdir(old_cwd)

        assert result.exit_code == 0
        assert "Key not found" in result.stdout

    def test_config_set_value(self, tmp_path: Path) -> None:
        """測試設定值"""
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            _create_minimal_config(tmp_path)
            result = runner.invoke(app, ["config", "set", "test.key", "test_value"])

            # 驗證設定已儲存
            config_manager = ConfigManager()
            assert config_manager.get("test.key") == "test_value"
        finally:
            os.chdir(old_cwd)

        assert result.exit_code == 0
        assert "Set test.key = test_value" in result.stdout

    def test_config_without_args_shows_help(self, tmp_path: Path) -> None:
        """測試沒有參數時顯示所有設定"""
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            _create_minimal_config(tmp_path)
            result = runner.invoke(app, ["config"])
        finally:
            os.chdir(old_cwd)

        assert result.exit_code == 0
        # Without args, config command shows all configuration
        assert "agents" in result.stdout or "Configuration" in result.stdout


class TestCloseCommand:
    """Keep the existing seven close scenarios on the cleanup-only API."""

    @pytest.fixture
    def cleanup(self, tmp_path, monkeypatch):
        import yaml
        from cafe.ui.commands import lifecycle

        monkeypatch.setenv("HOME", str(tmp_path / "home"))

        def arrange(linked=False):
            source = tmp_path / "source"
            retained = tmp_path / "retained" if linked else source
            for path in {source, retained}:
                (path / ".git").mkdir(parents=True)
            issue = source / ".cafe/issues/test-issue"
            issue.mkdir(parents=True)
            config = {"base_branch": "main", "feature_branch": "test-issue"}
            if linked:
                config["worktree_path"] = str(source)
            (issue / "issue.yaml").write_text(yaml.safe_dump(config))
            snapshot = {"source_oid": "a" * 40, "target_oid": "b" * 40}
            git = MagicMock(spec=GitOperations)
            git.get_current_branch.return_value = "test-issue"
            git.cleanup_snapshot.return_value = snapshot
            git.has_in_progress_operation.return_value = False
            git.list_worktrees.return_value = [
                {"path": str(source), "branch": "test-issue"},
                *([{"path": str(retained), "branch": "main"}] if linked else []),
            ]

            def run_git(*args):
                if args == ("rev-parse", "--show-toplevel"):
                    return str(source)
                if args == ("rev-parse", "refs/heads/test-issue"):
                    return snapshot["source_oid"]
                if args == ("status", "--porcelain", "--untracked-files=all"):
                    return ""
                if args == ("merge", "--ff-only", snapshot["target_oid"]):
                    return ""
                raise AssertionError(f"unexpected cleanup command: {args}")

            git.run_git.side_effect = run_git
            github = MagicMock()
            github.get_pr_for_branch.return_value = None
            monkeypatch.setattr("cafe.ui.cli.GitOperations", lambda *args: git)
            monkeypatch.setattr("cafe.ui.cli.GitHubOps", lambda: github)
            monkeypatch.setattr(lifecycle, "_get_project_root", lambda: retained)
            monkeypatch.chdir(source)
            return git, github, snapshot, source, issue
        return arrange

    @pytest.mark.parametrize("linked", [False, True], ids=["checkout", "worktree"])
    def test_close_success(self, cleanup, linked):
        git, github, snapshot, source, issue = cleanup(linked)
        result = runner.invoke(app, ["close"])
        assert result.exit_code == 0, result.output
        assert "Successfully closed issue" in result.output
        git.require_integrated.assert_called_once_with(snapshot)
        git.checkout_branch.assert_called_once_with("main")
        git.run_git.assert_any_call("merge", "--ff-only", snapshot["target_oid"])
        git.delete_cleanup_remote.assert_called_once_with(snapshot)
        git.delete_cleanup_branch.assert_called_once_with(snapshot)
        if linked:
            git.remove_worktree.assert_called_once_with(str(source))
        else:
            git.remove_worktree.assert_not_called()
            assert not issue.exists()
        git.pull.assert_not_called()
        git.delete_branch.assert_not_called()

    @pytest.mark.parametrize("failure", ["checkout", "ff_sync", "delete_branch", "remove_worktree"])
    def test_close_stops_at_cleanup_failure(self, cleanup, failure):
        git, github, snapshot, source, issue = cleanup(failure == "remove_worktree")
        if failure == "ff_sync":
            original = git.run_git.side_effect
            def fail_sync(*args):
                if args == ("merge", "--ff-only", snapshot["target_oid"]):
                    raise RuntimeError("injected ff_sync failure")
                return original(*args)
            git.run_git.side_effect = fail_sync
        else:
            method = {"checkout": git.checkout_branch, "delete_branch": git.delete_cleanup_branch,
                      "remove_worktree": git.remove_worktree}[failure]
            method.side_effect = RuntimeError(f"injected {failure} failure")
        result = runner.invoke(app, ["close"])
        assert result.exit_code == 1
        assert f"injected {failure} failure" in result.output
        git.require_integrated.assert_called_once_with(snapshot)
        assert issue.exists(), "failed cleanup must retain live evidence"
        if failure != "delete_branch":
            git.delete_cleanup_branch.assert_not_called()
        if failure in {"checkout", "ff_sync"}:
            git.delete_cleanup_remote.assert_not_called()
        git.pull.assert_not_called()
        git.delete_branch.assert_not_called()

    def test_close_blocks_on_open_pr(self, cleanup):
        git, github, snapshot, source, issue = cleanup()
        github.get_pr_for_branch.return_value = {"number": 123, "state": "OPEN", "isDraft": False}
        result = runner.invoke(app, ["close"])
        assert result.exit_code == 1
        assert "open or draft PR" in result.output
        assert issue.exists()
        git.require_integrated.assert_called_once_with(snapshot)
        git.checkout_branch.assert_not_called()
        git.delete_cleanup_remote.assert_not_called()
        git.delete_cleanup_branch.assert_not_called()
        git.pull.assert_not_called()
        git.delete_branch.assert_not_called()


class TestEditFileWithEditor:
    """測試 _edit_file_with_editor 函式"""

    @patch("cafe.ui.cli.subprocess.run")
    @patch.dict("os.environ", {"EDITOR": "nano"})
    def test_edit_file_with_custom_editor(
        self, mock_subprocess: Mock, tmp_path: Path
    ) -> None:
        """測試使用 $EDITOR 環境變數開啟檔案"""
        from cafe.ui.cli import _edit_file_with_editor

        # Setup
        test_file = tmp_path / "test.md"
        test_file.write_text("test content")
        mock_subprocess.return_value = Mock(returncode=0)

        # Execute
        _edit_file_with_editor(test_file)

        # Verify
        mock_subprocess.assert_called_once_with(["nano", str(test_file)], check=True)

    @patch("cafe.ui.cli.subprocess.run")
    @patch.dict("os.environ", {}, clear=True)
    def test_edit_file_with_default_vim(
        self, mock_subprocess: Mock, tmp_path: Path
    ) -> None:
        """測試預設使用 vim 作為編輯器"""
        from cafe.ui.cli import _edit_file_with_editor

        # Setup
        test_file = tmp_path / "test.md"
        test_file.write_text("test content")
        mock_subprocess.return_value = Mock(returncode=0)

        # Execute
        _edit_file_with_editor(test_file)

        # Verify
        mock_subprocess.assert_called_once_with(["vim", str(test_file)], check=True)

    @patch("cafe.ui.cli.subprocess.run")
    @patch.dict("os.environ", {"EDITOR": "nonexistent-editor"})
    def test_edit_file_editor_not_found(
        self, mock_subprocess: Mock, tmp_path: Path
    ) -> None:
        """測試編輯器不存在時錯誤處理"""
        from cafe.ui.cli import _edit_file_with_editor

        # Setup
        test_file = tmp_path / "test.md"
        test_file.write_text("test content")
        mock_subprocess.side_effect = FileNotFoundError()

        # Execute & Verify
        with pytest.raises(typer.Exit) as exc_info:
            _edit_file_with_editor(test_file)
        assert exc_info.value.exit_code == 1

    @patch("cafe.ui.cli.subprocess.run")
    @patch.dict("os.environ", {"EDITOR": "vim"})
    def test_edit_file_editor_execution_failed(
        self, mock_subprocess: Mock, tmp_path: Path
    ) -> None:
        """測試編輯器執行失敗時錯誤處理"""
        from cafe.ui.cli import _edit_file_with_editor
        import subprocess

        # Setup
        test_file = tmp_path / "test.md"
        test_file.write_text("test content")
        mock_subprocess.side_effect = subprocess.CalledProcessError(1, "vim")

        # Execute & Verify
        with pytest.raises(typer.Exit) as exc_info:
            _edit_file_with_editor(test_file)
        assert exc_info.value.exit_code == 1


class TestEditCommand:
    """測試 cafe edit <phase> 指令"""

    def test_edit_rejects_unknown_phase(self) -> None:
        result = runner.invoke(app, ["edit", "unknown"])

        assert result.exit_code == 1
        assert "phase must be one of spec, plan, develop, review, pr" in result.stdout

    @patch("cafe.ui.cli.GitOperations")
    @patch("cafe.ui.cli._edit_file_with_editor")
    def test_edit_spec_opens_latest_file(
        self, mock_edit: Mock, mock_git_ops_class: Mock, tmp_path: Path
    ) -> None:
        """測試正確找到並開啟最新 spec artifact."""
        import os

        # Setup
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "test-issue"
        mock_git_ops_class.return_value = mock_git_instance

        # Create issue directory and spec files in new structure
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        iter1_dir = spec_dir / "iteration_001"
        iter2_dir = spec_dir / "iteration_002"
        iter1_dir.mkdir(parents=True)
        iter2_dir.mkdir(parents=True)
        spec_file_1 = iter1_dir / "output.md"
        spec_file_2 = iter2_dir / "output.md"
        spec_file_1.write_text("Spec version 1")
        spec_file_2.write_text("Spec version 2")

        # Execute
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["edit", "spec"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 0
        mock_edit.assert_called_once()
        called_path = mock_edit.call_args[0][0]
        assert called_path.name == "output.md"
        assert called_path.parent.name == "iteration_002"

    @patch("cafe.ui.cli.GitOperations")
    def test_edit_spec_no_file_shows_error(
        self, mock_git_ops_class: Mock, tmp_path: Path
    ) -> None:
        """測試沒有 spec 檔案時顯示錯誤"""
        import os

        # Setup
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "test-issue"
        mock_git_ops_class.return_value = mock_git_instance

        # Create issue directory but no spec files
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True)

        # Execute
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["edit", "spec"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 1
        assert "No spec file found" in result.stdout
        assert "cafe make --user-input" in result.stdout

    @patch("cafe.ui.cli.GitOperations")
    def test_edit_spec_not_in_issue_branch_shows_error(
        self, mock_git_ops_class: Mock, tmp_path: Path
    ) -> None:
        """測試不在 issue branch 上時顯示錯誤"""
        import os

        # Setup
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "test-issue"
        mock_git_ops_class.return_value = mock_git_instance

        # Don't create .cafe directory to simulate not initialized
        # Execute
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["edit", "spec"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 1
        assert "not been initialized" in result.stdout
        assert "cafe prepare" in result.stdout

    @patch("cafe.ui.cli.GitOperations")
    @patch("cafe.ui.cli._edit_file_with_editor")
    def test_edit_spec_shows_success_message(
        self, mock_edit: Mock, mock_git_ops_class: Mock, tmp_path: Path
    ) -> None:
        """測試編輯完成後顯示成功訊息"""
        import os

        # Setup
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "test-issue"
        mock_git_ops_class.return_value = mock_git_instance

        # Create spec file in new structure
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        iter_dir = spec_dir / "iteration_001"
        iter_dir.mkdir(parents=True)
        spec_file = iter_dir / "output.md"
        spec_file.write_text("Spec content")

        # Execute
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["edit", "spec"])
        finally:
            os.chdir(old_cwd)

        # Verify - message from _edit_file_with_editor
        assert result.exit_code == 0
        mock_edit.assert_called_once()


    @patch("cafe.ui.cli.GitOperations")
    @patch("cafe.ui.cli._edit_file_with_editor")
    def test_edit_plan_opens_latest_file(
        self, mock_edit: Mock, mock_git_ops_class: Mock, tmp_path: Path
    ) -> None:
        """測試正確找到並開啟最新 plan artifact."""
        import os

        # Setup
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "test-issue"
        mock_git_ops_class.return_value = mock_git_instance

        # Create issue directory and plan files in new structure
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        plan_dir = issue_dir / "plan"
        iter1_dir = plan_dir / "iteration_001"
        iter2_dir = plan_dir / "iteration_002"
        iter1_dir.mkdir(parents=True)
        iter2_dir.mkdir(parents=True)
        plan_file_1 = iter1_dir / "output.md"
        plan_file_2 = iter2_dir / "output.md"
        plan_file_1.write_text("Plan version 1")
        plan_file_2.write_text("Plan version 2")

        # Execute
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["edit", "plan"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 0
        mock_edit.assert_called_once()
        called_path = mock_edit.call_args[0][0]
        assert called_path.name == "output.md"
        assert called_path.parent.name == "iteration_002"

    @patch("cafe.ui.cli.GitOperations")
    def test_edit_plan_no_file_shows_error(
        self, mock_git_ops_class: Mock, tmp_path: Path
    ) -> None:
        """測試沒有 plan 檔案時顯示錯誤"""
        import os

        # Setup
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "test-issue"
        mock_git_ops_class.return_value = mock_git_instance

        # Create issue directory but no plan files
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        plan_dir = issue_dir / "plan"
        plan_dir.mkdir(parents=True)

        # Execute
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["edit", "plan"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 1
        assert "No plan file found" in result.stdout
        assert "Run 'cafe make' first." in result.stdout

    @patch("cafe.ui.cli.GitOperations")
    def test_edit_plan_not_in_issue_branch_shows_error(
        self, mock_git_ops_class: Mock, tmp_path: Path
    ) -> None:
        """測試不在 issue branch 上時顯示錯誤"""
        import os

        # Setup
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "test-issue"
        mock_git_ops_class.return_value = mock_git_instance

        # Don't create .cafe directory to simulate not initialized
        # Execute
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["edit", "plan"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 1
        assert "not been initialized" in result.stdout
        assert "cafe prepare" in result.stdout

    @patch("cafe.ui.cli.GitOperations")
    @patch("cafe.ui.cli._edit_file_with_editor")
    def test_edit_plan_shows_success_message(
        self, mock_edit: Mock, mock_git_ops_class: Mock, tmp_path: Path
    ) -> None:
        """測試編輯完成後顯示成功訊息"""
        import os

        # Setup
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "test-issue"
        mock_git_ops_class.return_value = mock_git_instance

        # Create plan file in new structure
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        plan_dir = issue_dir / "plan"
        iter_dir = plan_dir / "iteration_001"
        iter_dir.mkdir(parents=True)
        plan_file = iter_dir / "output.md"
        plan_file.write_text("Plan content")

        # Execute
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["edit", "plan"])
        finally:
            os.chdir(old_cwd)

        # Verify - message from _edit_file_with_editor
        assert result.exit_code == 0
        mock_edit.assert_called_once()


    @patch("cafe.ui.cli.GitOperations")
    @patch("cafe.ui.cli._edit_file_with_editor")
    def test_edit_review_opens_latest_file(
        self, mock_edit: Mock, mock_git_ops_class: Mock, tmp_path: Path
    ) -> None:
        """測試正確找到並開啟最新 review artifact."""
        import os

        # Setup
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "test-issue"
        mock_git_ops_class.return_value = mock_git_instance

        # Create issue directory and review files in new structure
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        review_dir = issue_dir / "review"
        iter1_dir = review_dir / "iteration_001"
        iter2_dir = review_dir / "iteration_002"
        iter1_dir.mkdir(parents=True)
        iter2_dir.mkdir(parents=True)
        review_file_1 = iter1_dir / "output.md"
        review_file_2 = iter2_dir / "output.md"
        review_file_1.write_text("Review version 1")
        review_file_2.write_text("Review version 2")

        # Execute
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["edit", "review"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 0
        mock_edit.assert_called_once()
        called_path = mock_edit.call_args[0][0]
        assert called_path.name == "output.md"
        assert called_path.parent.name == "iteration_002"

    @patch("cafe.ui.cli.GitOperations")
    def test_edit_review_no_file_shows_error(
        self, mock_git_ops_class: Mock, tmp_path: Path
    ) -> None:
        """測試沒有 review 檔案時顯示錯誤"""
        import os

        # Setup
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "test-issue"
        mock_git_ops_class.return_value = mock_git_instance

        # Create issue directory but no review files
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        review_dir = issue_dir / "review"
        review_dir.mkdir(parents=True)

        # Execute
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["edit", "review"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 1
        assert "No review file found" in result.stdout
        assert "Run 'cafe make' first." in result.stdout

    @patch("cafe.ui.cli.GitOperations")
    def test_edit_review_not_in_issue_branch_shows_error(
        self, mock_git_ops_class: Mock, tmp_path: Path
    ) -> None:
        """測試不在 issue branch 上時顯示錯誤"""
        import os

        # Setup
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "test-issue"
        mock_git_ops_class.return_value = mock_git_instance

        # Don't create .cafe directory to simulate not initialized
        # Execute
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["edit", "review"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 1
        assert "not been initialized" in result.stdout
        assert "cafe prepare" in result.stdout

    @patch("cafe.ui.cli.GitOperations")
    @patch("cafe.ui.cli._edit_file_with_editor")
    def test_edit_review_shows_success_message(
        self, mock_edit: Mock, mock_git_ops_class: Mock, tmp_path: Path
    ) -> None:
        """測試編輯完成後顯示成功訊息"""
        import os

        # Setup
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "test-issue"
        mock_git_ops_class.return_value = mock_git_instance

        # Create review file in new structure
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        review_dir = issue_dir / "review"
        iter_dir = review_dir / "iteration_001"
        iter_dir.mkdir(parents=True)
        review_file = iter_dir / "output.md"
        review_file.write_text("Review content")

        # Execute
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["edit", "review"])
        finally:
            os.chdir(old_cwd)

        # Verify - message from _edit_file_with_editor
        assert result.exit_code == 0
        mock_edit.assert_called_once()

    @patch("cafe.ui.cli.GitOperations")
    @patch("cafe.ui.cli._edit_file_with_editor")
    def test_edit_spec_opens_latest_artifact(
        self, mock_edit: Mock, mock_git_ops_class: Mock, tmp_path: Path
    ) -> None:
        import os

        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "test-issue"
        mock_git_ops_class.return_value = mock_git_instance

        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue" / "spec" / "iteration_001"
        issue_dir.mkdir(parents=True)
        (issue_dir / "output.md").write_text("Spec content")

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["edit", "spec"])
        finally:
            os.chdir(old_cwd)

        assert result.exit_code == 0
        mock_edit.assert_called_once()


class TestExplicitUpdate:
    """U8: runtime updates are exposed only through explicit CLI commands."""

    def test_update_check_exposes_read_only_json_contract(self) -> None:
        service = MagicMock()
        service.check.return_value.to_dict.return_value = {
            "status": "update_available",
            "installed_version": "1.0.0",
            "latest_version": "1.1.0",
            "release_url": "https://example.test/1.1.0",
            "token": "approval-token",
            "error": None,
        }

        with patch(
            "cafe.ui.commands.update._build_update_service",
            return_value=service,
        ):
            result = runner.invoke(app, ["update", "check", "--json"])

        assert result.exit_code == 0
        assert '"status": "update_available"' in result.stdout
        assert '"token": "approval-token"' in result.stdout
        service.apply.assert_not_called()

    def test_update_apply_requires_explicit_token_and_reports_post_check(self) -> None:
        service = MagicMock()
        service.apply.return_value.to_dict.return_value = {
            "status": "current",
            "installed_version": "1.1.0",
            "latest_version": "1.1.0",
            "release_url": "https://example.test/1.1.0",
            "token": "post-check-token",
            "error": None,
        }

        with patch(
            "cafe.ui.commands.update._build_update_service",
            return_value=service,
        ):
            missing = runner.invoke(app, ["update", "apply"])
            approved = runner.invoke(
                app,
                ["update", "apply", "--token", "approval-token", "--json"],
            )

        assert missing.exit_code != 0
        assert approved.exit_code == 0
        assert '"status": "current"' in approved.stdout
        service.apply.assert_called_once_with("approval-token")
