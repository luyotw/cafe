"""Tests for checklist generator."""

import pytest
from pathlib import Path
from cafe.utils.checklist_generator import (
    generate_spec_checklist,
    generate_plan_checklist,
    generate_develop_checklist,
    generate_review_checklist,
    generate_pr_checklist,
)
from cafe.utils import checklist_templates


class TestGenerateSpecChecklist:
    """Tests for generate_spec_checklist function."""

    def test_generates_spec_checklist_iteration_1(self, tmp_path):
        """Test generates spec checklist for iteration 1."""
        checklist_path = tmp_path / "checklist.md"

        generate_spec_checklist(
            iteration=1,
            agent_name="Roger",
            current_spec_file=".cafe/issues/test/spec/iteration_001/output.md",
            prev_spec_file=None,
            checklist_file_path=checklist_path,
        )

        assert checklist_path.exists()
        content = checklist_path.read_text()
        assert "## Checklist" in content
        assert ".cafe/issues/test/spec/iteration_001/output.md" in content
        assert "Read README.md" in content  # Iteration 1 specific

    def test_generates_spec_checklist_iteration_n(self, tmp_path):
        """Test generates spec checklist for iteration N."""
        checklist_path = tmp_path / "checklist.md"

        generate_spec_checklist(
            iteration=2,
            agent_name="Roger",
            current_spec_file=".cafe/issues/test/spec/iteration_002/output.md",
            prev_spec_file=".cafe/issues/test/spec/iteration_001/output.md",
            checklist_file_path=checklist_path,
        )

        assert checklist_path.exists()
        content = checklist_path.read_text()
        assert ".cafe/issues/test/spec/iteration_001/output.md" in content
        assert ".cafe/issues/test/spec/iteration_002/output.md" in content
        assert "Review user's answer" in content  # Iteration N specific

    def test_adds_iteration_4_plus_constraint(self, tmp_path):
        """Test adds constraint for iteration 4+."""
        checklist_path = tmp_path / "checklist.md"

        generate_spec_checklist(
            iteration=4,
            agent_name="Roger",
            current_spec_file=".cafe/issues/test/spec/iteration_004/output.md",
            prev_spec_file=".cafe/issues/test/spec/iteration_003/output.md",
            checklist_file_path=checklist_path,
        )

        content = checklist_path.read_text()
        assert "Round 4" in content
        assert "Only clarify existing questions" in content


class TestGeneratePlanChecklist:
    """Tests for generate_plan_checklist function."""

    def test_generates_plan_checklist(self, tmp_path):
        """Test generates plan checklist."""
        checklist_path = tmp_path / "checklist.md"

        generate_plan_checklist(
            agent_name="Nick",
            plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
            spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
            checklist_file_path=checklist_path,
        )

        assert checklist_path.exists()
        content = checklist_path.read_text()
        assert "## Checklist" in content
        assert ".cafe/issues/test/plan/iteration_001/output.md" in content
        assert ".cafe/issues/test/spec/iteration_001/output.md" in content
        assert "planning, not implementation" in content


class TestGenerateDevelopChecklist:
    """Tests for generate_develop_checklist function."""

    def test_generates_develop_checklist_normal_mode(self, tmp_path):
        """Test generates develop checklist in normal mode."""
        checklist_path = tmp_path / "checklist.md"

        generate_develop_checklist(
            agent_name="David",
            spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
            plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
            develop_file=None,
            checklist_file_path=checklist_path,
            correction_mode=False,
        )

        assert checklist_path.exists()
        content = checklist_path.read_text()
        assert "## Checklist" in content
        assert "Execute development tasks in strict order" in content

    def test_generates_develop_checklist_correction_mode(self, tmp_path):
        """Test generates develop checklist in correction mode."""
        checklist_path = tmp_path / "checklist.md"

        generate_develop_checklist(
            agent_name="David",
            spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
            plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
            develop_file=".cafe/issues/test/develop/iteration_001/output.md",
            checklist_file_path=checklist_path,
            correction_mode=True,
        )

        assert checklist_path.exists()
        content = checklist_path.read_text()
        assert "Address each issue raised in the feedback" in content

    def test_develop_checklist_correction_mode_uses_unified_feedback_file_path(self, tmp_path):
        """Test that correction mode uses {feedback_file_path} instead of separate review/PR paths."""
        checklist_path = tmp_path / "checklist.md"

        generate_develop_checklist(
            agent_name="David",
            spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
            plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
            develop_file=None,
            checklist_file_path=checklist_path,
            correction_mode=True,
            feedback_file_path=".cafe/issues/test/review/iteration_001/output.md",
        )

        assert checklist_path.exists()
        content = checklist_path.read_text()
        # Should have unified feedback_file_path
        assert ".cafe/issues/test/review/iteration_001/output.md" in content
        assert "Read feedback todo list" in content
        assert "Mark completed items" in content
        # Should NOT have separate review_file_path or pr_feedback_file_path placeholders
        assert "{review_file_path}" not in content
        assert "{pr_feedback_file_path}" not in content


