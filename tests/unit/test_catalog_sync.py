"""U3/U4/U5: content-bound, transactional catalog publication."""

import json
import subprocess
import sys
from pathlib import Path
from threading import Event, Thread

import pytest

from cafe.agents.manager import AgentManager
from cafe.catalogs.migration import AgentSnapshotMigrator
from cafe.catalogs.resolver import (
    CatalogKind,
    CatalogResolver,
    CatalogValidationError,
    content_digest,
)
from cafe.catalogs.sync import CatalogSyncError, CatalogSyncService, StaleComparisonError
from cafe.catalogs.transactions import CatalogRecoveryError
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader


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


def _preserve_legacy_agents(service: CatalogSyncService) -> None:
    migrator = AgentSnapshotMigrator(service.resolver, is_tracked=lambda _path: False)
    preview = migrator.preview()
    migrator.apply(
        preview.token,
        {item.entry_id: "preserve" for item in preview.items},
    )


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
    _preserve_legacy_agents(service)

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


def test_effective_digests_cover_global_and_builtin_only_entries(tmp_path: Path) -> None:
    service, _project, global_root = _service(tmp_path)
    builtin_root = service.resolver.builtin_root
    _entry(builtin_root, CatalogKind.PLAYBOOK, "standard", "builtin")
    _entry(global_root, CatalogKind.PHASE, "develop", "global")
    _entry(builtin_root, CatalogKind.AGENT, "developer/David", "builtin")

    before = service.compare()

    assert before.status == "no_project_entries"
    assert set(before.effective_digests) == {"playbook", "phase", "agent"}
    assert all(len(digest) == 64 for digest in before.effective_digests.values())

    _entry(global_root, CatalogKind.PHASE, "develop", "changed-global")
    after = service.compare()
    assert after.effective_digests["phase"] != before.effective_digests["phase"]
    assert after.token != before.token


def test_generated_agent_snapshot_is_not_a_publication_candidate(tmp_path: Path) -> None:
    service, project, _global_root = _service(tmp_path)
    builtin = _entry(
        service.resolver.builtin_root,
        CatalogKind.AGENT,
        "developer/David",
        "fallback",
    )
    snapshot = _entry(
        project / ".cafe",
        CatalogKind.AGENT,
        "developer/David",
        "fallback",
    )
    assert content_digest(snapshot) == content_digest(builtin)

    report = service.compare()

    assert report.status == "no_project_entries"
    assert report.entries == ()

    migrator = AgentSnapshotMigrator(service.resolver, is_tracked=lambda _path: False)
    preview = migrator.preview()
    migrator.apply(preview.token, {"agent:developer/David": "preserve"})

    assert [item.entry_id for item in service.compare().entries] == ["agent:developer/David"]


def test_ambiguous_agent_requires_migration_decision_before_publication(
    tmp_path: Path,
) -> None:
    service, project, global_root = _service(tmp_path)
    _entry(
        service.resolver.builtin_root,
        CatalogKind.AGENT,
        "developer/David",
        "fallback",
    )
    project_agent = _entry(
        project / ".cafe",
        CatalogKind.AGENT,
        "developer/David",
        "ambiguous project",
    )
    migrator = AgentSnapshotMigrator(service.resolver, is_tracked=lambda _path: False)

    preview = migrator.preview()
    assert [(item.entry_id, item.status) for item in preview.items] == [
        ("agent:developer/David", "ambiguous")
    ]
    assert migrator.publication_blocked_entry_ids() == {"agent:developer/David"}

    blocked_report = service.compare()
    with pytest.raises(CatalogSyncError):
        service.sync(blocked_report.token, ["agent:developer/David"])
    assert not (global_root / "agents" / "developer" / "David.md").exists()

    migrator.apply(preview.token, {"agent:developer/David": "preserve"})
    approved_report = service.compare()
    result = service.sync(approved_report.token, ["agent:developer/David"])

    assert result.updated == ("agent:developer/David",)
    assert content_digest(global_root / "agents" / "developer" / "David.md") == content_digest(
        project_agent
    )
    assert migrator.preview().items == ()


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


