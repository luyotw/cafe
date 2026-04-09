"""Tests for skill loader."""

from pathlib import Path

import pytest

from cafe.skills.importer import import_skills
from cafe.skills.loader import SkillLoader


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


def test_builtin_catalog_includes_pr_skill(tmp_path: Path) -> None:
    builtin_root = Path(__file__).resolve().parents[2] / "src" / "cafe" / "data"
    loader = SkillLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    items = loader.discover()

    assert any(item.name == "pr" and item.source == "builtin" for item in items)


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