class TestGenerateReviewChecklist:
    """Tests for generate_review_checklist function."""

    def test_generates_review_checklist(self, tmp_path):
        """Test generates review checklist."""
        checklist_path = tmp_path / "checklist.md"

        generate_review_checklist(
            agent_name="Richard",
            spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
            review_file_path=".cafe/issues/test/review/iteration_001/output.md",
            base_branch="main",
            checklist_file_path=checklist_path,
        )

        assert checklist_path.exists()
        content = checklist_path.read_text()
        assert "## Checklist" in content
        assert ".cafe/issues/test/review/iteration_001/output.md" in content
        assert "git log main..HEAD" in content


    def test_review_checklist_includes_commit_message_checking(self, tmp_path):
        """Test that review checklist always includes commit message checking steps."""
        checklist_path = tmp_path / "checklist.md"

        generate_review_checklist(
            agent_name="Richard",
            spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
            review_file_path=".cafe/issues/test/review/iteration_001/output.md",
            base_branch="main",
            checklist_file_path=checklist_path,
        )

        content = checklist_path.read_text()
        # Verify commit message checking steps are present
        assert "git log main..HEAD" in content

    def test_review_checklist_includes_todo_list_format_instructions(self, tmp_path):
        """Test that review checklist includes instructions to output in todo list format."""
        checklist_path = tmp_path / "checklist.md"

        generate_review_checklist(
            agent_name="Richard",
            spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
            review_file_path=".cafe/issues/test/review/iteration_001/output.md",
            base_branch="main",
            checklist_file_path=checklist_path,
        )

        content = checklist_path.read_text()
        # Verify todo list format instructions are present
        assert "## Todo List" in content
        assert "- [ ]" in content
        assert "- [x]" in content


class TestGeneratePRChecklist:
    """Tests for generate_pr_checklist function."""

    def test_generates_pr_checklist(self, tmp_path):
        """Test generates PR checklist."""
        checklist_path = tmp_path / "checklist.md"

        generate_pr_checklist(
            agent_name="David",
            spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
            plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
            pr_file=".cafe/issues/test/pr/iteration_001/output.md",
            checklist_file_path=checklist_path,
        )

        assert checklist_path.exists()
        content = checklist_path.read_text()
        assert "## Checklist" in content
        assert ".cafe/issues/test/pr/iteration_001/output.md" in content


class TestChecklistFileCreation:
    """Tests for checklist file creation behavior."""

    def test_creates_parent_directories_automatically(self, tmp_path):
        """Test creates parent directories if they don't exist."""
        checklist_path = tmp_path / "nested" / "dir" / "checklist.md"

        generate_spec_checklist(
            iteration=1,
            agent_name="Roger",
            current_spec_file=".cafe/issues/test/spec/iteration_001/output.md",
            prev_spec_file=None,
            checklist_file_path=checklist_path,
        )

        assert checklist_path.exists()
        assert checklist_path.parent.exists()

    def test_resolves_agent_guidelines_from_real_agent_file(self, tmp_path):
        """Test resolves agent guidelines from actual agent file."""
        checklist_path = tmp_path / "checklist.md"

        # Use a real agent name that exists in the system
        generate_spec_checklist(
            iteration=1,
            agent_name="Roger",
            current_spec_file=".cafe/issues/test/spec/iteration_001/output.md",
            prev_spec_file=None,
            checklist_file_path=checklist_path,
        )

        content = checklist_path.read_text()
        # Should have agent guidelines checklist section
        assert "## Agent Guidelines Checklist" in content