@pytest.mark.parametrize("target_change", ["content", "mode"])
def test_root_symlink_digest_binds_confined_target_behavior(
    tmp_path: Path, target_change: str
) -> None:
    service, project, global_root = _service(tmp_path)
    playbooks = project / ".cafe" / "playbooks"
    target = playbooks / "standard.data"
    target.parent.mkdir(parents=True)
    target.write_text("playbook: {id: standard}\nsteps: {}\n", encoding="utf-8")
    (playbooks / "standard.yaml").symlink_to(target.name)
    global_playbook = _entry(global_root, CatalogKind.PLAYBOOK, "standard", "global")
    approved = service.compare()
    before = content_digest(global_playbook)

    if target_change == "content":
        target.write_text(
            "playbook: {id: standard}\nsteps: {}\nchanged: true\n",
            encoding="utf-8",
        )
    else:
        target.chmod(0o744)

    with pytest.raises(StaleComparisonError):
        service.sync(approved.token, ["playbook:standard"])
    assert content_digest(global_playbook) == before


@pytest.mark.parametrize("link_level", ["entry", "nested"])
def test_catalog_comparison_rejects_symlink_targets_outside_entry_authority(
    tmp_path: Path, link_level: str
) -> None:
    service, project, _global_root = _service(tmp_path)
    external = tmp_path / "external"
    if link_level == "entry":
        external.write_text("playbook: {id: standard}\nsteps: {}\n", encoding="utf-8")
        link = project / ".cafe" / "playbooks" / "standard.yaml"
        link.parent.mkdir(parents=True)
        link.symlink_to(external)
    else:
        skill = _entry(project / ".cafe", CatalogKind.PHASE, "develop", "project")
        external.write_text("outside\n", encoding="utf-8")
        (skill / "policy.md").symlink_to(external)

    with pytest.raises(CatalogValidationError):
        service.compare()


@pytest.mark.parametrize(
    ("limit", "value"),
    [("max_nodes", 1), ("max_bytes", 3), ("max_depth", 0)],
)
def test_catalog_digest_rejects_work_beyond_explicit_bounds(
    tmp_path: Path, limit: str, value: int
) -> None:
    tree = tmp_path / "tree"
    nested = tree / "nested"
    nested.mkdir(parents=True)
    (nested / "content").write_text("1234", encoding="utf-8")

    with pytest.raises(CatalogValidationError):
        content_digest(tree, **{limit: value})


@pytest.mark.parametrize("target_change", ["content", "mode"])
def test_skill_symlink_target_change_invalidates_publication_approval(
    tmp_path: Path, target_change: str
) -> None:
    service, project, global_root = _service(tmp_path)
    project_skill = _entry(project / ".cafe", CatalogKind.PHASE, "develop", "project")
    global_skill = _entry(global_root, CatalogKind.PHASE, "develop", "global")
    target = project_skill / "policy.data"
    target.write_text("approved\n", encoding="utf-8")
    (project_skill / "policy.md").symlink_to(target.name)
    report = service.compare()
    before = content_digest(global_skill)

    if target_change == "content":
        target.write_text("replaced\n", encoding="utf-8")
    else:
        target.chmod(0o744)

    with pytest.raises(StaleComparisonError):
        service.sync(report.token, ["phase:develop"])
    assert content_digest(global_skill) == before


@pytest.mark.parametrize(
    "selected",
    [
        [],
        ["phase:develop", "phase:develop"],
        ["unknown:value"],
        ["phase:../escape"],
    ],
)
def test_selection_validation_fails_before_publication(tmp_path: Path, selected: list[str]) -> None:
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
    assert "marker: global" in (global_root / "playbooks" / "standard.yaml").read_text(
        encoding="utf-8"
    )


