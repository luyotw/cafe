"""測試 plan phase checklist 的 XML 問答指令支援"""

from pathlib import Path
from cafe.utils.checklist_generator import generate_plan_checklist


def test_generate_plan_checklist_includes_xml_instruction_when_path_provided(tmp_path):
    """測試 generate_plan_checklist() 接受 questions_xml_file 參數並包含 XML 指令"""
    checklist_path = tmp_path / "checklist.md"
    plan_file = tmp_path / "plan.md"
    spec_file = tmp_path / "spec.md"
    questions_xml_file = tmp_path / "questions.xml"

    generate_plan_checklist(
        agent_name="David",
        plan_file_path=str(plan_file),
        spec_file_path=str(spec_file),
        checklist_file_path=checklist_path,
        iteration=1,
        questions_xml_file=str(questions_xml_file),
    )

    content = checklist_path.read_text()

    # Verify XML schema and file path are included
    assert str(questions_xml_file) in content
    assert "<questions>" in content
    assert "need_clarification" in content


def test_generate_plan_checklist_without_xml_file_omits_instruction(tmp_path):
    """Test that XML instructions are omitted when questions_xml_file is not provided."""
    checklist_path = tmp_path / "checklist.md"
    plan_file = tmp_path / "plan.md"
    spec_file = tmp_path / "spec.md"

    generate_plan_checklist(
        agent_name="David",
        plan_file_path=str(plan_file),
        spec_file_path=str(spec_file),
        checklist_file_path=checklist_path,
        iteration=1,
        questions_xml_file=None,
    )

    content = checklist_path.read_text()

    assert "<questions>" not in content
    assert "need_clarification" not in content


def test_generate_plan_checklist_iteration_n_includes_xml_instruction(tmp_path):
    """Test that iteration > 1 also includes XML instructions."""
    checklist_path = tmp_path / "checklist.md"
    plan_file = tmp_path / "plan_002.md"
    prev_plan_file = tmp_path / "plan_001.md"
    spec_file = tmp_path / "spec.md"
    questions_xml_file = tmp_path / "questions.xml"

    generate_plan_checklist(
        agent_name="David",
        plan_file_path=str(plan_file),
        spec_file_path=str(spec_file),
        checklist_file_path=checklist_path,
        iteration=2,
        prev_plan_file=str(prev_plan_file),
        questions_xml_file=str(questions_xml_file),
    )

    content = checklist_path.read_text()

    assert str(questions_xml_file) in content
    assert "<questions>" in content
