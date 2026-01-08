"""Tests for checklist templates."""

import pytest
from cafe.utils import checklist_templates


class TestSpecPhaseTemplates:
    """Tests for spec phase checklist templates."""

    def test_spec_execution_steps_iteration_1_has_required_placeholders(self):
        """Test spec iteration 1 template has expected placeholders."""
        template = checklist_templates.SPEC_EXECUTION_STEPS_ITERATION_1

        assert "{agent_file}" in template
        assert "{current_spec_file}" in template
        assert "## Execution Steps Checklist" in template

    def test_spec_execution_steps_iteration_n_has_required_placeholders(self):
        """Test spec iteration N template has expected placeholders."""
        template = checklist_templates.SPEC_EXECUTION_STEPS_ITERATION_N

        assert "{agent_file}" in template
        assert "{prev_spec_file}" in template
        assert "{current_spec_file}" in template

    def test_spec_important_notes_has_required_placeholders(self):
        """Test spec important notes template has expected placeholders."""
        template = checklist_templates.SPEC_IMPORTANT_NOTES

        assert "{current_spec_file}" in template
        assert "## Important Notes Checklist" in template


class TestPlanPhaseTemplates:
    """Tests for plan phase checklist templates."""

    def test_plan_execution_steps_has_required_placeholders(self):
        """Test plan execution steps template has expected placeholders."""
        template = checklist_templates.PLAN_EXECUTION_STEPS

        assert "{agent_file}" in template
        assert "{plan_file_path}" in template
        assert "{spec_file_path}" in template
        assert "## Execution Steps Checklist" in template

    def test_plan_important_notes_contains_planning_reminders(self):
        """Test plan important notes emphasize planning not implementation."""
        template = checklist_templates.PLAN_IMPORTANT_NOTES

        assert "PLANNING" in template
        assert "not implementation" in template


class TestDevelopPhaseTemplates:
    """Tests for develop phase checklist templates."""

    def test_develop_normal_mode_has_required_placeholders(self):
        """Test develop normal mode template has expected placeholders."""
        template = checklist_templates.DEVELOP_EXECUTION_STEPS_NORMAL

        assert "{agent_file}" in template
        assert "{spec_file_path}" in template
        assert "{plan_file_path}" in template

    def test_develop_correction_mode_has_required_placeholders(self):
        """Test develop correction mode template has expected placeholders."""
        template = checklist_templates.DEVELOP_EXECUTION_STEPS_CORRECTION

        assert "{agent_file}" in template
        assert "{develop_file}" in template
        assert "{spec_file_path}" in template
        assert "{plan_file_path}" in template

    def test_develop_verification_checklist_has_required_placeholders(self):
        """Test develop verification checklist has expected placeholders."""
        template = checklist_templates.DEVELOP_VERIFICATION_CHECKLIST

        assert "{plan_file_path}" in template
        assert "## Verification Checklist" in template


class TestReviewPhaseTemplates:
    """Tests for review phase checklist templates."""

    def test_review_execution_steps_has_required_placeholders(self):
        """Test review execution steps template has expected placeholders."""
        template = checklist_templates.REVIEW_EXECUTION_STEPS

        assert "{agent_file}" in template
        assert "{review_file_path}" in template

    def test_review_tasks_checklist_has_required_placeholders(self):
        """Test review tasks checklist has expected placeholders."""
        template = checklist_templates.REVIEW_TASKS_CHECKLIST

        assert "{base_branch}" in template
        assert "{spec_file_path}" in template
        assert "## Review Tasks Checklist" in template


class TestPRPhaseTemplates:
    """Tests for PR phase checklist templates."""

    def test_pr_execution_steps_has_required_placeholders(self):
        """Test PR execution steps template has expected placeholders."""
        template = checklist_templates.PR_EXECUTION_STEPS

        assert "{agent_file}" in template
        assert "{spec_file_path}" in template
        assert "{plan_file_path}" in template

    def test_pr_important_notes_has_required_placeholders(self):
        """Test PR important notes template has expected placeholders."""
        template = checklist_templates.PR_IMPORTANT_NOTES

        assert "{pr_title_file}" in template
        assert "{pr_body_file}" in template


class TestTemplateConsistency:
    """Tests for consistency across templates."""

    def test_all_templates_start_with_section_header(self):
        """Test all templates start with markdown section header."""
        templates = [
            checklist_templates.SPEC_EXECUTION_STEPS_ITERATION_1,
            checklist_templates.SPEC_EXECUTION_STEPS_ITERATION_N,
            checklist_templates.SPEC_IMPORTANT_NOTES,
            checklist_templates.PLAN_EXECUTION_STEPS,
            checklist_templates.PLAN_IMPORTANT_NOTES,
            checklist_templates.DEVELOP_EXECUTION_STEPS_NORMAL,
            checklist_templates.DEVELOP_EXECUTION_STEPS_CORRECTION,
            checklist_templates.DEVELOP_IMPORTANT_NOTES,
            checklist_templates.DEVELOP_VERIFICATION_CHECKLIST,
            checklist_templates.REVIEW_EXECUTION_STEPS,
            checklist_templates.REVIEW_TASKS_CHECKLIST,
            checklist_templates.REVIEW_IMPORTANT_NOTES,
            checklist_templates.PR_EXECUTION_STEPS,
            checklist_templates.PR_IMPORTANT_NOTES,
        ]

        for template in templates:
            assert template.strip().startswith("##"), f"Template should start with section header: {template[:50]}"

    def test_all_execution_steps_read_agent_file_first(self):
        """Test all execution steps checklists read agent file first."""
        execution_templates = [
            checklist_templates.SPEC_EXECUTION_STEPS_ITERATION_1,
            checklist_templates.SPEC_EXECUTION_STEPS_ITERATION_N,
            checklist_templates.PLAN_EXECUTION_STEPS,
            checklist_templates.DEVELOP_EXECUTION_STEPS_NORMAL,
            checklist_templates.DEVELOP_EXECUTION_STEPS_CORRECTION,
            checklist_templates.REVIEW_EXECUTION_STEPS,
            checklist_templates.PR_EXECUTION_STEPS,
        ]

        for template in execution_templates:
            lines = [line for line in template.split("\n") if line.strip().startswith("[ ]")]
            if lines:
                first_item = lines[0]
                assert "agent_file" in first_item, f"First checklist item should read agent file: {first_item}"