def test_selected_mixed_catalog_entries_publish_as_one_verified_operation(
    tmp_path: Path,
) -> None:
    service, project, global_root = _service(tmp_path)
    playbook = _entry(project / ".cafe", CatalogKind.PLAYBOOK, "standard", "project")
    phase = _entry(project / ".cafe", CatalogKind.PHASE, "develop", "project")
    declined = _entry(project / ".cafe", CatalogKind.AGENT, "developer/David", "project")
    report = service.compare()

    result = service.sync(report.token, ["playbook:standard", "phase:develop"])

    assert set(result.updated) == {"playbook:standard", "phase:develop"}
    assert content_digest(global_root / "playbooks" / "standard.yaml") == content_digest(playbook)
    assert content_digest(global_root / "skills" / "develop") == content_digest(phase)
    assert not (global_root / "agents" / "developer" / "David.md").exists()
    assert declined.is_file()


def test_catalog_readers_do_not_observe_an_in_progress_multi_entry_publish(
    tmp_path: Path,
) -> None:
    first_published = Event()
    allow_completion = Event()

    def pause_after_first_publish(boundary: str, entry_id: str | None) -> None:
        if boundary == "published" and entry_id == "playbook:standard":
            first_published.set()
            assert allow_completion.wait(timeout=5)

    service, project, global_root = _service(tmp_path, failure_injector=pause_after_first_publish)
    _entry(project / ".cafe", CatalogKind.PLAYBOOK, "standard", "new-playbook")
    _entry(project / ".cafe", CatalogKind.PHASE, "develop", "new-phase")
    _entry(global_root, CatalogKind.PLAYBOOK, "standard", "old-playbook")
    _entry(global_root, CatalogKind.PHASE, "develop", "old-phase")
    report = service.compare()
    reader = CatalogResolver(
        project_root=tmp_path / "reader-project",
        canonical_root=tmp_path / "reader-project",
        global_root=global_root,
        builtin_root=tmp_path / "reader-builtin",
    )
    publish_errors: list[BaseException] = []
    observed: list[str] = []

    def publish() -> None:
        try:
            service.sync(report.token, ["playbook:standard", "phase:develop"])
        except BaseException as exc:  # pragma: no cover - asserted below
            publish_errors.append(exc)

    def read_catalogs() -> None:
        entries = reader.entries([CatalogKind.PLAYBOOK, CatalogKind.PHASE])
        observed.extend(
            (entry.path / "SKILL.md" if entry.kind is CatalogKind.PHASE else entry.path).read_text(
                encoding="utf-8"
            )
            for entry in entries
        )

    publisher = Thread(target=publish)
    publisher.start()
    assert first_published.wait(timeout=5)
    catalog_reader = Thread(target=read_catalogs)
    catalog_reader.start()
    assert catalog_reader.is_alive()

    allow_completion.set()
    publisher.join(timeout=5)
    catalog_reader.join(timeout=5)

    assert not publisher.is_alive()
    assert not catalog_reader.is_alive()
    assert publish_errors == []
    assert any("new-playbook" in content for content in observed)
    assert any("new-phase" in content for content in observed)
    assert all("old-" not in content for content in observed)


def test_keyboard_interrupt_after_first_publish_restores_the_complete_selection(
    tmp_path: Path,
) -> None:
    def interrupt(boundary: str, entry_id: str | None) -> None:
        if boundary == "published" and entry_id == "playbook:standard":
            raise KeyboardInterrupt

    service, project, global_root = _service(tmp_path, failure_injector=interrupt)
    _entry(project / ".cafe", CatalogKind.PLAYBOOK, "standard", "new-playbook")
    _entry(project / ".cafe", CatalogKind.PHASE, "develop", "new-phase")
    old_playbook = _entry(global_root, CatalogKind.PLAYBOOK, "standard", "old-playbook")
    old_phase = _entry(global_root, CatalogKind.PHASE, "develop", "old-phase")
    expected = (content_digest(old_playbook), content_digest(old_phase))
    report = service.compare()

    with pytest.raises(KeyboardInterrupt):
        service.sync(report.token, ["playbook:standard", "phase:develop"])

    assert content_digest(global_root / "playbooks" / "standard.yaml") == expected[0]
    assert content_digest(global_root / "skills" / "develop") == expected[1]


