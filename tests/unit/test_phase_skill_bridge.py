"""Tests for legacy phase/checklist integration with SkillLoader."""

from pathlib import Path
from unittest.mock import patch

import pytest

from cafe.skills.bridge import load_skill_body, try_load_skill_body, try_load_skill_reference
from cafe.skills.checklist_composer import generate_plan_checklist
from cafe.skills.exceptions import SkillDiscoveryError


def test_generate_plan_checklist_uses_skill_references(tmp_path: Path) -> None:
    checklist_path = tmp_path / "checklist.md"

    generate_plan_checklist(
        agent_name="Nick",
        plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
        spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
        checklist_file_path=checklist_path,
        template_mode="manual",
    )

    content = checklist_path.read_text(encoding="utf-8")
    assert "planning, not implementation" in content
    assert ".cafe/issues/test/plan/iteration_001/output.md" in content


def test_load_skill_body_prefers_project_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    skill_dir = tmp_path / ".cafe" / "skills" / "cafe-plan"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: plan\ndescription: custom\n---\n\nCustom project plan skill\n",
        encoding="utf-8",
    )

    body = load_skill_body("cafe-plan")

    assert "Custom project plan skill" in body


def test_try_load_skill_body_returns_empty_string_on_skill_discovery_error() -> None:
    with patch("cafe.skills.bridge.load_skill_body", side_effect=SkillDiscoveryError("missing")):
        result = try_load_skill_body("missing")
    assert result == ""


def test_try_load_skill_body_returns_empty_string_on_file_not_found_error() -> None:
    with patch("cafe.skills.bridge.load_skill_body", side_effect=FileNotFoundError("gone")):
        result = try_load_skill_body("gone")
    assert result == ""


def test_try_load_skill_body_propagates_unexpected_errors() -> None:
    with patch("cafe.skills.bridge.load_skill_body", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            try_load_skill_body("cafe-plan")


def test_try_load_skill_reference_returns_empty_string_on_skill_discovery_error() -> None:
    with patch(
        "cafe.skills.bridge.load_skill_reference", side_effect=SkillDiscoveryError("missing")
    ):
        result = try_load_skill_reference("missing", "checklist.md")
    assert result == ""


def test_try_load_skill_reference_propagates_unexpected_errors() -> None:
    with patch(
        "cafe.skills.bridge.load_skill_reference", side_effect=RuntimeError("boom")
    ):
        with pytest.raises(RuntimeError):
            try_load_skill_reference("cafe-plan", "checklist.md")
