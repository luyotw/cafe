"""Skill copy is bound to its resolved owner before task materialization."""

from pathlib import Path

import pytest
import yaml

from cafe.core import runtime_locales
from cafe.core.human_tasks import HumanTaskPolicy
from cafe.skills.loader import SkillLoader
from cafe.skills.workflow_composition import resolve_step_workflow_composition


def write_owner(root: Path, name: str, task_id: str, text: str) -> Path:
    directory = root / name
    (directory / "locales").mkdir(parents=True)
    reference = {"message_key": "task.prompt"}
    workflow = {
        "human_tasks": [
            {
                "id": task_id,
                "pattern": "confirm_output",
                "input_schema": "decision",
                "prompt": reference,
                "prompt_locales": {"zh-TW": reference},
                "decisions": [
                    {"id": "accept", "label": reference, "label_locales": {"zh-TW": reference}}
                ],
            }
        ],
    }
    metadata = {"name": name, "description": "test owner", "workflow": workflow}
    (directory / "SKILL.md").write_text(
        "---\n" + yaml.safe_dump(metadata) + "---\n# Test\n", encoding="utf-8"
    )
    for locale in ("en-US", "zh-TW"):
        (directory / "locales" / f"{locale}.yaml").write_text(
            yaml.safe_dump({"task.prompt": f"{text} {locale}"}), encoding="utf-8"
        )
    return directory


def test_public_composition_binds_primary_and_shared_copy_to_selected_owner(tmp_path, monkeypatch):
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    write_owner(global_root / "skills", "primary", "approve-primary", "Shadowed")
    write_owner(project / ".cafe/skills", "primary", "approve-primary", "Primary")
    write_owner(global_root / "skills", "shared", "approve-shared", "Shared")
    loader = SkillLoader(
        project_root=project, global_root=global_root, builtin_root=tmp_path / "empty-builtin"
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    composition = resolve_step_workflow_composition(
        loader, primary_skill="primary", workflow_skills=["shared"], step_name="arbitrary"
    )
    for contributor, expected in zip(composition.contributors, ("Primary", "Shared"), strict=True):
        policy = contributor.declaration.human_tasks[0]
        snapshot = policy.for_locale("zh-TW").model_dump(mode="json")
        assert snapshot["prompt"] == f"{expected} zh-TW"
        assert snapshot["decisions"][0]["label"] == f"{expected} zh-TW"
        assert policy.for_locale("zh-Hant").prompt == f"{expected} en-US"
        # Plain snapshots remain usable after the authoring resources disappear.
        for resource in contributor.source.skill_root.glob("locales/*.yaml"):
            resource.unlink()
        runtime_locales.load_catalogs.cache_clear()
        assert HumanTaskPolicy.model_validate(snapshot).model_dump(mode="json") == snapshot


def test_skill_copy_does_not_fall_back_to_central_catalog(tmp_path):
    owner = write_owner(tmp_path / ".cafe/skills", "custom", "approve", "Owned")
    (owner / "locales/zh-TW.yaml").unlink()
    loader = SkillLoader(project_root=tmp_path, global_root=tmp_path / "global")
    with pytest.raises(ValueError) as rejected:
        loader.get_workflow_declaration("custom")
    assert "custom" in str(rejected.value) and "zh-TW.yaml" in str(rejected.value)


def test_reference_requires_declaration_owner():
    with pytest.raises(ValueError):
        HumanTaskPolicy.model_validate(
            {
                "id": "copy",
                "pattern": "revision_feedback",
                "input_schema": "feedback",
                "prompt": {"message_key": "notification.task_headline"},
            }
        )


def test_declaration_reference_cannot_require_interpolation_arguments(tmp_path):
    owner = write_owner(tmp_path / ".cafe/skills", "custom", "approve", "Owned")
    for resource in owner.glob("locales/*.yaml"):
        resource.write_text('task.prompt: "Argument {value}"\n')
    loader = SkillLoader(project_root=tmp_path, global_root=tmp_path / "global")
    with pytest.raises(ValueError) as rejected:
        loader.get_workflow_declaration("custom")
    assert "task.prompt" in str(rejected.value)


def test_explicit_catalog_roots_are_isolated_and_single_pass(tmp_path, monkeypatch):
    owners = [write_owner(tmp_path, name, name, name) for name in ("first", "second")]
    monkeypatch.chdir(tmp_path)
    for owner in owners:
        root = owner / "locales"
        for locale in ("en-US", "zh-TW"):
            (root / f"{locale}.yaml").write_text(
                yaml.safe_dump({"task.prompt": f"{owner.name} {locale} {{value}}"})
            )
        assert (
            runtime_locales.render_text(
                "task.prompt", catalog_root=root, locale="zh-HK", value="{literal}"
            )
            == f"{owner.name} zh-TW {{literal}}"
        )
        assert (
            runtime_locales.render_text(
                "task.prompt", catalog_root=root, locale="zh-CN", value="{literal}"
            )
            == f"{owner.name} en-US {{literal}}"
        )
        with pytest.raises(TypeError):
            runtime_locales.load_catalogs(root)["en-US"]["task.prompt"] = "changed"


def test_phase_copy_is_absent_from_central_catalogs():
    for catalog in runtime_locales.load_catalogs().values():
        assert not any(key.startswith("human_task.") for key in catalog)


@pytest.mark.parametrize(
    "document",
    [
        "[a, b]",
        "{}",
        "task.prompt: 42",
        "task.prompt: null",
        'task.prompt: " "',
        '42: "text"',
        'bad key: "text"',
        'task.prompt: "{value"',
        'task.prompt: "{value.name}"',
        'task.prompt: "{value[0]}"',
        'task.prompt: "{}"',
        'task.prompt: "{value!r}"',
        'task.prompt: "{value:>20}"',
        'task.prompt: "{value:{width}}"',
        'task.prompt: "first"\ntask.prompt: "second"',
        "task.prompt: [",
        'other.prompt: "test"',
        'task.prompt: "{different}"',
    ],
)
def test_owner_catalog_validation_reports_owning_resource(tmp_path, document):
    owner = write_owner(tmp_path, "custom", "approve", "Owned")
    root = owner / "locales"
    (root / "zh-TW.yaml").write_text(document)
    with pytest.raises(runtime_locales.LocaleCatalogError) as rejected:
        runtime_locales.load_catalogs(root)
    assert str(root / "zh-TW.yaml") in str(rejected.value)
