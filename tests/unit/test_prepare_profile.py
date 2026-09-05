"""Unit tests for playbook-driven prepare profile."""

from pathlib import Path

import pytest
import yaml

from cafe.core.playbook import PlaybookDefinition, default_prepare_config, resolve_prepare_config
from cafe.core.prepare_profile import PrepareProfile, PrepareRigorError
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader

pytestmark = pytest.mark.usefixtures("cached_builtin_playbook_models")


def _write_skill(root: Path, name: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: desc-{name}\n---\n\n# {name}\n",
        encoding="utf-8",
    )


def _minimal_playbook_yaml(*, prepare_block: str = "", include_pr_step: bool = False) -> str:
    pr_step = (
        """
  pr:
    role: developer
    skill: spec_first
    capability_requests: [cafe.pr.publish]
    "on":
      await_agent: _done
"""
        if include_pr_step
        else ""
    )
    return f"""
playbook: {{id: test}}
steps:
  spec:
    role: pm
    skill: spec_first
    "on":
      await_agent: _done
{pr_step}
{prepare_block}
"""


def _loader(tmp_path: Path) -> PlaybookLoader:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "spec_first")
    return PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )


def _profile_from_yaml(
    tmp_path: Path, prepare_yaml: str, *, is_github_repo: bool
) -> PrepareProfile:
    loader = _loader(tmp_path)
    playbook_dir = loader._roots()[-1]
    playbook_dir.mkdir(parents=True, exist_ok=True)
    (playbook_dir / "test.yaml").write_text(
        _minimal_playbook_yaml(prepare_block=prepare_yaml),
        encoding="utf-8",
    )
    model = loader.load_model("test").model
    return PrepareProfile.from_playbook(model, is_github_repo)


class TestPrepareProfilePromptGating:
    """Test List unit #1 — profile gates spec/plan prompts."""

    def test_disables_spec_plan_prompts_when_playbook_metadata_false(self, tmp_path: Path) -> None:
        profile = _profile_from_yaml(
            tmp_path,
            "commands:\n  prepare:\n    prompt_for_spec_plan_config: false\n",
            is_github_repo=True,
        )
        assert profile.should_prompt_spec_plan_config(True) is False

    def test_respects_base_flag_when_metadata_allows(self) -> None:
        profile = PrepareProfile.from_playbook(
            PlaybookDefinition.model_validate(yaml.safe_load(_minimal_playbook_yaml())),
            is_github_repo=True,
        )
        assert profile.should_prompt_spec_plan_config(True) is True
        assert profile.should_prompt_spec_plan_config(False) is False


class TestPrepareProfileQuickSetup:
    """Test List unit #2 and #3 — quick setup defaults."""

    def test_quick_setup_with_issue_id_enables_sync(self) -> None:
        profile = PrepareProfile.from_playbook(
            PlaybookDefinition.model_validate(yaml.safe_load(_minimal_playbook_yaml())),
            is_github_repo=True,
        )
        result = profile.quick_setup_issue_config(issue_id=42)
        assert result.spec["rigor"] == "medium"
        assert result.spec["template"] == "auto"
        assert result.plan["template"] == "auto"
        assert result.spec["sync_github"] is True
        assert result.plan["sync_github"] is True

    def test_quick_setup_without_issue_id_disables_sync(self) -> None:
        profile = PrepareProfile.from_playbook(
            PlaybookDefinition.model_validate(yaml.safe_load(_minimal_playbook_yaml())),
            is_github_repo=True,
        )
        result = profile.quick_setup_issue_config(issue_id=None)
        assert result.spec["sync_github"] is False
        assert result.plan["sync_github"] is False

    def test_github_repo_sets_pr_defaults_from_metadata(self) -> None:
        profile = PrepareProfile.from_playbook(
            PlaybookDefinition.model_validate(
                yaml.safe_load(_minimal_playbook_yaml(include_pr_step=True))
            ),
            is_github_repo=True,
        )
        result = profile.quick_setup_issue_config(issue_id=None)
        assert result.pr["auto_create"] is True
        assert result.pr["post_todo_list"] is True

    def test_non_github_repo_skips_pr_auto_create(self) -> None:
        profile = PrepareProfile.from_playbook(
            PlaybookDefinition.model_validate(
                yaml.safe_load(_minimal_playbook_yaml(include_pr_step=True))
            ),
            is_github_repo=False,
        )
        result = profile.quick_setup_issue_config(issue_id=None)
        assert result.pr["auto_create"] is False
        assert "post_todo_list" not in result.pr

    def test_playbook_without_pr_step_omits_pr_config(self) -> None:
        profile = PrepareProfile.from_playbook(
            PlaybookDefinition.model_validate(yaml.safe_load(_minimal_playbook_yaml())),
            is_github_repo=True,
        )
        result = profile.quick_setup_issue_config(issue_id=None)
        assert result.pr == {}

    def test_pr_config_support_follows_capability_instead_of_step_name(self) -> None:
        capable = yaml.safe_load(_minimal_playbook_yaml())
        capable["steps"]["spec"]["capability_requests"] = ["cafe.pr.publish"]
        capable_profile = PrepareProfile.from_playbook(
            PlaybookDefinition.model_validate(capable),
            is_github_repo=True,
        )

        named_pr = yaml.safe_load(_minimal_playbook_yaml(include_pr_step=True))
        named_pr["steps"]["pr"]["capability_requests"] = []
        named_pr_profile = PrepareProfile.from_playbook(
            PlaybookDefinition.model_validate(named_pr),
            is_github_repo=True,
        )

        assert capable_profile.supports_pr_config() is True
        assert named_pr_profile.supports_pr_config() is False


