"""Tests for prepare field schema and fields_ref loading."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from cafe.core.prepare_fields import (
    FieldsRefKind,
    load_fields_ref,
    parse_fields_ref,
    parse_prepare_fields,
    parse_prepare_fields_document,
    resolve_fields_ref_path,
)
from cafe.skills.loader import SkillLoader


MINIMAL_FIELD = {
    "id": "rigor",
    "type": "enum",
    "label": "Rigor",
    "write": "spec.rigor",
    "default": "medium",
    "choices": [
        {"value": "low", "label": "Low"},
        {"value": "medium", "label": "Medium"},
        {"value": "high", "label": "High"},
    ],
}


def test_prepare_field_schema_parses_valid_definition() -> None:
    fields = parse_prepare_fields([MINIMAL_FIELD])
    assert fields[0].id == "rigor"
    assert fields[0].write == "spec.rigor"


def test_prepare_field_schema_rejects_malformed_write_target() -> None:
    invalid = dict(MINIMAL_FIELD, write="spec")
    with pytest.raises(ValidationError):
        parse_prepare_fields([invalid])


def test_prepare_field_schema_rejects_enum_without_choices() -> None:
    invalid = {key: value for key, value in MINIMAL_FIELD.items() if key != "choices"}
    with pytest.raises(ValidationError):
        parse_prepare_fields([invalid])


def test_prepare_field_schema_rejects_invalid_type() -> None:
    invalid = dict(MINIMAL_FIELD, type="checkbox")
    with pytest.raises(ValidationError):
        parse_prepare_fields([invalid])


def test_prepare_field_schema_accepts_declared_custom_step_template_target() -> None:
    field = dict(MINIMAL_FIELD, id="report_template", type="template", write="synthesis.template")
    field.pop("choices")

    parsed = parse_prepare_fields([field])

    assert parsed[0].write == "synthesis.template"


def test_prepare_field_schema_rejects_invalid_declared_defaults_and_normalizer() -> None:
    invalid_enum_default = dict(MINIMAL_FIELD, default="unexpected")
    invalid_normalizer = dict(MINIMAL_FIELD, normalize="github_issue")

    with pytest.raises(ValidationError):
        parse_prepare_fields([invalid_enum_default])
    with pytest.raises(ValidationError):
        parse_prepare_fields([invalid_normalizer])


def _write_skill(root: Path, name: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: desc-{name}\n---\n\n# {name}\n",
        encoding="utf-8",
    )


def _loader(tmp_path: Path) -> SkillLoader:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "cafe-spec")
    return SkillLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )


def test_fields_ref_playbook_relative_loads_yaml(tmp_path: Path) -> None:
    playbook_dir = tmp_path / "playbooks"
    playbook_dir.mkdir(parents=True)
    asset = playbook_dir / "fields.yaml"
    asset.write_text(yaml.safe_dump({"fields": [MINIMAL_FIELD]}), encoding="utf-8")
    playbook_path = playbook_dir / "test.yaml"
    playbook_path.write_text("playbook: {id: test}\n", encoding="utf-8")

    parsed = load_fields_ref(
        ref="fields.yaml",
        playbook_path=playbook_path,
        skill_loader=_loader(tmp_path),
    )
    assert parsed.fields[0].id == "rigor"


def test_fields_ref_skill_uri_loads_asset(tmp_path: Path) -> None:
    loader = _loader(tmp_path)
    asset_dir = loader.get_skill_dir("cafe-spec") / "assets" / "prepare"
    asset_dir.mkdir(parents=True)
    asset = asset_dir / "fields.yaml"
    asset.write_text(yaml.safe_dump({"fields": [MINIMAL_FIELD]}), encoding="utf-8")

    parsed = load_fields_ref(
        ref="skill://spec/assets/prepare/fields.yaml",
        playbook_path=tmp_path / "playbooks" / "test.yaml",
        skill_loader=loader,
    )
    assert parsed.fields[0].default == "medium"


def test_fields_ref_playbook_relative_rejects_unsafe_paths(tmp_path: Path) -> None:
    playbook_path = tmp_path / "playbooks" / "test.yaml"
    playbook_path.parent.mkdir(parents=True)
    playbook_path.write_text("playbook: {id: test}\n", encoding="utf-8")
    loader = _loader(tmp_path)

    for ref in ("../secrets.yaml", "/etc/passwd.yaml", "fields.py", "scripts/hook.yaml"):
        with pytest.raises(ValueError):
            resolve_fields_ref_path(ref=ref, playbook_path=playbook_path, skill_loader=loader)


def test_fields_ref_skill_uri_rejects_unsafe_refs(tmp_path: Path) -> None:
    loader = _loader(tmp_path)
    playbook_path = tmp_path / "playbooks" / "test.yaml"
    playbook_path.parent.mkdir(parents=True)
    playbook_path.write_text("playbook: {id: test}\n", encoding="utf-8")

    unsafe_refs = [
        "skill://missing/assets/fields.yaml",
        "skill://spec/references/policy.md",
        "skill://spec/assets/../scripts/run.sh",
        "skill://spec/assets/prepare/fields.py",
        "http://example.com/fields.yaml",
    ]
    for ref in unsafe_refs:
        with pytest.raises((ValueError, FileNotFoundError)):
            resolve_fields_ref_path(ref=ref, playbook_path=playbook_path, skill_loader=loader)


def test_fields_ref_rejects_missing_asset(tmp_path: Path) -> None:
    playbook_path = tmp_path / "playbooks" / "test.yaml"
    playbook_path.parent.mkdir(parents=True)
    playbook_path.write_text("playbook: {id: test}\n", encoding="utf-8")
    loader = _loader(tmp_path)

    with pytest.raises(FileNotFoundError):
        load_fields_ref(
            ref="missing.yaml",
            playbook_path=playbook_path,
            skill_loader=loader,
        )


def test_parse_fields_ref_classifies_sources() -> None:
    assert parse_fields_ref("assets/fields.yaml") is FieldsRefKind.PLAYBOOK_RELATIVE
    assert parse_fields_ref("skill://spec/assets/prepare/fields.yaml") is FieldsRefKind.SKILL_ASSET


def test_skill_loader_precedence_for_fields_ref(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "cafe-spec")
    _write_skill(project_root / ".cafe" / "skills", "cafe-spec")

    builtin_asset = builtin_root / "skills" / "cafe-spec" / "assets" / "prepare" / "fields.yaml"
    builtin_asset.parent.mkdir(parents=True)
    builtin_asset.write_text(
        yaml.safe_dump({"fields": [dict(MINIMAL_FIELD, default="low")]}),
        encoding="utf-8",
    )

    project_asset = (
        project_root / ".cafe" / "skills" / "cafe-spec" / "assets" / "prepare" / "fields.yaml"
    )
    project_asset.parent.mkdir(parents=True)
    project_asset.write_text(
        yaml.safe_dump({"fields": [dict(MINIMAL_FIELD, default="high")]}),
        encoding="utf-8",
    )

    loader = SkillLoader(
        project_root=project_root,
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    parsed = load_fields_ref(
        ref="skill://spec/assets/prepare/fields.yaml",
        playbook_path=project_root / ".cafe" / "playbooks" / "test.yaml",
        skill_loader=loader,
    )
    assert parsed.fields[0].default == "high"


def test_prepare_fields_document_parses_meta() -> None:
    parsed = parse_prepare_fields_document(
        {"meta": {"prompt_for_spec_plan_config": False}, "fields": [MINIMAL_FIELD]}
    )
    assert parsed.meta is not None
    assert parsed.meta.prompt_for_spec_plan_config is False
