"""U1/U2: unified catalog resolution invariants."""

import shutil
from pathlib import Path

import pytest

from cafe.catalogs.resolver import (
    CatalogKind,
    CatalogResolver,
    CatalogValidationError,
    discover_project_roots,
)
from cafe.skills.loader import SkillLoader


def _write_entry(root: Path, kind: CatalogKind, key: str, marker: str) -> Path:
    if kind is CatalogKind.PLAYBOOK:
        path = root / "playbooks" / f"{key}.yaml"
        content = f"playbook: {{id: {key}}}\nsteps: {{}}\nmarker: {marker}\n"
    elif kind is CatalogKind.PHASE:
        path = root / "skills" / key / "SKILL.md"
        content = f"---\nname: {key}\ndescription: {marker}\n---\n\n# {key}\n"
    else:
        role, name = key.split("/", 1)
        path = root / "agents" / role / f"{name}.md"
        content = f"---\nname: {name}\ndescription: {marker}\n---\n\n# {name}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path.parent if kind is CatalogKind.PHASE else path


@pytest.mark.parametrize(
    ("kind", "key"),
    [
        (CatalogKind.PLAYBOOK, "standard"),
        (CatalogKind.PHASE, "cafe-develop"),
        (CatalogKind.AGENT, "developer/David"),
    ],
)
def test_project_global_builtin_precedence_never_materializes_fallback(
    tmp_path: Path, kind: CatalogKind, key: str
) -> None:
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    builtin = tmp_path / "builtin"
    builtin_path = _write_entry(builtin, kind, key, "builtin")
    global_path = _write_entry(global_root, kind, key, "global")
    project_path = _write_entry(project / ".cafe", kind, key, "project")

    resolver = CatalogResolver(
        project_root=project,
        canonical_root=project,
        global_root=global_root,
        builtin_root=builtin,
    )

    entry = resolver.resolve(kind, key)
    assert (entry.source, entry.path) == ("project", project_path)
    shutil.rmtree(project_path) if project_path.is_dir() else project_path.unlink()
    assert resolver.resolve(kind, key).path == global_path
    shutil.rmtree(global_path) if global_path.is_dir() else global_path.unlink()
    assert resolver.resolve(kind, key).path == builtin_path
    assert not project_path.exists()


