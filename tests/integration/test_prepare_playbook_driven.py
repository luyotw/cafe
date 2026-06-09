"""Integration tests for playbook-driven cafe prepare behavior."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from cafe.ui.cli import app

runner = CliRunner()


@pytest.fixture
def temp_repo_dir(tmp_path):
    from tests.conftest import create_minimal_config

    create_minimal_config(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def change_test_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def mock_git_ops():
    with patch("cafe.ui.cli.GitOperations") as MockGitOperations, patch(
        "cafe.utils.git_utils.is_github_repo"
    ) as mock_is_github_repo, patch(
        "cafe.ui.phase_prompts.is_github_repo"
    ) as mock_is_github_repo_phase:
        mock_git = MagicMock()
        MockGitOperations.return_value = mock_git
        mock_git.get_current_branch.return_value = "main"
        mock_git.has_uncommitted_changes.return_value = False
        mock_git.branch_exists.return_value = False
        mock_git.worktree_exists.return_value = False
        mock_is_github_repo.return_value = True
        mock_is_github_repo_phase.return_value = True
        yield mock_git


def _write_config_with_playbook(base_dir: Path, playbook_id: str) -> None:
    config_path = base_dir / ".cafe" / "config.yaml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["playbook"] = playbook_id
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _write_custom_playbook(base_dir: Path, name: str, prepare_block: str) -> None:
    playbooks_dir = base_dir / ".cafe" / "playbooks"
    playbooks_dir.mkdir(parents=True, exist_ok=True)
    content = f"""
playbook:
  id: {name}
steps:
  spec:
    type: skill
    skill: spec
    role: pm
    "on":
      await_agent: plan
  plan:
    type: skill
    skill: plan
    role: developer
    "on":
      await_agent: _done
{prepare_block}
"""
    (playbooks_dir / f"{name}.yaml").write_text(content, encoding="utf-8")


class TestPreparePlaybookDriven:
    """Test List integration #1–#4."""

    @patch("cafe.ui.phase_prompts.prompt_text")
    @patch("cafe.ui.phase_prompts.GitHubOps")
    @patch("cafe.ui.cli.GitHubOps")
    @patch("cafe.ui.cli.prompt_confirm")
    @patch("cafe.ui.template_selector.prompt_list")
    @patch("cafe.ui.phase_prompts.prompt_list")
    @patch("cafe.ui.cli.prompt_list")
    @patch("cafe.ui.cli.prompt_text")
    def test_default_playbook_quick_setup_writes_expected_issue_yaml(
        self,
        mock_prompt_text_cli,
        mock_cli_list,
        mock_phase_list,
        mock_template_list,
        mock_prompt_confirm,
        MockGitHubOps_cli,
        MockGitHubOps_phase,
        mock_prompt_text_phase,
        temp_repo_dir,
        mock_git_ops,
    ):
        """Integration #1 — default playbook interactive quick setup parity."""
        mock_github_ops = MagicMock()
        MockGitHubOps_cli.return_value = mock_github_ops
        MockGitHubOps_phase.return_value = mock_github_ops
        mock_github_ops.extract_issue_number.return_value = "123"

        mock_prompt_text_cli.return_value = "parity-quick"
        mock_prompt_text_phase.return_value = "123"
        mock_prompt_confirm.return_value = False
        mock_phase_list.return_value = "2. Fetch from GitHub Issue"
        mock_cli_list.return_value = "Quick setup (use recommended defaults)"

        result = runner.invoke(app, ["prepare"])

        assert result.exit_code == 0
        config_file = temp_repo_dir / ".cafe" / "issues" / "parity-quick" / "issue.yaml"
        config_data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        assert config_data["spec"]["rigor"] == "medium"
        assert config_data["spec"]["template"] == "auto"
        assert config_data["plan"]["template"] == "auto"
        assert config_data["spec"]["sync_github"] is True
        assert config_data["pr"]["auto_create"] is True

    def test_default_playbook_non_interactive_manual_input(
        self, temp_repo_dir, mock_git_ops
    ):
        """Integration #2 — default playbook non-interactive parity."""
        result = runner.invoke(
            app,
            ["prepare", "parity-ni", "--no-interactive", "--input-method=manual"],
        )

        assert result.exit_code == 0
        config_file = temp_repo_dir / ".cafe" / "issues" / "parity-ni" / "issue.yaml"
        config_data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        assert config_data["spec"]["input_method"] == "manual"
        assert config_data["spec"]["rigor"] == "medium"
        assert config_data["spec"]["template"] == "auto"
        assert config_data["plan"]["template"] == "default"

    @patch("cafe.ui.cli.prompt_confirm")
    @patch("cafe.ui.cli.prompt_text")
    def test_hotfix_playbook_skips_spec_plan_config(
        self, mock_prompt_text, mock_prompt_confirm, temp_repo_dir, mock_git_ops
    ):
        """Integration #3 — hotfix playbook skips spec/plan pre-configuration."""
        _write_config_with_playbook(temp_repo_dir, "hotfix")
        mock_prompt_text.return_value = "hotfix-issue"
        mock_prompt_confirm.return_value = False

        result = runner.invoke(app, ["prepare"])

        assert result.exit_code == 0
        assert "Pre-configure spec and plan phases" not in result.stdout
        config_file = temp_repo_dir / ".cafe" / "issues" / "hotfix-issue" / "issue.yaml"
        config_data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        assert "spec" not in config_data
        assert "plan" not in config_data
        assert "pr" not in config_data

    @patch("cafe.ui.cli.prompt_confirm")
    @patch("cafe.ui.template_selector.prompt_list")
    @patch("cafe.ui.phase_prompts.prompt_list")
    @patch("cafe.ui.cli.prompt_list")
    @patch("cafe.ui.cli.prompt_text")
    def test_custom_playbook_quick_setup_and_rigor_constraint(
        self,
        mock_prompt_text,
        mock_cli_list,
        mock_phase_list,
        mock_template_list,
        mock_prompt_confirm,
        temp_repo_dir,
        mock_git_ops,
    ):
        """Integration #4 — custom playbook overrides quick-setup and enforces rigor."""
        prepare_block = """
commands:
  prepare:
    quick_setup:
      spec:
        rigor: high
    non_interactive_defaults:
      rigor: high
    constraints:
      rigor: [high]
"""
        _write_custom_playbook(temp_repo_dir, "strict", prepare_block)
        _write_config_with_playbook(temp_repo_dir, "strict")

        mock_prompt_text.return_value = "strict-quick"
        mock_prompt_confirm.return_value = False
        mock_phase_list.return_value = "1. Manual input"
        mock_cli_list.return_value = "Quick setup (use recommended defaults)"

        quick_result = runner.invoke(app, ["prepare"])
        assert quick_result.exit_code == 0
        config_file = temp_repo_dir / ".cafe" / "issues" / "strict-quick" / "issue.yaml"
        config_data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        assert config_data["spec"]["rigor"] == "high"

        bad_result = runner.invoke(
            app,
            [
                "prepare",
                "strict-bad",
                "--no-interactive",
                "--input-method=manual",
                "--rigor=low",
            ],
        )
        assert bad_result.exit_code == 1

    def test_invalid_playbook_exits_with_actionable_error(
        self, temp_repo_dir, mock_git_ops
    ):
        """DoD — unloadable playbook fails gracefully."""
        _write_config_with_playbook(temp_repo_dir, "does-not-exist")

        result = runner.invoke(app, ["prepare", "bad-playbook"])

        assert result.exit_code == 1
        assert "Failed to load playbook" in result.stdout