class TestPrepareProfileNonInteractive:
    """Test List unit #4 and #5 — non-interactive defaults and rigor validation."""

    def test_non_interactive_defaults_from_metadata(self, tmp_path: Path) -> None:
        prepare_yaml = """
commands:
  prepare:
    non_interactive_defaults:
      rigor: high
      spec_template: detailed
      plan_template: bug
"""
        profile = _profile_from_yaml(tmp_path, prepare_yaml, is_github_repo=False)
        defaults = profile.non_interactive_defaults()
        assert defaults.rigor == "high"
        assert defaults.spec_template == "detailed"
        assert defaults.plan_template == "bug"

    def test_validate_rigor_rejects_disallowed_value(self, tmp_path: Path) -> None:
        prepare_yaml = """
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
        profile = _profile_from_yaml(tmp_path, prepare_yaml, is_github_repo=False)
        with pytest.raises(PrepareRigorError):
            profile.validate_rigor("low")

    def test_validate_rigor_accepts_allowed_value(self, tmp_path: Path) -> None:
        prepare_yaml = """
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
        profile = _profile_from_yaml(tmp_path, prepare_yaml, is_github_repo=False)
        profile.validate_rigor("high")
        assert profile.allowed_rigor_values() == ["high"]


class TestPrepareProfileSetupModes:
    """Test List unit #6 — setup mode labels."""

    def test_only_enabled_modes_listed(self, tmp_path: Path) -> None:
        prepare_yaml = """
commands:
  prepare:
    setup_modes:
      quick:
        enabled: false
        label: "Quick"
      custom:
        enabled: true
        label: "Custom only"
"""
        profile = _profile_from_yaml(tmp_path, prepare_yaml, is_github_repo=False)
        assert profile.enabled_setup_mode_labels() == ["Custom only"]


class TestPrepareProfileFallback:
    """Test List unit #7 — missing prepare metadata fallback."""

    def test_playbook_without_prepare_matches_default_config(self) -> None:
        model = PlaybookDefinition.model_validate(yaml.safe_load(_minimal_playbook_yaml()))
        profile = PrepareProfile.from_playbook(model, is_github_repo=True)
        assert profile.prepare.model_dump() == default_prepare_config().model_dump()


class TestPrepareProfileInputMethod:
    """Test List unit #8 — input method prompt gating."""

    def test_non_github_uses_default_without_prompt(self) -> None:
        profile = PrepareProfile.from_playbook(
            PlaybookDefinition.model_validate(yaml.safe_load(_minimal_playbook_yaml())),
            is_github_repo=False,
        )
        assert profile.should_prompt_input_method() is False
        assert profile.default_input_method() == "manual"

    def test_github_skips_prompt_when_metadata_disables(self, tmp_path: Path) -> None:
        prepare_yaml = """
commands:
  prepare:
    input_method:
      prompt_on_github_repo: false
      non_github_default: manual
"""
        profile = _profile_from_yaml(tmp_path, prepare_yaml, is_github_repo=True)
        assert profile.should_prompt_input_method() is False


class TestPrepareProfileResolvedFields:
    """Test List unit #14 — resolved prepare field contract."""

    def test_simple_playbook_resolves_declared_fields(self) -> None:
        loader = PlaybookLoader()
        loaded = loader.load_model("simple")
        profile = PrepareProfile.from_playbook(loaded.model, is_github_repo=True)
        assert (
            profile.resolved_prepare_fields(
                playbook_path=loaded.path,
                skill_loader=SkillLoader(),
            )
            is not None
        )

    def test_returns_fields_for_standard_playbook(self) -> None:
        loader = PlaybookLoader()
        loaded = loader.load_model("standard")
        profile = PrepareProfile.from_playbook(loaded.model, is_github_repo=True)
        parsed = profile.resolved_prepare_fields(
            playbook_path=loaded.path,
            skill_loader=SkillLoader(),
        )
        assert parsed is not None
        assert any(field.id == "setup_mode" for field in parsed.fields)