def test_existing_agent_matching_fallback_bytes_remains_project_authority(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    builtin = tmp_path / "builtin"
    builtin_path = _write_entry(
        builtin, CatalogKind.AGENT, "developer/David", "shared"
    )
    global_path = _write_entry(
        global_root, CatalogKind.AGENT, "developer/David", "shared"
    )
    project_path = _write_entry(
        project / ".cafe", CatalogKind.AGENT, "developer/David", "shared"
    )
    resolver = CatalogResolver(
        project_root=project,
        canonical_root=project,
        global_root=global_root,
        builtin_root=builtin,
    )

    assert resolver.resolve(CatalogKind.AGENT, "developer/David").path == project_path
    project_path.unlink()
    assert resolver.resolve(CatalogKind.AGENT, "developer/David").path == global_path
    global_path.unlink()
    assert resolver.resolve(CatalogKind.AGENT, "developer/David").path == builtin_path
    assert not project_path.exists()


def test_invalid_project_entry_fails_instead_of_falling_through(tmp_path: Path) -> None:
    project = tmp_path / "project"
    builtin = tmp_path / "builtin"
    _write_entry(builtin, CatalogKind.PLAYBOOK, "standard", "builtin")
    invalid = project / ".cafe" / "playbooks" / "standard.yaml"
    invalid.parent.mkdir(parents=True)
    invalid.write_text("- not-a-mapping\n", encoding="utf-8")

    resolver = CatalogResolver(
        project_root=project,
        canonical_root=project,
        global_root=tmp_path / "global",
        builtin_root=builtin,
    )

    with pytest.raises(CatalogValidationError):
        resolver.resolve(CatalogKind.PLAYBOOK, "standard")


def test_broken_project_catalog_root_fails_instead_of_falling_through(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    builtin = tmp_path / "builtin"
    _write_entry(builtin, CatalogKind.PHASE, "develop", "builtin")
    catalog_root = project / ".cafe" / "skills"
    catalog_root.parent.mkdir(parents=True)
    catalog_root.symlink_to(project / "missing-skills", target_is_directory=True)
    loader = SkillLoader(
        project_root=project,
        global_root=tmp_path / "global",
        builtin_root=builtin,
    )

    with pytest.raises(CatalogValidationError):
        loader.discover()


@pytest.mark.parametrize(
    ("kind", "key", "directory"),
    [
        (CatalogKind.PLAYBOOK, "standard", "playbooks"),
        (CatalogKind.PHASE, "develop", "skills"),
        (CatalogKind.AGENT, "developer/David", "agents"),
    ],
)
def test_direct_resolution_rejects_broken_project_catalog_root(
    tmp_path: Path,
    kind: CatalogKind,
    key: str,
    directory: str,
) -> None:
    project = tmp_path / "project"
    builtin = tmp_path / "builtin"
    _write_entry(builtin, kind, key, "builtin")
    catalog_root = project / ".cafe" / directory
    catalog_root.parent.mkdir(parents=True)
    catalog_root.symlink_to(project / f"missing-{directory}", target_is_directory=True)
    resolver = CatalogResolver(
        project_root=project,
        canonical_root=project,
        global_root=tmp_path / "global",
        builtin_root=builtin,
    )

    with pytest.raises(CatalogValidationError):
        resolver.resolve(kind, key)


@pytest.mark.parametrize(
    ("kind", "key", "directory"),
    [
        (CatalogKind.PLAYBOOK, "standard", "playbooks"),
        (CatalogKind.PHASE, "develop", "skills"),
        (CatalogKind.AGENT, "developer/David", "agents"),
    ],
)
def test_direct_and_enumerated_resolution_share_lower_root_validation(
    tmp_path: Path,
    kind: CatalogKind,
    key: str,
    directory: str,
) -> None:
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    _write_entry(project / ".cafe", kind, key, "project")
    broken_global = global_root / directory
    broken_global.parent.mkdir(parents=True)
    broken_global.symlink_to(global_root / f"missing-{directory}", target_is_directory=True)
    resolver = CatalogResolver(
        project_root=project,
        canonical_root=project,
        global_root=global_root,
        builtin_root=tmp_path / "builtin",
    )

    with pytest.raises(CatalogValidationError):
        resolver.resolve(kind, key)
    with pytest.raises(CatalogValidationError):
        resolver.keys(kind)


@pytest.mark.parametrize(
    ("invalid_layer", "invalid_content"),
    [
        ("project", b"\xff\xfeinvalid-agent"),
        ("global", b"# missing frontmatter\n"),
        (
            "builtin",
            b"---\nname: Wrong\ndescription: invalid\n---\n\n# Wrong\n",
        ),
    ],
)
def test_invalid_agent_at_effective_precedence_fails_closed(
    tmp_path: Path, invalid_layer: str, invalid_content: bytes
) -> None:
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    builtin = tmp_path / "builtin"
    roots = {
        "project": project / ".cafe",
        "global": global_root,
        "builtin": builtin,
    }
    precedence = ["builtin", "global", "project"]
    invalid_index = precedence.index(invalid_layer)
    for lower_layer in precedence[:invalid_index]:
        _write_entry(
            roots[lower_layer],
            CatalogKind.AGENT,
            "developer/David",
            lower_layer,
        )
    invalid = roots[invalid_layer] / "agents" / "developer" / "David.md"
    invalid.parent.mkdir(parents=True, exist_ok=True)
    invalid.write_bytes(invalid_content)
    resolver = CatalogResolver(
        project_root=project,
        canonical_root=project,
        global_root=global_root,
        builtin_root=builtin,
    )

    with pytest.raises(CatalogValidationError):
        resolver.resolve(CatalogKind.AGENT, "developer/David")


def test_active_worktree_overlays_only_matching_canonical_entry(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical"
    worktree = tmp_path / "worktree"
    builtin = tmp_path / "builtin"
    canonical_standard = _write_entry(
        canonical / ".cafe", CatalogKind.PLAYBOOK, "standard", "canonical"
    )
    _write_entry(canonical / ".cafe", CatalogKind.PHASE, "review", "canonical")
    active_standard = _write_entry(
        worktree / ".cafe", CatalogKind.PLAYBOOK, "standard", "active"
    )
    fallback = _write_entry(builtin, CatalogKind.AGENT, "developer/David", "builtin")

    resolver = CatalogResolver(
        project_root=worktree,
        canonical_root=canonical,
        global_root=tmp_path / "global",
        builtin_root=builtin,
    )

    assert resolver.resolve(CatalogKind.PLAYBOOK, "standard").path == active_standard
    assert resolver.resolve(CatalogKind.PHASE, "review").source == "project"
    assert resolver.resolve(CatalogKind.AGENT, "developer/David").path == fallback
    assert canonical_standard.read_text(encoding="utf-8").endswith("marker: canonical\n")


def test_git_root_discovery_keeps_active_and_canonical_roots_distinct(tmp_path: Path) -> None:
    active = tmp_path / "worktree"
    canonical = tmp_path / "canonical"
    active.mkdir()

    def git_runner(args: tuple[str, ...], cwd: Path) -> str:
        assert cwd == active
        if args == ("rev-parse", "--show-toplevel"):
            return str(active)
        if args == ("rev-parse", "--path-format=absolute", "--git-common-dir"):
            return str(canonical / ".git")
        raise AssertionError(args)

    roots = discover_project_roots(active, git_runner=git_runner)
    assert roots.active == active
    assert roots.canonical == canonical


def test_resolver_uses_discovered_worktree_root_from_a_subdirectory(
    tmp_path: Path,
) -> None:
    active = tmp_path / "worktree"
    nested = active / "nested" / "command-directory"
    canonical = tmp_path / "canonical"
    nested.mkdir(parents=True)

    def git_runner(args: tuple[str, ...], cwd: Path) -> str:
        assert cwd == nested
        if args == ("rev-parse", "--show-toplevel"):
            return str(active)
        if args == ("rev-parse", "--path-format=absolute", "--git-common-dir"):
            return str(canonical / ".git")
        raise AssertionError(args)

    resolver = CatalogResolver(
        project_root=nested,
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
        git_runner=git_runner,
    )

    assert resolver.project_root == active
    assert resolver.canonical_root == canonical


def test_git_root_discovery_falls_back_when_runner_returns_non_path(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    (project / ".cafe").mkdir(parents=True)

    roots = discover_project_roots(
        project,
        git_runner=lambda _args, _cwd: object(),  # type: ignore[return-value]
    )

    assert roots.active == project
    assert roots.canonical == project


def test_explicit_non_git_project_root_is_not_replaced_by_an_ancestor(
    tmp_path: Path,
) -> None:
    (tmp_path / ".cafe").mkdir()
    project = tmp_path / "nested" / "project"
    project.mkdir(parents=True)

    resolver = CatalogResolver(
        project_root=project,
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
        git_runner=lambda _args, _cwd: object(),  # type: ignore[return-value]
    )

    assert resolver.project_root == project
    assert resolver.canonical_root == project
