"""U6: conservative legacy-agent migration invariants."""

import json
import os
from pathlib import Path

import pytest

from cafe.catalogs.migration import (
    AgentSnapshotMigrator,
    MigrationDecisionError,
    StaleMigrationDecision,
)
from cafe.catalogs.resolver import CatalogKind, CatalogResolver, CatalogValidationError


def _agent(path: Path, name: str, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: test\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def _migrator(
    tmp_path: Path, *, tracked: set[str] | None = None
) -> tuple[AgentSnapshotMigrator, Path, Path]:
    project = tmp_path / "project"
    builtin = tmp_path / "builtin"
    resolver = CatalogResolver(
        project_root=project,
        canonical_root=project,
        global_root=tmp_path / "global",
        builtin_root=builtin,
    )
    tracked = tracked or set()
    migrator = AgentSnapshotMigrator(
        resolver,
        is_tracked=lambda path: path.relative_to(project).as_posix() in tracked,
    )
    return migrator, project, builtin


def test_preview_classifies_proven_tracked_ambiguous_and_invalid_files(tmp_path: Path) -> None:
    migrator, project, builtin = _migrator(tmp_path, tracked={".cafe/agents/reviewer/Richard.md"})
    builtin_david = _agent(builtin / "agents" / "developer" / "David.md", "David", "fallback")
    _agent(
        project / ".cafe" / "agents" / "developer" / "David.md",
        "David",
        builtin_david.read_text(encoding="utf-8").split("\n\n", 1)[1].strip(),
    )
    _agent(builtin / "agents" / "reviewer" / "Richard.md", "Richard", "fallback")
    _agent(project / ".cafe" / "agents" / "reviewer" / "Richard.md", "Richard", "custom")
    _agent(project / ".cafe" / "agents" / "pm" / "Roger.md", "Roger", "custom")
    invalid = project / ".cafe" / "agents" / "ops" / "Broken.md"
    invalid.parent.mkdir(parents=True)
    invalid.write_text("missing frontmatter", encoding="utf-8")

    preview = migrator.preview()
    statuses = {item.entry_id: item.status for item in preview.items}
    assert statuses == {
        "agent:developer/David": "generated",
        "agent:ops/Broken": "invalid",
        "agent:pm/Roger": "ambiguous",
        "agent:reviewer/Richard": "intentional",
    }
    assert all(item.effect == "shadows_fallback" for item in preview.items)


def test_retirement_is_recoverable_and_preserves_other_project_agents(tmp_path: Path) -> None:
    migrator, project, builtin = _migrator(tmp_path)
    fallback = _agent(builtin / "agents" / "developer" / "David.md", "David", "same")
    snapshot = _agent(
        project / ".cafe" / "agents" / "developer" / "David.md",
        "David",
        "same",
    )
    intentional = _agent(
        project / ".cafe" / "agents" / "reviewer" / "Richard.md",
        "Richard",
        "custom",
    )
    preview = migrator.preview()

    result = migrator.apply(
        preview.token,
        {"agent:developer/David": "retire", "agent:reviewer/Richard": "preserve"},
    )

    assert not snapshot.exists()
    assert result.retired[0].read_text(encoding="utf-8") == fallback.read_text(encoding="utf-8")
    assert intentional.is_file()
    assert result.manifest.is_file()


def test_changed_file_rejects_preview_token_without_modification(tmp_path: Path) -> None:
    migrator, project, builtin = _migrator(tmp_path)
    _agent(builtin / "agents" / "developer" / "David.md", "David", "same")
    snapshot = _agent(project / ".cafe" / "agents" / "developer" / "David.md", "David", "same")
    preview = migrator.preview()
    snapshot.write_text(snapshot.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")

    with pytest.raises(StaleMigrationDecision):
        migrator.apply(preview.token, {"agent:developer/David": "retire"})
    assert snapshot.is_file()


@pytest.mark.parametrize("change_point", ["before_apply", "after_complete"])
def test_symlink_target_change_invalidates_migration_decision_and_replay(
    tmp_path: Path, change_point: str
) -> None:
    migrator, project, _builtin = _migrator(tmp_path)
    source = project / ".cafe" / "agents" / "developer" / "David.md"
    target = _agent(source.with_suffix(".agent"), "David", "approved")
    source.symlink_to(target.name)
    preview = migrator.preview()
    decisions = {"agent:developer/David": "retire"}

    if change_point == "before_apply":
        target.write_text(target.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
        with pytest.raises(StaleMigrationDecision):
            migrator.apply(preview.token, decisions)
        assert source.is_symlink()
    else:
        migrator.apply(preview.token, decisions)
        target.write_text(target.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
        with pytest.raises(StaleMigrationDecision):
            migrator.apply(preview.token, decisions)


def test_migration_preview_rejects_symlink_target_outside_entry_authority(
    tmp_path: Path,
) -> None:
    migrator, project, _builtin = _migrator(tmp_path)
    external = _agent(tmp_path / "external-agent", "David", "outside")
    source = project / ".cafe" / "agents" / "developer" / "David.md"
    source.parent.mkdir(parents=True)
    source.symlink_to(external)

    with pytest.raises(CatalogValidationError):
        migrator.preview()


def test_retirement_fails_closed_when_source_is_swapped_at_the_move_boundary(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    builtin = tmp_path / "builtin"
    source = _agent(
        project / ".cafe" / "agents" / "developer" / "David.md",
        "David",
        "approved",
    )
    _agent(builtin / "agents" / "developer" / "David.md", "David", "fallback")
    replacement = _agent(source.with_suffix(".replacement"), "David", "replacement")
    swapped = False

    def swap_source(boundary: str, entry_id: str | None) -> None:
        nonlocal swapped
        if boundary == "before_retire" and not swapped:
            swapped = True
            os.replace(replacement, source)

    migrator = AgentSnapshotMigrator(
        CatalogResolver(
            project_root=project,
            canonical_root=project,
            global_root=tmp_path / "global",
            builtin_root=builtin,
        ),
        is_tracked=lambda _path: False,
        failure_injector=swap_source,
    )
    preview = migrator.preview()

    with pytest.raises(StaleMigrationDecision):
        migrator.apply(preview.token, {"agent:developer/David": "retire"})

    manifest = (
        project / ".cafe" / "migrations" / "agent-snapshots" / preview.token[:16] / "manifest.json"
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["status"] == "in_progress"
    assert payload["items"][0]["state"] == "retiring"


def test_retirement_fails_closed_when_fallback_changes_at_the_move_boundary(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    source = _agent(
        project / ".cafe" / "agents" / "developer" / "David.md",
        "David",
        "approved",
    )
    fallback = _agent(
        global_root / "agents" / "developer" / "David.md",
        "David",
        "approved",
    )
    changed = False

    def change_fallback(boundary: str, entry_id: str | None) -> None:
        nonlocal changed
        if boundary == "before_retire" and not changed:
            changed = True
            _agent(fallback, "David", "replacement")

    resolver = CatalogResolver(
        project_root=project,
        canonical_root=project,
        global_root=global_root,
        builtin_root=tmp_path / "builtin",
    )
    migrator = AgentSnapshotMigrator(
        resolver,
        is_tracked=lambda _path: False,
        failure_injector=change_fallback,
    )
    preview = migrator.preview()

    with pytest.raises(StaleMigrationDecision):
        migrator.apply(preview.token, {"agent:developer/David": "retire"})

    manifest = (
        project / ".cafe" / "migrations" / "agent-snapshots" / preview.token[:16] / "manifest.json"
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert source.is_file()
    assert payload["status"] == "in_progress"
    assert payload["items"][0]["state"] == "retiring"


def test_retirement_restores_project_source_when_fallback_changes_after_final_read(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    source = _agent(
        project / ".cafe" / "agents" / "developer" / "David.md",
        "David",
        "approved",
    )
    fallback = _agent(
        global_root / "agents" / "developer" / "David.md",
        "David",
        "approved",
    )
    replacement = _agent(fallback.with_suffix(".replacement"), "David", "replacement")
    resolver = CatalogResolver(
        project_root=project,
        canonical_root=project,
        global_root=global_root,
        builtin_root=tmp_path / "builtin",
    )
    armed = False
    changed = False

    def arm_after_boundary(boundary: str, _entry_id: str | None) -> None:
        nonlocal armed
        if boundary == "before_retire":
            armed = True

    migrator = AgentSnapshotMigrator(
        resolver,
        is_tracked=lambda _path: False,
        failure_injector=arm_after_boundary,
    )
    preview = migrator.preview()
    fallback_digest = migrator._fallback_digest

    def replace_after_digest_read(key: str) -> str:
        nonlocal changed
        digest = fallback_digest(key)
        if armed and not changed:
            changed = True
            os.replace(replacement, fallback)
        return digest

    migrator._fallback_digest = replace_after_digest_read  # type: ignore[method-assign]

    with pytest.raises(StaleMigrationDecision):
        migrator.apply(preview.token, {"agent:developer/David": "retire"})

    assert source.is_file()
    assert resolver.resolve(CatalogKind.AGENT, "developer/David").source == "project"


def test_apply_requires_a_decision_for_every_legacy_file(tmp_path: Path) -> None:
    migrator, project, _builtin = _migrator(tmp_path)
    _agent(project / ".cafe" / "agents" / "pm" / "Roger.md", "Roger", "custom")
    preview = migrator.preview()

    with pytest.raises(MigrationDecisionError):
        migrator.apply(preview.token, {})


def test_intentional_tracked_agent_cannot_be_retired(tmp_path: Path) -> None:
    migrator, project, _builtin = _migrator(tmp_path, tracked={".cafe/agents/reviewer/Richard.md"})
    intentional = _agent(
        project / ".cafe" / "agents" / "reviewer" / "Richard.md",
        "Richard",
        "custom",
    )
    preview = migrator.preview()

    with pytest.raises(MigrationDecisionError):
        migrator.apply(preview.token, {"agent:reviewer/Richard": "retire"})

    assert intentional.is_file()


def test_interrupted_retirement_resumes_from_durable_manifest(tmp_path: Path) -> None:
    failed_once = False

    def interrupt_after_move(boundary: str, entry_id: str | None) -> None:
        nonlocal failed_once
        if boundary == "after_retire" and not failed_once:
            failed_once = True
            raise OSError("injected interruption after move")

    project = tmp_path / "project"
    builtin = tmp_path / "builtin"
    resolver = CatalogResolver(
        project_root=project,
        canonical_root=project,
        global_root=tmp_path / "global",
        builtin_root=builtin,
    )
    migrator = AgentSnapshotMigrator(
        resolver,
        is_tracked=lambda _path: False,
        failure_injector=interrupt_after_move,
    )
    for role, name in (("developer", "David"), ("reviewer", "Richard")):
        _agent(builtin / "agents" / role / f"{name}.md", name, "same")
        _agent(project / ".cafe" / "agents" / role / f"{name}.md", name, "same")
    preview = migrator.preview()
    decisions = {item.entry_id: "retire" for item in preview.items}

    with pytest.raises(OSError, match="injected interruption"):
        migrator.apply(preview.token, decisions)

    result = migrator.apply(preview.token, decisions)
    repeated = migrator.apply(preview.token, decisions)

    assert len(result.retired) == 2
    assert all(path.is_file() for path in result.retired)
    assert repeated == result


def test_interrupted_fallback_validation_restores_project_authority_on_resume(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    source = _agent(
        project / ".cafe" / "agents" / "developer" / "David.md",
        "David",
        "approved",
    )
    fallback = _agent(
        global_root / "agents" / "developer" / "David.md",
        "David",
        "approved",
    )
    replacement = _agent(fallback.with_suffix(".replacement"), "David", "replacement")
    resolver = CatalogResolver(
        project_root=project,
        canonical_root=project,
        global_root=global_root,
        builtin_root=tmp_path / "builtin",
    )
    migrator = AgentSnapshotMigrator(resolver, is_tracked=lambda _path: False)
    preview = migrator.preview()
    decisions = {"agent:developer/David": "retire"}
    original_check = migrator._fallback_identity_is_current
    interrupted = False

    def interrupt_post_move_validation(*args, **kwargs) -> bool:
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            os.replace(replacement, fallback)
            raise OSError("injected interruption during fallback validation")
        return original_check(*args, **kwargs)

    migrator._fallback_identity_is_current = (  # type: ignore[method-assign]
        interrupt_post_move_validation
    )

    with pytest.raises(OSError, match="fallback validation"):
        migrator.apply(preview.token, decisions)
    with pytest.raises(StaleMigrationDecision):
        migrator.apply(preview.token, decisions)

    assert source.is_file()
    assert resolver.resolve(CatalogKind.AGENT, "developer/David").source == "project"


def test_preserve_decision_is_shared_by_canonical_and_linked_project_views(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "canonical"
    linked = tmp_path / "linked"
    builtin = tmp_path / "builtin"
    global_root = tmp_path / "global"
    fallback = _agent(builtin / "agents" / "developer" / "David.md", "David", "same")
    canonical_agent = _agent(
        canonical / ".cafe" / "agents" / "developer" / "David.md",
        "David",
        "same",
    )
    assert canonical_agent.read_bytes() == fallback.read_bytes()
    linked_migrator = AgentSnapshotMigrator(
        CatalogResolver(
            project_root=linked,
            canonical_root=canonical,
            global_root=global_root,
            builtin_root=builtin,
        ),
        is_tracked=lambda _path: False,
    )

    preview = linked_migrator.preview()
    assert preview.items[0].path == canonical_agent
    result = linked_migrator.apply(preview.token, {"agent:developer/David": "preserve"})

    assert result.manifest.is_relative_to(canonical)
    assert linked_migrator.preview().items == ()
    canonical_migrator = AgentSnapshotMigrator(
        CatalogResolver(
            project_root=canonical,
            canonical_root=canonical,
            global_root=global_root,
            builtin_root=builtin,
        ),
        is_tracked=lambda _path: False,
    )
    assert canonical_migrator.preview().items == ()
    assert canonical_migrator.publication_blocked_entry_ids() == set()


def test_retirement_journals_are_bound_to_the_active_project_view(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "canonical"
    linked = tmp_path / "linked"
    builtin = tmp_path / "builtin"
    global_root = tmp_path / "global"
    for root in (canonical, linked, builtin):
        _agent(root / ".cafe" / "agents" / "developer" / "David.md", "David", "same")
    builtin_agent = builtin / ".cafe" / "agents" / "developer" / "David.md"
    target = builtin / "agents" / "developer" / "David.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    builtin_agent.replace(target)

    canonical_migrator = AgentSnapshotMigrator(
        CatalogResolver(
            project_root=canonical,
            canonical_root=canonical,
            global_root=global_root,
            builtin_root=builtin,
        ),
        is_tracked=lambda _path: False,
    )
    linked_migrator = AgentSnapshotMigrator(
        CatalogResolver(
            project_root=linked,
            canonical_root=canonical,
            global_root=global_root,
            builtin_root=builtin,
        ),
        is_tracked=lambda _path: False,
    )
    canonical_preview = canonical_migrator.preview()
    linked_preview = linked_migrator.preview()

    assert canonical_preview.token != linked_preview.token
    canonical_result = canonical_migrator.apply(
        canonical_preview.token, {"agent:developer/David": "retire"}
    )
    linked_result = linked_migrator.apply(linked_preview.token, {"agent:developer/David": "retire"})

    assert not (canonical / ".cafe" / "agents" / "developer" / "David.md").exists()
    assert not (linked / ".cafe" / "agents" / "developer" / "David.md").exists()
    assert canonical_result.manifest != linked_result.manifest


def test_interrupted_relative_symlink_retirement_resumes_from_recovery_entry(
    tmp_path: Path,
) -> None:
    interrupted = False

    def interrupt_after_move(boundary: str, entry_id: str | None) -> None:
        nonlocal interrupted
        if boundary == "after_retire" and not interrupted:
            interrupted = True
            raise OSError("injected interruption after symlink move")

    project = tmp_path / "project"
    role_dir = project / ".cafe" / "agents" / "developer"
    _agent(role_dir / "David.agent", "David", "custom")
    source = role_dir / "David.md"
    relative_target = Path("David.agent")
    source.symlink_to(relative_target)
    migrator = AgentSnapshotMigrator(
        CatalogResolver(
            project_root=project,
            canonical_root=project,
            global_root=tmp_path / "global",
            builtin_root=tmp_path / "builtin",
        ),
        is_tracked=lambda _path: False,
        failure_injector=interrupt_after_move,
    )
    preview = migrator.preview()
    decisions = {"agent:developer/David": "retire"}

    with pytest.raises(OSError, match="symlink move"):
        migrator.apply(preview.token, decisions)

    result = migrator.apply(preview.token, decisions)

    assert not source.exists()
    assert result.retired[0].is_symlink()
    assert result.retired[0].readlink() == relative_target


@pytest.mark.parametrize("changed_action", ["preserve", "retire"])
def test_resume_revalidates_completed_checkpoint_state(tmp_path: Path, changed_action: str) -> None:
    interrupted = False

    def interrupt_before_second_checkpoint(boundary: str, entry_id: str | None) -> None:
        nonlocal interrupted
        if (
            boundary == "before_manifest_write"
            and entry_id == "agent:reviewer/Richard"
            and not interrupted
        ):
            interrupted = True
            raise OSError("injected interruption before second checkpoint")

    project = tmp_path / "project"
    builtin = tmp_path / "builtin"
    resolver = CatalogResolver(
        project_root=project,
        canonical_root=project,
        global_root=tmp_path / "global",
        builtin_root=builtin,
    )
    migrator = AgentSnapshotMigrator(
        resolver,
        is_tracked=lambda _path: False,
        failure_injector=interrupt_before_second_checkpoint,
    )
    for role, name in (("developer", "David"), ("reviewer", "Richard")):
        _agent(builtin / "agents" / role / f"{name}.md", name, "same")
        _agent(project / ".cafe" / "agents" / role / f"{name}.md", name, "same")
    preview = migrator.preview()
    decisions = {
        "agent:developer/David": changed_action,
        "agent:reviewer/Richard": "preserve",
    }

    with pytest.raises(OSError, match="injected interruption"):
        migrator.apply(preview.token, decisions)

    manifest = (
        project / ".cafe" / "migrations" / "agent-snapshots" / preview.token[:16] / "manifest.json"
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    completed = payload["items"][0]
    assert completed["state"] == "completed"
    if changed_action == "preserve":
        Path(completed["path"]).write_text("changed\n", encoding="utf-8")
    else:
        Path(completed["retired_path"]).unlink()

    with pytest.raises(StaleMigrationDecision):
        migrator.apply(preview.token, decisions)


@pytest.mark.parametrize(
    "replacement",
    ["token", "project_root", "source_path", "retired_path"],
)
def test_manifest_identity_replacement_fails_before_migration_mutation(
    tmp_path: Path, replacement: str
) -> None:
    def interrupt_before_retire(boundary: str, _entry_id: str | None) -> None:
        if boundary == "before_retire":
            raise OSError("injected interruption before retirement")

    project = tmp_path / "project"
    builtin = tmp_path / "builtin"
    source = _agent(
        project / ".cafe" / "agents" / "developer" / "David.md",
        "David",
        "same",
    )
    _agent(builtin / "agents" / "developer" / "David.md", "David", "same")
    resolver = CatalogResolver(
        project_root=project,
        canonical_root=project,
        global_root=tmp_path / "global",
        builtin_root=builtin,
    )
    interrupted = AgentSnapshotMigrator(
        resolver,
        is_tracked=lambda _path: False,
        failure_injector=interrupt_before_retire,
    )
    preview = interrupted.preview()
    decisions = {"agent:developer/David": "retire"}
    with pytest.raises(OSError):
        interrupted.apply(preview.token, decisions)

    manifest = (
        project / ".cafe" / "migrations" / "agent-snapshots" / preview.token[:16] / "manifest.json"
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    external_source = _agent(tmp_path / "unrelated" / "David.md", "David", "same")
    external_target = tmp_path / "outside" / "David.md"
    if replacement == "token":
        payload["token"] = "0" * 64
    elif replacement == "project_root":
        payload["project_root"] = str(tmp_path / "other-project")
    elif replacement == "source_path":
        payload["items"][0]["path"] = str(external_source)
    else:
        payload["items"][0]["retired_path"] = str(external_target)
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    resumed = AgentSnapshotMigrator(resolver, is_tracked=lambda _path: False)
    with pytest.raises(StaleMigrationDecision):
        resumed.apply(preview.token, decisions)
    assert source.is_file()
    assert external_source.is_file()
    assert not external_target.exists()


@pytest.mark.parametrize("unsafe_ancestor", ["transaction_root", "source", "retired"])
def test_migration_rejects_symlinked_ancestry_before_external_write(
    tmp_path: Path, unsafe_ancestor: str
) -> None:
    migrator, project, builtin = _migrator(tmp_path)
    source = _agent(
        project / ".cafe" / "agents" / "developer" / "David.md",
        "David",
        "same",
    )
    _agent(builtin / "agents" / "developer" / "David.md", "David", "same")
    preview = migrator.preview()
    transaction_root = project / ".cafe" / "migrations" / "agent-snapshots" / preview.token[:16]
    external = tmp_path / "external"
    external.mkdir()

    if unsafe_ancestor == "transaction_root":
        transaction_root.parent.mkdir(parents=True)
        transaction_root.symlink_to(external, target_is_directory=True)
    elif unsafe_ancestor == "source":
        external_source_parent = external / "developer"
        source.parent.rename(external_source_parent)
        source.parent.symlink_to(external_source_parent, target_is_directory=True)
    else:
        (transaction_root / "retired").mkdir(parents=True)
        (transaction_root / "retired" / "developer").symlink_to(external, target_is_directory=True)

    with pytest.raises(StaleMigrationDecision):
        migrator.apply(preview.token, {"agent:developer/David": "retire"})
    assert source.is_file()
    assert not (external / "David.md").exists()
    assert not (external / "manifest.json").exists()


@pytest.mark.parametrize("retargeted_ancestor", ["source", "retired"])
def test_migration_revalidates_ancestry_at_the_move_boundary(
    tmp_path: Path, retargeted_ancestor: str
) -> None:
    project = tmp_path / "project"
    builtin = tmp_path / "builtin"
    source = _agent(
        project / ".cafe" / "agents" / "developer" / "David.md",
        "David",
        "same",
    )
    _agent(builtin / "agents" / "developer" / "David.md", "David", "same")
    resolver = CatalogResolver(
        project_root=project,
        canonical_root=project,
        global_root=tmp_path / "global",
        builtin_root=builtin,
    )
    external = tmp_path / "external"
    external.mkdir()
    changed = False

    def retarget_before_move(boundary: str, _entry_id: str | None) -> None:
        nonlocal changed
        if boundary != "before_retire" or changed:
            return
        changed = True
        if retargeted_ancestor == "source":
            moved = external / "source-parent"
            source.parent.rename(moved)
            source.parent.symlink_to(moved, target_is_directory=True)
        else:
            destination_parent = (
                project
                / ".cafe"
                / "migrations"
                / "agent-snapshots"
                / preview.token[:16]
                / "retired"
                / "developer"
            )
            destination_parent.rename(external / "retired-parent")
            destination_parent.symlink_to(external, target_is_directory=True)

    migrator = AgentSnapshotMigrator(
        resolver,
        is_tracked=lambda _path: False,
        failure_injector=retarget_before_move,
    )
    preview = migrator.preview()

    with pytest.raises(StaleMigrationDecision):
        migrator.apply(preview.token, {"agent:developer/David": "retire"})
    assert source.is_file()
    assert not (external / "David.md").exists()
