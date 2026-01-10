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
        assert ".cafe/issues/test/develop/iteration_001/output.md" in content
        assert "Address each issue raised in the review" in content


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