class TestBasicPrinciples:
    """Tests for basic principles in checklists."""

    def test_spec_checklist_with_basic_principles(self, tmp_path):
        """Test spec checklist includes basic principles when provided."""
        checklist_path = tmp_path / "checklist.md"

        basic_principles = """- Write in native language
- No technical details"""

        generate_spec_checklist(
            iteration=1,
            agent_name="Roger",
            current_spec_file=".cafe/issues/test/spec/iteration_001/output.md",
            prev_spec_file=None,
            checklist_file_path=checklist_path,
            basic_principles=basic_principles,
        )

        content = checklist_path.read_text()
        assert "## Basic Principles" in content
        assert "[ ] Write in native language" in content
        assert "[ ] No technical details" in content

    def test_spec_checklist_with_template_file(self, tmp_path):
        """Test spec checklist includes template instruction when provided in manual mode."""
        checklist_path = tmp_path / "checklist.md"

        generate_spec_checklist(
            iteration=1,
            agent_name="Roger",
            current_spec_file=".cafe/issues/test/spec/iteration_001/output.md",
            prev_spec_file=None,
            checklist_file_path=checklist_path,
            template_file=".cafe/templates/spec.md",
            template_mode="manual",
        )

        content = checklist_path.read_text()
        assert ".cafe/templates/spec.md" in content

    def test_spec_checklist_auto_template_mode(self, tmp_path):
        """Test spec checklist lists available templates in auto mode."""
        checklist_path = tmp_path / "checklist.md"

        generate_spec_checklist(
            iteration=1,
            agent_name="Roger",
            current_spec_file=".cafe/issues/test/spec/iteration_001/output.md",
            prev_spec_file=None,
            checklist_file_path=checklist_path,
            template_mode="auto",
        )

        content = checklist_path.read_text()
        assert "Pick a most suitable spec template" in content

    def test_plan_checklist_with_basic_principles(self, tmp_path):
        """Test plan checklist includes basic principles when provided."""
        checklist_path = tmp_path / "checklist.md"

        basic_principles = """- Write plan content in your native language"""

        generate_plan_checklist(
            agent_name="Nick",
            plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
            spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
            checklist_file_path=checklist_path,
            basic_principles=basic_principles,
        )

        content = checklist_path.read_text()
        assert "## Basic Principles" in content
        assert "[ ] Write plan content in your native language" in content

    def test_plan_checklist_auto_template_mode(self, tmp_path):
        """Test plan checklist lists available templates in auto mode."""
        checklist_path = tmp_path / "checklist.md"

        generate_plan_checklist(
            agent_name="Nick",
            plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
            spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
            checklist_file_path=checklist_path,
            template_mode="auto",
        )

        content = checklist_path.read_text()
        assert "Pick a most suitable plan template" in content

    def test_develop_checklist_with_basic_principles(self, tmp_path):
        """Test develop checklist includes basic principles when provided."""
        checklist_path = tmp_path / "checklist.md"

        basic_principles = """- Follow existing commit message style
- Use same language as existing code comments
- Maximize code reuse"""

        generate_develop_checklist(
            agent_name="David",
            spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
            plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
            develop_file=None,
            checklist_file_path=checklist_path,
            correction_mode=False,
            basic_principles=basic_principles,
        )

        content = checklist_path.read_text()
        assert "## Basic Principles" in content
        assert "[ ] Follow existing commit message style" in content
        assert "[ ] Use same language as existing code comments" in content
        assert "[ ] Maximize code reuse" in content

    def test_pr_checklist_with_basic_principles(self, tmp_path):
        """Test PR checklist includes basic principles when provided."""
        checklist_path = tmp_path / "checklist.md"

        basic_principles = """- Use same language as commit messages"""

        generate_pr_checklist(
            agent_name="David",
            spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
            plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
            pr_file=".cafe/issues/test/pr/iteration_001/output.md",
            checklist_file_path=checklist_path,
            basic_principles=basic_principles,
        )

        content = checklist_path.read_text()
        assert "## Basic Principles" in content
        assert "[ ] Use same language as commit messages" in content

    def test_checklist_without_basic_principles(self, tmp_path):
        """Test checklist works without basic principles (backward compatibility)."""
        checklist_path = tmp_path / "checklist.md"

        generate_spec_checklist(
            iteration=1,
            agent_name="Roger",
            current_spec_file=".cafe/issues/test/spec/iteration_001/output.md",
            prev_spec_file=None,
            checklist_file_path=checklist_path,
            basic_principles=None,
        )

        content = checklist_path.read_text()
        # Should not have basic principles section
        assert "## Basic Principles" not in content
        # But should still have the main checklist
        assert "## Checklist" in content


