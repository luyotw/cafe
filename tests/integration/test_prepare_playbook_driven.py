"""Integration tests for playbook-driven cafe prepare behavior."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from cafe.ui.cli import app

pytestmark = pytest.mark.usefixtures("cached_builtin_playbook_models")

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
    with (
        patch("cafe.ui.cli.GitOperations") as MockGitOperations,
        patch("cafe.utils.git_utils.is_github_repo") as mock_is_github_repo,
        patch("cafe.ui.phase_prompts.is_github_repo") as mock_is_github_repo_phase,
    ):
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


def _write_custom_playbook(
    base_dir: Path,
    name: str,
    prepare_block: str,
    *,
    publish_capability_step: str | None = None,
) -> None:
    playbooks_dir = base_dir / ".cafe" / "playbooks"
    playbooks_dir.mkdir(parents=True, exist_ok=True)
    content = f"""
playbook:
  id: {name}
steps:
  spec:
    type: skill
    skill: cafe-spec
    role: pm
    {"capability_requests: [cafe.pr.publish]" if publish_capability_step == "spec" else ""}
    "on":
      await_agent: plan
  plan:
    type: skill
    skill: cafe-plan
    role: developer
    {"capability_requests: [cafe.pr.publish]" if publish_capability_step == "plan" else ""}
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
        mock_phase_list.return_value = "2. GitHub issue"
        mock_cli_list.return_value = "Quick setup (use recommended defaults)"

        result = runner.invoke(app, ["prepare", "--auto-create-pr"])

        assert result.exit_code == 0
        config_file = temp_repo_dir / ".cafe" / "issues" / "parity-quick" / "issue.yaml"
        config_data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        assert config_data["spec"]["rigor"] == "medium"
        assert config_data["spec"]["template"] == "auto"
        assert config_data["plan"]["template"] == "auto"
        assert config_data["spec"]["sync_github"] is True
        assert config_data["pr"]["auto_create"] is True

    def test_default_playbook_non_interactive_manual_input(self, temp_repo_dir, mock_git_ops):
        """Integration #2 — default playbook non-interactive parity."""
        result = runner.invoke(
            app,
            [
                "prepare",
                "parity-ni",
                "--no-interactive",
                "--input-method=manual",
                "--no-auto-create-pr",
            ],
        )

        assert result.exit_code == 0
        config_file = temp_repo_dir / ".cafe" / "issues" / "parity-ni" / "issue.yaml"
        config_data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        assert config_data["spec"]["input_method"] == "manual"
        assert config_data["initial_input"] == {"provider": "manual_text"}
        assert config_data["spec"]["rigor"] == "medium"
        assert config_data["spec"]["template"] == "auto"
        assert config_data["plan"]["template"] == "default"
        assert not (temp_repo_dir / ".cafe" / "agents").exists()

    def test_pr_capable_positional_prepare_requires_explicit_publication_choice(
        self, temp_repo_dir, mock_git_ops
    ) -> None:
        """Integration 1: every public prepare shape requires the confirmed choice."""
        result = runner.invoke(
            app,
            ["prepare", "probe-missing-choice", "--playbook", "standard", "--no-check"],
        )

        assert result.exit_code == 1
        assert "--auto-create-pr or --no-auto-create-pr is required" in result.stdout
        assert not (
            temp_repo_dir / ".cafe" / "issues" / "probe-missing-choice" / "issue.yaml"
        ).exists()
        mock_git_ops.create_branch.assert_not_called()

    def test_cli_playbook_selection_is_persisted_without_repository_default(
        self, temp_repo_dir, mock_git_ops
    ):
        """A confirmed issue playbook drives prepare without repository config."""
        result = runner.invoke(
            app,
            [
                "prepare",
                "issue-owned-hotfix",
                "--playbook",
                "hotfix",
                "--no-interactive",
                "--input-method=manual",
                "--no-auto-create-pr",
            ],
        )

        assert result.exit_code == 0, result.stdout
        repository_config = yaml.safe_load(
            (temp_repo_dir / ".cafe" / "config.yaml").read_text(encoding="utf-8")
        )
        assert "playbook" not in repository_config

        issue_dir = temp_repo_dir / ".cafe" / "issues" / "issue-owned-hotfix"
        issue_config = yaml.safe_load(
            (issue_dir / "issue.yaml").read_text(encoding="utf-8")
        )
        assert issue_config["playbook_id"] == "hotfix"
        assert (issue_dir / "develop").is_dir()
        assert not (issue_dir / "spec").exists()

    def test_no_pr_playbook_rejects_non_interactive_pr_flags(self, temp_repo_dir, mock_git_ops):
        """A playbook without a pr step must reject legacy PR config flags."""
        prepare_block = """
commands:
  prepare:
    prompt_for_spec_plan_config: false
"""
        _write_custom_playbook(temp_repo_dir, "no-pr", prepare_block)
        _write_config_with_playbook(temp_repo_dir, "no-pr")

        result = runner.invoke(
            app,
            [
                "prepare",
                "no-pr-issue",
                "--no-interactive",
                "--input-method=github",
                "--issue-id=28",
                "--auto-create-pr",
                "--post-pr-todo-list",
            ],
        )

        assert result.exit_code == 1
        assert "not applicable" in result.stdout
        config_file = temp_repo_dir / ".cafe" / "issues" / "no-pr-issue" / "issue.yaml"
        assert not config_file.exists()

    @pytest.mark.parametrize(
        ("flag", "expected"),
        [("--auto-create-pr", True), ("--no-auto-create-pr", False)],
    )
    def test_custom_named_capability_persists_explicit_publication_choice(
        self,
        temp_repo_dir,
        mock_git_ops,
        flag: str,
        expected: bool,
    ) -> None:
        """Integration 1: supported prepare persists the confirmed Boolean unchanged."""
        prepare_block = """
commands:
  prepare:
    prompt_for_spec_plan_config: false
"""
        _write_custom_playbook(
            temp_repo_dir,
            "capable-custom",
            prepare_block,
            publish_capability_step="plan",
        )
        _write_config_with_playbook(temp_repo_dir, "capable-custom")

        result = runner.invoke(
            app,
            [
                "prepare",
                f"capable-{str(expected).lower()}",
                "--no-interactive",
                "--input-method=manual",
                flag,
            ],
        )

        assert result.exit_code == 0, result.stdout
        config_file = (
            temp_repo_dir
            / ".cafe"
            / "issues"
            / f"capable-{str(expected).lower()}"
            / "issue.yaml"
        )
        config = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        assert config["pr"]["auto_create"] is expected

    def test_promptless_playbook_ignores_legacy_development_flags(
        self, temp_repo_dir, mock_git_ops
    ):
        """A promptless workflow does not persist undeclared development config."""
        prepare_block = """
commands:
  prepare:
    prompt_for_spec_plan_config: false
"""
        _write_custom_playbook(temp_repo_dir, "no-pr", prepare_block)
        _write_config_with_playbook(temp_repo_dir, "no-pr")

        result = runner.invoke(
            app,
            [
                "prepare",
                "no-pr-issue",
                "--no-interactive",
                "--input-method=github",
                "--issue-id=28",
            ],
        )

        assert result.exit_code == 0
        config_file = temp_repo_dir / ".cafe" / "issues" / "no-pr-issue" / "issue.yaml"
        config_data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        assert not {"spec", "plan", "pr"}.intersection(config_data)

    @patch("cafe.ui.cli.prompt_confirm")
    @patch("cafe.ui.cli.prompt_text")
    def test_hotfix_playbook_skips_spec_plan_config(
        self, mock_prompt_text, mock_prompt_confirm, temp_repo_dir, mock_git_ops
    ):
        """Integration #3 — hotfix playbook skips spec/plan pre-configuration."""
        _write_config_with_playbook(temp_repo_dir, "hotfix")
        mock_prompt_text.return_value = "hotfix-issue"
        mock_prompt_confirm.return_value = False

        result = runner.invoke(app, ["prepare", "--no-auto-create-pr"])

        assert result.exit_code == 0
        assert "Pre-configure spec and plan phases" not in result.stdout
        config_file = temp_repo_dir / ".cafe" / "issues" / "hotfix-issue" / "issue.yaml"
        config_data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        assert "spec" not in config_data
        assert "plan" not in config_data
        assert config_data["pr"] == {"auto_create": False}

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

    @patch("cafe.ui.phase_prompts.prompt_confirm")
    @patch("cafe.ui.cli.prompt_confirm")
    @patch("cafe.ui.template_selector.prompt_list")
    @patch("cafe.ui.phase_prompts.prompt_list")
    @patch("cafe.ui.cli.prompt_list")
    @patch("cafe.ui.cli.prompt_text")
    def test_custom_configuration_rigor_choices_follow_playbook_constraints(
        self,
        mock_prompt_text,
        mock_cli_list,
        mock_phase_list,
        mock_template_list,
        mock_cli_confirm,
        mock_phase_confirm,
        temp_repo_dir,
        mock_git_ops,
    ):
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
        _write_custom_playbook(temp_repo_dir, "strict-custom", prepare_block)
        _write_config_with_playbook(temp_repo_dir, "strict-custom")

        mock_prompt_text.return_value = "strict-custom"
        mock_cli_confirm.return_value = False
        mock_phase_confirm.return_value = False
        mock_cli_list.return_value = "Custom configuration"
        mock_phase_list.side_effect = [
            "1. Manual input",
            "High - Precise specification mode\n   • Ask all details and edge cases\n   • Ensure requirements are testable, no ambiguity\n   • Suitable for: core features, API design, external products",
        ]
        mock_template_list.return_value = "auto"

        result = runner.invoke(app, ["prepare"])

        assert result.exit_code == 0
        rigor_prompt = mock_phase_list.call_args_list[1]
        assert rigor_prompt.kwargs["choices"] == [
            "High - Precise specification mode\n   • Ask all details and edge cases\n   • Ensure requirements are testable, no ambiguity\n   • Suitable for: core features, API design, external products"
        ]
        config_file = temp_repo_dir / ".cafe" / "issues" / "strict-custom" / "issue.yaml"
        config_data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        assert config_data["spec"]["rigor"] == "high"

    def test_invalid_playbook_exits_with_actionable_error(self, temp_repo_dir, mock_git_ops):
        """DoD — unloadable playbook fails gracefully."""
        _write_config_with_playbook(temp_repo_dir, "does-not-exist")

        result = runner.invoke(app, ["prepare", "bad-playbook"])

        assert result.exit_code == 1
        assert "Failed to load playbook" in result.stdout

    @pytest.mark.parametrize("playbook_id", ["standard", "hotfix"])
    def test_builtin_playbooks_non_interactive_defaults(
        self, playbook_id, temp_repo_dir, mock_git_ops
    ):
        """Integration — built-in playbooks keep non-interactive default parity."""
        if playbook_id != "standard":
            _write_config_with_playbook(temp_repo_dir, playbook_id)

        result = runner.invoke(
            app,
            [
                "prepare",
                f"ni-{playbook_id}",
                "--no-interactive",
                "--input-method=manual",
                "--no-auto-create-pr",
            ],
        )

        assert result.exit_code == 0
        config_file = temp_repo_dir / ".cafe" / "issues" / f"ni-{playbook_id}" / "issue.yaml"
        config_data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        assert config_data["spec"]["input_method"] == "manual"
        assert config_data["spec"]["rigor"] == "medium"
        assert config_data["spec"]["template"] == "auto"
        assert config_data["plan"]["template"] == "default"

    def test_direct_non_interactive_prepare_records_only_entry_input(
        self, temp_repo_dir, mock_git_ops
    ):
        """Direct prepare delivers GitHub input without phantom spec/plan config."""
        _write_config_with_playbook(temp_repo_dir, "direct")

        result = runner.invoke(
            app,
            [
                "prepare",
                "direct-issue",
                "--no-interactive",
                "--input-method=github",
                "--issue-id=420",
                "--no-auto-create-pr",
            ],
        )

        assert result.exit_code == 0
        config_file = temp_repo_dir / ".cafe" / "issues" / "direct-issue" / "issue.yaml"
        config_data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        assert config_data["initial_input"] == {
            "provider": "github_issue",
            "issue_id": 420,
        }
        assert config_data["develop"] == {
            "input_method": "github",
            "issue_id": "420",
        }
        assert "spec" not in config_data
        assert "plan" not in config_data

    def test_invalid_rigor_exits_before_polluted_issue_yaml(self, temp_repo_dir, mock_git_ops):
        """Integration — invalid rigor fails before writing polluted issue.yaml."""
        prepare_block = """
commands:
  prepare:
    non_interactive_defaults:
      rigor: high
    constraints:
      rigor: [high]
"""
        _write_custom_playbook(temp_repo_dir, "strict", prepare_block)
        _write_config_with_playbook(temp_repo_dir, "strict")

        result = runner.invoke(
            app,
            [
                "prepare",
                "strict-bad",
                "--no-interactive",
                "--input-method=manual",
                "--rigor=low",
            ],
        )

        assert result.exit_code == 1
        config_file = temp_repo_dir / ".cafe" / "issues" / "strict-bad" / "issue.yaml"
        if config_file.exists():
            config_data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
            assert "spec" not in config_data or config_data.get("spec", {}).get("rigor") != "low"

    def test_missing_plan_template_exits_before_polluted_issue_yaml(
        self, temp_repo_dir, mock_git_ops
    ):
        """Integration — missing plan template fails before polluted issue.yaml write."""
        result = runner.invoke(
            app,
            [
                "prepare",
                "bad-template",
                "--no-interactive",
                "--input-method=manual",
                "--plan-template=missing-template-name",
            ],
        )

        assert result.exit_code == 1
        config_file = temp_repo_dir / ".cafe" / "issues" / "bad-template" / "issue.yaml"
        if config_file.exists():
            config_data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
            assert config_data.get("plan", {}).get("template") != "missing-template-name"
