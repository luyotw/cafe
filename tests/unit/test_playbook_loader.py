"""Tests for playbook schema, loader, and semantic validation."""

from pathlib import Path

import pytest

from cafe.playbooks.loader import PlaybookLoader


def _write_skill(root: Path, name: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: desc-{name}\n---\n\n# {name}\n",
        encoding="utf-8",
    )


def _write_playbook(root: Path, name: str, content: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.yaml").write_text(content, encoding="utf-8")


def test_load_uses_project_override(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    global_root = tmp_path / "global"
    project_root = tmp_path / "project"

    _write_skill(builtin_root / "skills", "spec_first")
    _write_playbook(
        builtin_root / "playbooks",
        "default",
        """
playbook: {id: default}
steps:
  spec:
    role: pm
    skill: spec_first
    valid_status_codes: [CAFE_CONFIRMED]
    on:
      CAFE_CONFIRMED: _done
""",
    )
    _write_playbook(
        global_root / "playbooks",
        "default",
        """
playbook: {id: default}
steps:
  spec:
    role: developer
    skill: spec_first
    valid_status_codes: [CAFE_CONFIRMED]
    on:
      CAFE_CONFIRMED: _done
""",
    )
    _write_playbook(
        project_root / ".cafe" / "playbooks",
        "default",
        """
playbook: {id: default}
roles:
  reviewer:
    description: review
steps:
  spec:
    role: reviewer
    skill: spec_first
    valid_status_codes: [CAFE_CONFIRMED]
    on:
      CAFE_CONFIRMED: _done
""",
    )

    loader = PlaybookLoader(
        project_root=project_root,
        global_root=global_root,
        builtin_root=builtin_root,
    )
    result = loader.load_model("default")
    assert result.source == "project"
    assert result.model.steps["spec"].role == "reviewer"


def test_load_supports_iteration_aware_skill_and_defaults(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "spec_first")
    _write_skill(builtin_root / "skills", "spec_revise")
    _write_playbook(
        builtin_root / "playbooks",
        "default",
        """
playbook:
  id: default
roles:
  pm:
    description: product
steps:
  spec:
    skill:
      1: spec_first
      default: spec_revise
    role: pm
    valid_status_codes: [CAFE_CONFIRMED]
    on:
      CAFE_CONFIRMED: _done
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    result = loader.load_model("default")

    assert result.model.entry_point == "spec"
    assert result.model.steps["spec"].type == "skill"
    assert result.model.steps["spec"].assignee_type == "agent"
    assert result.model.steps["spec"].auto_snapshot is True
    assert result.model.steps["spec"].skill == {
        "1": "spec_first",
        "default": "spec_revise",
    }


def test_load_missing_skill_raises(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_playbook(
        builtin_root / "playbooks",
        "bad",
        """
playbook: {id: bad}
steps:
  spec:
    role: pm
    skill: missing_skill
    valid_status_codes: [CAFE_CONFIRMED]
    on:
      CAFE_CONFIRMED: _done
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    with pytest.raises(ValueError, match="unknown skill"):
        loader.load("bad")


def test_load_invalid_allowed_goto_raises(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "develop")
    _write_playbook(
        builtin_root / "playbooks",
        "bad",
        """
playbook: {id: bad}
steps:
  develop:
    role: developer
    skill: develop
    valid_status_codes: [CAFE_CONFIRMED]
    allowed_goto: [review]
    on:
      CAFE_CONFIRMED: _done
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    with pytest.raises(ValueError, match="invalid allowed_goto target"):
        loader.load("bad")


def test_load_invalid_transition_raises(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "review")
    _write_playbook(
        builtin_root / "playbooks",
        "bad",
        """
playbook: {id: bad}
steps:
  review:
    role: reviewer
    skill: review
    valid_status_codes: [CAFE_CONFIRMED]
    on:
      CAFE_CONFIRMED: not_exist
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    with pytest.raises(ValueError, match="invalid transition"):
        loader.load("bad")


def test_custom_playbook_reports_redundant_tool_warning(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    project_root = tmp_path / "project"
    _write_skill(builtin_root / "skills", "develop")
    _write_playbook(
        project_root / ".cafe" / "playbooks",
        "custom",
        """
playbook: {id: custom}
steps:
  develop:
    role: developer
    skill: develop
    allowed_tools: [Bash, "Bash(git:*)"]
    valid_status_codes: [CAFE_CONFIRMED]
    on:
      CAFE_CONFIRMED: _done
""",
    )

    loader = PlaybookLoader(
        project_root=project_root,
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    result = loader.load_model("custom")

    assert any("redundant allowed_tools entry" in warning for warning in result.warnings)


def test_strict_mode_upgrades_custom_warning_to_error(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    project_root = tmp_path / "project"
    _write_skill(builtin_root / "skills", "develop")
    _write_playbook(
        project_root / ".cafe" / "playbooks",
        "custom",
        """
playbook: {id: custom}
steps:
  develop:
    role: developer
    skill: develop
    allowed_tools: [Bash, "Bash(git:*)"]
    valid_status_codes: [CAFE_CONFIRMED]
    on:
      CAFE_CONFIRMED: _done
""",
    )

    loader = PlaybookLoader(
        project_root=project_root,
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    with pytest.raises(ValueError, match="redundant allowed_tools entry"):
        loader.load_model("custom", strict=True)


def test_builtin_catalog_includes_hotfix_and_simple() -> None:
    loader = PlaybookLoader()

    playbooks = loader.list_playbooks()

    assert "default" in playbooks
    assert "hotfix" in playbooks
    assert "simple" in playbooks


def test_builtin_hotfix_and_simple_playbooks_load() -> None:
    loader = PlaybookLoader()

    hotfix = loader.load_model("hotfix").model
    simple = loader.load_model("simple").model

    assert hotfix.entry_point == "develop"
    assert list(hotfix.steps.keys()) == ["develop", "review", "pr"]
    assert hotfix.steps["review"].max_iterations == 1

    assert simple.entry_point == "spec"
    assert list(simple.steps.keys()) == ["spec", "develop", "pr"]
    assert simple.steps["develop"].on["CAFE_CONFIRMED"] == "pr"
