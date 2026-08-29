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
from cafe.catalogs.resolver import CatalogResolver


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
    migrator, project, builtin = _migrator(
        tmp_path, tracked={".cafe/agents/reviewer/Richard.md"}
    )
    builtin_david = _agent(
        builtin / "agents" / "developer" / "David.md", "David", "fallback"
    )
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
    assert result.retired[0].read_text(encoding="utf-8") == fallback.read_text(
        encoding="utf-8"
    )
    assert intentional.is_file()
    assert result.manifest.is_file()


def test_changed_file_rejects_preview_token_without_modification(tmp_path: Path) -> None:
    migrator, project, builtin = _migrator(tmp_path)
    _agent(builtin / "agents" / "developer" / "David.md", "David", "same")
    snapshot = _agent(
        project / ".cafe" / "agents" / "developer" / "David.md", "David", "same"
    )
    preview = migrator.preview()
    snapshot.write_text(snapshot.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")

    with pytest.raises(StaleMigrationDecision):
        migrator.apply(preview.token, {"agent:developer/David": "retire"})
    assert snapshot.is_file()


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
        project
        / ".cafe"
        / "migrations"
        / "agent-snapshots"
        / preview.token[:16]
        / "manifest.json"
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["status"] == "in_progress"
    assert payload["items"][0]["state"] == "pending"


def test_apply_requires_a_decision_for_every_legacy_file(tmp_path: Path) -> None:
    migrator, project, _builtin = _migrator(tmp_path)
    _agent(project / ".cafe" / "agents" / "pm" / "Roger.md", "Roger", "custom")
    preview = migrator.preview()

    with pytest.raises(MigrationDecisionError):
        migrator.apply(preview.token, {})


def test_intentional_tracked_agent_cannot_be_retired(tmp_path: Path) -> None:
    migrator, project, _builtin = _migrator(
        tmp_path, tracked={".cafe/agents/reviewer/Richard.md"}
    )
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


def test_preserve_decision_is_shared_by_canonical_and_linked_project_views(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "canonical"
    linked = tmp_path / "linked"
    builtin = tmp_path / "builtin"
    global_root = tmp_path / "global"
    fallback = _agent(
        builtin / "agents" / "developer" / "David.md", "David", "same"
    )
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
    result = linked_migrator.apply(
        preview.token, {"agent:developer/David": "preserve"}
    )

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
    linked_result = linked_migrator.apply(
        linked_preview.token, {"agent:developer/David": "retire"}
    )

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
    _agent(project / ".cafe" / "agent-assets" / "David.md", "David", "custom")
    source = role_dir / "David.md"
    source.parent.mkdir(parents=True)
    relative_target = Path("../../agent-assets/David.md")
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
def test_resume_revalidates_completed_checkpoint_state(
    tmp_path: Path, changed_action: str
) -> None:
    interrupted = False

    def interrupt_before_second_checkpoint(
        boundary: str, entry_id: str | None
    ) -> None:
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
        project
        / ".cafe"
        / "migrations"
        / "agent-snapshots"
        / preview.token[:16]
        / "manifest.json"
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