def test_restart_recovers_a_crashed_multi_entry_publish_before_catalog_read(
    tmp_path: Path,
) -> None:
    service, project, global_root = _service(tmp_path)
    _entry(project / ".cafe", CatalogKind.PLAYBOOK, "standard", "new-playbook")
    _entry(project / ".cafe", CatalogKind.PHASE, "develop", "new-phase")
    old_playbook = _entry(global_root, CatalogKind.PLAYBOOK, "standard", "old-playbook")
    old_phase = _entry(global_root, CatalogKind.PHASE, "develop", "old-phase")
    expected = (content_digest(old_playbook), content_digest(old_phase))
    report = service.compare()
    crash_script = """
import os
import sys
from pathlib import Path

from cafe.catalogs.resolver import CatalogResolver
from cafe.catalogs.sync import CatalogSyncService

project = Path(sys.argv[1])
global_root = Path(sys.argv[2])
builtin = Path(sys.argv[3])
token = sys.argv[4]

def crash(boundary, entry_id):
    if boundary == "published" and entry_id == "playbook:standard":
        os._exit(86)

resolver = CatalogResolver(
    project_root=project,
    canonical_root=project,
    global_root=global_root,
    builtin_root=builtin,
)
CatalogSyncService(resolver, failure_injector=crash).sync(
    token, ["playbook:standard", "phase:develop"]
)
"""

    crashed = subprocess.run(
        [
            sys.executable,
            "-c",
            crash_script,
            str(project),
            str(global_root),
            str(tmp_path / "builtin"),
            report.token,
        ],
        check=False,
    )
    assert crashed.returncode == 86

    reader_project = tmp_path / "reader-project"
    reader = CatalogResolver(
        project_root=reader_project,
        canonical_root=reader_project,
        global_root=global_root,
        builtin_root=tmp_path / "reader-builtin",
    )
    observed = {
        entry.entry_id: entry.digest
        for entry in reader.entries([CatalogKind.PLAYBOOK, CatalogKind.PHASE])
    }

    assert observed == {
        "playbook:standard": expected[0],
        "phase:develop": expected[1],
    }
    receipts = list((global_root / ".catalog-transactions").glob("*/recovery.json"))
    assert len(receipts) == 1
    evidence = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert evidence["status"] == "rolled_back"
    assert evidence["selected"] == ["playbook:standard", "phase:develop"]
    assert evidence["rollback_errors"] == []


def test_catalog_reader_fails_closed_for_an_unjournaled_orphan_transaction(
    tmp_path: Path,
) -> None:
    global_root = tmp_path / "global"
    _entry(global_root, CatalogKind.PLAYBOOK, "standard", "possibly-mixed")
    orphan = global_root / ".catalog-transactions" / "legacy-orphan"
    orphan.mkdir(parents=True)
    reader = CatalogResolver(
        project_root=tmp_path / "reader-project",
        canonical_root=tmp_path / "reader-project",
        global_root=global_root,
        builtin_root=tmp_path / "builtin",
    )

    with pytest.raises(CatalogRecoveryError):
        reader.resolve(CatalogKind.PLAYBOOK, "standard")

    evidence = json.loads((orphan / "recovery.json").read_text(encoding="utf-8"))
    assert evidence["status"] == "incomplete"
    assert evidence["selected"] == []
    assert len(evidence["rollback_errors"]) == 1


def _write_recovery_journal(
    transaction: Path,
    record: dict[str, str],
    **payload_updates: object,
) -> None:
    (transaction / "backups").mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "schema_version": 1,
        "status": "publishing",
        "records": [record],
    }
    payload.update(payload_updates)
    (transaction / "transaction.json").write_text(json.dumps(payload), encoding="utf-8")


