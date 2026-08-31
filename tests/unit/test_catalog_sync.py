"""U3/U4/U5: content-bound, transactional catalog publication."""

import json
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest

import cafe.catalogs.resolver as resolver_module
import cafe.catalogs.sync as sync_module
import cafe.catalogs.transactions as transactions_module
from cafe.agents.manager import AgentManager
from cafe.catalogs.resolver import (
    MAX_CATALOG_DISCOVERY_ENTRIES,
    MAX_CATALOG_OPERATION_ENTRIES,
    CatalogKind,
    CatalogOperationLimitError,
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


def test_combined_comparison_rejects_an_operation_over_the_shared_entry_limit(
    tmp_path: Path,
) -> None:
    service, project, global_root = _service(tmp_path)
    for index in range(MAX_CATALOG_OPERATION_ENTRIES + 1):
        _entry(
            project / ".cafe",
            CatalogKind.PHASE,
            f"phase-{index:03d}",
            "project",
        )

    with pytest.raises(CatalogOperationLimitError) as raised:
        service.compare(kinds=[CatalogKind.PHASE])

    assert raised.value.limit == MAX_CATALOG_OPERATION_ENTRIES
    discovery = service.discover_over_budget(kinds=[CatalogKind.PHASE])
    assert discovery.discovery_complete is True
    assert discovery.discovery_entry_limit == MAX_CATALOG_DISCOVERY_ENTRIES
    assert len(discovery.affected_entry_ids) == MAX_CATALOG_OPERATION_ENTRIES + 1
    assert discovery.affected_entry_ids[0] == "phase:phase-000"
    assert discovery.affected_entry_ids[-1] == "phase:phase-512"

    selected = ["phase:phase-512"]
    exact = service.compare(
        kinds=[CatalogKind.PHASE],
        entry_ids=selected,
    )
    _entry(project / ".cafe", CatalogKind.PHASE, "phase-000a", "inserted")
    refreshed = service.compare(kinds=[CatalogKind.PHASE], entry_ids=selected)
    assert refreshed.token != exact.token
    assert not (global_root / ".catalog-transactions").exists()


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


def test_existing_agent_matching_builtin_is_an_immediate_publication_candidate(
    tmp_path: Path,
) -> None:
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

    assert report.status == "differences"
    assert [item.entry_id for item in report.entries] == ["agent:developer/David"]
    assert report.entries[0].effective_source == "project"


def test_existing_custom_agent_publishes_without_prerequisite_state(
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
    report = service.compare()
    result = service.sync(report.token, ["agent:developer/David"])

    assert result.updated == ("agent:developer/David",)
    assert content_digest(global_root / "agents" / "developer" / "David.md") == content_digest(
        project_agent
    )


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


def test_approved_root_symlink_publishes_self_contained_global_content(
    tmp_path: Path,
) -> None:
    service, project, global_root = _service(tmp_path)
    playbooks = project / ".cafe" / "playbooks"
    target = playbooks / "standard.data"
    target.parent.mkdir(parents=True)
    target.write_text("playbook: {id: standard}\nsteps: {}\n", encoding="utf-8")
    project_entry = playbooks / "standard.yaml"
    project_entry.symlink_to(target.name)
    approved = service.compare()

    result = service.sync(approved.token, ["playbook:standard"])
    project_entry.unlink()
    target.unlink()
    global_entry = CatalogResolver(
        project_root=tmp_path / "other-project",
        canonical_root=tmp_path / "other-project",
        global_root=global_root,
        builtin_root=tmp_path / "other-builtin",
    ).resolve(CatalogKind.PLAYBOOK, "standard")

    assert result.updated == ("playbook:standard",)
    assert result.comparison.status == "identical"
    assert global_entry.source == "global"
    assert global_entry.digest == approved.differences[0].project_digest


def test_publication_replaces_supported_global_root_symlink(
    tmp_path: Path,
) -> None:
    service, project, global_root = _service(tmp_path)
    project_entry = _entry(
        project / ".cafe",
        CatalogKind.PLAYBOOK,
        "standard",
        "approved-new",
    )
    global_playbooks = global_root / "playbooks"
    global_target = global_playbooks / "standard.data"
    global_target.parent.mkdir(parents=True)
    global_target.write_text(
        "playbook: {id: standard}\nsteps: {}\nmarker: approved-old\n",
        encoding="utf-8",
    )
    global_entry = global_playbooks / "standard.yaml"
    global_entry.symlink_to(global_target.name)
    approved = service.compare()
    approved_new_digest = content_digest(project_entry)

    result = service.sync(approved.token, ["playbook:standard"])
    resolved = CatalogResolver(
        project_root=tmp_path / "other-project",
        canonical_root=tmp_path / "other-project",
        global_root=global_root,
        builtin_root=tmp_path / "other-builtin",
    ).resolve(CatalogKind.PLAYBOOK, "standard")

    assert result.updated == ("playbook:standard",)
    assert result.comparison.status == "identical"
    assert not global_entry.is_symlink()
    assert content_digest(global_entry) == approved_new_digest
    assert resolved is not None
    assert resolved.source == "global"
    assert resolved.digest == approved_new_digest


def test_publication_restores_supported_global_root_symlink_after_failure(
    tmp_path: Path,
) -> None:
    def fail_after_backup(boundary: str, entry_id: str | None) -> None:
        if boundary == "backed_up" and entry_id == "playbook:standard":
            raise RuntimeError("injected failure after backup")

    service, project, global_root = _service(
        tmp_path,
        failure_injector=fail_after_backup,
    )
    _entry(project / ".cafe", CatalogKind.PLAYBOOK, "standard", "approved-new")
    global_playbooks = global_root / "playbooks"
    global_target = global_playbooks / "standard.data"
    global_target.parent.mkdir(parents=True)
    global_target.write_text(
        "playbook: {id: standard}\nsteps: {}\nmarker: approved-old\n",
        encoding="utf-8",
    )
    global_entry = global_playbooks / "standard.yaml"
    global_entry.symlink_to(global_target.name)
    approved_old_digest = content_digest(global_entry)
    approved = service.compare()

    with pytest.raises(CatalogSyncError):
        service.sync(approved.token, ["playbook:standard"])

    resolved = CatalogResolver(
        project_root=tmp_path / "other-project",
        canonical_root=tmp_path / "other-project",
        global_root=global_root,
        builtin_root=tmp_path / "other-builtin",
    ).resolve(CatalogKind.PLAYBOOK, "standard")
    receipt = next((global_root / ".catalog-transactions").glob("*/recovery.json"))
    evidence = json.loads(receipt.read_text(encoding="utf-8"))

    assert content_digest(global_entry) == approved_old_digest
    assert resolved is not None
    assert resolved.source == "global"
    assert resolved.digest == approved_old_digest
    assert evidence["status"] == "rolled_back"
    assert evidence["restored"] == ["playbook:standard"]


def test_publication_recovers_durable_global_root_symlink_after_backing_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, project, global_root = _service(tmp_path)
    _entry(project / ".cafe", CatalogKind.PLAYBOOK, "standard", "approved-new")
    global_playbooks = global_root / "playbooks"
    global_target = global_playbooks / "standard.data"
    global_target.parent.mkdir(parents=True)
    global_target.write_text(
        "playbook: {id: standard}\nsteps: {}\nmarker: approved-old\n",
        encoding="utf-8",
    )
    global_entry = global_playbooks / "standard.yaml"
    global_entry.symlink_to(global_target.name)
    approved_old_digest = content_digest(global_entry)
    real_move = transactions_module._native_move_without_replacement
    backing_removed = False

    def remove_backing_after_move(*args, **kwargs) -> None:
        nonlocal backing_removed
        source_directory = kwargs["source_directory"]
        real_move(*args, **kwargs)
        if not backing_removed and source_directory.path == global_playbooks:
            global_target.unlink()
            backing_removed = True

    monkeypatch.setattr(
        transactions_module,
        "_native_move_without_replacement",
        remove_backing_after_move,
    )
    approved = service.compare()

    with pytest.raises(CatalogSyncError):
        service.sync(approved.token, ["playbook:standard"])

    receipt = next((global_root / ".catalog-transactions").glob("*/recovery.json"))
    evidence = json.loads(receipt.read_text(encoding="utf-8"))
    resolved = _recovery_reader(tmp_path, global_root).resolve(
        CatalogKind.PLAYBOOK,
        "standard",
    )

    assert backing_removed is True
    assert content_digest(global_entry) == approved_old_digest
    assert resolved is not None
    assert resolved.source == "global"
    assert resolved.digest == approved_old_digest
    assert evidence["status"] == "rolled_back"
    assert evidence["restored"] == ["playbook:standard"]


def test_approved_directory_root_symlink_publishes_self_contained_global_content(
    tmp_path: Path,
) -> None:
    service, project, global_root = _service(tmp_path)
    skills = project / ".cafe" / "skills"
    support = skills / "support"
    support.mkdir(parents=True)
    (support / "SKILL.md").write_text(
        "---\nname: support\ndescription: project\n---\n",
        encoding="utf-8",
    )
    target = support / "variants" / "develop"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(
        "---\nname: develop\ndescription: project\n---\n",
        encoding="utf-8",
    )
    project_entry = skills / "develop"
    project_entry.symlink_to(target.relative_to(skills), target_is_directory=True)
    approved = service.compare()
    approved_digest = next(
        item.project_digest
        for item in approved.differences
        if item.entry_id == "phase:develop"
    )

    result = service.sync(approved.token, ["phase:develop"])
    project_entry.unlink()
    (target / "SKILL.md").unlink()
    target.rmdir()
    target.parent.rmdir()
    global_entry = CatalogResolver(
        project_root=tmp_path / "other-project",
        canonical_root=tmp_path / "other-project",
        global_root=global_root,
        builtin_root=tmp_path / "other-builtin",
    ).resolve(CatalogKind.PHASE, "develop")

    assert result.updated == ("phase:develop",)
    assert "phase:develop" not in {
        item.entry_id for item in result.comparison.differences
    }
    assert global_entry.source == "global"
    assert global_entry.digest == approved_digest


def test_root_directory_symlink_materializes_confined_nested_target(
    tmp_path: Path,
) -> None:
    service, project, global_root = _service(tmp_path)
    skills = project / ".cafe" / "skills"
    support = skills / "support"
    target = support / "variants" / "develop"
    target.mkdir(parents=True)
    (support / "SKILL.md").write_text(
        "---\nname: support\ndescription: support\n---\n",
        encoding="utf-8",
    )
    (target / "SKILL.md").write_text(
        "---\nname: develop\ndescription: project\n---\n",
        encoding="utf-8",
    )
    shared_policy = support / "shared" / "policy.md"
    shared_policy.parent.mkdir()
    shared_policy.write_text("approved policy\n", encoding="utf-8")
    nested_link = target / "policy.md"
    nested_link.symlink_to("../../shared/policy.md")
    project_entry = skills / "develop"
    project_entry.symlink_to(target.relative_to(skills), target_is_directory=True)
    approved = service.compare()
    approved_digest = next(
        item.project_digest
        for item in approved.differences
        if item.entry_id == "phase:develop"
    )

    result = service.sync(approved.token, ["phase:develop"])
    project_entry.unlink()
    nested_link.unlink()
    shared_policy.unlink()
    global_entry = CatalogResolver(
        project_root=tmp_path / "other-project",
        canonical_root=tmp_path / "other-project",
        global_root=global_root,
        builtin_root=tmp_path / "other-builtin",
    ).resolve(CatalogKind.PHASE, "develop")

    assert result.updated == ("phase:develop",)
    assert "phase:develop" not in {
        item.entry_id for item in result.comparison.differences
    }
    assert global_entry.digest == approved_digest
    assert (global_entry.path / "policy.md").read_text(encoding="utf-8") == (
        "approved policy\n"
    )


def test_nested_symlink_replacement_is_rejected_before_staging_target_content(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    skills = project / ".cafe" / "skills"
    support = skills / "support"
    target = support / "variants" / "develop"
    target.mkdir(parents=True)
    (support / "SKILL.md").write_text(
        "---\nname: support\ndescription: support\n---\n",
        encoding="utf-8",
    )
    (target / "SKILL.md").write_text(
        "---\nname: develop\ndescription: project\n---\n",
        encoding="utf-8",
    )
    approved_policy = support / "shared" / "policy.md"
    approved_policy.parent.mkdir()
    approved_policy.write_text("approved policy\n", encoding="utf-8")
    nested_link = target / "policy.md"
    nested_link.symlink_to("../../shared/policy.md")
    (skills / "develop").symlink_to(target.relative_to(skills), target_is_directory=True)
    external = tmp_path / "external-policy.md"
    external_marker = b"unapproved external policy\n"
    external.write_bytes(external_marker)

    def replace_nested_link(boundary: str, entry_id: str | None) -> None:
        if boundary == "stage" and entry_id == "phase:develop":
            nested_link.unlink()
            nested_link.symlink_to(external)

    resolver = CatalogResolver(
        project_root=project,
        canonical_root=project,
        global_root=global_root,
        builtin_root=tmp_path / "builtin",
    )
    service = CatalogSyncService(resolver, failure_injector=replace_nested_link)
    approved = service.compare()

    with pytest.raises(CatalogSyncError):
        service.sync(approved.token, ["phase:develop"])

    transactions = global_root / ".catalog-transactions"
    staged_files = [path for path in transactions.rglob("*") if path.is_file()]
    assert all(path.read_bytes() != external_marker for path in staged_files)
    assert not (global_root / "skills" / "develop").exists()


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


def test_catalog_digest_stops_directory_iteration_at_node_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    yielded = 0

    @contextmanager
    def oversized_directory(_path: object):
        def entries():
            nonlocal yielded
            for index in range(100):
                yielded += 1
                yield SimpleNamespace(name=f"child-{index}")

        yield entries()

    monkeypatch.setattr(resolver_module.os, "scandir", oversized_directory)

    with pytest.raises(CatalogValidationError):
        content_digest(tree, max_nodes=2)

    assert yielded == 2


def test_catalog_copy_stops_directory_iteration_at_node_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, project, global_root = _service(tmp_path)
    _entry(project / ".cafe", CatalogKind.PHASE, "develop", "project")
    approved = service.compare()
    real_scandir = sync_module.os.scandir
    yielded = 0

    @contextmanager
    def oversized_directory(descriptor: object):
        nonlocal yielded
        if not isinstance(descriptor, int):
            with real_scandir(descriptor) as entries:
                yield entries
            return

        def entries():
            nonlocal yielded
            for index in range(sync_module.MAX_CATALOG_NODES * 2):
                yielded += 1
                yield SimpleNamespace(name=f"child-{index}")

        yield entries()

    monkeypatch.setattr(sync_module.os, "scandir", oversized_directory)

    with pytest.raises(CatalogSyncError):
        service.sync(approved.token, ["phase:develop"])

    assert yielded == sync_module.MAX_CATALOG_NODES
    assert not (global_root / "skills" / "develop").exists()


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


def test_catalog_reader_rejects_unbound_pending_backup_before_mutation(
    tmp_path: Path,
) -> None:
    global_root = tmp_path / "global"
    transaction = global_root / ".catalog-transactions" / "unbound-pending-backup"
    approved_old = _entry(
        tmp_path / "approved-old",
        CatalogKind.PLAYBOOK,
        "standard",
        "approved-old",
    )
    approved_new = _entry(
        tmp_path / "approved-new",
        CatalogKind.PLAYBOOK,
        "standard",
        "approved-new",
    )
    unbound_backup = _entry(
        transaction / "backups",
        CatalogKind.PLAYBOOK,
        "standard",
        "unbound-backup",
    )
    unbound_digest = content_digest(unbound_backup)
    _write_recovery_journal(
        transaction,
        {
            "entry_id": "playbook:standard",
            "relative_path": "playbooks/standard.yaml",
            "old_digest": content_digest(approved_old),
            "new_digest": content_digest(approved_new),
            "state": "pending",
        },
    )

    with pytest.raises(CatalogRecoveryError):
        _recovery_reader(tmp_path, global_root).resolve(CatalogKind.PLAYBOOK, "standard")

    assert not (global_root / "playbooks" / "standard.yaml").exists()
    assert content_digest(unbound_backup) == unbound_digest


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


def test_publication_rejects_target_parent_replaced_after_backup(
    tmp_path: Path,
) -> None:
    service, project, global_root = _service(tmp_path)
    _entry(project / ".cafe", CatalogKind.PLAYBOOK, "standard", "approved-new")
    old_target = _entry(
        global_root,
        CatalogKind.PLAYBOOK,
        "standard",
        "approved-old",
    )
    old_digest = content_digest(old_target)
    outside = tmp_path / "outside"
    outside.mkdir()
    displaced = global_root / "approved-playbooks"

    def replace_parent(boundary: str, entry_id: str | None) -> None:
        if boundary == "backed_up" and entry_id == "playbook:standard":
            (global_root / "playbooks").rename(displaced)
            (global_root / "playbooks").symlink_to(outside, target_is_directory=True)

    service.failure_injector = replace_parent
    report = service.compare()

    with pytest.raises(CatalogSyncError):
        service.sync(report.token, ["playbook:standard"])

    assert not (outside / "standard.yaml").exists()
    receipt = next((global_root / ".catalog-transactions").glob("*/recovery.json"))
    evidence = json.loads(receipt.read_text(encoding="utf-8"))
    assert evidence["status"] == "incomplete"
    assert content_digest(receipt.parent / "backups" / "playbooks" / "standard.yaml") == old_digest


def test_publication_preserves_target_created_after_backup(tmp_path: Path) -> None:
    service, project, global_root = _service(tmp_path)
    _entry(project / ".cafe", CatalogKind.PLAYBOOK, "standard", "approved-new")
    old_target = _entry(
        global_root,
        CatalogKind.PLAYBOOK,
        "standard",
        "approved-old",
    )
    old_digest = content_digest(old_target)
    intervening = b"playbook: {id: standard}\nsteps: {}\nmarker: intervening\n"

    def create_target(boundary: str, entry_id: str | None) -> None:
        if boundary == "backed_up" and entry_id == "playbook:standard":
            (global_root / "playbooks" / "standard.yaml").write_bytes(intervening)

    service.failure_injector = create_target
    report = service.compare()

    with pytest.raises(CatalogSyncError):
        service.sync(report.token, ["playbook:standard"])

    target = global_root / "playbooks" / "standard.yaml"
    assert target.read_bytes() == intervening
    receipt = next((global_root / ".catalog-transactions").glob("*/recovery.json"))
    evidence = json.loads(receipt.read_text(encoding="utf-8"))
    assert evidence["status"] == "incomplete"
    assert content_digest(receipt.parent / "backups" / "playbooks" / "standard.yaml") == old_digest


def test_publication_preserves_global_leaf_replaced_immediately_before_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, project, global_root = _service(tmp_path)
    _entry(project / ".cafe", CatalogKind.PLAYBOOK, "standard", "approved-new")
    _entry(project / ".cafe", CatalogKind.PHASE, "develop", "approved-new-phase")
    target = _entry(
        global_root,
        CatalogKind.PLAYBOOK,
        "standard",
        "approved-old",
    )
    old_phase = _entry(
        global_root,
        CatalogKind.PHASE,
        "develop",
        "approved-old-phase",
    )
    old_phase_digest = content_digest(old_phase)
    intervening = b"playbook: {id: standard}\nsteps: {}\nmarker: intervening\n"
    real_move = sync_module.move_without_replacement
    replaced = False

    def replace_leaf_before_move(*args, **kwargs) -> None:
        nonlocal replaced
        source_directory = kwargs["source_directory"]
        if not replaced and source_directory.path == target.parent:
            replacement = target.with_suffix(".replacement")
            replacement.write_bytes(intervening)
            replacement.replace(target)
            replaced = True
        real_move(*args, **kwargs)

    monkeypatch.setattr(sync_module, "move_without_replacement", replace_leaf_before_move)
    report = service.compare()

    with pytest.raises(CatalogSyncError):
        service.sync(report.token, ["phase:develop", "playbook:standard"])

    assert replaced is True
    assert target.read_bytes() == intervening
    assert content_digest(old_phase) == old_phase_digest
    receipt = next((global_root / ".catalog-transactions").glob("*/recovery.json"))
    evidence = json.loads(receipt.read_text(encoding="utf-8"))
    assert evidence["status"] == "rolled_back"
    resolved = _recovery_reader(tmp_path, global_root).resolve(
        CatalogKind.PLAYBOOK,
        "standard",
    )
    assert resolved is not None
    assert resolved.digest == content_digest(target)


def test_publication_preserves_global_leaf_modified_in_place_before_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, project, global_root = _service(tmp_path)
    _entry(project / ".cafe", CatalogKind.PLAYBOOK, "standard", "approved-new")
    target = _entry(
        global_root,
        CatalogKind.PLAYBOOK,
        "standard",
        "approved-old",
    )
    intervening = b"playbook: {id: standard}\nsteps: {}\nmarker: intervening\n"
    original_identity = (target.stat().st_dev, target.stat().st_ino)
    real_move = transactions_module._native_move_without_replacement
    modified = False

    def modify_leaf_before_move(*args, **kwargs) -> None:
        nonlocal modified
        source_directory = kwargs["source_directory"]
        if not modified and source_directory.path == target.parent:
            target.write_bytes(intervening)
            assert (target.stat().st_dev, target.stat().st_ino) == original_identity
            modified = True
        real_move(*args, **kwargs)

    monkeypatch.setattr(
        transactions_module,
        "_native_move_without_replacement",
        modify_leaf_before_move,
    )
    report = service.compare()

    with pytest.raises(CatalogSyncError):
        service.sync(report.token, ["playbook:standard"])

    assert modified is True
    assert target.read_bytes() == intervening
    receipt = next((global_root / ".catalog-transactions").glob("*/recovery.json"))
    evidence = json.loads(receipt.read_text(encoding="utf-8"))
    assert evidence["status"] == "rolled_back"
    assert evidence["preserved"] == ["playbook:standard"]


def test_publication_rejects_staged_leaf_modified_in_place_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, project, global_root = _service(tmp_path)
    _entry(project / ".cafe", CatalogKind.PLAYBOOK, "standard", "approved-new")
    target = _entry(
        global_root,
        CatalogKind.PLAYBOOK,
        "standard",
        "approved-old",
    )
    approved_old = target.read_bytes()
    unapproved = b"playbook: {id: standard}\nsteps: {}\nmarker: unapproved\n"
    real_move = transactions_module._native_move_without_replacement
    modified = False

    def modify_staged_leaf_before_move(*args, **kwargs) -> None:
        nonlocal modified
        source_directory = kwargs["source_directory"]
        source = source_directory.path / args[0]
        if not modified and "staged" in source_directory.path.parts:
            staged_identity = (source.stat().st_dev, source.stat().st_ino)
            source.write_bytes(unapproved)
            assert (source.stat().st_dev, source.stat().st_ino) == staged_identity
            modified = True
        real_move(*args, **kwargs)

    monkeypatch.setattr(
        transactions_module,
        "_native_move_without_replacement",
        modify_staged_leaf_before_move,
    )
    report = service.compare()

    with pytest.raises(CatalogSyncError):
        service.sync(report.token, ["playbook:standard"])

    assert modified is True
    assert target.read_bytes() == approved_old
    receipt = next((global_root / ".catalog-transactions").glob("*/recovery.json"))
    evidence = json.loads(receipt.read_text(encoding="utf-8"))
    assert evidence["status"] == "rolled_back"
    assert evidence["restored"] == ["playbook:standard"]
    assert evidence["preserved"] == []


def test_rollback_preserves_global_leaf_replaced_inside_source_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rollback_started = False

    def fail_after_publish(boundary: str, entry_id: str | None) -> None:
        nonlocal rollback_started
        if boundary == "published" and entry_id == "playbook:standard":
            rollback_started = True
            raise OSError("trigger rollback")

    service, project, global_root = _service(
        tmp_path,
        failure_injector=fail_after_publish,
    )
    _entry(project / ".cafe", CatalogKind.PLAYBOOK, "standard", "approved-new")
    target = _entry(
        global_root,
        CatalogKind.PLAYBOOK,
        "standard",
        "approved-old",
    )
    intervening = b"playbook: {id: standard}\nsteps: {}\nmarker: intervening\n"
    real_entry_identity = transactions_module.entry_identity
    rollback_target_checks = 0

    def replace_leaf_after_source_check(directory, name):
        nonlocal rollback_target_checks
        identity = real_entry_identity(directory, name)
        if rollback_started and directory.path == target.parent and name == target.name:
            rollback_target_checks += 1
            if rollback_target_checks == 2:
                replacement = target.with_suffix(".replacement")
                replacement.write_bytes(intervening)
                replacement.replace(target)
        return identity

    monkeypatch.setattr(
        transactions_module,
        "entry_identity",
        replace_leaf_after_source_check,
    )
    report = service.compare()

    with pytest.raises(CatalogSyncError):
        service.sync(report.token, ["playbook:standard"])

    assert rollback_target_checks >= 2
    assert target.read_bytes() == intervening
    receipt = next((global_root / ".catalog-transactions").glob("*/recovery.json"))
    evidence = json.loads(receipt.read_text(encoding="utf-8"))
    assert evidence["status"] == "rolled_back"
    resolved = _recovery_reader(tmp_path, global_root).resolve(
        CatalogKind.PLAYBOOK,
        "standard",
    )
    assert resolved is not None
    assert resolved.digest == content_digest(target)


@pytest.mark.parametrize("ancestor", ["target-remove", "target-restore", "backup-restore"])
def test_rollback_rejects_ancestor_replacement_without_external_mutation(
    tmp_path: Path,
    ancestor: str,
) -> None:
    service, project, global_root = _service(tmp_path)
    _entry(project / ".cafe", CatalogKind.PLAYBOOK, "standard", "approved-new")
    old_target = _entry(
        global_root,
        CatalogKind.PLAYBOOK,
        "standard",
        "approved-old",
    )
    old_digest = content_digest(old_target)
    outside = tmp_path / f"outside-{ancestor}"
    outside.mkdir()
    outside_target = outside / "standard.yaml"
    outside_marker = b"playbook: {id: standard}\nsteps: {}\nmarker: unrelated-outside\n"
    outside_target.write_bytes(outside_marker)
    changed = False

    def replace_parent(boundary: str, entry_id: str | None) -> None:
        nonlocal changed
        if boundary == "published" and entry_id == "playbook:standard":
            raise OSError("trigger rollback")
        expected_boundary = "rollback_remove" if ancestor == "target-remove" else "rollback_restore"
        if changed or boundary != expected_boundary or entry_id != "playbook:standard":
            return
        transaction = next((global_root / ".catalog-transactions").iterdir())
        if ancestor.startswith("target"):
            parent = global_root / "playbooks"
            displaced = global_root / f"approved-playbooks-{ancestor}"
        else:
            parent = transaction / "backups" / "playbooks"
            displaced = transaction / "backups" / "approved-playbooks"
        parent.rename(displaced)
        parent.symlink_to(outside_target.parent, target_is_directory=True)
        changed = True

    service.failure_injector = replace_parent
    report = service.compare()

    with pytest.raises(CatalogSyncError):
        service.sync(report.token, ["playbook:standard"])

    assert outside_target.read_bytes() == outside_marker
    receipt = next((global_root / ".catalog-transactions").glob("*/recovery.json"))
    evidence = json.loads(receipt.read_text(encoding="utf-8"))
    assert evidence["status"] == "incomplete"
    backup_candidates = list(receipt.parent.glob("backups/**/standard.yaml"))
    assert any(content_digest(candidate) == old_digest for candidate in backup_candidates)


def test_rollback_restore_preserves_intervening_target(tmp_path: Path) -> None:
    service, project, global_root = _service(tmp_path)
    _entry(project / ".cafe", CatalogKind.PLAYBOOK, "standard", "approved-new")
    old_target = _entry(
        global_root,
        CatalogKind.PLAYBOOK,
        "standard",
        "approved-old",
    )
    old_digest = content_digest(old_target)
    intervening = b"playbook: {id: standard}\nsteps: {}\nmarker: intervening\n"

    def create_target(boundary: str, entry_id: str | None) -> None:
        if boundary == "published" and entry_id == "playbook:standard":
            raise OSError("trigger rollback")
        if boundary == "rollback_restore" and entry_id == "playbook:standard":
            (global_root / "playbooks" / "standard.yaml").write_bytes(intervening)

    service.failure_injector = create_target
    report = service.compare()

    with pytest.raises(CatalogSyncError):
        service.sync(report.token, ["playbook:standard"])

    target = global_root / "playbooks" / "standard.yaml"
    assert target.read_bytes() == intervening
    receipt = next((global_root / ".catalog-transactions").glob("*/recovery.json"))
    evidence = json.loads(receipt.read_text(encoding="utf-8"))
    assert evidence["status"] == "incomplete"
    assert content_digest(receipt.parent / "backups" / "playbooks" / "standard.yaml") == old_digest


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


def test_maximum_accepted_selection_recovers_from_first_stage_failure(
    tmp_path: Path,
) -> None:
    failed = False

    def fail(boundary: str, _entry_id: str | None) -> None:
        nonlocal failed
        if boundary == "stage" and not failed:
            failed = True
            raise OSError("injected first-stage failure")

    service, project, _global_root = _service(tmp_path, failure_injector=fail)
    for index in range(MAX_CATALOG_OPERATION_ENTRIES):
        _entry(
            project / ".cafe",
            CatalogKind.PHASE,
            f"phase-{index:03d}",
            "project",
        )
    report = service.compare(kinds=[CatalogKind.PHASE])
    selected = [item.entry_id for item in report.differences]

    with pytest.raises(CatalogSyncError):
        service.sync(report.token, selected, kinds=[CatalogKind.PHASE])

    recovered = service.compare(kinds=[CatalogKind.PHASE])
    assert len(recovered.differences) == MAX_CATALOG_OPERATION_ENTRIES


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
