"""U6: conservative legacy-agent migration invariants."""

from pathlib import Path

import pytest

from cafe.catalogs.migration import (
    AgentSnapshotMigrator,
    MigrationDecisionError,
    StaleMigrationDecision,
)
from cafe.catalogs.resolver import CatalogKind, CatalogResolver


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


def test_apply_requires_a_decision_for_every_legacy_file(tmp_path: Path) -> None:
    migrator, project, _builtin = _migrator(tmp_path)
    _agent(project / ".cafe" / "agents" / "pm" / "Roger.md", "Roger", "custom")
    preview = migrator.preview()

    with pytest.raises(MigrationDecisionError):
        migrator.apply(preview.token, {})