def _recovery_reader(tmp_path: Path, global_root: Path) -> CatalogResolver:
    return CatalogResolver(
        project_root=tmp_path / "reader-project",
        canonical_root=tmp_path / "reader-project",
        global_root=global_root,
        builtin_root=tmp_path / "reader-builtin",
    )


def test_catalog_reader_finishes_cleanup_for_committed_transaction_without_backups(
    tmp_path: Path,
) -> None:
    global_root = tmp_path / "global"
    target = _entry(global_root, CatalogKind.PLAYBOOK, "standard", "approved-old")
    old_digest = content_digest(target)
    target = _entry(global_root, CatalogKind.PLAYBOOK, "standard", "approved-new")
    new_digest = content_digest(target)
    transaction = global_root / ".catalog-transactions" / "committed"
    _write_recovery_journal(
        transaction,
        {
            "entry_id": "playbook:standard",
            "relative_path": "playbooks/standard.yaml",
            "old_digest": old_digest,
            "new_digest": new_digest,
            "state": "published",
        },
        status="committed",
    )
    (transaction / "backups").rmdir()

    resolved = _recovery_reader(tmp_path, global_root).resolve(CatalogKind.PLAYBOOK, "standard")

    assert resolved is not None
    assert resolved.digest == new_digest
    assert not transaction.exists()


def test_interrupted_committed_cleanup_does_not_block_catalog_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, project, global_root = _service(tmp_path)
    project_entry = _entry(project / ".cafe", CatalogKind.PLAYBOOK, "standard", "approved-new")
    _entry(global_root, CatalogKind.PLAYBOOK, "standard", "approved-old")
    report = service.compare()

    def interrupt_cleanup(path: Path) -> None:
        (path / "transaction.json").unlink()
        raise KeyboardInterrupt

    monkeypatch.setattr("cafe.catalogs.transactions.shutil.rmtree", interrupt_cleanup)

    with pytest.raises(KeyboardInterrupt):
        service.sync(report.token, ["playbook:standard"])
    monkeypatch.undo()

    resolved = _recovery_reader(tmp_path, global_root).resolve(CatalogKind.PLAYBOOK, "standard")
    assert resolved is not None
    assert resolved.digest == content_digest(project_entry)


@pytest.mark.parametrize(
    "record_update",
    [
        {"entry_id": "phase:develop"},
        {"relative_path": "playbooks"},
        {"new_digest": "not-a-content-digest"},
        {"new_digest": "0" * 64},
        {"state": "committed"},
    ],
    ids=[
        "entry-path-mismatch",
        "whole-catalog-path",
        "digest",
        "published-content-mismatch",
        "state",
    ],
)
def test_catalog_reader_rejects_unsafe_recovery_records_before_mutation(
    tmp_path: Path, record_update: dict[str, str]
) -> None:
    global_root = tmp_path / "global"
    target = _entry(global_root, CatalogKind.PLAYBOOK, "standard", "outside-authority")
    original_digest = content_digest(target)
    record = {
        "entry_id": "playbook:standard",
        "relative_path": "playbooks/standard.yaml",
        "old_digest": "missing",
        "new_digest": original_digest,
        "state": "published",
    }
    record.update(record_update)
    _write_recovery_journal(global_root / ".catalog-transactions" / "hostile-record", record)

    with pytest.raises(CatalogRecoveryError):
        _recovery_reader(tmp_path, global_root).resolve(CatalogKind.PLAYBOOK, "standard")

    assert content_digest(target) == original_digest


