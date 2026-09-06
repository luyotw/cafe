"""Tests for prepare CLI command."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from cafe.ui.cli import app
from cafe.ui.commands.lifecycle import _ensure_worktree_cafe_excluded

runner = CliRunner()


@pytest.fixture(scope="module")
def standard_playbook_for_prepare_tests(tmp_path_factory):
    """Validate the builtin once; prepare tests only consume the resolved model."""
    from cafe.playbooks.loader import PlaybookLoader

    project_root = Path(__file__).resolve().parents[2]
    global_root = tmp_path_factory.mktemp("prepare-global") / "global"
    return PlaybookLoader(
        project_root=project_root,
        global_root=global_root,
    ).load_model("standard")


@pytest.fixture
def temp_repo_dir(tmp_path):
    """Create a temporary git repository directory."""
    from tests.conftest import create_minimal_config

    # Create config.yaml (required by prepare command)
    create_minimal_config(tmp_path)

    return tmp_path


@pytest.fixture(autouse=True)
def change_test_dir(tmp_path, monkeypatch, standard_playbook_for_prepare_tests):
    """Automatically change to tmp_path for all tests to ensure isolation."""
    from cafe.playbooks.loader import PlaybookLoader

    real_load_model = PlaybookLoader.load_model

    def load_model(loader, name, *, strict=False):
        if name == "standard" and not strict:
            return standard_playbook_for_prepare_tests
        return real_load_model(loader, name, strict=strict)

    monkeypatch.setattr(PlaybookLoader, "load_model", load_model)
    monkeypatch.setattr(
        "cafe.utils.config.get_global_cafe_dir", lambda: tmp_path / "global"
    )
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def mock_git_ops():
    """Create a mock GitOperations instance."""
    with patch('cafe.ui.cli.GitOperations') as MockGitOperations, \
         patch('cafe.utils.git_utils.is_github_repo') as mock_is_github_repo1, \
         patch('cafe.ui.phase_prompts.is_github_repo') as mock_is_github_repo2:
        mock_git = MagicMock()
        MockGitOperations.return_value = mock_git

        # Default behaviors
        mock_git.get_current_branch.return_value = "main"
        mock_git.has_uncommitted_changes.return_value = False
        mock_git.branch_exists.return_value = False
        mock_git.create_branch.return_value = None
        mock_git.checkout_branch.return_value = None
        mock_git.worktree_exists.return_value = False  # Default: worktree doesn't exist
        mock_git.ensure_remote_base_ancestor.return_value = "origin/main"

        # Mock is_github_repo to return True by default (GitHub repo)
        mock_is_github_repo1.return_value = True
        mock_is_github_repo2.return_value = True

        yield mock_git


class TestPrepareCommand:
    """Test prepare command."""

    def test_prepare_with_issue_name_argument(self, temp_repo_dir, mock_git_ops):
        """測試使用 CLI 參數指定 issue name"""
        result = runner.invoke(app, ["prepare", "test-issue", "--no-auto-create-pr"])

        assert result.exit_code == 0
        assert "Successfully prepared issue: test-issue" in result.stdout
        assert "Feature branch: test-issue" in result.stdout
        assert "Base branch: main" in result.stdout
        assert "Next step: cafe make" in result.stdout

        # Verify directory structure created
        issue_dir = temp_repo_dir / ".cafe" / "issues" / "test-issue"
        assert issue_dir.exists()
        assert (issue_dir / "spec").exists()
        assert (issue_dir / "sessions").exists()

        # Verify config.yaml created
        config_file = issue_dir / "issue.yaml"
        assert config_file.exists()

        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            assert config_data["base_branch"] == "main"
            assert config_data["feature_branch"] == "test-issue"
            assert config_data["playbook_id"] == "standard"
            assert "auto" not in config_data

        # Verify git operations called
        mock_git_ops.branch_exists.assert_called_once_with("test-issue")
        mock_git_ops.create_branch.assert_called_once_with("test-issue")

        marker = (temp_repo_dir / ".cafe" / "active_issue").read_text(encoding="utf-8").strip()
        assert marker == "test-issue"

    @patch("cafe.ui.phase_prompts.prompt_confirm")
    @patch("cafe.ui.cli.prompt_confirm")
    @patch("cafe.ui.template_selector.prompt_list")
    @patch("cafe.ui.phase_prompts.prompt_list")
    @patch("cafe.ui.cli.prompt_list")
    @patch("cafe.ui.cli.prompt_text")
    def test_prepare_interactive_mode(self, mock_prompt_text, mock_cli_list, mock_phase_list, mock_template_list, mock_cli_confirm, mock_phase_confirm, temp_repo_dir, mock_git_ops):
        """測試互動式輸入 issue name"""
        # Mock user inputs
        mock_prompt_text.return_value = "my-feature"
        mock_cli_confirm.side_effect = [False, True, True]  # worktree, pr auto_create, post_todo_list
        mock_phase_confirm.return_value = True
        mock_cli_list.side_effect = ["Custom configuration", "Medium"]
        mock_phase_list.return_value = "1. Manual input"
        mock_template_list.return_value = "default (system default)"  # template

        result = runner.invoke(app, ["prepare", "--auto-create-pr"])

        assert result.exit_code == 0
        assert "Successfully prepared issue: my-feature" in result.stdout

        # Verify directory created
        issue_dir = temp_repo_dir / ".cafe" / "issues" / "my-feature"
        assert issue_dir.exists()

    def test_prepare_with_custom_base_branch(self, temp_repo_dir, mock_git_ops):
        """測試指定自訂 base branch"""
        result = runner.invoke(
            app, ["prepare", "feature-x", "--base", "develop", "--no-auto-create-pr"]
        )

        assert result.exit_code == 0
        assert "Base branch: develop" in result.stdout

        # Verify config contains custom base branch
        config_file = temp_repo_dir / ".cafe" / "issues" / "feature-x" / "issue.yaml"
        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            assert config_data["base_branch"] == "develop"

        # get_current_branch should not be called when base branch is specified
        mock_git_ops.get_current_branch.assert_not_called()

    def test_prepare_branch_already_exists(self, temp_repo_dir, mock_git_ops):
        """測試切換到已存在 branch"""
        # Mock branch exists
        mock_git_ops.branch_exists.return_value = True

        result = runner.invoke(
            app, ["prepare", "existing-issue", "--no-auto-create-pr"]
        )

        assert result.exit_code == 0
        assert "already exists, switching to it" in result.stdout

        # Should checkout instead of create
        mock_git_ops.checkout_branch.assert_called_once_with("existing-issue")
        mock_git_ops.create_branch.assert_not_called()

    @patch("cafe.ui.cli.prompt_confirm")
    def test_prepare_with_uncommitted_changes_cancel(self, mock_prompt_confirm, temp_repo_dir, mock_git_ops):
        """測試有未 commit 變更時取消"""
        mock_git_ops.has_uncommitted_changes.return_value = True
        mock_prompt_confirm.return_value = False  # User cancels

        # User cancels when prompted
        result = runner.invoke(app, ["prepare", "test-issue", "--no-auto-create-pr"])

        assert result.exit_code == 0
        assert "Warning: You have uncommitted changes" in result.stdout
        assert "Cancelled" in result.stdout

        # Should not create directories or branches
        issue_dir = temp_repo_dir / ".cafe" / "issues" / "test-issue"
        assert not issue_dir.exists()
        mock_git_ops.create_branch.assert_not_called()

    @patch("cafe.ui.cli.prompt_confirm")
    def test_prepare_with_uncommitted_changes_continue(self, mock_prompt_confirm, temp_repo_dir, mock_git_ops):
        """測試有未 commit 變更時繼續執行"""
        mock_git_ops.has_uncommitted_changes.return_value = True
        mock_prompt_confirm.return_value = True  # User continues

        # User continues when prompted
        result = runner.invoke(app, ["prepare", "test-issue", "--no-auto-create-pr"])

        assert result.exit_code == 0
        assert "Warning: You have uncommitted changes" in result.stdout
        assert "Successfully prepared issue: test-issue" in result.stdout

        # Should create directories and branches
        issue_dir = temp_repo_dir / ".cafe" / "issues" / "test-issue"
        assert issue_dir.exists()
        mock_git_ops.create_branch.assert_called_once()

    def test_prepare_skip_uncommitted_check(self, temp_repo_dir, mock_git_ops):
        """測試使用 --no-check 跳過 uncommitted changes 檢查"""
        mock_git_ops.has_uncommitted_changes.return_value = True

        result = runner.invoke(
            app, ["prepare", "test-issue", "--no-check", "--no-auto-create-pr"]
        )

        assert result.exit_code == 0
        assert "Successfully prepared issue: test-issue" in result.stdout
        # Should not show warning
        assert "Warning: You have uncommitted changes" not in result.stdout

        # has_uncommitted_changes should not be called
        mock_git_ops.has_uncommitted_changes.assert_not_called()

    def test_prepare_non_interactive_requires_explicit_git_initialization(self, temp_repo_dir):
        """非互動模式不得無聲建立 Git，需提供明確旗標。"""
        with patch("cafe.ui.cli.GitOperations") as mock_git_operations:
            mock_git_operations.is_repository.return_value = False

            result = runner.invoke(
                app,
                [
                    "prepare",
                    "test-issue",
                    "--no-interactive",
                    "--input-method=manual",
                    "--rigor=medium",
                    "--spec-template=auto",
                    "--plan-template=default",
                    "--no-auto-create-pr",
                ],
            )

            assert result.exit_code == 1
            assert "local version history" in result.stdout
            assert "--init-git" in result.stdout
            mock_git_operations.initialize_repository.assert_not_called()

    def test_prepare_interactive_can_initialize_git_after_plain_language_confirmation(
        self, temp_repo_dir
    ):
        """互動模式說明用途並取得同意後建立本機 Git。"""
        initialized = MagicMock()
        initialized.has_uncommitted_changes.return_value = False
        initialized.has_tracked_or_staged_changes.return_value = False
        initialized.get_current_branch.return_value = "main"
        initialized.branch_exists.return_value = False
        initialized.worktree_exists.return_value = False

        with (
            patch("cafe.ui.cli.GitOperations") as mock_git_operations,
            patch("cafe.ui.cli.prompt_confirm", return_value=True),
        ):
            mock_git_operations.is_repository.return_value = False
            mock_git_operations.initialize_repository.return_value = initialized

            result = runner.invoke(
                app, ["prepare", "test-issue", "--no-auto-create-pr"]
            )

        assert result.exit_code == 0
        assert "does not create or upload anything to GitHub" in result.stdout
        assert "Local version history is ready" in result.stdout
        mock_git_operations.initialize_repository.assert_called_once_with(initial_branch="main")
        initialized.has_uncommitted_changes.assert_not_called()
        initialized.create_branch.assert_called_once_with("test-issue")

    def test_prepare_interactive_decline_does_not_initialize_git(self, temp_repo_dir):
        """互動模式拒絕後停止，且不得建立 Git。"""
        with (
            patch("cafe.ui.cli.GitOperations") as mock_git_operations,
            patch("cafe.ui.cli.prompt_confirm", return_value=False),
        ):
            mock_git_operations.is_repository.return_value = False

            result = runner.invoke(
                app, ["prepare", "test-issue", "--no-auto-create-pr"]
            )

        assert result.exit_code == 1
        assert "Git was not initialized" in result.stdout
        mock_git_operations.initialize_repository.assert_not_called()

    def test_prepare_non_interactive_can_initialize_git_with_explicit_flag(
        self, temp_repo_dir
    ):
        """非互動模式收到明確旗標後可建立 Git 並繼續。"""
        initialized = MagicMock()
        initialized.has_uncommitted_changes.return_value = False
        initialized.has_tracked_or_staged_changes.return_value = False
        initialized.get_current_branch.return_value = "main"
        initialized.branch_exists.return_value = False
        initialized.worktree_exists.return_value = False

        with patch("cafe.ui.cli.GitOperations") as mock_git_operations:
            mock_git_operations.is_repository.return_value = False
            mock_git_operations.initialize_repository.return_value = initialized

            result = runner.invoke(
                app,
                [
                    "prepare",
                    "test-issue",
                    "--no-interactive",
                    "--init-git",
                    "--input-method=manual",
                    "--rigor=medium",
                    "--spec-template=auto",
                    "--plan-template=default",
                    "--no-auto-create-pr",
                ],
            )

        assert result.exit_code == 0
        mock_git_operations.initialize_repository.assert_called_once_with(initial_branch="main")
        initialized.has_uncommitted_changes.assert_not_called()
        initialized.create_branch.assert_called_once_with("test-issue")

    def test_prepare_first_initialized_task_rejects_worktree(self, temp_repo_dir):
        """新 repo 的第一個任務留在目前資料夾，避免遺漏初始未提交檔案。"""
        initialized = MagicMock()
        initialized.has_uncommitted_changes.return_value = False
        initialized.has_tracked_or_staged_changes.return_value = False
        initialized.get_current_branch.return_value = "main"

        with patch("cafe.ui.cli.GitOperations") as mock_git_operations:
            mock_git_operations.is_repository.return_value = False
            mock_git_operations.initialize_repository.return_value = initialized

            result = runner.invoke(
                app,
                [
                    "prepare",
                    "test-issue",
                    "--init-git",
                    "--worktree",
                    "worktrees/test-issue",
                    "--no-auto-create-pr",
                ],
            )

        assert result.exit_code == 1
        assert "initial project files" in result.stdout
        initialized.create_worktree.assert_not_called()

    def test_prepare_pending_bootstrap_still_checks_tracked_changes(
        self, temp_repo_dir
    ):
        """初始 untracked 檔可保留，但 tracked 變更仍須確認。"""
        git = MagicMock()
        git.has_commits.return_value = True
        git.requires_bootstrap_checkout.return_value = True
        git.has_tracked_or_staged_changes.return_value = True

        with (
            patch("cafe.ui.cli.GitOperations") as mock_git_operations,
            patch("cafe.ui.cli.prompt_confirm", return_value=False),
        ):
            mock_git_operations.is_repository.return_value = True
            mock_git_operations.return_value = git

            result = runner.invoke(
                app, ["prepare", "second-task", "--no-auto-create-pr"]
            )

        assert result.exit_code == 0
        assert "Warning: You have uncommitted changes" in result.stdout
        assert "Cancelled" in result.stdout
        git.has_tracked_or_staged_changes.assert_called_once_with()
        git.create_branch.assert_not_called()

    def test_prepare_creates_proper_directory_structure(self, temp_repo_dir, mock_git_ops):
        """測試創建正確目錄結構"""
        result = runner.invoke(app, ["prepare", "my-issue", "--no-auto-create-pr"])

        assert result.exit_code == 0

        issue_dir = temp_repo_dir / ".cafe" / "issues" / "my-issue"
        spec_dir = issue_dir / "spec"
        sessions_dir = issue_dir / "sessions"

        assert issue_dir.is_dir()
        assert spec_dir.is_dir()
        assert sessions_dir.is_dir()

    def test_prepare_config_yaml_format(self, temp_repo_dir, mock_git_ops):
        """測試 config.yaml 格式正確"""
        result = runner.invoke(app, ["prepare", "format-test", "--no-auto-create-pr"])

        assert result.exit_code == 0

        config_file = temp_repo_dir / ".cafe" / "issues" / "format-test" / "issue.yaml"
        assert config_file.exists()

        # Read and verify YAML format
        with open(config_file) as f:
            content = f.read()
            assert "base_branch:" in content
            assert "feature_branch:" in content

            # Parse YAML
            config_data = yaml.safe_load(content)
            assert isinstance(config_data, dict)
            assert len(config_data) == 4
            assert config_data["playbook_id"] == "standard"
            assert config_data["pr"] == {"auto_create": False}
            assert "auto" not in config_data

    def test_prepare_idempotent(self, temp_repo_dir, mock_git_ops):
        """測試重複執行 prepare 是否安全（冪等性）"""
        # First execution
        result1 = runner.invoke(
            app, ["prepare", "idempotent-test", "--no-auto-create-pr"]
        )
        assert result1.exit_code == 0

        # Mock branch now exists
        mock_git_ops.branch_exists.return_value = True

        # Second execution
        result2 = runner.invoke(
            app, ["prepare", "idempotent-test", "--no-auto-create-pr"]
        )
        assert result2.exit_code == 0
        assert "already exists" in result2.stdout

        # Config should still exist and be valid
        config_file = temp_repo_dir / ".cafe" / "issues" / "idempotent-test" / "issue.yaml"
        assert config_file.exists()

    def test_prepare_with_different_base_branches(self, temp_repo_dir, mock_git_ops):
        """測試不同 base branches 配置"""
        test_cases = [
            ("develop", "develop"),
            ("staging", "staging"),
            ("release/v1.0", "release/v1.0"),
        ]

        for issue_name, base_branch in test_cases:
            result = runner.invoke(
                app,
                [
                    "prepare",
                    f"issue-{issue_name}",
                    "--base",
                    base_branch,
                    "--no-auto-create-pr",
                ],
            )

            assert result.exit_code == 0
            assert f"Base branch: {base_branch}" in result.stdout

            config_file = temp_repo_dir / ".cafe" / "issues" / f"issue-{issue_name}" / "issue.yaml"
            with open(config_file) as f:
                config_data = yaml.safe_load(f)
                assert config_data["base_branch"] == base_branch


    def test_prepare_error_when_base_branch_equals_feature_branch(self, temp_repo_dir, mock_git_ops):
        """Test error when user runs prepare on the feature branch without --base."""
        # Simulate: user is already on the feature branch
        mock_git_ops.get_current_branch.return_value = "my-feature"

        result = runner.invoke(app, ["prepare", "my-feature", "--no-auto-create-pr"])

        assert result.exit_code == 1
        assert "base_branch and feature_branch are both" in result.stdout
        assert "--base" in result.stdout

        # Should not create directories or branches
        issue_dir = temp_repo_dir / ".cafe" / "issues" / "my-feature"
        assert not issue_dir.exists()
        mock_git_ops.create_branch.assert_not_called()

    def test_prepare_explicit_base_overrides_same_branch_check(self, temp_repo_dir, mock_git_ops):
        """Test that --base flag works even when on the feature branch."""
        mock_git_ops.get_current_branch.return_value = "my-feature"

        result = runner.invoke(
            app, ["prepare", "my-feature", "--base", "main", "--no-auto-create-pr"]
        )

        assert result.exit_code == 0
        assert "Base branch: main" in result.stdout

        config_file = temp_repo_dir / ".cafe" / "issues" / "my-feature" / "issue.yaml"
        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            assert config_data["base_branch"] == "main"
            assert config_data["feature_branch"] == "my-feature"

    def test_prepare_auto_pr_verifies_remote_base_before_creating_branch(
        self, temp_repo_dir, mock_git_ops
    ):
        result = runner.invoke(
            app,
            ["prepare", "remote-safe", "--auto-create-pr"],
        )

        assert result.exit_code == 0
        mock_git_ops.ensure_remote_base_ancestor.assert_called_once_with("main", "main")
        mock_git_ops.create_branch.assert_called_once_with("remote-safe")

    def test_prepare_auto_pr_stops_when_remote_base_advanced(
        self, temp_repo_dir, mock_git_ops
    ):
        from cafe.core.git import GitError

        mock_git_ops.ensure_remote_base_ancestor.side_effect = GitError(
            "Remote base origin/main is not contained in main"
        )

        result = runner.invoke(
            app,
            ["prepare", "remote-drift", "--auto-create-pr"],
        )

        assert result.exit_code == 1
        assert "Remote base origin/main is not" in result.stdout
        assert "contained in main" in result.stdout
        mock_git_ops.create_branch.assert_not_called()


class TestPrepareCommandWorktree:
    """Test prepare command with worktree support (TDD Red phase)."""

    def test_prepare_with_worktree_non_interactive(self, temp_repo_dir, mock_git_ops):
        """測試使用 --worktree 參數在非互動模式建立 worktree"""
        worktree_path = "worktrees/test-issue"
        result = runner.invoke(
            app,
            ["prepare", "test-issue", "--worktree", worktree_path, "--no-auto-create-pr"],
        )

        assert result.exit_code == 0
        # 驗證呼叫 create_worktree 而非 create_branch
        mock_git_ops.create_worktree.assert_called_once()
        mock_git_ops.create_branch.assert_not_called()

        # 驗證 worktree_path 儲存到 config.yaml（在 worktree 內）
        config_file = temp_repo_dir / worktree_path / ".cafe" / "issues" / "test-issue" / "issue.yaml"
        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            assert config_data["worktree_path"] == worktree_path

    def test_prepare_without_worktree_uses_branch(self, temp_repo_dir, mock_git_ops):
        """測試不使用 --worktree 時應建立分支"""
        result = runner.invoke(app, ["prepare", "normal-issue", "--no-auto-create-pr"])

        assert result.exit_code == 0
        # 驗證呼叫 create_branch 而非 create_worktree
        mock_git_ops.create_branch.assert_called_once()
        assert not hasattr(mock_git_ops, 'create_worktree') or not mock_git_ops.create_worktree.called

        # 驗證 config.yaml 不包含 worktree_path
        config_file = temp_repo_dir / ".cafe" / "issues" / "normal-issue" / "issue.yaml"
        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            assert "worktree_path" not in config_data

    def test_prepare_with_worktree_calls_create_worktree_with_correct_params(self, temp_repo_dir, mock_git_ops):
        """測試 create_worktree 使用正確參數"""
        worktree_path = "worktrees/test-branch"
        base_branch = "develop"
        result = runner.invoke(app, [
            "prepare", "test-branch",
            "--worktree", worktree_path,
            "--base", base_branch,
            "--no-auto-create-pr",
        ])

        assert result.exit_code == 0
        # 驗證參數：路徑、分支名稱、base branch
        mock_git_ops.create_worktree.assert_called_once_with(
            worktree_path, "test-branch", base_branch
        )

    @patch("cafe.ui.phase_prompts.prompt_confirm")
    @patch("cafe.ui.cli.prompt_confirm")
    @patch("cafe.ui.template_selector.prompt_list")
    @patch("cafe.ui.phase_prompts.prompt_list")
    @patch("cafe.ui.cli.prompt_list")
    @patch("cafe.ui.cli.prompt_text")
    def test_prepare_interactive_worktree_prompt_yes(self, mock_prompt_text, mock_cli_list, mock_phase_list, mock_template_list, mock_cli_confirm, mock_phase_confirm, temp_repo_dir, mock_git_ops):
        """測試互動模式詢問是否使用 worktree, 使用者選擇 Yes"""
        # Mock user inputs: issue name, worktree path
        mock_prompt_text.side_effect = ["my-feature", "worktrees/my-feature"]
        mock_cli_confirm.side_effect = [True, True, True]  # worktree, pr auto_create, post_todo_list
        mock_phase_confirm.return_value = True
        mock_cli_list.side_effect = ["Custom configuration", "Medium"]
        mock_phase_list.return_value = "1. Manual input"
        mock_template_list.return_value = "default (system default)"

        result = runner.invoke(app, ["prepare", "--auto-create-pr"])

        assert result.exit_code == 0
        # 驗證有詢問 worktree 相關問題
        assert "worktree" in result.stdout.lower()
        # 驗證呼叫 create_worktree
        mock_git_ops.create_worktree.assert_called_once_with(
            "worktrees/my-feature", "my-feature", "main"
        )
        mock_git_ops.create_branch.assert_not_called()

        # 驗證 config.yaml 包含 worktree_path（在 worktree 內）
        config_file = temp_repo_dir / "worktrees/my-feature" / ".cafe" / "issues" / "my-feature" / "issue.yaml"
        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            assert config_data["worktree_path"] == "worktrees/my-feature"

    @patch("cafe.ui.phase_prompts.prompt_confirm")
    @patch("cafe.ui.cli.prompt_confirm")
    @patch("cafe.ui.template_selector.prompt_list")
    @patch("cafe.ui.phase_prompts.prompt_list")
    @patch("cafe.ui.cli.prompt_list")
    @patch("cafe.ui.cli.prompt_text")
    def test_prepare_interactive_worktree_prompt_no(self, mock_prompt_text, mock_cli_list, mock_phase_list, mock_template_list, mock_cli_confirm, mock_phase_confirm, temp_repo_dir, mock_git_ops):
        """測試互動模式詢問是否使用 worktree, 使用者選擇 No"""
        # Mock user inputs
        mock_prompt_text.return_value = "normal-feature"
        mock_cli_confirm.side_effect = [False, True, True]  # worktree, pr auto_create, post_todo_list
        mock_phase_confirm.return_value = True
        mock_cli_list.side_effect = ["Custom configuration", "Medium"]
        mock_phase_list.return_value = "1. Manual input"
        mock_template_list.return_value = "default (system default)"

        result = runner.invoke(app, ["prepare", "--auto-create-pr"])

        assert result.exit_code == 0
        # 驗證呼叫 create_branch 而非 create_worktree
        mock_git_ops.create_branch.assert_called_once_with("normal-feature")
        assert not hasattr(mock_git_ops, 'create_worktree') or not mock_git_ops.create_worktree.called

        # 驗證 config.yaml 不包含 worktree_path
        config_file = temp_repo_dir / ".cafe" / "issues" / "normal-feature" / "issue.yaml"
        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            assert "worktree_path" not in config_data

    def test_prepare_creates_cafe_directory_in_worktree_not_symlink(self, temp_repo_dir, mock_git_ops):
        """測試 worktree 中創建實際 .cafe/ 目錄而非符號連結"""
        # Setup: 創建 repo root  .cafe/config.yaml
        repo_cafe_dir = temp_repo_dir / ".cafe"
        repo_cafe_dir.mkdir(parents=True, exist_ok=True)
        repo_config = repo_cafe_dir / "config.yaml"
        repo_config.write_text("test_config: value\n")
        (repo_cafe_dir / "phases.yaml").write_text(
            "develop:\n  clis:\n    - {cli: codex, model: repo-owned}\n",
            encoding="utf-8",
        )

        # 創建 worktree 目錄（模擬 git worktree add 行為）
        worktree_path = temp_repo_dir / "worktrees" / "test-issue"
        worktree_path.mkdir(parents=True, exist_ok=True)

        # Mock create_worktree 為空操作（worktree 目錄已存在）
        mock_git_ops.create_worktree.return_value = None

        result = runner.invoke(
            app,
            [
                "prepare",
                "test-issue",
                "--worktree",
                str(worktree_path),
                "--no-auto-create-pr",
            ],
        )

        assert result.exit_code == 0

        # 驗證 worktree 中有實際 .cafe/ 目錄（不是符號連結）
        worktree_cafe_dir = worktree_path / ".cafe"
        assert worktree_cafe_dir.exists(), ".cafe directory should exist in worktree"
        assert worktree_cafe_dir.is_dir(), ".cafe should be a real directory, not a symlink"
        assert not worktree_cafe_dir.is_symlink(), ".cafe should NOT be a symlink"

        # 驗證 config.yaml 被複製到 worktree
        worktree_config = worktree_cafe_dir / "config.yaml"
        assert worktree_config.exists(), "config.yaml should be copied to worktree"
        assert worktree_config.read_text() == "test_config: value\n"
        assert not (worktree_cafe_dir / "phases.yaml").exists()

        # 驗證 issue 目錄結構被創建
        worktree_issue_dir = worktree_cafe_dir / "issues" / "test-issue"
        assert worktree_issue_dir.exists(), "Issue directory should be created in worktree"
        assert (worktree_issue_dir / "spec").exists(), "spec directory should exist"
        assert (worktree_issue_dir / "sessions").exists(), "sessions directory should exist"

    def test_worktree_cafe_state_is_added_to_local_git_exclude(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        (repo / ".cafe" / "issues" / "demo").mkdir(parents=True)
        (repo / ".cafe" / "issues" / "demo" / "blackboard.json").write_text("{}\n")

        _ensure_worktree_cafe_excluded(repo)
        _ensure_worktree_cafe_excluded(repo)

        exclude_path = subprocess.run(
            ["git", "rev-parse", "--git-path", "info/exclude"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        exclude_file = repo / exclude_path
        assert exclude_file.read_text(encoding="utf-8").splitlines().count(".cafe/") == 1
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert ".cafe/" not in status

    def test_linked_worktree_cafe_state_is_excluded_without_hiding_tracked_changes(
        self, tmp_path
    ):
        repo = tmp_path / "repo"
        linked = tmp_path / "linked"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"], cwd=repo, check=True
        )
        (repo / "tracked.txt").write_text("clean\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True)
        subprocess.run(
            ["git", "worktree", "add", "-b", "feature", str(linked)],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (linked / ".cafe" / "issues" / "demo").mkdir(parents=True)
        (linked / ".cafe" / "issues" / "demo" / "blackboard.json").write_text(
            "{}\n", encoding="utf-8"
        )

        _ensure_worktree_cafe_excluded(linked)
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=linked,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert ".cafe/" not in status

        (linked / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        dirty_status = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=linked,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert " M tracked.txt" in dirty_status

    # Tests removed: Agents and templates are no longer copied to worktree .cafe directory
    # They are now managed globally at ~/.cafe/

    @patch("cafe.ui.phase_prompts.prompt_confirm")
    @patch("cafe.ui.cli.prompt_confirm")
    @patch("cafe.ui.template_selector.prompt_list")
    @patch("cafe.ui.phase_prompts.prompt_list")
    @patch("cafe.ui.cli.prompt_list")
    @patch("cafe.ui.cli.prompt_text")
    def test_prepare_interactive_saves_pr_auto_create_false(self, mock_prompt_text, mock_cli_list, mock_phase_list, mock_template_list, mock_cli_confirm, mock_phase_confirm, temp_repo_dir, mock_git_ops):
        """測試互動模式選擇不自動建立 PR (no)"""
        # Mock user inputs
        mock_prompt_text.return_value = "test-issue"
        mock_cli_confirm.side_effect = [False, False]  # worktree, pr auto_create
        mock_phase_confirm.return_value = False
        mock_cli_list.side_effect = ["Custom configuration", "Medium"]
        mock_phase_list.return_value = "1. Manual input"
        mock_template_list.return_value = "default (system default)"

        result = runner.invoke(app, ["prepare", "--no-auto-create-pr"])

        assert result.exit_code == 0

        config_file = temp_repo_dir / ".cafe" / "issues" / "test-issue" / "issue.yaml"
        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            assert "pr" in config_data
            assert config_data["pr"]["auto_create"] is False

    def test_prepare_with_issue_argument_persists_local_only_choice(
        self, temp_repo_dir, mock_git_ops
    ):
        """測試 issue argument 路徑原樣保存明確的 local-only 選擇。"""
        result = runner.invoke(app, ["prepare", "test-issue", "--no-auto-create-pr"])

        assert result.exit_code == 0

        config_file = temp_repo_dir / ".cafe" / "issues" / "test-issue" / "issue.yaml"
        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            assert config_data["pr"] == {"auto_create": False}

    def test_prepare_worktree_overwrites_copied_active_issue_marker(self, temp_repo_dir, mock_git_ops):
        """Worktree prepare overwrites a copied stale active_issue marker."""
        worktree_path = temp_repo_dir / ".cafe" / "worktrees" / "new-issue"
        worktree_cafe = worktree_path / ".cafe"
        worktree_cafe.mkdir(parents=True)
        (worktree_cafe / "active_issue").write_text("old-issue\n", encoding="utf-8")

        result = runner.invoke(
            app,
            [
                "prepare",
                "new-issue",
                "--worktree",
                str(worktree_path),
                "--no-auto-create-pr",
            ],
        )

        assert result.exit_code == 0
        assert (worktree_cafe / "active_issue").read_text(encoding="utf-8").strip() == "new-issue"

    def test_prepare_worktree_creates_issue_yaml_in_repo_root(self, temp_repo_dir, mock_git_ops):
        """測試 worktree 模式下，在 repo root 也創建 issue.yaml 供 cafe ls 讀取"""
        # Execute prepare with worktree mode (non-interactive)
        result = runner.invoke(
            app,
            [
                "prepare",
                "test-issue",
                "--worktree",
                ".cafe/worktrees/test-issue",
                "--no-auto-create-pr",
            ]
        )

        assert result.exit_code == 0

        # Verify: issue.yaml should exist in BOTH locations
        # 1. In worktree location
        worktree_config = temp_repo_dir / ".cafe" / "worktrees" / "test-issue" / ".cafe" / "issues" / "test-issue" / "issue.yaml"
        assert worktree_config.exists(), "issue.yaml should exist in worktree location"

        # 2. In repo root location (for cafe ls to read)
        repo_root_config = temp_repo_dir / ".cafe" / "issues" / "test-issue" / "issue.yaml"
        assert repo_root_config.exists(), "issue.yaml should exist in repo root location for cafe ls"

        # Verify: Both files should contain worktree_path
        with open(worktree_config) as f:
            worktree_data = yaml.safe_load(f)
            assert "worktree_path" in worktree_data
            assert worktree_data["worktree_path"] == ".cafe/worktrees/test-issue"

        with open(repo_root_config) as f:
            repo_data = yaml.safe_load(f)
            assert "worktree_path" in repo_data
            assert repo_data["worktree_path"] == ".cafe/worktrees/test-issue"


class TestPrepareNonInteractiveMode:
    """測試 prepare 命令的 non-interactive 模式"""

    def test_non_interactive_missing_required_input_method(self, temp_repo_dir, mock_git_ops):
        """Test 1.3: 驗證 non-interactive 模式下缺少必填參數時顯示錯誤"""
        # 測試場景：--no-interactive 但缺少 --input-method
        result = runner.invoke(
            app,
            ["prepare", "test-issue", "--no-interactive", "--no-auto-create-pr"],
        )

        assert result.exit_code == 1
        assert "Error" in result.stdout
        assert "--input-method" in result.stdout or "input-method" in result.stdout

    def test_non_interactive_github_mode_missing_issue_id(self, temp_repo_dir, mock_git_ops):
        """Test 1.3: 驗證 GitHub 模式下缺少 --issue-id 時顯示錯誤"""
        # 測試場景：--input-method=github 但缺少 --issue-id
        result = runner.invoke(app, [
            "prepare", "test-issue",
            "--no-interactive",
            "--input-method=github",
            "--no-auto-create-pr",
        ])

        assert result.exit_code == 1
        assert "Error" in result.stdout
        assert "--issue-id" in result.stdout or "issue-id" in result.stdout or "issue_id" in result.stdout

    def test_non_interactive_with_defaults(self, temp_repo_dir, mock_git_ops):
        """Test 1.4: 驗證參數預設值在 non-interactive 模式下正確使用"""
        # 測試場景：--no-interactive --input-method=manual（不指定 rigor 和 template）
        result = runner.invoke(app, [
            "prepare", "test-issue",
            "--no-interactive",
            "--input-method=manual",
            "--no-auto-create-pr",
        ])

        assert result.exit_code == 0

        # 檢查設定檔是否使用預設值
        config_file = temp_repo_dir / ".cafe" / "issues" / "test-issue" / "issue.yaml"
        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            # 應該有 spec 設定
            assert "spec" in config_data
            assert config_data["spec"]["rigor"] == "medium"  # 預設值
            # 應該有 plan 設定
            assert "plan" in config_data
            assert config_data["plan"]["template"] == "default"  # 預設值


class TestPrepareSpecTemplateParameter:
    """測試 prepare 命令的 spec template 參數"""

    def test_both_templates_can_be_specified(self, temp_repo_dir, mock_git_ops):
        """測試可以同時指定 spec 和 plan template"""
        result = runner.invoke(app, [
            "prepare", "test-issue",
            "--no-interactive",
            "--input-method=manual",
            "--spec-template=simple",
            "--plan-template=bug",
            "--no-auto-create-pr",
        ])

        assert result.exit_code == 0

        config_file = temp_repo_dir / ".cafe" / "issues" / "test-issue" / "issue.yaml"
        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            assert config_data["spec"]["template"] == "simple"
            assert config_data["plan"]["template"] == "bug"

    def test_spec_template_defaults_to_auto(self, temp_repo_dir, mock_git_ops):
        """測試 spec template 預設為 auto"""
        result = runner.invoke(app, [
            "prepare", "test-issue",
            "--no-interactive",
            "--input-method=manual",
            "--no-auto-create-pr",
        ])

        assert result.exit_code == 0

        config_file = temp_repo_dir / ".cafe" / "issues" / "test-issue" / "issue.yaml"
        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            # Should have spec template with default value
            assert "spec" in config_data
            # Default is "auto"
            if "template" in config_data["spec"]:
                assert config_data["spec"]["template"] == "auto"


class TestPrepareCommandSetupMode:
    """測試 prepare 命令的 Quick setup vs Custom configuration 功能"""

    @patch("cafe.ui.phase_prompts.prompt_confirm")
    @patch("cafe.ui.cli.prompt_confirm")
    @patch("cafe.ui.template_selector.prompt_list")
    @patch("cafe.ui.phase_prompts.prompt_list")
    @patch("cafe.ui.cli.prompt_list")
    @patch("cafe.ui.cli.prompt_text")
    def test_quick_setup_skips_all_config_questions(self, mock_prompt_text, mock_cli_list, mock_phase_list, mock_template_list, mock_cli_confirm, mock_phase_confirm, temp_repo_dir, mock_git_ops):
        """測試選擇 Quick setup 時跳過所有設定問題並套用預設值"""
        # Mock user inputs
        mock_prompt_text.return_value = "test-feature"
        mock_cli_confirm.return_value = False  # worktree (n)
        
        # Input method 選擇 -> Manual input (第一個 prompt)
        mock_phase_list.return_value = "1. Manual input"
        
        # Setup mode 選擇 -> Quick setup (第二個 prompt)
        mock_cli_list.return_value = "Quick setup (use recommended defaults)"
        
        result = runner.invoke(app, ["prepare", "--auto-create-pr"])

        assert result.exit_code == 0
        
        # 驗證設定檔包含預設值
        config_file = temp_repo_dir / ".cafe" / "issues" / "test-feature" / "issue.yaml"
        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            
            # 驗證套用預設值
            assert config_data["spec"]["rigor"] == "medium"
            assert config_data["spec"]["template"] == "auto"
            assert config_data["spec"]["input_method"] == "manual"
            assert config_data["spec"]["sync_github"] == False  # manual input -> false
            assert config_data["plan"]["template"] == "auto"
            assert config_data["plan"]["sync_github"] == False  # manual input -> false
            assert config_data["pr"]["auto_create"] == True  # GitHub repo -> true

        # 驗證先詢問了 input method (mock_phase_list 呼叫 1 次)
        assert mock_phase_list.call_count == 1
        # 驗證然後詢問了設定模式 (mock_cli_list 呼叫 1 次)
        assert mock_cli_list.call_count == 1
        # 驗證沒有詢問 templates (mock_template_list 不應該被呼叫)
        mock_template_list.assert_not_called()
        # 驗證沒有詢問 sync 或其他 confirm 問題 (mock_phase_confirm 不應該被呼叫)
        mock_phase_confirm.assert_not_called()

    def test_non_interactive_mode_not_affected_by_setup_mode(self, temp_repo_dir, mock_git_ops):
        """測試 non-interactive mode 不受設定模式影響"""
        result = runner.invoke(app, [
            "prepare", "non-interactive-test",
            "--no-interactive",
            "--input-method=manual",
            "--rigor=low",
            "--spec-template=auto",
            "--plan-template=default",
            "--no-auto-create-pr",
        ])

        assert result.exit_code == 0
        
        # 驗證設定檔使用 CLI 參數的值
        config_file = temp_repo_dir / ".cafe" / "issues" / "non-interactive-test" / "issue.yaml"
        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            
            assert config_data["spec"]["rigor"] == "low"
            assert config_data["spec"]["template"] == "auto"
            assert config_data["plan"]["template"] == "default"

    def test_issue_name_argument_skips_setup_mode_prompt(self, temp_repo_dir, mock_git_ops):
        """測試提供 issue name 參數時不顯示設定模式提示（向後相容）"""
        result = runner.invoke(
            app, ["prepare", "backward-compat-test", "--no-auto-create-pr"]
        )

        assert result.exit_code == 0
        
        # 不應該詢問設定模式，直接使用舊的行為
        # 驗證不會產生 spec/plan 設定（舊行為）
        config_file = temp_repo_dir / ".cafe" / "issues" / "backward-compat-test" / "issue.yaml"
        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            
            # 舊行為：不儲存 spec/plan 設定
            assert "spec" not in config_data
            assert "plan" not in config_data
            assert config_data["pr"] == {"auto_create": False}

    @patch("cafe.ui.phase_prompts.prompt_text")
    @patch("cafe.ui.phase_prompts.GitHubOps")
    @patch("cafe.ui.cli.GitHubOps")
    @patch("cafe.ui.phase_prompts.prompt_confirm")
    @patch("cafe.ui.cli.prompt_confirm")
    @patch("cafe.ui.template_selector.prompt_list")
    @patch("cafe.ui.phase_prompts.prompt_list")
    @patch("cafe.ui.cli.prompt_list")
    @patch("cafe.ui.cli.prompt_text")
    def test_github_issue_mode_quick_setup(self, mock_prompt_text_cli, mock_cli_list, mock_phase_list, mock_template_list, mock_cli_confirm, mock_phase_confirm, MockGitHubOps_cli, MockGitHubOps_phase, mock_prompt_text_phase, temp_repo_dir, mock_git_ops):
        """測試選擇 GitHub Issue 模式後再選擇 Quick setup"""
        # Mock GitHubOps
        mock_github_ops = MagicMock()
        MockGitHubOps_cli.return_value = mock_github_ops
        MockGitHubOps_phase.return_value = mock_github_ops
        mock_github_ops.extract_issue_number.return_value = "123"
        
        # Mock user inputs
        mock_prompt_text_cli.return_value = "github-issue-test"  # Issue name
        mock_prompt_text_phase.return_value = "123"  # GitHub Issue ID
        mock_cli_confirm.return_value = False  # worktree (n)
        
        # Input method 選擇 -> Fetch from GitHub Issue (第一個 prompt)
        mock_phase_list.return_value = "2. GitHub issue"
        
        # Setup mode 選擇 -> Quick setup (第二個 prompt，在輸入 Issue ID 後)
        mock_cli_list.return_value = "Quick setup (use recommended defaults)"
        
        result = runner.invoke(app, ["prepare", "--auto-create-pr"])
        
        assert result.exit_code == 0
        
        # 驗證設定檔包含預設值，且 sync_github 為 true（因為使用 GitHub Issue）
        config_file = temp_repo_dir / ".cafe" / "issues" / "github-issue-test" / "issue.yaml"
        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            
            # 驗證套用預設值
            assert config_data["spec"]["rigor"] == "medium"
            assert config_data["spec"]["template"] == "auto"
            assert config_data["spec"]["input_method"] == "github"
            assert config_data["spec"]["issue_id"] == "123"
            assert config_data["spec"]["sync_github"] == True  # GitHub Issue -> true
            assert config_data["plan"]["template"] == "auto"
            assert config_data["plan"]["sync_github"] == True  # GitHub Issue -> true
            assert config_data["pr"]["auto_create"] == True  # GitHub repo -> true

        # 驗證先詢問了 input method (mock_phase_list 呼叫 1 次)
        assert mock_phase_list.call_count == 1
        # 驗證然後詢問了設定模式 (mock_cli_list 呼叫 1 次)
        assert mock_cli_list.call_count == 1
        # 驗證沒有詢問 templates 或其他設定
        mock_template_list.assert_not_called()
        mock_phase_confirm.assert_not_called()


class TestPrepareCommandPostPrTodoList:
    """Test --post-pr-todo-list option in cafe prepare command."""

    @patch("cafe.ui.phase_prompts.prompt_text")
    @patch("cafe.ui.phase_prompts.GitHubOps")
    @patch("cafe.ui.cli.GitHubOps")
    @patch("cafe.ui.phase_prompts.prompt_confirm")
    @patch("cafe.ui.cli.prompt_confirm")
    @patch("cafe.ui.template_selector.prompt_list")
    @patch("cafe.ui.phase_prompts.prompt_list")
    @patch("cafe.ui.cli.prompt_list")
    @patch("cafe.ui.cli.prompt_text")
    def test_quick_setup_persists_explicit_post_todo_list_true(
        self,
        mock_prompt_text_cli,
        mock_cli_list,
        mock_phase_list,
        mock_template_list,
        mock_cli_confirm,
        mock_phase_confirm,
        MockGitHubOps_cli,
        MockGitHubOps_phase,
        mock_prompt_text_phase,
        temp_repo_dir,
        mock_git_ops,
    ):
        """Test 3.1: Quick setup preserves an explicit PR todo-list choice."""
        mock_github_ops = MagicMock()
        MockGitHubOps_cli.return_value = mock_github_ops
        MockGitHubOps_phase.return_value = mock_github_ops
        mock_github_ops.extract_issue_number.return_value = "123"

        mock_prompt_text_cli.return_value = "quick-setup-test"
        mock_prompt_text_phase.return_value = "123"
        mock_cli_confirm.return_value = False  # worktree (n)

        mock_phase_list.return_value = "2. GitHub issue"
        mock_cli_list.return_value = "Quick setup (use recommended defaults)"

        result = runner.invoke(
            app, ["prepare", "--auto-create-pr", "--post-pr-todo-list"]
        )

        assert result.exit_code == 0

        config_file = temp_repo_dir / ".cafe" / "issues" / "quick-setup-test" / "issue.yaml"
        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            assert config_data["pr"]["auto_create"] is True
            assert config_data["pr"]["post_todo_list"] is True

    @patch("cafe.ui.phase_prompts.prompt_text")
    @patch("cafe.ui.phase_prompts.GitHubOps")
    @patch("cafe.ui.cli.GitHubOps")
    @patch("cafe.ui.phase_prompts.prompt_confirm")
    @patch("cafe.ui.cli.prompt_confirm")
    @patch("cafe.ui.template_selector.prompt_list")
    @patch("cafe.ui.phase_prompts.prompt_list")
    @patch("cafe.ui.cli.prompt_list")
    @patch("cafe.ui.cli.prompt_text")
    def test_custom_config_auto_create_true_prompts_post_todo_list(
        self,
        mock_prompt_text_cli,
        mock_cli_list,
        mock_phase_list,
        mock_template_list,
        mock_cli_confirm,
        mock_phase_confirm,
        MockGitHubOps_cli,
        MockGitHubOps_phase,
        mock_prompt_text_phase,
        temp_repo_dir,
        mock_git_ops,
    ):
        """Test 3.2: Custom 設定モードで auto_create=True の場合、post_todo_list が尋ねられる。"""
        mock_github_ops = MagicMock()
        MockGitHubOps_cli.return_value = mock_github_ops
        MockGitHubOps_phase.return_value = mock_github_ops
        mock_github_ops.extract_issue_number.return_value = "42"

        mock_prompt_text_cli.return_value = "custom-auto-create"
        mock_prompt_text_phase.return_value = "42"
        # worktree=No, sync_spec=True, sync_plan=True, auto_create=True, post_todo_list=False
        mock_cli_confirm.side_effect = [False, True, True, True, False]

        mock_phase_list.return_value = "2. GitHub issue"
        mock_cli_list.side_effect = ["Custom configuration", "Medium"]
        mock_template_list.return_value = "default (system default)"

        result = runner.invoke(app, ["prepare", "--auto-create-pr"])

        assert result.exit_code == 0

        # post_todo_list should have been set (either True or False from prompt)
        config_file = temp_repo_dir / ".cafe" / "issues" / "custom-auto-create" / "issue.yaml"
        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            # post_todo_list should be present when auto_create is True
            assert "post_todo_list" in config_data.get("pr", {})

    @patch("cafe.ui.phase_prompts.prompt_text")
    @patch("cafe.ui.phase_prompts.GitHubOps")
    @patch("cafe.ui.cli.GitHubOps")
    @patch("cafe.ui.phase_prompts.prompt_confirm")
    @patch("cafe.ui.cli.prompt_confirm")
    @patch("cafe.ui.template_selector.prompt_list")
    @patch("cafe.ui.phase_prompts.prompt_list")
    @patch("cafe.ui.cli.prompt_list")
    @patch("cafe.ui.cli.prompt_text")
    def test_custom_config_auto_create_false_does_not_prompt_post_todo_list(
        self,
        mock_prompt_text_cli,
        mock_cli_list,
        mock_phase_list,
        mock_template_list,
        mock_cli_confirm,
        mock_phase_confirm,
        MockGitHubOps_cli,
        MockGitHubOps_phase,
        mock_prompt_text_phase,
        temp_repo_dir,
        mock_git_ops,
    ):
        """Test 3.3: Custom 設定モードで auto_create=False の場合、post_todo_list は尋ねられない。"""
        mock_github_ops = MagicMock()
        MockGitHubOps_cli.return_value = mock_github_ops
        MockGitHubOps_phase.return_value = mock_github_ops
        mock_github_ops.extract_issue_number.return_value = "42"

        mock_prompt_text_cli.return_value = "custom-no-auto-create"
        mock_prompt_text_phase.return_value = "42"
        # worktree=No, sync_spec=True, sync_plan=True, auto_create=False
        mock_cli_confirm.side_effect = [False, True, True, False]

        mock_phase_list.return_value = "2. GitHub issue"
        mock_cli_list.side_effect = ["Custom configuration", "Medium"]
        mock_template_list.return_value = "default (system default)"

        result = runner.invoke(app, ["prepare", "--no-auto-create-pr"])

        assert result.exit_code == 0

        # post_todo_list should NOT be set when auto_create is False
        config_file = temp_repo_dir / ".cafe" / "issues" / "custom-no-auto-create" / "issue.yaml"
        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            assert "post_todo_list" not in config_data.get("pr", {})

    def test_non_interactive_post_pr_todo_list_flag_saves_to_config(self, temp_repo_dir, mock_git_ops):
        """Test 3.4/3.5: --no-interactive モードで --post-pr-todo-list フラグが issue.yaml に保存される。"""
        result = runner.invoke(app, [
            "prepare", "noninteractive-test",
            "--no-interactive",
            "--input-method", "manual",
            "--rigor", "medium",
            "--auto-create-pr",
            "--post-pr-todo-list",
        ])

        assert result.exit_code == 0

        config_file = temp_repo_dir / ".cafe" / "issues" / "noninteractive-test" / "issue.yaml"
        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            assert config_data["pr"]["post_todo_list"] is True

    def test_non_interactive_no_auto_create_pr_persists_false(
        self, temp_repo_dir, mock_git_ops
    ):
        result = runner.invoke(
            app,
            [
                "prepare",
                "noninteractive-local-pr",
                "--no-interactive",
                "--input-method",
                "manual",
                "--rigor",
                "medium",
                "--no-auto-create-pr",
            ],
        )

        assert result.exit_code == 0
        config_file = (
            temp_repo_dir
            / ".cafe"
            / "issues"
            / "noninteractive-local-pr"
            / "issue.yaml"
        )
        config_data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        assert config_data["pr"]["auto_create"] is False

    def test_issue_argument_no_auto_create_pr_persists_false(
        self, temp_repo_dir, mock_git_ops
    ):
        result = runner.invoke(
            app,
            ["prepare", "argument-local-pr", "--no-auto-create-pr"],
        )

        assert result.exit_code == 0
        config_file = (
            temp_repo_dir / ".cafe" / "issues" / "argument-local-pr" / "issue.yaml"
        )
        config_data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        assert config_data["pr"]["auto_create"] is False

    def test_non_interactive_no_post_pr_todo_list_flag_saves_false_to_config(self, temp_repo_dir, mock_git_ops):
        """Test 3.4b: --no-interactive モードで --no-post-pr-todo-list フラグが issue.yaml に保存される。"""
        result = runner.invoke(app, [
            "prepare", "noninteractive-false-test",
            "--no-interactive",
            "--input-method", "manual",
            "--rigor", "medium",
            "--auto-create-pr",
            "--no-post-pr-todo-list",
        ])

        assert result.exit_code == 0

        config_file = temp_repo_dir / ".cafe" / "issues" / "noninteractive-false-test" / "issue.yaml"
        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            assert config_data["pr"]["post_todo_list"] is False
