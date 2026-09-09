"""Integration tests for field-driven cafe prepare interactive prompts."""

from pathlib import Path
from types import SimpleNamespace
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


def _write_custom_playbook(base_dir: Path, name: str, prepare_block: str) -> None:
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
    "on":
      await_agent: _done
{prepare_block}
"""
    (playbooks_dir / f"{name}.yaml").write_text(content, encoding="utf-8")


def _write_workflow_owned_fields_playbook(base_dir: Path, name: str) -> None:
    """Create a non-development workflow with only workflow-owned setup."""
    playbooks_dir = base_dir / ".cafe" / "playbooks"
    playbooks_dir.mkdir(parents=True, exist_ok=True)
    (playbooks_dir / f"{name}.yaml").write_text(
        f"""
playbook:
  id: {name}
steps:
  synthesize:
    type: skill
    skill: cafe-spec
    role: pm
    "on":
      await_agent: _done
commands:
  prepare:
    fields:
      - id: audience
        type: enum
        label: Audience
        write: synthesize.audience
        default: internal
        choices:
          - value: internal
            label: Internal
          - value: public
            label: Public
""",
        encoding="utf-8",
    )


class TestPrepareFieldDriven:
    @patch("cafe.ui.phase_prompts.prompt_text")
    @patch("cafe.ui.phase_prompts.GitHubOps")
    @patch("cafe.ui.cli.GitHubOps")
    @patch("cafe.ui.cli.prompt_confirm")
    @patch("cafe.ui.template_selector.prompt_list")
    @patch("cafe.ui.phase_prompts.prompt_list")
    @patch("cafe.ui.cli.prompt_list")
    @patch("cafe.ui.cli.prompt_text")
    def test_custom_fields_playbook_overrides_rigor_default(
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
        prepare_block = """
commands:
  prepare:
    fields:
      - id: setup_mode
        type: setup_mode
        label: "Choose setup mode"
        choices:
          - value: quick
            label: "Quick setup (use recommended defaults)"
          - value: custom
            label: "Custom configuration"
      - id: custom_rigor
        type: enum
        label: "Custom rigor label"
        write: spec.rigor
        default: low
        show_when:
          setup_mode: custom
        choices:
          - value: low
            label: "Low"
          - value: medium
            label: "Medium"
          - value: high
            label: "High"
"""
        _write_custom_playbook(temp_repo_dir, "fields-custom", prepare_block)
        _write_config_with_playbook(temp_repo_dir, "fields-custom")

        mock_prompt_text_cli.return_value = "fields-custom-issue"
        mock_prompt_confirm.return_value = False
        mock_phase_list.return_value = "1. Manual input"
        mock_cli_list.side_effect = [
            "Custom configuration",
            "Low",
        ]
        mock_template_list.return_value = "auto (agent decides)"

        result = runner.invoke(app, ["prepare"])

        assert result.exit_code == 0
        rigor_call = [
            call
            for call in mock_cli_list.call_args_list
            if call.kwargs.get("message") == "Custom rigor label"
        ]
        assert rigor_call
        config_file = temp_repo_dir / ".cafe" / "issues" / "fields-custom-issue" / "issue.yaml"
        config_data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        assert config_data["spec"]["rigor"] == "low"

    @patch("cafe.ui.cli.prompt_confirm")
    @patch("cafe.ui.cli.prompt_text")
    def test_legacy_playbook_without_fields_keeps_legacy_path(
        self,
        mock_prompt_text,
        mock_prompt_confirm,
        temp_repo_dir,
        mock_git_ops,
    ):
        prepare_block = """
commands:
  prepare:
    quick_setup:
      spec:
        rigor: medium
    constraints:
      rigor: [medium]
