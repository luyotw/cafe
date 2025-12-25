"""Tests for prepare CLI command."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from cafe.ui.cli import app

runner = CliRunner()


@pytest.fixture
def temp_repo_dir(tmp_path):
    """Create a temporary git repository directory."""
    from tests.conftest import create_minimal_config

    # Create config.yaml (required by prepare command)
    create_minimal_config(tmp_path)

    return tmp_path


@pytest.fixture(autouse=True)
def change_test_dir(tmp_path, monkeypatch):
    """Automatically change to tmp_path for all tests to ensure isolation."""
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

        # Mock is_github_repo to return True by default (GitHub repo)
        mock_is_github_repo1.return_value = True
        mock_is_github_repo2.return_value = True

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
            assert config_data["auto"]["max_review_iterations"] == 5

        # Verify git operations called
        mock_git_ops.branch_exists.assert_called_once_with("test-issue")
        mock_git_ops.create_branch.assert_called_once_with("test-issue")

    @patch("cafe.ui.phase_prompts.prompt_confirm")
    @patch("cafe.ui.cli.prompt_confirm")
    @patch("cafe.ui.template_selector.prompt_list")
    @patch("cafe.ui.phase_prompts.prompt_list")
    @patch("cafe.ui.cli.prompt_text")
    def test_prepare_interactive_mode(self, mock_prompt_text, mock_phase_list, mock_template_list, mock_cli_confirm, mock_phase_confirm, temp_repo_dir, mock_git_ops):
        """測試互動式輸入 issue name"""
        # Mock user inputs
        mock_prompt_text.return_value = "my-feature"
        mock_cli_confirm.return_value = False  # worktree (n)
        mock_phase_confirm.return_value = True  # pr auto_create (y)
        mock_phase_list.side_effect = [
            "1. Manual input",  # input method (manual)
            "Medium - Balanced mode [Default]\n   • Ask important details and key scenarios\n   • Balance speed and precision\n   • Suitable for: general feature development",  # rigor
        ]
        mock_template_list.return_value = "1. default"  # template

        result = runner.invoke(app, ["prepare"])

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

        result = runner.invoke(app, ["prepare", "existing-issue"])

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
        result = runner.invoke(app, ["prepare", "test-issue"])

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
        result = runner.invoke(app, ["prepare", "test-issue"])

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
        """測試創建正確目錄結構"""
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
            assert len(config_data) == 3  # base_branch, feature_branch, auto
            assert "auto" in config_data
            assert config_data["auto"]["max_review_iterations"] == 5

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
            result = runner.invoke(app, ["prepare", f"issue-{issue_name}", "--base", base_branch])

            assert result.exit_code == 0
            assert f"Base branch: {base_branch}" in result.stdout

            config_file = temp_repo_dir / ".cafe" / "issues" / f"issue-{issue_name}" / "issue.yaml"
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

        # 驗證 worktree_path 儲存到 config.yaml（在 worktree 內）
        config_file = temp_repo_dir / worktree_path / ".cafe" / "issues" / "test-issue" / "issue.yaml"
        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            assert config_data["worktree_path"] == worktree_path

    def test_prepare_with_worktree_default_path_suggestion(self, temp_repo_dir, mock_git_ops):
        """測試 --worktree 使用預設路徑格式 worktrees/{issue-name}"""
        # 非互動模式下, --worktree 必須帶路徑參數
        # 這個測試驗證使用預設路徑格式情況
        default_worktree_path = "worktrees/my-feature"
        result = runner.invoke(app, ["prepare", "my-feature", "--worktree", default_worktree_path])

        assert result.exit_code == 0
        # 應使用指定路徑
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
        config_file = temp_repo_dir / ".cafe" / "issues" / "normal-issue" / "issue.yaml"
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
        """測試 create_worktree 使用正確參數"""
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

    @patch("cafe.ui.phase_prompts.prompt_confirm")
    @patch("cafe.ui.cli.prompt_confirm")
    @patch("cafe.ui.template_selector.prompt_list")
    @patch("cafe.ui.phase_prompts.prompt_list")
    @patch("cafe.ui.cli.prompt_text")
    def test_prepare_interactive_worktree_prompt_yes(self, mock_prompt_text, mock_phase_list, mock_template_list, mock_cli_confirm, mock_phase_confirm, temp_repo_dir, mock_git_ops):
        """測試互動模式詢問是否使用 worktree, 使用者選擇 Yes"""
        # Mock user inputs: issue name, worktree path
        mock_prompt_text.side_effect = ["my-feature", "worktrees/my-feature"]
        mock_cli_confirm.return_value = True  # worktree (y)
        mock_phase_confirm.return_value = True  # pr auto_create (y)
        mock_phase_list.side_effect = [
            "1. Manual input",  # input method (manual)
            "Medium - Balanced mode [Default]\n   • Ask important details and key scenarios\n   • Balance speed and precision\n   • Suitable for: general feature development",  # rigor
        ]
        mock_template_list.return_value = "1. default"

        result = runner.invoke(app, ["prepare"])

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
    @patch("cafe.ui.cli.prompt_text")
    def test_prepare_interactive_worktree_prompt_no(self, mock_prompt_text, mock_phase_list, mock_template_list, mock_cli_confirm, mock_phase_confirm, temp_repo_dir, mock_git_ops):
        """測試互動模式詢問是否使用 worktree, 使用者選擇 No"""
        # Mock user inputs
        mock_prompt_text.return_value = "normal-feature"
        mock_cli_confirm.return_value = False  # worktree (n)
        mock_phase_confirm.return_value = True  # pr auto_create (y)
        mock_phase_list.side_effect = [
            "1. Manual input",  # input method (manual)
            "Medium - Balanced mode [Default]\n   • Ask important details and key scenarios\n   • Balance speed and precision\n   • Suitable for: general feature development",  # rigor
        ]
        mock_template_list.return_value = "1. default"

        result = runner.invoke(app, ["prepare"])

        assert result.exit_code == 0
        # 驗證呼叫 create_branch 而非 create_worktree
        mock_git_ops.create_branch.assert_called_once_with("normal-feature")
        assert not hasattr(mock_git_ops, 'create_worktree') or not mock_git_ops.create_worktree.called

        # 驗證 config.yaml 不包含 worktree_path
        config_file = temp_repo_dir / ".cafe" / "issues" / "normal-feature" / "issue.yaml"
        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            assert "worktree_path" not in config_data

    @patch("cafe.ui.phase_prompts.prompt_confirm")
    @patch("cafe.ui.cli.prompt_confirm")
    @patch("cafe.ui.template_selector.prompt_list")
    @patch("cafe.ui.phase_prompts.prompt_list")
    @patch("cafe.ui.cli.prompt_text")
    def test_prepare_interactive_worktree_default_path_suggestion(self, mock_prompt_text, mock_phase_list, mock_template_list, mock_cli_confirm, mock_phase_confirm, temp_repo_dir, mock_git_ops):
        """測試互動模式建議預設路徑 .cafe/worktrees/{issue-name}"""
        # Mock user inputs: issue name, default path (empty string)
        mock_prompt_text.side_effect = ["test-issue", ".cafe/worktrees/test-issue"]
        mock_cli_confirm.return_value = True  # worktree (y)
        mock_phase_confirm.return_value = True  # pr auto_create (y)
        mock_phase_list.side_effect = [
            "1. Manual input",  # input method (manual)
            "Medium - Balanced mode [Default]\n   • Ask important details and key scenarios\n   • Balance speed and precision\n   • Suitable for: general feature development",  # rigor
        ]
        mock_template_list.return_value = "1. default"

        result = runner.invoke(app, ["prepare"])

        assert result.exit_code == 0
        # 驗證輸出中有顯示預設路徑建議
        assert ".cafe/worktrees/test-issue" in result.stdout
        # 驗證使用預設路徑
        mock_git_ops.create_worktree.assert_called_once_with(
            ".cafe/worktrees/test-issue", "test-issue", "main"
        )

    def test_prepare_creates_cafe_directory_in_worktree_not_symlink(self, temp_repo_dir, mock_git_ops):
        """測試 worktree 中創建實際 .cafe/ 目錄而非符號連結"""
        # Setup: 創建 repo root  .cafe/config.yaml
        repo_cafe_dir = temp_repo_dir / ".cafe"
        repo_cafe_dir.mkdir(parents=True, exist_ok=True)
        repo_config = repo_cafe_dir / "config.yaml"
        repo_config.write_text("test_config: value\n")

        # 創建 worktree 目錄（模擬 git worktree add 行為）
        worktree_path = temp_repo_dir / "worktrees" / "test-issue"
        worktree_path.mkdir(parents=True, exist_ok=True)

        # Mock create_worktree 為空操作（worktree 目錄已存在）
        mock_git_ops.create_worktree.return_value = None

        result = runner.invoke(app, ["prepare", "test-issue", "--worktree", str(worktree_path)])

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

        # 驗證 issue 目錄結構被創建
        worktree_issue_dir = worktree_cafe_dir / "issues" / "test-issue"
        assert worktree_issue_dir.exists(), "Issue directory should be created in worktree"
        assert (worktree_issue_dir / "spec").exists(), "spec directory should exist"
        assert (worktree_issue_dir / "sessions").exists(), "sessions directory should exist"

    def test_prepare_copies_agents_to_worktree(self, temp_repo_dir, mock_git_ops):
        """測試 worktree 模式下會複製 agents 到 worktree  .cafe/agents/"""
        # 創建 worktree 目錄（模擬 git worktree add 行為）
        worktree_path = temp_repo_dir / "worktrees" / "test-issue"
        worktree_path.mkdir(parents=True, exist_ok=True)

        # Mock create_worktree 為空操作
        mock_git_ops.create_worktree.return_value = None

        result = runner.invoke(app, ["prepare", "test-issue", "--worktree", str(worktree_path)])

        assert result.exit_code == 0

        # 驗證 agents 目錄被複製到 worktree
        worktree_agents_dir = worktree_path / ".cafe" / "agents"
        assert worktree_agents_dir.exists(), "agents directory should be copied to worktree"
        assert worktree_agents_dir.is_dir(), "agents should be a directory"

        # 驗證 agent 檔案存在
        assert (worktree_agents_dir / "pm" / "Roger.md").exists(), "Roger.md should exist in worktree"
        assert (worktree_agents_dir / "developer" / "David.md").exists(), "David.md should exist in worktree"
        assert (worktree_agents_dir / "reviewer" / "Richard.md").exists(), "Richard.md should exist in worktree"

    def test_prepare_copies_templates_to_worktree(self, temp_repo_dir, mock_git_ops):
        """測試 worktree 模式下會複製 templates 到 worktree  .cafe/templates/"""
        # 創建 worktree 目錄（模擬 git worktree add 行為）
        worktree_path = temp_repo_dir / "worktrees" / "test-issue"
        worktree_path.mkdir(parents=True, exist_ok=True)

        # Mock create_worktree 為空操作
        mock_git_ops.create_worktree.return_value = None

        result = runner.invoke(app, ["prepare", "test-issue", "--worktree", str(worktree_path)])

        assert result.exit_code == 0

        # 驗證 templates 目錄被複製到 worktree
        worktree_templates_dir = worktree_path / ".cafe" / "templates"
        assert worktree_templates_dir.exists(), "templates directory should be copied to worktree"
        assert worktree_templates_dir.is_dir(), "templates should be a directory"

        # 驗證 default template 存在
        assert (worktree_templates_dir / "plan" / "default.md").exists(), "default.md should exist in worktree"

    @patch("cafe.ui.phase_prompts.prompt_confirm")
    @patch("cafe.ui.cli.prompt_confirm")
    @patch("cafe.ui.template_selector.prompt_list")
    @patch("cafe.ui.phase_prompts.prompt_list")
    @patch("cafe.ui.cli.prompt_text")
    def test_prepare_interactive_saves_pr_auto_create_true(self, mock_prompt_text, mock_phase_list, mock_template_list, mock_cli_confirm, mock_phase_confirm, temp_repo_dir, mock_git_ops):
        """測試互動模式選擇自動建立 PR (yes)"""
        # Mock user inputs
        mock_prompt_text.return_value = "test-issue"
        mock_cli_confirm.return_value = False  # worktree (n)
        mock_phase_confirm.return_value = True  # pr auto_create (y)
        mock_phase_list.side_effect = [
            "1. Manual input",  # input method (manual)
            "Medium - Balanced mode [Default]\n   • Ask important details and key scenarios\n   • Balance speed and precision\n   • Suitable for: general feature development",  # rigor
        ]
        mock_template_list.return_value = "1. default"

        result = runner.invoke(app, ["prepare"])

        assert result.exit_code == 0

        config_file = temp_repo_dir / ".cafe" / "issues" / "test-issue" / "issue.yaml"
        assert config_file.exists()

        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            assert "pr" in config_data
            assert config_data["pr"]["auto_create"] is True

    @patch("cafe.ui.phase_prompts.prompt_confirm")
    @patch("cafe.ui.cli.prompt_confirm")
    @patch("cafe.ui.template_selector.prompt_list")
    @patch("cafe.ui.phase_prompts.prompt_list")
    @patch("cafe.ui.cli.prompt_text")
    def test_prepare_interactive_saves_pr_auto_create_false(self, mock_prompt_text, mock_phase_list, mock_template_list, mock_cli_confirm, mock_phase_confirm, temp_repo_dir, mock_git_ops):
        """測試互動模式選擇不自動建立 PR (no)"""
        # Mock user inputs
        mock_prompt_text.return_value = "test-issue"
        mock_cli_confirm.return_value = False  # worktree (n)
        mock_phase_confirm.return_value = False  # pr auto_create (n)
        mock_phase_list.side_effect = [
            "1. Manual input",  # input method (manual)
            "Medium - Balanced mode [Default]\n   • Ask important details and key scenarios\n   • Balance speed and precision\n   • Suitable for: general feature development",  # rigor
        ]
        mock_template_list.return_value = "1. default"

        result = runner.invoke(app, ["prepare"])

        assert result.exit_code == 0

        config_file = temp_repo_dir / ".cafe" / "issues" / "test-issue" / "issue.yaml"
        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            assert "pr" in config_data
            assert config_data["pr"]["auto_create"] is False

    def test_prepare_non_interactive_does_not_save_pr_config(self, temp_repo_dir, mock_git_ops):
        """測試非互動模式不儲存 PR 配置"""
        result = runner.invoke(app, ["prepare", "test-issue"])

        assert result.exit_code == 0

        config_file = temp_repo_dir / ".cafe" / "issues" / "test-issue" / "issue.yaml"
        with open(config_file) as f:
            config_data = yaml.safe_load(f)
            # Non-interactive mode should not have pr config
            assert "pr" not in config_data

    def test_prepare_worktree_creates_issue_yaml_in_repo_root(self, temp_repo_dir, mock_git_ops):
        """測試 worktree 模式下，在 repo root 也創建 issue.yaml 供 cafe ls 讀取"""
        # Execute prepare with worktree mode (non-interactive)
        result = runner.invoke(
            app,
            ["prepare", "test-issue", "--worktree", ".cafe/worktrees/test-issue"]
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
