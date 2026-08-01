"""Tests for skill loader."""

from pathlib import Path
from unittest.mock import patch

import pytest

from cafe.core.types import AgentCLI
from cafe.skills.exceptions import SkillDiscoveryError
from cafe.skills.importer import import_skills
from cafe.skills.loader import SkillLoader, canonical_skill_name
from cafe.skills.native_bridge import NativeSkillBridge


def _write_skill(root: Path, name: str, *, frontmatter_name: str | None = None) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    fm_name = frontmatter_name if frontmatter_name is not None else name
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {fm_name}\ndescription: desc-{name}\n---\n\n# {name}\n",
        encoding="utf-8",
    )


def test_discover_respects_project_global_builtin_precedence(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin" / "skills"
    global_root = tmp_path / "global" / "skills"
    project = tmp_path / "project" / ".cafe" / "skills"
    _write_skill(builtin, "plan")
    _write_skill(global_root, "plan")
    _write_skill(project, "plan")

    loader = SkillLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )
    items = loader.discover()
    assert len(items) == 1
    assert items[0].name == "plan"
    assert items[0].source == "project"


def test_discover_builtin_name_mismatch_raises(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin" / "skills"
    _write_skill(builtin, "plan", frontmatter_name="plan_mismatch")

    loader = SkillLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )
    with pytest.raises(ValueError, match="does not match folder"):
        loader.discover()


def test_activate_replaces_placeholders(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin" / "skills"
    skill_dir = builtin / "spec_first"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: spec_first\ndescription: desc\n---\n\nHello {name}\n",
        encoding="utf-8",
    )
    loader = SkillLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )
    loader.discover()

    text = loader.activate("spec_first", context={"name": "World"})
    assert "Hello World" in text


