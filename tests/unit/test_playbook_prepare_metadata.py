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

pytestmark = pytest.mark.usefixtures("cached_builtin_playbook_models")


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


def _minimal_playbook_yaml(*, prepare_block: str = "", playbook_id: str = "test") -> str:
    return f"""
playbook: {{id: {playbook_id}}}
steps:
  spec:
    role: pm
    skill: spec_first
    "on":
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
        loader._roots()[-1],
        "test",
        _minimal_playbook_yaml(prepare_block=STANDARD_PREPARE_YAML),
    )

    result = loader.load_model("test")

    assert result.model.commands is not None
    assert result.model.commands.prepare is not None
    assert result.model.commands.prepare.prompt_for_spec_plan_config is True
    assert result.model.commands.prepare.quick_setup.spec.rigor == "medium"


def test_builtin_missing_prepare_section_is_rejected(tmp_path: Path) -> None:
    """U1 — built-ins cannot inherit implicit interactive legacy prompts."""
    loader = _loader(tmp_path)
    _write_playbook(loader._roots()[0], "test", _minimal_playbook_yaml())

    with pytest.raises(ValueError, match="fields.*fields_ref"):
        loader.load_model("test")


def test_required_skill_inputs_must_be_declared_by_the_playbook_step(tmp_path: Path) -> None:
    """A strict load rejects a step that cannot supply its skill's required artifact."""
    loader = _loader(tmp_path)
    skill_dir = tmp_path / "builtin" / "skills" / "requires-brief"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: requires-brief\n"
        "description: requires a brief\n"
        "workflow:\n"
        "  prompt_inputs:\n"
        "    - artifacts: [brief]\n"
        "      placeholder: brief_file\n"
        "      required: true\n"
        "---\n",
        encoding="utf-8",
    )
    _write_playbook(
        loader._roots()[-1],
        "missing-input",
        """
playbook: {id: missing-input}
steps:
  synthesize:
    role: pm
    skill: requires-brief
    input_artifacts: []
    "on": {await_agent: _done}
""",
    )

    with pytest.raises(ValueError, match="synthesize.*brief"):
        loader.load_model("missing-input", strict=True)


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
    prompt_for_spec_plan_config:
      enabled: true
"""
        )
    )

    with pytest.raises(ValidationError) as exc_info:
        PlaybookDefinition.model_validate(data)

    assert "prompt_for_spec_plan_config" in str(exc_info.value)


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
        loader._roots()[-1],
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
    "on":
      await_agent: _done
commands:
  prepare:
    non_interactive_defaults:
      plan_template: missing-template-name
"""
    path = loader._roots()[-1] / "bad.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

    skill_loader = SkillLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )

    with pytest.raises(ValueError, match="non_interactive_defaults.plan_template"):
        load_playbook_file(path, source="project", skill_loader=skill_loader)

    _write_playbook(loader._roots()[-1], "bad", content)
    with pytest.raises(ValueError, match="non_interactive_defaults.plan_template"):
        loader.load_model("bad")


def test_legacy_playbook_without_prepare_section_loads(tmp_path: Path) -> None:
    loader = _loader(tmp_path)
    _write_playbook(
        loader._roots()[-1],
        "legacy",
        """
playbook: {id: legacy}
steps:
  spec:
    role: pm
    skill: spec_first
    "on":
      await_agent: _done
""",
    )

    result = loader.load_model("legacy")

    assert result.model.commands is None
    assert resolve_prepare_config(result.model) == default_prepare_config()
    assert any("Legacy interactive prepare is deprecated" in warning for warning in result.warnings)


def test_builtin_interactive_prepare_requires_declared_fields(tmp_path: Path) -> None:
    """Bundled interactive setup must not fall back to hidden legacy prompts."""
    loader = _loader(tmp_path)
    _write_playbook(
        loader._roots()[0],
        "missing-fields",
        _minimal_playbook_yaml(
            playbook_id="missing-fields",
            prepare_block=STANDARD_PREPARE_YAML,
        ),
    )

    with pytest.raises(ValueError, match="fields.*fields_ref"):
        loader.load_model("missing-fields")


def _expected_standard_prepare() -> dict:
    return _legacy_prepare_dump(default_prepare_config())


def _legacy_prepare_dump(prepare) -> dict:
    data = prepare.model_dump()
    data.pop("fields", None)
    data.pop("fields_ref", None)
    return data


def test_builtin_standard_playbook_prepare_parity() -> None:
    loader = PlaybookLoader()
    resolved = resolve_prepare_config(loader.load_model("standard").model)

    assert _legacy_prepare_dump(resolved) == _expected_standard_prepare()
    assert resolved.fields_ref == "skill://cafe-spec/assets/prepare/default_prepare_fields.yaml"


def test_builtin_spec_plan_playbooks_match_standard_prepare() -> None:
    loader = PlaybookLoader()
    standard_prepare = _legacy_prepare_dump(
        resolve_prepare_config(loader.load_model("standard").model)
    )

    for name in ("simple", "standard-qa", "tdd", "tdd-qa"):
        resolved = resolve_prepare_config(loader.load_model(name).model)
        assert _legacy_prepare_dump(resolved) == standard_prepare


def test_builtin_hotfix_disables_spec_plan_prompts() -> None:
    loader = PlaybookLoader()
    resolved = resolve_prepare_config(loader.load_model("hotfix").model)

    assert resolved.prompt_for_spec_plan_config is False
    assert resolved.quick_setup.pr.auto_create_on_github_repo is True
    assert resolved.quick_setup.pr.post_todo_list_when_auto_create is True


