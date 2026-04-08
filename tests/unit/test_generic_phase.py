"""Tests for GenericPhase."""

from pathlib import Path

import pytest

from cafe.core.status_codes import PhaseStatusCode
from cafe.phases.generic_phase import GenericPhase
from cafe.skills.loader import SkillLoader


def _setup_loader(tmp_path: Path) -> SkillLoader:
    builtin = tmp_path / "builtin" / "skills" / "plan"
    builtin.mkdir(parents=True, exist_ok=True)
    (builtin / "SKILL.md").write_text(
        "---\nname: plan\ndescription: desc\n---\n\nHello {who}\n",
        encoding="utf-8",
    )
    loader = SkillLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )
    loader.discover()
    return loader


def test_build_prompt_includes_files_and_checklist_guard(tmp_path: Path) -> None:
    phase = GenericPhase(_setup_loader(tmp_path))
    prompt = phase.build_prompt(
        skill_name="plan",
        context={"who": "team"},
        output_file=Path("out.md"),
        checklist_file=Path("checklist.md"),
        questions_xml_file=Path("questions.xml"),
    )
    assert "Hello team" in prompt
    assert "Do NOT return a status code until ALL checklist items are marked as [x]." in prompt
    assert "questions.xml" in prompt


def test_parse_response_extracts_status_and_goto(tmp_path: Path) -> None:
    phase = GenericPhase(_setup_loader(tmp_path))
    status, goto_target = phase.parse_response(
        response="CAFE_CONFIRMED\nCAFE_GOTO:review",
        valid_status_codes=[PhaseStatusCode.CONFIRMED],
    )
    assert status == PhaseStatusCode.CONFIRMED
    assert goto_target == "review"


def test_validate_clarification_output_requires_valid_xml(tmp_path: Path) -> None:
    phase = GenericPhase(_setup_loader(tmp_path))
    xml_file = tmp_path / "questions.xml"
    xml_file.write_text("<bad></bad>", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        phase.validate_clarification_output(
            status_code=PhaseStatusCode.NEED_CLARIFICATION,
            questions_xml_file=xml_file,
        )
