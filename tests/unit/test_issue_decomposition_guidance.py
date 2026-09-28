"""Conditional issue-splitting guidance and removal of mandatory forms."""

import re
from pathlib import Path

import pytest

from cafe.phases.generic_phase import GenericPhase
from cafe.skills.loader import SkillLoader

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = PROJECT_ROOT / "src" / "cafe" / "data" / "skills"


def _read(relative_path: str) -> str:
    return (SKILLS_ROOT / relative_path).read_text(encoding="utf-8")


@pytest.mark.parametrize("context", [None, {}, {"allow_issue_decomposition": False}])
def test_disabled_decomposition_does_not_require_shared_reference(tmp_path, context):
    loader = SkillLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "empty",
    )
    prompt = GenericPhase(loader).build_prompt(
        skill_name="custom", skill_invocation="/custom", context=context
    )
    assert "Issue Split Proposals" not in prompt


def test_phase_skills_and_templates_no_longer_require_assessments():
    for name in ("cafe-spec", "cafe-plan"):
        surfaces = [_read(f"{name}/SKILL.md")]
        surfaces.extend(
            path.read_text(encoding="utf-8")
            for path in (SKILLS_ROOT / name / "assets" / "templates").glob("*.md")
        )
        for surface in surfaces:
            assert "Issue Decomposition Assessment" not in surface
            assert "Decision: `keep` or `split`" not in surface
            assert "issue_decomposition.md" not in surface


def test_driver_receives_proposals_without_stage_or_extra_gate():
    driver = " ".join(_read("use-cafe-workflow/references/issue_decomposition.md").split())
    assert "any step" in driver
    assert "including workflow completion" in driver
    assert "proposal alone adds no checkpoint or validation gate" in driver
    assert "existing authorized Driver path" in driver
    assert "grant no authority to create issues" in driver
    assert "must not enter develop" not in driver
    assert "Decision:" not in driver


def test_touched_driver_reference_paths_resolve_from_skill_root():
    for name in ("issue_decomposition", "model_selection"):
        text = _read(f"use-cafe-workflow/references/{name}.md")
        for reference in re.findall(r"`(references/[^`]+\.md)`", text):
            assert (SKILLS_ROOT / "use-cafe-workflow" / reference).is_file()


def test_project_position_is_reconstructed_from_durable_records() -> None:
    """UT-004 — position has required fields and no second state store."""
    driver = _read("use-cafe-workflow/references/issue_decomposition.md")

    for field in (
        "project",
        "milestone",
        "current issue",
        "current phase",
        "completed count",
        "blocked issues",
        "next action",
        "required user decision",
        "strategic context",
        "confirmed roadmap",
        "issue state",
        "active workflow records",
        "Do not create duplicate project state",
    ):
        assert field in driver