@pytest.mark.parametrize(
    "payload_update",
    [
        {"schema_version": 2},
        {"status": "attacker-controlled"},
        {"records": []},
    ],
    ids=["schema", "status", "empty-records"],
)
def test_catalog_reader_rejects_unsafe_recovery_journals_before_mutation(
    tmp_path: Path, payload_update: dict[str, object]
) -> None:
    global_root = tmp_path / "global"
    target = _entry(global_root, CatalogKind.PLAYBOOK, "standard", "outside-authority")
    original_digest = content_digest(target)
    record = {
        "entry_id": "playbook:standard",
        "relative_path": "playbooks/standard.yaml",
        "old_digest": "missing",
        "new_digest": original_digest,
        "state": "published",
    }
    _write_recovery_journal(
        global_root / ".catalog-transactions" / "hostile-journal",
        record,
        **payload_update,
    )

    with pytest.raises(CatalogRecoveryError):
        _recovery_reader(tmp_path, global_root).resolve(CatalogKind.PLAYBOOK, "standard")

    assert content_digest(target) == original_digest


@pytest.mark.parametrize("symlink_level", ["transactions-root", "transaction", "journal"])
def test_catalog_reader_rejects_symlinked_transaction_ancestry_before_mutation(
    tmp_path: Path, symlink_level: str
) -> None:
    global_root = tmp_path / "global"
    target = _entry(global_root, CatalogKind.PLAYBOOK, "standard", "outside-authority")
    original_digest = content_digest(target)
    external_transaction = tmp_path / "external-transactions" / "hostile"
    _write_recovery_journal(
        external_transaction,
        {
            "entry_id": "playbook:standard",
            "relative_path": "playbooks/standard.yaml",
            "old_digest": "missing",
            "new_digest": original_digest,
            "state": "published",
        },
    )
    transactions_root = global_root / ".catalog-transactions"
    if symlink_level == "transactions-root":
        transactions_root.symlink_to(external_transaction.parent, target_is_directory=True)
    else:
        transactions_root.mkdir()
        transaction = transactions_root / "hostile"
        if symlink_level == "transaction":
            transaction.symlink_to(external_transaction, target_is_directory=True)
        else:
            (transaction / "backups").mkdir(parents=True)
            (transaction / "transaction.json").symlink_to(external_transaction / "transaction.json")

    with pytest.raises(CatalogRecoveryError):
        _recovery_reader(tmp_path, global_root).resolve(CatalogKind.PLAYBOOK, "standard")

    assert content_digest(target) == original_digest


def test_catalog_reader_rejects_symlinked_target_ancestor_before_mutation(
    tmp_path: Path,
) -> None:
    global_root = tmp_path / "global"
    global_root.mkdir()
    external_root = tmp_path / "external-target"
    target = _entry(
        external_root,
        CatalogKind.PLAYBOOK,
        "standard",
        "outside-authority",
    )
    (global_root / "playbooks").symlink_to(target.parent, target_is_directory=True)
    original_digest = content_digest(target)
    _write_recovery_journal(
        global_root / ".catalog-transactions" / "hostile-target",
        {
            "entry_id": "playbook:standard",
            "relative_path": "playbooks/standard.yaml",
            "old_digest": "missing",
            "new_digest": original_digest,
            "state": "published",
        },
    )

    with pytest.raises(CatalogRecoveryError):
        _recovery_reader(tmp_path, global_root).resolve(CatalogKind.PLAYBOOK, "standard")

    assert content_digest(target) == original_digest


def test_catalog_reader_rejects_symlinked_backup_ancestor_before_mutation(
    tmp_path: Path,
) -> None:
    global_root = tmp_path / "global"
    target = _entry(global_root, CatalogKind.PLAYBOOK, "standard", "published")
    published_digest = content_digest(target)
    external_root = tmp_path / "external-backup"
    backup = _entry(external_root, CatalogKind.PLAYBOOK, "standard", "approved-old")
    old_digest = content_digest(backup)
    transaction = global_root / ".catalog-transactions" / "hostile-backup"
    backup_root = transaction / "backups"
    backup_root.mkdir(parents=True)
    (backup_root / "playbooks").symlink_to(backup.parent, target_is_directory=True)
    _write_recovery_journal(
        transaction,
        {
            "entry_id": "playbook:standard",
            "relative_path": "playbooks/standard.yaml",
            "old_digest": old_digest,
            "new_digest": published_digest,
            "state": "published",
        },
    )

    with pytest.raises(CatalogRecoveryError):
        _recovery_reader(tmp_path, global_root).resolve(CatalogKind.PLAYBOOK, "standard")

    assert content_digest(target) == published_digest
    assert content_digest(backup) == old_digest


