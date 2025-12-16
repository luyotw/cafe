"""測試 cafe prepare 在非 GitHub repo 的行為."""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import yaml
from typer.testing import CliRunner

from cafe.ui.cli import app

runner = CliRunner()


@pytest.fixture
def temp_repo_dir(tmp_path):
    """建立臨時 repository 目錄."""
    cafe_dir = tmp_path / ".cafe"
    cafe_dir.mkdir(parents=True)
    return tmp_path


@pytest.fixture(autouse=True)
def change_test_dir(tmp_path, monkeypatch):
    """自動切換到 tmp_path 以確保測試隔離."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def mock_git_ops_non_github():
    """建立 mock GitOperations 和 non-GitHub repo 環境."""
    with patch('cafe.ui.cli.GitOperations') as MockGitOperations, \
         patch('cafe.utils.git_utils.is_github_repo') as mock_is_github_repo:
        mock_git = MagicMock()
        MockGitOperations.return_value = mock_git

        # Default Git behaviors
        mock_git.get_current_branch.return_value = "main"
        mock_git.has_uncommitted_changes.return_value = False
        mock_git.branch_exists.return_value = False
        mock_git.create_branch.return_value = None
        mock_git.checkout_branch.return_value = None

        # Mock as non-GitHub repo
        mock_is_github_repo.return_value = False

        yield mock_git


class TestPrepareNonGitHubRepo:
    """測試 prepare 指令在非 GitHub repo 的行為."""

    @patch("cafe.ui.cli.prompt_confirm")
    @patch("cafe.ui.template_selector.prompt_list")
    @patch("cafe.ui.phase_prompts.prompt_list")
    @patch("cafe.ui.cli.prompt_text")
    def test_prepare_non_github_repo_interactive_skips_input_method_prompt(
        self, mock_prompt_text, mock_phase_list, mock_template_list, mock_prompt_confirm, temp_repo_dir, mock_git_ops_non_github
    ):
        """測試在非 GitHub repo 中執行 prepare 會自動設定 spec.input_method 為 manual."""
        # Mock user inputs: issue name, worktree (n), rigor, template
        # Note: Should NOT ask for input method or PR auto-create in non-GitHub repos
        mock_prompt_text.return_value = "test-issue"
        mock_prompt_confirm.return_value = False  # worktree (n)
        mock_phase_list.return_value = "Medium (中) - 平衡模式 [預設]\n   • 詢問重要細節和關鍵場景\n   • 在速度和精確度間取得平衡\n   • 適合：一般功能開發"
        mock_template_list.return_value = "1. default"

        result = runner.invoke(app, ["prepare"])

        assert result.exit_code == 0
        assert "Successfully prepared issue: test-issue" in result.stdout

        # Verify config.yaml
        config_file = temp_repo_dir / ".cafe" / "issues" / "test-issue" / "config.yaml"
        assert config_file.exists()

        with open(config_file) as f:
            config_data = yaml.safe_load(f)

        # Should automatically set spec.input_method to "manual"
        assert config_data["spec"]["input_method"] == "manual"
        # Should not have issue_id in config
        assert "issue_id" not in config_data.get("spec", {})

    @patch("cafe.ui.cli.prompt_confirm")
    @patch("cafe.ui.template_selector.prompt_list")
    @patch("cafe.ui.phase_prompts.prompt_list")
    @patch("cafe.ui.cli.prompt_text")
    def test_prepare_non_github_repo_sets_pr_auto_create_false(
        self, mock_prompt_text, mock_phase_list, mock_template_list, mock_prompt_confirm, temp_repo_dir, mock_git_ops_non_github
    ):
        """測試在非 GitHub repo 中執行 prepare 會自動設定 pr.auto_create 為 False."""
        # Mock user inputs
        mock_prompt_text.return_value = "my-feature"
        mock_prompt_confirm.return_value = False  # worktree (n)
        mock_phase_list.return_value = "Medium (中) - 平衡模式 [預設]\n   • 詢問重要細節和關鍵場景\n   • 在速度和精確度間取得平衡\n   • 適合：一般功能開發"
        mock_template_list.return_value = "1. default"

        result = runner.invoke(app, ["prepare"])

        assert result.exit_code == 0

        # Verify config.yaml
        config_file = temp_repo_dir / ".cafe" / "issues" / "my-feature" / "config.yaml"
        assert config_file.exists()

        with open(config_file) as f:
            config_data = yaml.safe_load(f)

        # Should automatically set pr.auto_create to False
        assert config_data["pr"]["auto_create"] is False

    def test_prepare_non_github_repo_non_interactive_mode(
        self, temp_repo_dir, mock_git_ops_non_github
    ):
        """測試在非 GitHub repo 的非互動模式下 prepare 的行為."""
        # Non-interactive mode: provide issue name as argument
        result = runner.invoke(app, ["prepare", "quick-fix"])

        assert result.exit_code == 0
        assert "Successfully prepared issue: quick-fix" in result.stdout

        # Verify config.yaml
        config_file = temp_repo_dir / ".cafe" / "issues" / "quick-fix" / "config.yaml"
        assert config_file.exists()

        with open(config_file) as f:
            config_data = yaml.safe_load(f)

        # Should not have spec or pr config in non-interactive mode
        assert "spec" not in config_data
        assert "pr" not in config_data


@pytest.fixture
def mock_git_ops_github():
    """建立 mock GitOperations 和 GitHub repo 環境."""
    with patch('cafe.ui.cli.GitOperations') as MockGitOperations, \
         patch('cafe.utils.git_utils.is_github_repo') as mock_is_github_repo:
        mock_git = MagicMock()
        MockGitOperations.return_value = mock_git

        # Default Git behaviors
        mock_git.get_current_branch.return_value = "main"
        mock_git.has_uncommitted_changes.return_value = False
        mock_git.branch_exists.return_value = False
        mock_git.create_branch.return_value = None
        mock_git.checkout_branch.return_value = None

        # Mock as GitHub repo
        mock_is_github_repo.return_value = True

        yield mock_git


class TestPrepareGitHubRepo:
    """測試 prepare 指令在 GitHub repo 的行為保持不變."""

    @patch("cafe.ui.cli.prompt_confirm")
    @patch("cafe.ui.template_selector.prompt_list")
    @patch("cafe.ui.phase_prompts.prompt_list")
    @patch("cafe.ui.cli.prompt_text")
    def test_prepare_github_repo_interactive_asks_input_method(
        self, mock_prompt_text, mock_phase_list, mock_template_list, mock_prompt_confirm, temp_repo_dir, mock_git_ops_github
    ):
        """測試在 GitHub repo 中執行 prepare 會詢問 input method."""
        # Mock user inputs: issue name, worktree (n), input method (manual), rigor, template, pr (y)
        mock_prompt_text.return_value = "gh-issue"
        mock_prompt_confirm.side_effect = [False, True]  # worktree (n), pr (y)
        # Note: phase_prompts has input method selection too, need to handle both
        mock_phase_list.side_effect = [
            "1. 手動輸入需求",  # input method
            "Medium (中) - 平衡模式 [預設]\n   • 詢問重要細節和關鍵場景\n   • 在速度和精確度間取得平衡\n   • 適合：一般功能開發",  # rigor
        ]
        mock_template_list.return_value = "1. default"  # template

        result = runner.invoke(app, ["prepare"])

        assert result.exit_code == 0

        # Verify config.yaml
        config_file = temp_repo_dir / ".cafe" / "issues" / "gh-issue" / "config.yaml"
        assert config_file.exists()

        with open(config_file) as f:
            config_data = yaml.safe_load(f)

        # User selected manual input
        assert config_data["spec"]["input_method"] == "manual"
        # User confirmed PR auto-create
        assert config_data["pr"]["auto_create"] is True