"""
        _write_custom_playbook(temp_repo_dir, "legacy-only", prepare_block)
        _write_config_with_playbook(temp_repo_dir, "legacy-only")

        mock_prompt_text.return_value = "legacy-only-issue"
        mock_prompt_confirm.return_value = False

        with patch("cafe.ui.commands.lifecycle._run_field_driven_prepare_prompts") as mock_field:
            with patch("cafe.ui.commands.lifecycle._run_legacy_prepare_prompts") as mock_legacy:
                mock_legacy.return_value = (
                    {"input_method": "manual", "rigor": "medium", "template": "auto"},
                    {"template": "auto"},
                    {"auto_create": False},
                    None,
                )
                result = runner.invoke(app, ["prepare"])

        assert result.exit_code == 0
        mock_legacy.assert_called_once()
        mock_field.assert_not_called()
        assert "Deprecated" in result.stdout

    def test_non_development_fields_supply_non_interactive_defaults(
        self, temp_repo_dir, mock_git_ops
    ):
        """I4 — workflow-owned defaults need no development CLI flags."""
        _write_workflow_owned_fields_playbook(temp_repo_dir, "audience-workflow")
        _write_config_with_playbook(temp_repo_dir, "audience-workflow")

        result = runner.invoke(
            app,
            ["prepare", "audience-default", "--no-interactive"],
        )

        assert result.exit_code == 0
        config = yaml.safe_load(
            (temp_repo_dir / ".cafe" / "issues" / "audience-default" / "issue.yaml").read_text(
                encoding="utf-8"
            )
        )
        assert config["synthesize"] == {"audience": "internal"}
        assert not {"spec", "plan", "pr"}.intersection(config)

    @pytest.mark.parametrize("playbook_id", ["research", "editorial", "incident"])
    def test_promptless_builtin_prepare_creates_no_development_config(
        self, playbook_id, temp_repo_dir, mock_git_ops
    ):
        """I4 — promptless bundled workflows need no development CLI flags."""
        _write_config_with_playbook(temp_repo_dir, playbook_id)

        result = runner.invoke(
            app,
            ["prepare", f"{playbook_id}-default", "--no-interactive"],
        )

        assert result.exit_code == 0
        config = yaml.safe_load(
            (
                temp_repo_dir / ".cafe" / "issues" / f"{playbook_id}-default" / "issue.yaml"
            ).read_text(encoding="utf-8")
        )
        assert not {"spec", "plan", "pr"}.intersection(config)

    @patch("cafe.ui.cli.prompt_confirm")
    @patch("cafe.ui.cli.prompt_list")
    @patch("cafe.ui.cli.prompt_text")
    def test_non_development_fields_own_interactive_prompts(
        self,
        mock_prompt_text,
        mock_prompt_list,
        mock_prompt_confirm,
        temp_repo_dir,
        mock_git_ops,
    ):
        """I3 — interactive setup renders only the workflow's declared prompt."""
        _write_workflow_owned_fields_playbook(temp_repo_dir, "audience-workflow")
        _write_config_with_playbook(temp_repo_dir, "audience-workflow")
        mock_prompt_text.return_value = "audience-interactive"
        mock_prompt_confirm.return_value = False
        mock_prompt_list.return_value = "Public"

        result = runner.invoke(app, ["prepare"])

        assert result.exit_code == 0
        config = yaml.safe_load(
            (temp_repo_dir / ".cafe" / "issues" / "audience-interactive" / "issue.yaml").read_text(
                encoding="utf-8"
            )
        )
        assert config["synthesize"] == {"audience": "public"}
        assert [call.kwargs["message"] for call in mock_prompt_list.call_args_list] == ["Audience"]


def test_field_driven_prepare_passes_declared_custom_template_managers() -> None:
    """Lifecycle wiring keeps custom step catalogs available to the renderer."""
    from cafe.ui.commands.lifecycle import _run_field_driven_prepare_prompts

    custom_manager = MagicMock()
    rendered_config = SimpleNamespace(spec={}, plan={}, pr={}, steps={})
    with (
        patch("cafe.ui.commands.lifecycle.TemplateManager") as manager_cls,
        patch("cafe.ui.prepare_field_renderer.run_field_driven_prepare_flow") as run_flow,
    ):
        manager_cls.side_effect = [MagicMock(), MagicMock()]
        run_flow.return_value = (rendered_config, None)

        _run_field_driven_prepare_prompts(
            MagicMock(),
            MagicMock(),
            display=MagicMock(),
            github_ops=MagicMock(),
            template_managers={"synthesis": custom_manager},
        )

    assert run_flow.call_args.kwargs["deps"].template_managers == {"synthesis": custom_manager}
