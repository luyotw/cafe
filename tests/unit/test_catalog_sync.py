"""U3/U4/U5: content-bound, transactional catalog publication."""

from pathlib import Path

import pytest

from cafe.catalogs.resolver import CatalogKind, CatalogResolver, content_digest
from cafe.catalogs.sync import CatalogSyncError, CatalogSyncService, StaleComparisonError


def _entry(root: Path, kind: CatalogKind, key: str, marker: str) -> Path:
    if kind is CatalogKind.PLAYBOOK:
        path = root / "playbooks" / f"{key}.yaml"
        content = f"playbook: {{id: {key}}}\nsteps: {{}}\nmarker: {marker}\n"
    elif kind is CatalogKind.PHASE:
        path = root / "skills" / key / "SKILL.md"
        content = f"---\nname: {key}\ndescription: {marker}\n---\n\n{marker}\n"
    else:
        role, name = key.split("/", 1)
        path = root / "agents" / role / f"{name}.md"
        content = f"---\nname: {name}\ndescription: {marker}\n---\n\n{marker}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path.parent if kind is CatalogKind.PHASE else path


def _service(tmp_path: Path, **kwargs) -> tuple[CatalogSyncService, Path, Path]:
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    resolver = CatalogResolver(
        project_root=project,
        canonical_root=project,
        global_root=global_root,
        builtin_root=tmp_path / "builtin",
    )
    return CatalogSyncService(resolver, **kwargs), project, global_root


def test_combined_comparison_is_content_bound_and_silent_without_project_entries(
    tmp_path: Path,
) -> None:
    service, project, global_root = _service(tmp_path)
    empty = service.compare()
    assert empty.status == "no_project_entries"
    _entry(project / ".cafe", CatalogKind.PLAYBOOK, "standard", "project")
    _entry(project / ".cafe", CatalogKind.PHASE, "develop", "project")
    _entry(project / ".cafe", CatalogKind.AGENT, "developer/David", "project")
    _entry(global_root, CatalogKind.PLAYBOOK, "standard", "global")

    report = service.compare()
    assert report.status == "differences"
    assert {item.entry_id for item in report.differences} == {
        "playbook:standard",
        "phase:develop",
        "agent:developer/David",
    }
    assert all(item.effective_source == "project" for item in report.entries)

    original_token = report.token
    _entry(global_root, CatalogKind.AGENT, "developer/David", "changed")
    assert service.compare().token != original_token


