"""Tests for phase checklist integration."""

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from cafe.utils.checklist_generator import (
    generate_spec_checklist,
    generate_plan_checklist,
    generate_develop_checklist,
    generate_review_checklist,
    generate_pr_checklist,
)


class TestSpecChecklistGeneration:
    """Test spec phase checklist generation."""

    def test_generates_spec_checklist_iteration_1(self):
        """Test spec checklist generation for iteration 1."""
        with tempfile.TemporaryDirectory() as tmpdir:
            checklist_path = Path(tmpdir) / "checklist.md"

            generate_spec_checklist(
                iteration=1,
                agent_name="Roger",
                current_spec_file=".cafe/issues/test/spec/iteration_001/output.md",
                prev_spec_file=None,
                checklist_file_path=checklist_path,
            )

            assert checklist_path.exists()
            content = checklist_path.read_text()
            assert "## Execution Steps Checklist" in content
            assert "Roger.md" in content  # Agent file reference

    def test_generates_spec_checklist_iteration_n(self):
        """Test spec checklist generation for iteration > 1."""
        with tempfile.TemporaryDirectory() as tmpdir:
            checklist_path = Path(tmpdir) / "checklist.md"

            generate_spec_checklist(
                iteration=2,
                agent_name="Roger",
                current_spec_file=".cafe/issues/test/spec/iteration_002/output.md",
                prev_spec_file=".cafe/issues/test/spec/iteration_001/output.md",
                checklist_file_path=checklist_path,
            )

            assert checklist_path.exists()
            content = checklist_path.read_text()
            assert "## Execution Steps Checklist" in content


class TestPlanChecklistGeneration:
    """Test plan phase checklist generation."""

    def test_generates_plan_checklist(self):
        """Test plan checklist generation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            checklist_path = Path(tmpdir) / "checklist.md"

            generate_plan_checklist(
                agent_name="Nick",
                plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
                spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
                checklist_file_path=checklist_path,
            )

            assert checklist_path.exists()
            content = checklist_path.read_text()
            assert "## Execution Steps Checklist" in content


class TestDevelopChecklistGeneration:
    """Test develop phase checklist generation."""

    def test_generates_develop_checklist_normal_mode(self):
        """Test develop checklist generation for normal mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            checklist_path = Path(tmpdir) / "checklist.md"

            generate_develop_checklist(
                agent_name="Nick",
                spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
                plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
                develop_file=None,
                checklist_file_path=checklist_path,
                correction_mode=False,
            )

            assert checklist_path.exists()
            content = checklist_path.read_text()
            assert "## Execution Steps Checklist" in content

    def test_generates_develop_checklist_correction_mode(self):
        """Test develop checklist generation for correction mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            checklist_path = Path(tmpdir) / "checklist.md"

            generate_develop_checklist(
                agent_name="Nick",
                spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
                plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
                develop_file=".cafe/issues/test/develop/iteration_001/output.md",
                checklist_file_path=checklist_path,
                correction_mode=True,
            )

            assert checklist_path.exists()
            content = checklist_path.read_text()
            assert "## Execution Steps Checklist" in content


class TestReviewChecklistGeneration:
    """Test review phase checklist generation."""

    def test_generates_review_checklist(self):
        """Test review checklist generation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            checklist_path = Path(tmpdir) / "checklist.md"

            generate_review_checklist(
                agent_name="Richard",
                spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
                review_file_path=".cafe/issues/test/review/iteration_001/output.md",
                base_branch="main",
                checklist_file_path=checklist_path,
            )

            assert checklist_path.exists()
            content = checklist_path.read_text()
            assert "## Execution Steps Checklist" in content


class TestPRChecklistGeneration:
    """Test PR phase checklist generation."""

    def test_generates_pr_checklist(self):
        """Test PR checklist generation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            checklist_path = Path(tmpdir) / "checklist.md"

            generate_pr_checklist(
                agent_name="Nick",
                spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
                plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
                pr_file=".cafe/issues/test/pr/iteration_001/output.md",
                checklist_file_path=checklist_path,
            )

            assert checklist_path.exists()
            content = checklist_path.read_text()
            assert "## Execution Steps Checklist" in content