def test_prompt_only_workflow_rejects_missing_reference(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    skill_dir = project_root / ".cafe" / "skills" / "prompt-only"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: prompt-only
description: Prompt-only workflow contract.
workflow:
  prompt_references:
    optional_instruction: missing.md
---
""",
        encoding="utf-8",
    )
    loader = SkillLoader(
        project_root=project_root,
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )

    loader.discover()

    with pytest.raises(ValueError, match="workflow reference not found: missing.md"):
        loader.get_workflow_contract("prompt-only")


def test_builtin_catalog_includes_pr_skill(tmp_path: Path) -> None:
    builtin_root = Path(__file__).resolve().parents[2] / "src" / "cafe" / "data"
    loader = SkillLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    items = loader.discover()

    assert any(item.name == "cafe-pr" and item.source == "builtin" for item in items)


def test_write_cafe_phase_legacy_aliases_resolve_to_renamed_skill() -> None:
    assert canonical_skill_name("write-cafe-skill") == "write-cafe-phase"
    assert canonical_skill_name("write-skill") == "write-cafe-phase"


def test_imported_project_skill_is_discovered_with_project_precedence(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin" / "skills"
    project_root = tmp_path / "project"
    _write_skill(builtin, "plan")
    source_dir = tmp_path / "incoming" / "plan"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "SKILL.md").write_text(
        "---\nname: plan\ndescription: imported plan\n---\n\nImported project body\n",
        encoding="utf-8",
    )

    summary = import_skills(tmp_path / "incoming", project_root)
    loader = SkillLoader(
        project_root=project_root,
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )
    items = loader.discover()

    assert summary.imported_count == 1
    assert any(item.name == "plan" and item.source == "project" for item in items)
    assert "Imported project body" in loader.activate("plan")


def test_builtin_catalog_includes_chat_handoff_skills(tmp_path: Path) -> None:
    builtin_root = Path(__file__).resolve().parents[2] / "src" / "cafe" / "data"
    loader = SkillLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    items = loader.discover()
    names = {item.name for item in items if item.source == "builtin"}

    assert {
        "cafe-common-chat-handoff",
        "cafe-chat-develop-change",
        "cafe-chat-spec-revision",
        "cafe-chat-plan-revision",
    }.issubset(names)


# --- Task 1: SkillDiscoveryError ---


def test_skill_discovery_error_is_lookup_error() -> None:
    err = SkillDiscoveryError("my-skill")
    assert isinstance(err, LookupError)
    assert err.skill_name == "my-skill"
    assert "my-skill" in str(err)


def test_skill_discovery_error_without_cli_has_no_cli_context() -> None:
    err = SkillDiscoveryError("my-skill")
    assert err.cli is None
    assert "cli=" not in str(err)


def test_skill_discovery_error_with_cli_includes_cli_context() -> None:
    err = SkillDiscoveryError("my-skill", cli=AgentCLI.CLAUDE)
    assert err.cli == AgentCLI.CLAUDE
    assert AgentCLI.CLAUDE.value in str(err)


# --- Task 2: SkillLoader raises SkillDiscoveryError ---


def test_get_skill_dir_raises_skill_discovery_error_for_unknown_skill(tmp_path: Path) -> None:
    loader = SkillLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )
    loader.discover()

    with pytest.raises(SkillDiscoveryError) as exc_info:
        loader.get_skill_dir("nonexistent-skill")
    assert "nonexistent-skill" in str(exc_info.value)


# --- Task 5: Resolution order ---


def test_all_three_roots_same_skill_project_wins(tmp_path: Path) -> None:
    """Explicitly documents project > global > builtin precedence."""
    builtin = tmp_path / "builtin" / "skills"
    global_root = tmp_path / "global" / "skills"
    project = tmp_path / "project" / ".cafe" / "skills"
    for root in (builtin, global_root, project):
        _write_skill(root, "plan")

    loader = SkillLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )
    items = loader.discover()

    assert len(items) == 1
    assert items[0].source == "project"


def test_global_overrides_builtin_when_no_project_skill(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin" / "skills"
    global_root = tmp_path / "global" / "skills"
    _write_skill(builtin, "plan")
    _write_skill(global_root, "plan")

    loader = SkillLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )
    items = loader.discover()

    assert len(items) == 1
    assert items[0].source == "global"


def test_install_skill_uses_project_version_over_global(tmp_path: Path) -> None:
    """When a project skill overrides a global skill, install_skill uses the project version."""
    global_root = tmp_path / "global" / "skills"
    project = tmp_path / "project" / ".cafe" / "skills"
    _write_skill(global_root, "cafe-plan")
    project_skill_dir = project / "cafe-plan"
    project_skill_dir.mkdir(parents=True, exist_ok=True)
    (project_skill_dir / "SKILL.md").write_text(
        "---\nname: cafe-plan\ndescription: project plan\n---\n\nProject version\n",
        encoding="utf-8",
    )

    loader = SkillLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )
    loader.discover()
    bridge = NativeSkillBridge(
        loader,
        project_root=tmp_path / "project",
        home_dir=tmp_path / "home",
    )
    bridge.install_skill("cafe-plan", AgentCLI.CLAUDE)

    installed = tmp_path / "project" / ".claude" / "skills" / "cafe-plan" / "SKILL.md"
    assert "Project version" in installed.read_text(encoding="utf-8")


def test_install_skill_recovers_when_skills_root_is_file(tmp_path: Path) -> None:
    global_root = tmp_path / "global" / "skills"
    _write_skill(global_root, "cafe-plan")
    project_root = tmp_path / "project"
    bad_root = project_root / ".copilot" / "skills"
    bad_root.parent.mkdir(parents=True, exist_ok=True)
    bad_root.write_text("not-a-directory", encoding="utf-8")

    loader = SkillLoader(
        project_root=project_root,
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )
    loader.discover()
    bridge = NativeSkillBridge(loader, project_root=project_root, home_dir=tmp_path / "home")

    installed = bridge.synchronize_skills(["cafe-plan"], AgentCLI.COPILOT)[0]
    assert installed.exists()
    assert bad_root.is_dir()


def test_install_skill_recovers_when_skills_root_is_broken_symlink(tmp_path: Path) -> None:
    global_root = tmp_path / "global" / "skills"
    _write_skill(global_root, "cafe-plan")
    project_root = tmp_path / "project"
    bad_root = project_root / ".copilot" / "skills"
    bad_root.parent.mkdir(parents=True, exist_ok=True)
    bad_root.symlink_to(tmp_path / "missing-target")

    loader = SkillLoader(
        project_root=project_root,
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )
    loader.discover()
    bridge = NativeSkillBridge(loader, project_root=project_root, home_dir=tmp_path / "home")

    installed = bridge.synchronize_skills(["cafe-plan"], AgentCLI.COPILOT)[0]
    assert installed.exists()
    assert bad_root.is_dir()


@pytest.mark.parametrize("manifest_contents", ["{broken", "{}"])
def test_synchronize_skills_recovers_stale_skills_from_a_corrupted_manifest(
    tmp_path: Path, manifest_contents: str
) -> None:
    """A damaged manifest still permits replace-style native skill cleanup."""
    global_root = tmp_path / "global" / "skills"
    _write_skill(global_root, "cafe-plan")
    _write_skill(global_root, "cafe-stale")
    project_root = tmp_path / "project"
    loader = SkillLoader(
        project_root=project_root,
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )
    loader.discover()
    bridge = NativeSkillBridge(loader, project_root=project_root, home_dir=tmp_path / "home")

    bridge.synchronize_skills(["cafe-stale"], AgentCLI.COPILOT)
    native_skills = project_root / ".copilot" / "skills"
    (native_skills / bridge.MANAGED_SKILLS_MANIFEST).write_text(
        manifest_contents, encoding="utf-8"
    )

    bridge.synchronize_skills(["cafe-plan"], AgentCLI.COPILOT)

    assert not (native_skills / "cafe-stale").exists()
    assert (native_skills / "cafe-plan" / "SKILL.md").is_file()


def test_synchronize_skills_can_reconcile_without_reinstalling_desired_skills(
    tmp_path: Path,
) -> None:
    """Workflow prompt preparation remains the single installation path."""
    global_root = tmp_path / "global" / "skills"
    _write_skill(global_root, "cafe-plan")
    _write_skill(global_root, "cafe-stale")
    project_root = tmp_path / "project"
    loader = SkillLoader(
        project_root=project_root,
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )
    loader.discover()
    bridge = NativeSkillBridge(loader, project_root=project_root, home_dir=tmp_path / "home")

    bridge.synchronize_skills(["cafe-stale"], AgentCLI.COPILOT)
    with patch.object(bridge, "install_skill") as install_skill:
        installed = bridge.synchronize_skills(["cafe-plan"], AgentCLI.COPILOT, install=False)

    native_skills = project_root / ".copilot" / "skills"
    assert installed == []
    install_skill.assert_not_called()
    assert not (native_skills / "cafe-stale").exists()
