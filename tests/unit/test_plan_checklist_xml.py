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

    # 檢查包含 XML 問答指令
    assert "Interactive Q&A Questions (Conditional)" in content
    assert "CAFE_NEED_CLARIFICATION" in content
    assert str(questions_xml_file) in content
    assert "<?xml version=" in content
    assert "<questions>" in content
    assert "<question id=" in content
    assert "<title>" in content
    assert "<options>" in content


def test_generate_plan_checklist_without_xml_file_omits_instruction(tmp_path):
    """測試 generate_plan_checklist() 未提供 questions_xml_file 時不包含 XML 指令"""
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

    # 檢查不包含 XML 問答指令
    assert "Interactive Q&A Questions" not in content
    assert "<?xml version=" not in content


def test_generate_plan_checklist_iteration_n_includes_xml_instruction(tmp_path):
    """測試 iteration > 1 時也包含 XML 指令"""
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

    # 檢查包含 XML 問答指令
    assert "Interactive Q&A Questions (Conditional)" in content
    assert str(questions_xml_file) in content
