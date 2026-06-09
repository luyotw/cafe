"""Tests for playbook commands.prepare metadata schema and validation."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from cafe.core.playbook import (
    PlaybookDefinition,
    default_prepare_config,
    load_playbook_file,
    resolve_prepare_config,
)
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader


STANDARD_PREPARE_YAML = """
commands:
  prepare:
    prompt_for_spec_plan_config: true
    setup_modes:
      quick:
        enabled: true
        label: "Quick setup (use recommended defaults)"
      custom:
        enabled: true
        label: "Custom configuration"
    quick_setup:
      spec:
        rigor: medium
        template: auto
      plan:
        template: auto
      sync_github:
        when_issue_id_present: true
        when_manual_input: false
      pr:
        auto_create_on_github_repo: true
        post_todo_list_when_auto_create: true
    non_interactive_defaults:
      rigor: medium
      spec_template: auto
      plan_template: default
    input_method:
      prompt_on_github_repo: true
      non_github_default: manual
    constraints:
      rigor: [low, medium, high]
"""


def _write_skill(root: Path, name: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: desc-{name}\n---\n\n# {name}\n",
        encoding="utf-8",
    )


def _write_playbook(root: Path, name: str, content: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.yaml").write_text(content, encoding="utf-8")


def _minimal_playbook_yaml(*, prepare_block: str = "") -> str:
    return f"""
playbook: {{id: test}}
steps:
  spec:
    role: pm
    skill: spec_first
    on:
      await_agent: _done
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


def test_prepare_schema_parses_valid_block(tmp_path: Path) -> None:
    loader = _loader(tmp_path)
    _write_playbook(
        loader._roots()[0],
        "test",
        _minimal_playbook_yaml(prepare_block=STANDARD_PREPARE_YAML),
    )

    result = loader.load_model("test")

    assert result.model.commands is not None
    assert result.model.commands.prepare is not None
    assert result.model.commands.prepare.prompt_for_spec_plan_config is True
    assert result.model.commands.prepare.quick_setup.spec.rigor == "medium"


def test_omitted_prepare_section_resolves_defaults(tmp_path: Path) -> None:
    loader = _loader(tmp_path)
    _write_playbook(loader._roots()[0], "test", _minimal_playbook_yaml())

    result = loader.load_model("test")
    resolved = resolve_prepare_config(result.model)
    expected = default_prepare_config()

    assert resolved.model_dump() == expected.model_dump()


def test_invalid_rigor_rejected_with_field_path() -> None:
    data = yaml.safe_load(
        _minimal_playbook_yaml(
            prepare_block="""
commands:
  prepare:
    quick_setup:
      spec:
        rigor: extreme
"""
        )
    )

    with pytest.raises(ValidationError) as exc_info:
        PlaybookDefinition.model_validate(data)

    assert "rigor" in str(exc_info.value)


def test_invalid_boolean_shape_rejected() -> None:
    data = yaml.safe_load(
        _minimal_playbook_yaml(
            prepare_block="""
commands:
  prepare:
    prompt_for_spec_plan_config: yes
"""
        )
    )

    with pytest.raises(ValidationError):
        PlaybookDefinition.model_validate(data)


def test_unknown_nested_prepare_key_forbidden() -> None:
    data = yaml.safe_load(
        _minimal_playbook_yaml(
            prepare_block="""
commands:
  prepare:
    typo_field: true
"""
        )
    )

    with pytest.raises(ValidationError) as exc_info:
        PlaybookDefinition.model_validate(data)

    assert "typo_field" in str(exc_info.value)


def test_unknown_template_rejected_at_load_time(tmp_path: Path) -> None:
    loader = _loader(tmp_path)
    _write_playbook(
        loader._roots()[0],
        "test",
        _minimal_playbook_yaml(
            prepare_block="""
commands:
  prepare:
    quick_setup:
      plan:
        template: not-a-real-template
"""
        ),
    )

    with pytest.raises(ValueError, match="commands.prepare.quick_setup.plan.template"):
        loader.load_model("test")


def test_invalid_prepare_fails_via_load_playbook_file_and_loader(tmp_path: Path) -> None:
    loader = _loader(tmp_path)
    content = """
playbook: {id: bad}
steps:
  spec:
    role: pm
    skill: spec_first
    on:
      await_agent: _done
commands:
  prepare:
    non_interactive_defaults:
      plan_template: missing-template-name
"""
    path = loader._roots()[0] / "bad.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

    skill_loader = SkillLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )

    with pytest.raises(ValueError, match="non_interactive_defaults.plan_template"):
        load_playbook_file(path, source="builtin", skill_loader=skill_loader)

    _write_playbook(loader._roots()[0], "bad", content)
    with pytest.raises(ValueError, match="non_interactive_defaults.plan_template"):
        loader.load_model("bad")


def test_legacy_playbook_without_prepare_section_loads(tmp_path: Path) -> None:
    loader = _loader(tmp_path)
    _write_playbook(
        loader._roots()[0],
        "legacy",
        """
playbook: {id: legacy}
steps:
  spec:
    role: pm
    skill: spec_first
    on:
      await_agent: _done
""",
    )

    result = loader.load_model("legacy")

    assert result.model.commands is None
    assert resolve_prepare_config(result.model) == default_prepare_config()


def _expected_standard_prepare() -> dict:
    return default_prepare_config().model_dump()


def test_builtin_default_playbook_prepare_parity() -> None:
    loader = PlaybookLoader()
    resolved = resolve_prepare_config(loader.load_model("default").model)

    assert resolved.model_dump() == _expected_standard_prepare()


def test_builtin_simple_and_tdd_match_default_prepare() -> None:
    loader = PlaybookLoader()
    default_prepare = resolve_prepare_config(loader.load_model("default").model).model_dump()

    for name in ("simple", "tdd"):
        resolved = resolve_prepare_config(loader.load_model(name).model)
        assert resolved.model_dump() == default_prepare


def test_builtin_hotfix_disables_spec_plan_prompts() -> None:
    loader = PlaybookLoader()
    resolved = resolve_prepare_config(loader.load_model("hotfix").model)

    assert resolved.prompt_for_spec_plan_config is False
    assert resolved.quick_setup.pr.auto_create_on_github_repo is True
    assert resolved.quick_setup.pr.post_todo_list_when_auto_create is True


def test_builtin_non_prepare_playbooks_still_load_without_prepare_section() -> None:
    loader = PlaybookLoader()

    for name in ("research", "editorial", "incident"):
        model = loader.load_model(name).model
        assert model.commands is None or model.commands.prepare is None