def test_digest_covers_file_mode_and_symlink_target(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    file_path = tree / "SKILL.md"
    file_path.parent.mkdir()
    file_path.write_text("content", encoding="utf-8")
    link = tree / "reference"
    link.symlink_to("SKILL.md")
    initial = content_digest(tree)
    file_path.chmod(0o744)
    mode_changed = content_digest(tree)
    link.unlink()
    link.symlink_to("missing")
    target_changed = content_digest(tree)
    assert len({initial, mode_changed, target_changed}) == 3


@pytest.mark.parametrize(
    "selected",
    [
        [],
        ["phase:develop", "phase:develop"],
        ["unknown:value"],
        ["phase:../escape"],
    ],
)
def test_selection_validation_fails_before_publication(
    tmp_path: Path, selected: list[str]
) -> None:
    service, project, global_root = _service(tmp_path)
    _entry(project / ".cafe", CatalogKind.PHASE, "develop", "project")
    report = service.compare()

    with pytest.raises(CatalogSyncError):
        service.sync(report.token, selected)
    assert not (global_root / "skills" / "develop").exists()


def test_stale_project_or_global_content_rejects_approved_write(tmp_path: Path) -> None:
    service, project, global_root = _service(tmp_path)
    source = _entry(project / ".cafe", CatalogKind.PLAYBOOK, "standard", "project")
    _entry(global_root, CatalogKind.PLAYBOOK, "standard", "global")
    report = service.compare()
    source.write_text(source.read_text(encoding="utf-8") + "changed: true\n", encoding="utf-8")

    with pytest.raises(StaleComparisonError):
        service.sync(report.token, ["playbook:standard"])
    assert "marker: global" in (
        global_root / "playbooks" / "standard.yaml"
    ).read_text(encoding="utf-8")


def test_selected_mixed_catalog_entries_publish_as_one_verified_operation(
    tmp_path: Path,
) -> None:
    service, project, global_root = _service(tmp_path)
    playbook = _entry(
        project / ".cafe", CatalogKind.PLAYBOOK, "standard", "project"
    )
    phase = _entry(project / ".cafe", CatalogKind.PHASE, "develop", "project")
    declined = _entry(project / ".cafe", CatalogKind.AGENT, "developer/David", "project")
    report = service.compare()

    result = service.sync(
        report.token, ["playbook:standard", "phase:develop"]
    )

    assert set(result.updated) == {"playbook:standard", "phase:develop"}
    assert content_digest(global_root / "playbooks" / "standard.yaml") == content_digest(
        playbook
    )
    assert content_digest(global_root / "skills" / "develop") == content_digest(phase)
    assert not (global_root / "agents" / "developer" / "David.md").exists()
    assert declined.is_file()


def test_late_mixed_catalog_failure_restores_every_global_entry(tmp_path: Path) -> None:
    def fail(boundary: str, entry_id: str | None) -> None:
        if boundary == "published" and entry_id == "phase:develop":
            raise OSError("injected publish failure")

    service, project, global_root = _service(tmp_path, failure_injector=fail)
    _entry(project / ".cafe", CatalogKind.PLAYBOOK, "standard", "new-playbook")
    _entry(project / ".cafe", CatalogKind.PHASE, "develop", "new-phase")
    old_playbook = _entry(global_root, CatalogKind.PLAYBOOK, "standard", "old-playbook")
    old_phase = _entry(global_root, CatalogKind.PHASE, "develop", "old-phase")
    expected = (content_digest(old_playbook), content_digest(old_phase))
    report = service.compare()

    with pytest.raises(CatalogSyncError, match="recovery receipt"):
        service.sync(report.token, ["playbook:standard", "phase:develop"])

    assert content_digest(global_root / "playbooks" / "standard.yaml") == expected[0]
    assert content_digest(global_root / "skills" / "develop") == expected[1]
    receipts = list((global_root / ".catalog-transactions").glob("*/recovery.json"))
    assert len(receipts) == 1
    assert '"status": "rolled_back"' in receipts[0].read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("boundary", "failed_entry"),
    [
        ("stage", "playbook:standard"),
        ("pre_publish", None),
        ("published", "playbook:standard"),
        ("published", "phase:develop"),
        ("post_check", None),
    ],
)
def test_every_transaction_boundary_rolls_back_the_complete_selection(
    tmp_path: Path, boundary: str, failed_entry: str | None
) -> None:
    def fail(actual_boundary: str, entry_id: str | None) -> None:
        if actual_boundary == boundary and entry_id == failed_entry:
            raise OSError("injected boundary failure")

    service, project, global_root = _service(tmp_path, failure_injector=fail)
    _entry(project / ".cafe", CatalogKind.PLAYBOOK, "standard", "new-playbook")
    _entry(project / ".cafe", CatalogKind.PHASE, "develop", "new-phase")
    old_playbook = _entry(global_root, CatalogKind.PLAYBOOK, "standard", "old-playbook")
    old_phase = _entry(global_root, CatalogKind.PHASE, "develop", "old-phase")
    expected = (content_digest(old_playbook), content_digest(old_phase))
    report = service.compare()

    with pytest.raises(CatalogSyncError):
        service.sync(report.token, ["playbook:standard", "phase:develop"])

    assert content_digest(global_root / "playbooks" / "standard.yaml") == expected[0]
    assert content_digest(global_root / "skills" / "develop") == expected[1]


def test_rollback_failure_retains_bounded_recovery_evidence(tmp_path: Path) -> None:
    def fail(boundary: str, entry_id: str | None) -> None:
        if boundary == "published" and entry_id == "phase:develop":
            raise OSError("trigger rollback")
        if boundary == "rollback_restore" and entry_id == "playbook:standard":
            raise OSError("injected restore failure")

    service, project, global_root = _service(tmp_path, failure_injector=fail)
    _entry(project / ".cafe", CatalogKind.PLAYBOOK, "standard", "new-playbook")
    _entry(project / ".cafe", CatalogKind.PHASE, "develop", "new-phase")
    _entry(global_root, CatalogKind.PLAYBOOK, "standard", "old-playbook")
    _entry(global_root, CatalogKind.PHASE, "develop", "old-phase")
    report = service.compare()

    with pytest.raises(CatalogSyncError):
        service.sync(report.token, ["playbook:standard", "phase:develop"])

    receipt = next((global_root / ".catalog-transactions").glob("*/recovery.json"))
    evidence = receipt.read_text(encoding="utf-8")
    assert '"status": "incomplete"' in evidence
    assert '"backup_root"' in evidence