class TestSpecChecklistXmlInstructions:
    """測試 spec checklist 包含 XML 問題檔產出指示"""

    def test_iteration_1_contains_xml_instructions(self, tmp_path):
        """測試 iteration 1 checklist 包含 XML 問題檔指示"""
        checklist_path = tmp_path / "checklist.md"

        generate_spec_checklist(
            iteration=1,
            agent_name="Roger",
            current_spec_file=".cafe/issues/test/spec/iteration_001/output.md",
            prev_spec_file=None,
            checklist_file_path=checklist_path,
            questions_xml_file=".cafe/issues/test/spec/iteration_001/questions.xml",
        )

        content = checklist_path.read_text()
        assert "questions.xml" in content
        assert "<questions>" in content
        assert "<question" in content
        assert "If user clarification is needed" in content

    def test_iteration_n_contains_xml_instructions(self, tmp_path):
        """測試 iteration N checklist 包含 XML 問題檔指示"""
        checklist_path = tmp_path / "checklist.md"

        generate_spec_checklist(
            iteration=2,
            agent_name="Roger",
            current_spec_file=".cafe/issues/test/spec/iteration_002/output.md",
            prev_spec_file=".cafe/issues/test/spec/iteration_001/output.md",
            checklist_file_path=checklist_path,
            questions_xml_file=".cafe/issues/test/spec/iteration_002/questions.xml",
        )

        content = checklist_path.read_text()
        assert "questions.xml" in content
        assert "<questions>" in content

    def test_no_xml_instructions_without_questions_xml_file(self, tmp_path):
        """Test that XML schema instructions are not included when questions_xml_file is not provided."""
        checklist_path = tmp_path / "checklist.md"

        generate_spec_checklist(
            iteration=1,
            agent_name="Roger",
            current_spec_file=".cafe/issues/test/spec/iteration_001/output.md",
            prev_spec_file=None,
            checklist_file_path=checklist_path,
        )

        content = checklist_path.read_text()
        assert "<questions>" not in content

    def test_xml_instructions_require_native_language(self, tmp_path):
        """測試 XML 指示要求用 agent 母語撰寫問題"""
        checklist_path = tmp_path / "checklist.md"

        generate_spec_checklist(
            iteration=1,
            agent_name="Roger",
            current_spec_file=".cafe/issues/test/spec/iteration_001/output.md",
            prev_spec_file=None,
            checklist_file_path=checklist_path,
            questions_xml_file=".cafe/issues/test/spec/iteration_001/questions.xml",
        )

        content = checklist_path.read_text()
        assert "questions.xml" in content
        # 驗證 Rules 部分要求用母語撰寫問題
        assert "Rules:" in content
        # 檢查 Rules 後面有提到母語要求
        rules_section = content.split("Rules:")[1].split("```")[0]
        assert "native language" in rules_section.lower()


class TestSpecDodInstruction:
    """Tests for DoD instruction constant in checklist_templates."""

    def test_spec_dod_instruction_constant_exists(self) -> None:
        """Test that SPEC_DOD_INSTRUCTION constant exists in checklist_templates."""
        assert hasattr(checklist_templates, "SPEC_DOD_INSTRUCTION"), (
            "SPEC_DOD_INSTRUCTION constant should exist in checklist_templates"
        )

    def test_spec_dod_instruction_contains_dod_questions(self) -> None:
        """Test that SPEC_DOD_INSTRUCTION mentions DoD questions."""
        instruction = checklist_templates.SPEC_DOD_INSTRUCTION
        assert "DoD" in instruction, "SPEC_DOD_INSTRUCTION should mention DoD"

    def test_spec_dod_instruction_mentions_functional_requirements(self) -> None:
        """Test that SPEC_DOD_INSTRUCTION focuses on functional requirements."""
        instruction = checklist_templates.SPEC_DOD_INSTRUCTION
        assert "functional" in instruction.lower(), (
            "SPEC_DOD_INSTRUCTION should focus on functional requirements"
        )

    def test_spec_dod_instruction_mentions_custom_input(self) -> None:
        """Test that SPEC_DOD_INSTRUCTION allows custom user input."""
        instruction = checklist_templates.SPEC_DOD_INSTRUCTION
        assert "custom" in instruction.lower() or "optional" in instruction.lower(), (
            "SPEC_DOD_INSTRUCTION should mention custom/optional user input"
        )

    def test_spec_dod_instruction_mentions_acceptance_criteria(self) -> None:
        """Test that SPEC_DOD_INSTRUCTION instructs integration into Acceptance Criteria."""
        instruction = checklist_templates.SPEC_DOD_INSTRUCTION
        assert "Acceptance Criteria" in instruction, (
            "SPEC_DOD_INSTRUCTION should mention integration into Acceptance Criteria"
        )

    def test_spec_dod_instruction_mentions_dod_prefix_format(self) -> None:
        """Test that SPEC_DOD_INSTRUCTION specifies the DoD: prefix format."""
        instruction = checklist_templates.SPEC_DOD_INSTRUCTION
        assert "**DoD:**" in instruction, (
            "SPEC_DOD_INSTRUCTION should specify the DoD: prefix format"
        )

    def test_spec_dod_instruction_mentions_checkbox_type(self) -> None:
        """Test that SPEC_DOD_INSTRUCTION instructs PM to use type="checkbox" for DoD questions."""
        instruction = checklist_templates.SPEC_DOD_INSTRUCTION
        assert "checkbox" in instruction, (
            "SPEC_DOD_INSTRUCTION should instruct PM to use type='checkbox' for multi-select"
        )

    def test_spec_dod_instruction_does_not_contain_status_code_strings(self) -> None:
        """Test that SPEC_DOD_INSTRUCTION does not contain literal CAFE_ status codes.

        The status code parser (extract_all) scans the entire agent response for
        CAFE_ prefixed codes. If the DoD instruction contains a literal status code
        like CAFE_READY_FOR_REVIEW, and the agent also returns CAFE_NEED_CLARIFICATION,
        the parser finds two different codes and returns None, causing the phase to fail.
        """
        instruction = checklist_templates.SPEC_DOD_INSTRUCTION
        assert "CAFE_" not in instruction, (
            "SPEC_DOD_INSTRUCTION must not contain literal CAFE_ status code strings "
            "to avoid conflicts with status code parser"
        )


