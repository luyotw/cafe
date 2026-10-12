"""U1–U4/U6/U8: live links and ownership-safe native skill lifetimes."""

import os
import shutil
from pathlib import Path

import pytest

from cafe.core.types import AgentCLI
from cafe.skills.exceptions import SkillDiscoveryError
from cafe.skills.loader import SkillLoader
from cafe.skills.native_bridge import NativeSkillBridge

PROVIDERS = (AgentCLI.CODEX, AgentCLI.CLAUDE, AgentCLI.COPILOT)


def skill(root, name, body="original"):
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(f"---\nname: {name}\ndescription: test\n---\n{body}\n")
    (directory / "references").mkdir()
    (directory / "references/note.md").write_text("reference")
    (directory / "scripts").mkdir()
    (directory / "scripts/tool.sh").write_text("script")
    return directory


@pytest.fixture
def bridge(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    loader = SkillLoader(
        project_root=project, global_root=tmp_path / "global", builtin_root=tmp_path / "builtin"
    )
    return NativeSkillBridge(loader, project_root=project, home_dir=tmp_path / "home")


def local(bridge, name="local", body="original"):
    return skill(bridge.project_root / ".cafe/skills", name, body)


def test_all_local_skills_are_live_relative_links(bridge):
    source = local(bridge)
    other = local(bridge, "unselected")
    bridge.skill_loader.discover()
    later = local(bridge, "added-after-discovery")
    assert bridge.reconcile_project_skills() == []
    for cli in PROVIDERS:
        root = bridge.get_native_skills_dir(cli)
        assert root.is_dir() and not root.is_symlink()
        for directory in (source, other, later):
            link = root / directory.name
            assert link.is_symlink() and link.resolve() == directory
            assert not Path(os.readlink(link)).is_absolute()
    for relative in ("SKILL.md", "references/note.md", "scripts/tool.sh"):
        (source / relative).write_text("live-edit")
        assert all(
            (bridge.get_native_skills_dir(cli) / "local" / relative).read_text() == "live-edit"
            for cli in PROVIDERS
        )
    assert not bridge.home_dir.exists()


def test_selection_cleanup_preserves_project_links_and_sources(bridge):
    source = local(bridge)
    other = local(bridge, "unselected")
    skill(bridge.skill_loader.builtin_root / "skills", "transient-one")
    skill(bridge.skill_loader.builtin_root / "skills", "transient-two")
    bridge.reconcile_project_skills()
    bridge.synchronize_skills(["local", "transient-one"], AgentCLI.CODEX)
    bridge.synchronize_skills(["transient-two"], AgentCLI.CODEX, install=False)
    root = bridge.get_native_skills_dir(AgentCLI.CODEX)
    assert not (root / "transient-one").exists()
    assert not (root / "transient-two").exists()
    assert (root / "local").resolve() == source
    assert (root / "unselected").resolve() == other
    shutil.rmtree(other)
    bridge.reconcile_project_skills()
    assert not (root / "unselected").is_symlink()
    assert (bridge.get_native_skills_dir(AgentCLI.CLAUDE) / "local").resolve() == source
    assert source.is_dir()


@pytest.mark.parametrize("manifest", [None, "{broken", "[]", '["local"]'])
@pytest.mark.parametrize("entry", ["identical-copy", "directory", "file", "wrong-link"])
def test_ambiguous_entries_are_preserved_even_with_legacy_names(bridge, manifest, entry):
    source = local(bridge)
    root = bridge.get_native_skills_dir(AgentCLI.CODEX)
    root.mkdir(parents=True)
    target = root / "local"
    if entry == "identical-copy":
        shutil.copytree(source, target)
    elif entry == "directory":
        target.mkdir()
        (target / "user").write_text("user")
    elif entry == "file":
        target.write_text("user")
    else:
        target.symlink_to(bridge.project_root / "missing")
    before = target.lstat()
    if manifest is not None:
        (root / bridge.MANAGED_SKILLS_MANIFEST).write_text(manifest)
    errors = bridge.reconcile_project_skills()
    assert errors and "local" in " ".join(errors)
    assert target.lstat() == before
    with pytest.raises(SkillDiscoveryError):
        bridge.install_skill("local", AgentCLI.CODEX)
    assert source.is_dir()
    assert (bridge.get_native_skills_dir(AgentCLI.CLAUDE) / "local").resolve() == source


def test_correct_manual_link_is_adopted_and_repeated_install_is_safe(bridge):
    source = local(bridge)
    root = bridge.get_native_skills_dir(AgentCLI.CODEX)
    root.mkdir(parents=True)
    target = root / "local"
    target.symlink_to(os.path.relpath(source, root))
    inode = target.lstat().st_ino
    bridge.reconcile_project_skills()
    bridge.install_skill("local", AgentCLI.CODEX, context={"issue": "current"})
    assert target.lstat().st_ino == inode
    bridge._remove_managed_skill(AgentCLI.CODEX, "local")
    assert not target.is_symlink() and source.is_dir()


def test_user_replacement_is_not_removed_or_overwritten(bridge):
    local(bridge)
    bridge.reconcile_project_skills()
    target = bridge.get_native_skills_dir(AgentCLI.CODEX) / "local"
    target.unlink()
    target.mkdir()
    (target / "SKILL.md").write_text("replacement")
    bridge._remove_managed_skill(AgentCLI.CODEX, "local")
    assert (target / "SKILL.md").read_text() == "replacement"
    assert bridge.reconcile_project_skills()
    assert (target / "SKILL.md").read_text() == "replacement"


def test_proven_transient_copy_migrates_to_active_local_link(bridge):
    skill(bridge.skill_loader.builtin_root / "skills", "local")
    target = bridge.install_skill("local", AgentCLI.CODEX)
    source = local(bridge, body="new-local")
    bridge.reconcile_project_skills()
    assert target.is_symlink() and target.resolve() == source


@pytest.mark.parametrize("level", ["root", "ancestor", "file"])
def test_occupied_provider_roots_are_preserved(bridge, level):
    local(bridge)
    root = bridge.get_native_skills_dir(AgentCLI.CODEX)
    external = bridge.project_root.parent / "external"
    external.mkdir()
    (external / "sentinel").write_text("protected")
    if level == "ancestor":
        root.parent.symlink_to(external)
    else:
        root.parent.mkdir()
        if level == "file":
            root.write_text("user")
        else:
            root.symlink_to(external)
    assert bridge.reconcile_project_skills()
    assert (external / "sentinel").read_text() == "protected"
    assert not (external / "local").exists()
    if level == "file":
        assert root.read_text() == "user"


def test_broken_root_is_repaired_without_provider_traversal(bridge):
    source = local(bridge)
    root = bridge.get_native_skills_dir(AgentCLI.COPILOT)
    root.parent.mkdir()
    root.symlink_to(".claude/skills")
    assert bridge.reconcile_project_skills() == []
    assert root.is_dir() and not root.is_symlink()
    assert (root / "local").resolve() == source


def test_failed_link_migration_rolls_back_proven_copy(bridge, monkeypatch):
    skill(bridge.skill_loader.builtin_root / "skills", "local")
    target = bridge.install_skill("local", AgentCLI.CODEX)
    before = (target / "SKILL.md").read_bytes()
    local(bridge)
    original = Path.symlink_to

    def fail(path, *args, **kwargs):
        if path.parent == target.parent:
            raise OSError("injected link failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "symlink_to", fail)
    assert bridge.reconcile_project_skills()
    assert (target / "SKILL.md").read_bytes() == before


def test_invalid_source_does_not_prevent_safe_entries(bridge):
    local(bridge)
    (bridge.project_root / ".cafe/skills/invalid").mkdir()
    assert bridge.reconcile_project_skills()
    assert all((bridge.get_native_skills_dir(cli) / "local").is_symlink() for cli in PROVIDERS)


def test_observational_validation_installs_nothing(bridge):
    local(bridge)
    assert bridge.validate_skills(["local"], AgentCLI.CODEX).valid
    assert all(not bridge.get_native_skills_dir(cli).exists() for cli in PROVIDERS)


def test_correct_absolute_link_becomes_relative_without_changing_source(bridge):
    source = local(bridge)
    root = bridge.get_native_skills_dir(AgentCLI.CODEX)
    root.mkdir(parents=True)
    target = root / "local"
    target.symlink_to(source)
    before = (source / "SKILL.md").read_bytes()
    assert bridge.reconcile_project_skills() == []
    assert not Path(os.readlink(target)).is_absolute()
    assert target.resolve() == source
    assert (source / "SKILL.md").read_bytes() == before


def test_managed_dangling_and_old_target_links_repair_without_following_target(bridge):
    source = local(bridge)
    bridge.reconcile_project_skills()
    root = bridge.get_native_skills_dir(AgentCLI.CODEX)
    target = root / "local"
    target.unlink()
    external = bridge.project_root.parent / "old-source"
    external.mkdir()
    (external / "sentinel").write_text("protected")
    target.symlink_to(external)
    # Model a verifiable successful install from a previous bridge version.
    records = bridge._read_managed_skills(AgentCLI.CODEX)
    records["local"] = bridge._entry_record(target, "project_link", external)
    bridge._write_managed_skills(AgentCLI.CODEX, records)
    assert bridge.reconcile_project_skills() == []
    assert target.resolve() == source
    assert (external / "sentinel").read_text() == "protected"
    shutil.rmtree(source)
    local(bridge, body="new")
    assert bridge.reconcile_project_skills() == []
    assert target.resolve() == source and "new" in (target / "SKILL.md").read_text()


def test_interleaved_explicit_roots_never_link_canonical_fallback(bridge, tmp_path, monkeypatch):
    source = local(bridge)
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    loader = SkillLoader(
        project_root=sibling,
        global_root=bridge.skill_loader.global_root,
        builtin_root=bridge.skill_loader.builtin_root,
    )
    loader.resolver.canonical_root = bridge.project_root
    sibling_bridge = NativeSkillBridge(loader, project_root=sibling, home_dir=bridge.home_dir)
    monkeypatch.chdir(bridge.project_root)
    sibling_bridge.reconcile_project_skills()
    target = sibling_bridge.install_skill("local", AgentCLI.CODEX)
    assert target.is_dir() and not target.is_symlink()
    sibling_source = skill(sibling / ".cafe/skills", "local", "sibling")
    sibling_bridge.reconcile_project_skills()
    bridge.reconcile_project_skills()
    assert target.resolve() == sibling_source
    assert (bridge.get_native_skills_dir(AgentCLI.CODEX) / "local").resolve() == source
    assert not bridge.home_dir.exists()


def test_cleanup_restores_git_visibility_for_a_user_replacement(bridge):
    """U8: removing ownership must also remove CAFE's ignore rule for that name."""
    import subprocess

    subprocess.run(["git", "init", "-q", str(bridge.project_root)], check=True)
    (bridge.project_root / ".gitignore").write_text(".cafe/\n")
    local(bridge)
    bridge.reconcile_project_skills()
    root = bridge.get_native_skills_dir(AgentCLI.CODEX)
    target = root / "local"
    target.unlink()
    target.mkdir()
    (target / "SKILL.md").write_text("user replacement")
    bridge._remove_managed_skill(AgentCLI.CODEX, "local")
    status = subprocess.run(
        ["git", "-C", str(bridge.project_root), "status", "--porcelain", "--untracked-files=all"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert ".codex/skills/local/SKILL.md" in status
    assert ".claude/skills/local" not in status


def test_replacement_during_staging_is_preserved(bridge, monkeypatch):
    """U3: revalidate current-entry ownership at the mutation boundary."""
    skill(bridge.skill_loader.builtin_root / "skills", "local")
    target = bridge.install_skill("local", AgentCLI.CODEX)
    local(bridge)
    original = Path.symlink_to

    def replace_user_entry(path, *args, **kwargs):
        if path.parent == target.parent and path.name.startswith(".cafe-link-"):
            shutil.rmtree(target)
            target.mkdir()
            (target / "SKILL.md").write_text("new user content")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "symlink_to", replace_user_entry)
    assert bridge.reconcile_project_skills()
    assert not target.is_symlink()
    assert (target / "SKILL.md").read_text() == "new user content"
