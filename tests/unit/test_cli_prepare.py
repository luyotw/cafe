"""Tests for prepare CLI command."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from cafe.ui.cli import app

runner = CliRunner()


@pytest.fixture
def temp_repo_dir(tmp_path, monkeypatch):
    """Create a temporary git repository directory."""
    # Change to temp directory
    monkeypatch.chdir(tmp_path)

    # Create .cafe directory
    cafe_dir = tmp_path / ".cafe"
    cafe_dir.mkdir(parents=True)

    return tmp_path


@pytest.fixture
def mock_git_ops():
    """Create a mock GitOperations instance."""
    with patch('cafe.ui.cli.GitOperations') as MockGitOperations:
        mock_git = MagicMock()
        MockGitOperations.return_value = mock_git

        # Default behaviors
        mock_git.get_current_branch.return_value = "main"
        mock_git.has_uncommitted_changes.return_value = False
        mock_git.branch_exists.return_value = False
        mock_git.create_branch.return_value = None
        mock_git.checkout_branch.return_value = None

        yield mock_git


class TestPrepareCommand:
    """Test prepare command."""

    def test_prepare_with_issue_name_argument(self, temp_repo_dir, mock_git_ops):
        """測試使用 CLI 參數指定 issue name"""
        result = runner.invoke(app, ["prepare", "test-issue"])

        assert result.exit_code == 0
        assert "Successfully prepared issue: test-issue" in result.stdout
        assert "Feature branch: test-issue" in result.stdout
        assert "Base branch: main" in result.stdout
        assert "Next step: cafe spec" in result.stdout

        # Verify directory structure created
        issue_dir = temp_repo_dir / ".cafe" / "issues" / "test-issue"
        assert issue_dir.exists()
        assert (issue_dir / "spec").exists()
        assert (issue_dir / "sessions").exists()

        # Verify config.yaml created
        config_file = issue_dir / "config.yaml"
        assert config_file.exists()

        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            assert config_data["base_branch"] == "main"
            assert config_data["feature_branch"] == "test-issue"

        # Verify git operations called
        mock_git_ops.branch_exists.assert_called_once_with("test-issue")
        mock_git_ops.create_branch.assert_called_once_with("test-issue")

    def test_prepare_interactive_mode(self, temp_repo_dir, mock_git_ops):
        """測試互動式輸入 issue name"""
        # Simulate user input
        result = runner.invoke(app, ["prepare"], input="my-feature\n")

        assert result.exit_code == 0
        assert "Successfully prepared issue: my-feature" in result.stdout

        # Verify directory created
        issue_dir = temp_repo_dir / ".cafe" / "issues" / "my-feature"
        assert issue_dir.exists()

    def test_prepare_with_custom_base_branch(self, temp_repo_dir, mock_git_ops):
        """測試指定自訂 base branch"""
        result = runner.invoke(app, ["prepare", "feature-x", "--base", "develop"])

        assert result.exit_code == 0
        assert "Base branch: develop" in result.stdout

        # Verify config contains custom base branch
        config_file = temp_repo_dir / ".cafe" / "issues" / "feature-x" / "config.yaml"
        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            assert config_data["base_branch"] == "develop"

        # get_current_branch should not be called when base branch is specified
        mock_git_ops.get_current_branch.assert_not_called()

    def test_prepare_branch_already_exists(self, temp_repo_dir, mock_git_ops):
        """測試切換到已存在的 branch"""
        # Mock branch exists
        mock_git_ops.branch_exists.return_value = True

        result = runner.invoke(app, ["prepare", "existing-issue"])

        assert result.exit_code == 0
        assert "already exists, switching to it" in result.stdout

        # Should checkout instead of create
        mock_git_ops.checkout_branch.assert_called_once_with("existing-issue")
        mock_git_ops.create_branch.assert_not_called()

    def test_prepare_with_uncommitted_changes_cancel(self, temp_repo_dir, mock_git_ops):
        """測試有未 commit 變更時取消"""
        mock_git_ops.has_uncommitted_changes.return_value = True

        # User cancels when prompted
        result = runner.invoke(app, ["prepare", "test-issue"], input="n\n")

        assert result.exit_code == 0
        assert "Warning: You have uncommitted changes" in result.stdout
        assert "Cancelled" in result.stdout

        # Should not create directories or branches
        issue_dir = temp_repo_dir / ".cafe" / "issues" / "test-issue"
        assert not issue_dir.exists()
        mock_git_ops.create_branch.assert_not_called()

    def test_prepare_with_uncommitted_changes_continue(self, temp_repo_dir, mock_git_ops):
        """測試有未 commit 變更時繼續執行"""
        mock_git_ops.has_uncommitted_changes.return_value = True

        # User continues when prompted
        result = runner.invoke(app, ["prepare", "test-issue"], input="y\n")

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

        result = runner.invoke(app, ["prepare", "test-issue", "--no-check"])

        assert result.exit_code == 0
        assert "Successfully prepared issue: test-issue" in result.stdout
        # Should not show warning
        assert "Warning: You have uncommitted changes" not in result.stdout

        # has_uncommitted_changes should not be called
        mock_git_ops.has_uncommitted_changes.assert_not_called()

    def test_prepare_not_git_repo(self, temp_repo_dir):
        """測試在非 git repo 時失敗"""
        # Mock GitOperations to raise exception
        with patch('cafe.ui.cli.GitOperations') as MockGitOperations:
            MockGitOperations.side_effect = Exception("Not a git repository")

            result = runner.invoke(app, ["prepare", "test-issue"])

            assert result.exit_code == 1
            assert "Not a git repository" in result.stdout
            assert "git init" in result.stdout

    def test_prepare_creates_proper_directory_structure(self, temp_repo_dir, mock_git_ops):
        """測試創建正確的目錄結構"""
        result = runner.invoke(app, ["prepare", "my-issue"])

        assert result.exit_code == 0

        issue_dir = temp_repo_dir / ".cafe" / "issues" / "my-issue"
        spec_dir = issue_dir / "spec"
        sessions_dir = issue_dir / "sessions"

        assert issue_dir.is_dir()
        assert spec_dir.is_dir()
        assert sessions_dir.is_dir()

    def test_prepare_config_yaml_format(self, temp_repo_dir, mock_git_ops):
        """測試 config.yaml 格式正確"""
        result = runner.invoke(app, ["prepare", "format-test"])

        assert result.exit_code == 0

        config_file = temp_repo_dir / ".cafe" / "issues" / "format-test" / "config.yaml"
        assert config_file.exists()

        # Read and verify YAML format
        with open(config_file) as f:
            content = f.read()
            assert "base_branch:" in content
            assert "feature_branch:" in content

            # Parse YAML
            config_data = yaml.safe_load(content)
            assert isinstance(config_data, dict)
            assert len(config_data) == 2

    def test_prepare_idempotent(self, temp_repo_dir, mock_git_ops):
        """測試重複執行 prepare 是否安全（冪等性）"""
        # First execution
        result1 = runner.invoke(app, ["prepare", "idempotent-test"])
        assert result1.exit_code == 0

        # Mock branch now exists
        mock_git_ops.branch_exists.return_value = True

        # Second execution
        result2 = runner.invoke(app, ["prepare", "idempotent-test"])
        assert result2.exit_code == 0
        assert "already exists" in result2.stdout

        # Config should still exist and be valid
        config_file = temp_repo_dir / ".cafe" / "issues" / "idempotent-test" / "config.yaml"
        assert config_file.exists()

    def test_prepare_with_different_base_branches(self, temp_repo_dir, mock_git_ops):
        """測試不同 base branches 的配置"""
        test_cases = [
            ("develop", "develop"),
            ("staging", "staging"),
            ("release/v1.0", "release/v1.0"),
        ]

        for issue_name, base_branch in test_cases:
            result = runner.invoke(app, ["prepare", f"issue-{issue_name}", "--base", base_branch])

            assert result.exit_code == 0
            assert f"Base branch: {base_branch}" in result.stdout

            config_file = temp_repo_dir / ".cafe" / "issues" / f"issue-{issue_name}" / "config.yaml"
            with open(config_file) as f:
                config_data = yaml.safe_load(f)
                assert config_data["base_branch"] == base_branch


class TestPrepareCommandWorktree:
    """Test prepare command with worktree support (TDD Red phase)."""

    def test_prepare_with_worktree_non_interactive(self, temp_repo_dir, mock_git_ops):
        """測試使用 --worktree 參數在非互動模式建立 worktree"""
        worktree_path = "worktrees/test-issue"
        result = runner.invoke(app, ["prepare", "test-issue", "--worktree", worktree_path])

        assert result.exit_code == 0
        # 驗證呼叫 create_worktree 而非 create_branch
        mock_git_ops.create_worktree.assert_called_once()
        mock_git_ops.create_branch.assert_not_called()

        # 驗證 worktree_path 儲存到 config.yaml
        config_file = temp_repo_dir / ".cafe" / "issues" / "test-issue" / "config.yaml"
        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            assert config_data["worktree_path"] == worktree_path

    def test_prepare_with_worktree_default_path_suggestion(self, temp_repo_dir, mock_git_ops):
        """測試 --worktree 使用預設路徑格式 worktrees/{issue-name}"""
        # 非互動模式下，--worktree 必須帶路徑參數
        # 這個測試驗證使用預設路徑格式的情況
        default_worktree_path = "worktrees/my-feature"
        result = runner.invoke(app, ["prepare", "my-feature", "--worktree", default_worktree_path])

        assert result.exit_code == 0
        # 應使用指定的路徑
        call_args = mock_git_ops.create_worktree.call_args
        assert call_args[0][0] == default_worktree_path  # 第一個參數是路徑

    def test_prepare_without_worktree_uses_branch(self, temp_repo_dir, mock_git_ops):
        """測試不使用 --worktree 時應建立分支"""
        result = runner.invoke(app, ["prepare", "normal-issue"])

        assert result.exit_code == 0
        # 驗證呼叫 create_branch 而非 create_worktree
        mock_git_ops.create_branch.assert_called_once()
        assert not hasattr(mock_git_ops, 'create_worktree') or not mock_git_ops.create_worktree.called

        # 驗證 config.yaml 不包含 worktree_path
        config_file = temp_repo_dir / ".cafe" / "issues" / "normal-issue" / "config.yaml"
        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            assert "worktree_path" not in config_data

    def test_prepare_success_message_includes_worktree_path(self, temp_repo_dir, mock_git_ops):
        """測試成功訊息包含 worktree 路徑資訊"""
        worktree_path = "worktrees/feature-x"
        result = runner.invoke(app, ["prepare", "feature-x", "--worktree", worktree_path])

        assert result.exit_code == 0
        assert "worktree" in result.stdout.lower() or worktree_path in result.stdout

    def test_prepare_with_worktree_calls_create_worktree_with_correct_params(self, temp_repo_dir, mock_git_ops):
        """測試 create_worktree 使用正確的參數"""
        worktree_path = "worktrees/test-branch"
        base_branch = "develop"
        result = runner.invoke(app, [
            "prepare", "test-branch",
            "--worktree", worktree_path,
            "--base", base_branch
        ])

        assert result.exit_code == 0
        # 驗證參數：路徑、分支名稱、base branch
        mock_git_ops.create_worktree.assert_called_once_with(
            worktree_path, "test-branch", base_branch
        )