def test_builtin_direct_uses_its_declarative_input_fields() -> None:
    loaded = PlaybookLoader().load_model("direct")
    resolved = resolve_prepare_config(loaded.model)

    assert resolved.fields is not None
    assert [field.id for field in resolved.fields] == ["input_method", "github_issue_id"]


def test_builtin_non_prepare_playbooks_still_load_without_prepare_section() -> None:
    loader = PlaybookLoader()

    for name in ("research", "editorial", "incident"):
        model = loader.load_model(name).model
        assert model.commands is not None
        assert model.commands.prepare is not None
        assert model.commands.prepare.prompt_for_spec_plan_config is False


def test_every_builtin_prepare_is_declarative_or_explicitly_promptless() -> None:
    """U9 — bundled playbooks cannot reach the legacy interactive adapter."""
    loader = PlaybookLoader()

    for name in (
        "direct",
        "simple",
        "standard",
        "standard-qa",
        "tdd",
        "tdd-qa",
        "hotfix",
        "research",
        "editorial",
        "incident",
    ):
        model = loader.load_model(name).model
        prepare = model.commands.prepare if model.commands else None
        assert (
            prepare is None
            or not prepare.prompt_for_spec_plan_config
            or (prepare.fields is not None or prepare.fields_ref is not None)
        )


def test_prepare_fields_and_fields_ref_are_mutually_exclusive() -> None:
    data = yaml.safe_load(
        _minimal_playbook_yaml(
            prepare_block="""
commands:
  prepare:
    fields_ref: assets/fields.yaml
    fields:
      - id: rigor
        type: enum
        label: Rigor
        write: spec.rigor
        choices:
          - value: low
            label: Low
"""
        )
    )
    with pytest.raises(ValidationError):
        PlaybookDefinition.model_validate(data)


def test_invalid_prepare_field_write_target_fails_validate(tmp_path: Path) -> None:
    loader = _loader(tmp_path)
    playbook_dir = loader._roots()[-1]
    playbook_dir.mkdir(parents=True, exist_ok=True)
    asset = playbook_dir / "bad_fields.yaml"
    asset.write_text(
        yaml.safe_dump(
            {
                "fields": [
                    {
                        "id": "bad",
                        "type": "boolean",
                        "label": "Bad",
                        "write": "undeclared.unknown",
                        "default": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    _write_playbook(
        playbook_dir,
        "bad",
        _minimal_playbook_yaml(
            playbook_id="bad",
            prepare_block=f"""
commands:
  prepare:
    fields_ref: {asset.name}
""",
        ),
    )

    with pytest.raises((ValueError, ValidationError), match="undeclared workflow step"):
        loader.load_model("bad")


def test_prepare_fields_semantic_mismatch_fails_validate(tmp_path: Path) -> None:
    loader = _loader(tmp_path)
    playbook_dir = loader._roots()[-1]
    playbook_dir.mkdir(parents=True, exist_ok=True)
    asset = playbook_dir / "mismatch_fields.yaml"
    asset.write_text(
        yaml.safe_dump(
            {
                "fields": [
                    {
                        "id": "setup_mode",
                        "type": "setup_mode",
                        "label": "Setup",
                        "choices": [
                            {"value": "quick", "label": "Quick setup (use recommended defaults)"},
                            {"value": "custom", "label": "Custom configuration"},
                        ],
                    },
                    {
                        "id": "quick_rigor",
                        "type": "enum",
                        "label": "Rigor",
                        "write": "spec.rigor",
                        "default": "high",
                        "show_when": {"setup_mode": "quick"},
                        "choices": [
                            {"value": "low", "label": "Low"},
                            {"value": "medium", "label": "Medium"},
                            {"value": "high", "label": "High"},
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    _write_playbook(
        playbook_dir,
        "mismatch",
        _minimal_playbook_yaml(
            playbook_id="mismatch",
            prepare_block=f"""
commands:
  prepare:
    quick_setup:
      spec:
        rigor: medium
    fields_ref: {asset.name}
""",
        ),
    )

    with pytest.raises(ValueError, match="disagrees with legacy"):
        loader.load_model("mismatch")


def test_prepare_fields_without_legacy_metadata_skips_parity_validate(tmp_path: Path) -> None:
    loader = _loader(tmp_path)
    playbook_dir = loader._roots()[-1]
    playbook_dir.mkdir(parents=True, exist_ok=True)
    asset = playbook_dir / "declarative_only_fields.yaml"
    asset.write_text(
        yaml.safe_dump(
            {
                "fields": [
                    {
                        "id": "setup_mode",
                        "type": "setup_mode",
                        "label": "Setup",
                        "choices": [
                            {"value": "quick", "label": "Fast path"},
                        ],
                    },
                    {
                        "id": "quick_rigor",
                        "type": "enum",
                        "label": "Rigor",
                        "write": "spec.rigor",
                        "default": "high",
                        "show_when": {"setup_mode": "quick"},
                        "choices": [
                            {"value": "low", "label": "Low"},
                            {"value": "medium", "label": "Medium"},
                            {"value": "high", "label": "High"},
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    _write_playbook(
        playbook_dir,
        "declarative-only",
        _minimal_playbook_yaml(
            playbook_id="declarative-only",
            prepare_block=f"""
commands:
  prepare:
    fields_ref: {asset.name}
""",
        ),
    )

    loaded = loader.load_model("declarative-only")

    assert loaded.model.commands is not None
    assert loaded.model.commands.prepare is not None
    assert loaded.model.commands.prepare.fields_ref == asset.name


def test_standard_playbook_fields_ref_passes_semantic_validation() -> None:
    loader = PlaybookLoader()
    loaded = loader.load_model("standard")
    assert loaded.model.commands is not None
    assert loaded.model.commands.prepare is not None
    assert loaded.model.commands.prepare.fields_ref is not None
