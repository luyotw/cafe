"""Skill copy is bound to its resolved owner before task materialization."""

from pathlib import Path

import pytest
import yaml

from cafe.catalogs.resolver import CatalogResolver
from cafe.catalogs.sync import CatalogSyncService
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
        runtime_locales._packaged_catalogs.cache_clear()
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
    root = Path(__file__).resolve().parents[2] / "src/cafe/data/skills"
    phase_copy = {locale: set() for locale in ("en-US", "zh-TW")}
    for resource in root.glob("*/locales/*.yaml"):
        phase_copy[resource.stem].update(yaml.safe_load(resource.read_text()).values())
    allowed = {
        "workspace.correction",
        "notification.action_labels.agent_execution_interrupted",
        *[f"notification.{name}" for name in (
            "repository_fallback", "issue_fallback", "step_fallback", "action_fallback",
            "unknown_step", "field_separator", "task_headline", "repository_field",
            "issue_field", "step_field", "action_field", "task_closing", "callback_headline",
            "status_field", "reason_state", "reason_queue", "reason_generic", "callback_impact",
            "callback_closing",
        )],
        *[f"manager.progress.{name}" for name in (
            "missing", "iteration", "review", "confirmation", "delegable", "not_delegable",
            "closeout", "review_line", "confirmation_line", "closeout_line",
            "status.pending", "status.in_progress", "status.awaiting_input", "status.completed",
            "status.returned", "status.awaiting_confirmation", "status.skipped", "status.blocked",
            "status.unknown", "cost_footer", "cost.reported", "cost.estimated",
            "cost.legacy", "cost.unknown", "cost.partial", "cost.stale",
        )],
        *[f"manager.kickoff.{name}" for name in (
            "header_field", "header_value", "confirmation", "checks", "global_sync_heading",
            "global_sync_reminder", "commands_confirmation", "no_commands",
        )],
    }
    for locale, catalog in runtime_locales.load_catalogs().items():
        # An exhaustive generic inventory and text comparison also catch phase
        # copy hidden under another namespace or an existing generic key.
        assert set(catalog) == allowed
        assert not (set(catalog.values()) & phase_copy[locale])


@pytest.mark.parametrize("shared", [False, True])
def test_owner_parse_reads_each_locale_once_and_reloads_next_operation(
    tmp_path, monkeypatch, shared
):
    selected = write_owner(tmp_path / ".cafe/skills", "selected", "approve", "Original")
    write_owner(tmp_path / ".cafe/skills", "anchor", "anchor", "Anchor")
    loader = SkillLoader(project_root=tmp_path, global_root=tmp_path / "global")
    reads = []
    original = Path.read_text
    def read(path, *args, **kwargs):
        if path.parent == selected / "locales":
            reads.append(path.name)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read)
    def parse():
        return resolve_step_workflow_composition(
            loader, primary_skill="anchor" if shared else "selected",
            workflow_skills=["selected"] if shared else [], step_name="arbitrary",
        ).human_tasks[-1]
    first = parse()
    assert reads == ["en-US.yaml", "zh-TW.yaml"]
    for locale in ("en-US", "zh-TW"):
        (selected / "locales" / f"{locale}.yaml").write_text(
            yaml.safe_dump({"task.prompt": f"Updated {locale}"})
        )
    reads.clear()
    assert parse().prompt == "Updated en-US"
    assert reads == ["en-US.yaml", "zh-TW.yaml"]
    assert first.prompt == "Original en-US"
    (selected / "locales/zh-TW.yaml").unlink()
    with pytest.raises(ValueError) as rejected:
        parse()
    assert "zh-TW.yaml" in str(rejected.value)


def test_pr_declaration_reuses_one_pair_for_all_nested_references(tmp_path, monkeypatch):
    loader = SkillLoader(project_root=tmp_path, global_root=tmp_path / "global")
    entry, _raw = loader.get_workflow_declaration_data("cafe-pr")
    reads = []
    original = Path.read_text
    def read(path, *args, **kwargs):
        if path.parent == entry.directory / "locales":
            reads.append(path.name)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read)
    first = loader.get_workflow_declaration("cafe-pr")
    assert reads == ["en-US.yaml", "zh-TW.yaml"]
    second = loader.get_workflow_declaration("cafe-pr")
    assert second == first
    assert reads == ["en-US.yaml", "zh-TW.yaml"] * 2


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


@pytest.mark.parametrize("shared", [False, True])
def test_published_owner_replacement_reaches_fresh_public_composition(tmp_path, shared):
    project = tmp_path / "publisher"
    global_root = tmp_path / "global"
    consumer = tmp_path / "consumer"
    write_owner(global_root / "skills", "selected", "approve-old", "Original")
    write_owner(global_root / "skills", "anchor", "anchor", "Anchor")
    loader = SkillLoader(project_root=consumer, global_root=global_root)
    assert loader.get_workflow_declaration("selected").human_tasks[0].prompt == "Original en-US"
    write_owner(project / ".cafe/skills", "selected", "approve-new", "Updated")
    service = CatalogSyncService(
        CatalogResolver(
            project_root=project,
            canonical_root=project,
            global_root=global_root,
            builtin_root=tmp_path / "builtin",
        )
    )
    comparison = service.compare()
    result = service.sync(comparison.token, ["phase:selected"])
    assert result.updated == ("phase:selected",)
    fresh = SkillLoader(project_root=consumer, global_root=global_root)
    for current in (loader, fresh):
        composition = resolve_step_workflow_composition(
            current,
            primary_skill="anchor" if shared else "selected",
            workflow_skills=["selected"] if shared else [],
            step_name="inspect",
        )
        policy = composition.contributors[-1].declaration.human_tasks[0]
        assert policy.id == "approve-new"
        assert policy.prompt == "Updated en-US"
        assert policy.for_locale("zh-TW").prompt == "Updated zh-TW"


@pytest.mark.parametrize("mutation", ["remove", "invalid"])
def test_warm_owner_rejects_current_missing_or_invalid_resources(tmp_path, mutation):
    owner = write_owner(tmp_path / ".cafe/skills", "custom", "approve", "Original")
    loader = SkillLoader(project_root=tmp_path, global_root=tmp_path / "global")
    assert loader.get_workflow_declaration("custom").human_tasks
    resource = owner / "locales/zh-TW.yaml"
    if mutation == "remove":
        resource.unlink()
    else:
        resource.write_text('task.prompt: "{unexpected}"\n')
    fresh = SkillLoader(project_root=tmp_path, global_root=tmp_path / "global")
    for current in (loader, fresh):
        with pytest.raises(ValueError) as rejected:
            current.get_workflow_declaration("custom")
        assert str(resource) in str(rejected.value)


def test_structural_composition_preserves_primary_and_shared_machine_contracts(tmp_path):
    root = tmp_path / ".cafe/skills"
    for name in ("primary", "shared"):
        owner = write_owner(root, name, name, "Owned")
        for resource in owner.glob("locales/*.yaml"):
            resource.unlink()
    composition = resolve_step_workflow_composition(
        SkillLoader(
            project_root=tmp_path, global_root=tmp_path / "global", resolve_presentation=False
        ),
        primary_skill="primary",
        workflow_skills=["shared"],
        step_name="inspect",
    )
    assert [policy.id for policy in composition.human_tasks] == ["primary", "shared"]
    for policy in composition.human_tasks:
        assert policy.input_schema == "decision"
        assert [decision.id for decision in policy.decisions] == ["accept"]
    with pytest.raises(ValueError):
        SkillLoader(
            project_root=tmp_path, global_root=tmp_path / "global"
        ).get_workflow_declaration("primary")