class TestSpecChecklistDodIntegration:
    """Tests for DoD instruction integration in spec checklist generation."""

    def test_iteration_2_checklist_contains_dod_instruction(self, tmp_path: Path) -> None:
        """Test that iteration 2+ checklist contains DoD instruction."""
        checklist_path = tmp_path / "checklist.md"

        generate_spec_checklist(
            iteration=2,
            agent_name="Roger",
            current_spec_file=".cafe/issues/test/spec/iteration_002/output.md",
            prev_spec_file=".cafe/issues/test/spec/iteration_001/output.md",
            checklist_file_path=checklist_path,
            questions_xml_file=".cafe/issues/test/spec/iteration_002/questions.xml",
        )

        content = checklist_path.read_text()
        assert "DoD" in content, "Iteration 2+ checklist should contain DoD instruction"
        assert "Definition of Done" in content, (
            "Iteration 2+ checklist should contain 'Definition of Done' section"
        )

    def test_iteration_3_checklist_contains_dod_instruction(self, tmp_path: Path) -> None:
        """Test that iteration 3+ checklist also contains DoD instruction."""
        checklist_path = tmp_path / "checklist.md"

        generate_spec_checklist(
            iteration=3,
            agent_name="Roger",
            current_spec_file=".cafe/issues/test/spec/iteration_003/output.md",
            prev_spec_file=".cafe/issues/test/spec/iteration_002/output.md",
            checklist_file_path=checklist_path,
        )

        content = checklist_path.read_text()
        assert "DoD" in content, "Iteration 3+ checklist should contain DoD instruction"

    def test_iteration_1_checklist_contains_dod_instruction(self, tmp_path: Path) -> None:
        """Test that iteration 1 checklist also contains DoD instruction.

        DoD is asked in every iteration so it is not skipped when requirements are immediately clear.
        """
        checklist_path = tmp_path / "checklist.md"

        generate_spec_checklist(
            iteration=1,
            agent_name="Roger",
            current_spec_file=".cafe/issues/test/spec/iteration_001/output.md",
            prev_spec_file=None,
            checklist_file_path=checklist_path,
            questions_xml_file=".cafe/issues/test/spec/iteration_001/questions.xml",
        )

        content = checklist_path.read_text()
        assert "Definition of Done" in content, (
            "Iteration 1 checklist should contain DoD instruction"
        )

    def test_dod_instruction_references_questions_xml(self, tmp_path: Path) -> None:
        """Test that DoD instruction tells PM to append DoD questions to questions.xml."""
        checklist_path = tmp_path / "checklist.md"

        generate_spec_checklist(
            iteration=2,
            agent_name="Roger",
            current_spec_file=".cafe/issues/test/spec/iteration_002/output.md",
            prev_spec_file=".cafe/issues/test/spec/iteration_001/output.md",
            checklist_file_path=checklist_path,
        )

        content = checklist_path.read_text()
        assert "questions.xml" in content, (
            "DoD instruction should reference questions.xml"
        )