def test_production_loaders_hold_the_catalog_lock_through_content_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_published = Event()
    allow_completion = Event()

    def pause_after_first_publish(boundary: str, entry_id: str | None) -> None:
        if boundary == "published" and entry_id == "playbook:standard":
            first_published.set()
            assert allow_completion.wait(timeout=5)

    service, project, global_root = _service(tmp_path, failure_injector=pause_after_first_publish)

    def write_playbook(root: Path, role: str) -> None:
        path = root / "playbooks" / "standard.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "playbook: {id: standard}\n"
            "steps:\n"
            "  develop:\n"
            f"    role: {role}\n"
            "    skill: develop\n"
            "    on: {await_agent: _done}\n",
            encoding="utf-8",
        )

    write_playbook(project / ".cafe", "developer")
    _entry(project / ".cafe", CatalogKind.PHASE, "develop", "new-phase")
    _entry(project / ".cafe", CatalogKind.AGENT, "developer/David", "new-agent")
    write_playbook(global_root, "reviewer")
    _entry(global_root, CatalogKind.PHASE, "develop", "old-phase")
    _entry(global_root, CatalogKind.AGENT, "developer/David", "old-agent")
    _preserve_legacy_agents(service)
    report = service.compare()
    selected = [
        "playbook:standard",
        "phase:develop",
        "agent:developer/David",
    ]
    reader_project = tmp_path / "reader-project"
    cached_skills = SkillLoader(
        project_root=reader_project,
        global_root=global_root,
        builtin_root=tmp_path / "reader-builtin",
    )
    cached_skills.discover()
    monkeypatch.setattr("cafe.utils.config.get_global_cafe_dir", lambda: global_root)
    observed: dict[str, object] = {}
    errors: list[BaseException] = []
    publish_errors: list[BaseException] = []

    def capture(name: str, reader) -> None:
        try:
            observed[name] = reader()
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    def publish() -> None:
        try:
            service.sync(report.token, selected)
        except BaseException as exc:  # pragma: no cover - asserted below
            publish_errors.append(exc)

    publisher = Thread(target=publish)
    publisher.start()
    assert first_published.wait(timeout=5)

    readers = [
        Thread(target=capture, args=("cached", lambda: cached_skills.activate("develop"))),
        Thread(
            target=capture,
            args=(
                "fresh",
                lambda: next(
                    entry.description
                    for entry in SkillLoader(
                        project_root=reader_project,
                        global_root=global_root,
                        builtin_root=tmp_path / "reader-builtin",
                    ).discover()
                    if entry.name == "develop"
                ),
            ),
        ),
        Thread(
            target=capture,
            args=(
                "playbook",
                lambda: (
                    PlaybookLoader(
                        project_root=reader_project,
                        global_root=global_root,
                        builtin_root=tmp_path / "reader-builtin",
                    )
                    .load_model("standard")
                    .model.steps["develop"]
                    .role
                ),
            ),
        ),
        Thread(
            target=capture,
            args=(
                "agent",
                lambda: AgentManager.read_agent_file(
                    "David", "developer", str(reader_project / ".cafe")
                )[1],
            ),
        ),
    ]
    for reader in readers:
        reader.start()
    for reader in readers:
        reader.join(timeout=0.1)
        assert reader.is_alive()

    allow_completion.set()
    publisher.join(timeout=5)
    for reader in readers:
        reader.join(timeout=5)

    assert not publisher.is_alive()
    assert all(not reader.is_alive() for reader in readers)
    assert publish_errors == []
    assert errors == []
    assert "new-phase" in str(observed["cached"])
    assert observed["fresh"] == "new-phase"
    assert observed["playbook"] == "developer"
    assert "new-agent" in str(observed["agent"])


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
