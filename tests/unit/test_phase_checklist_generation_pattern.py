"""Tests demonstrating how phases would integrate checklist generation.

These tests show the integration pattern that phases can follow to automatically
generate checklist files during execution.
"""

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


class TestPhaseChecklistGenerationPattern:
    """Test the pattern for integrating checklist generation into phases."""

    def test_spec_phase_would_call_generate_spec_checklist(self):
        """Test how spec phase would generate checklist during execution."""
        # Simulate spec phase context
        iteration = 1
        agent_name = "Roger"
        issue_dir = Path(".cafe/issues/test_issue")
        spec_dir = issue_dir / "spec" / "iteration_001"
        current_spec_file = str(spec_dir / "output.md")
        checklist_file = spec_dir / "checklist.md"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            checklist_path = tmpdir_path / "checklist.md"

            # This is what spec phase execute() would do:
            # 1. At start of iteration, generate checklist
            generate_spec_checklist(
                iteration=iteration,
                agent_name=agent_name,
                current_spec_file=current_spec_file,
                prev_spec_file=None,
                checklist_file_path=checklist_path,
            )

            # 2. Verify checklist was generated
            assert checklist_path.exists()

            # 3. Agent would receive prompt (not tested here, but checklist is ready)
            content = checklist_path.read_text()
            assert "Roger.md" in content
            assert current_spec_file in content

    def test_plan_phase_would_call_generate_plan_checklist(self):
        """Test how plan phase would generate checklist during execution."""
        # Simulate plan phase context
        agent_name = "Nick"
        issue_dir = Path(".cafe/issues/test_issue")
        plan_file = str(issue_dir / "plan" / "iteration_001" / "output.md")
        spec_file = str(issue_dir / "spec" / "iteration_001" / "output.md")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            checklist_path = tmpdir_path / "checklist.md"

            # This is what plan phase execute() would do:
            generate_plan_checklist(
                agent_name=agent_name,
                plan_file_path=plan_file,
                spec_file_path=spec_file,
                checklist_file_path=checklist_path,
            )

            assert checklist_path.exists()
            content = checklist_path.read_text()
            assert "Nick.md" in content
            assert spec_file in content

    def test_develop_phase_would_call_generate_develop_checklist(self):
        """Test how develop phase would generate checklist during execution."""
        # Simulate develop phase context
        agent_name = "Nick"
        issue_dir = Path(".cafe/issues/test_issue")
        spec_file = str(issue_dir / "spec" / "iteration_001" / "output.md")
        plan_file = str(issue_dir / "plan" / "iteration_001" / "output.md")
        correction_mode = False

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            checklist_path = tmpdir_path / "checklist.md"

            # This is what develop phase execute() would do:
            generate_develop_checklist(
                agent_name=agent_name,
                spec_file_path=spec_file,
                plan_file_path=plan_file,
                develop_file=None,
                checklist_file_path=checklist_path,
                correction_mode=correction_mode,
            )

            assert checklist_path.exists()
            content = checklist_path.read_text()
            assert "Nick.md" in content

    def test_review_phase_would_call_generate_review_checklist(self):
        """Test how review phase would generate checklist during execution."""
        # Simulate review phase context
        agent_name = "Richard"
        issue_dir = Path(".cafe/issues/test_issue")
        spec_file = str(issue_dir / "spec" / "iteration_001" / "output.md")
        review_file = str(issue_dir / "review" / "iteration_001" / "output.md")
        base_branch = "develop"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            checklist_path = tmpdir_path / "checklist.md"

            # This is what review phase execute() would do:
            generate_review_checklist(
                agent_name=agent_name,
                spec_file_path=spec_file,
                review_file_path=review_file,
                base_branch=base_branch,
                checklist_file_path=checklist_path,
            )

            assert checklist_path.exists()
            content = checklist_path.read_text()
            assert "Richard.md" in content
            assert base_branch in content

    def test_pr_phase_would_call_generate_pr_checklist(self):
        """Test how PR phase would generate checklist during execution."""
        # Simulate PR phase context
        agent_name = "Nick"
        issue_dir = Path(".cafe/issues/test_issue")
        spec_file = str(issue_dir / "spec" / "iteration_001" / "output.md")
        plan_file = str(issue_dir / "plan" / "iteration_001" / "output.md")
        pr_title_file = str(issue_dir / "pr" / "iteration_001" / "title.txt")
        pr_body_file = str(issue_dir / "pr" / "iteration_001" / "body.md")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            checklist_path = tmpdir_path / "checklist.md"

            # This is what PR phase execute() would do:
            generate_pr_checklist(
                agent_name=agent_name,
                spec_file_path=spec_file,
                plan_file_path=plan_file,
                pr_title_file=pr_title_file,
                pr_body_file=pr_body_file,
                checklist_file_path=checklist_path,
            )

            assert checklist_path.exists()
            content = checklist_path.read_text()
            assert "Nick.md" in content

    def test_integration_pattern_with_phase_base_class_helper(self):
        """Test that Phase base class provides helper for checklist generation."""
        from cafe.core.phase import Phase

        # Verify the helper method exists
        assert hasattr(Phase, "_generate_checklist_file")

        # Create a mock phase instance
        class MockPhase(Phase):
            def execute(self):
                pass

            def __init__(self):
                self.phase_dir = Path(".cafe/issues/test/mock")
                self.iteration = 1

        phase = MockPhase()

        # Default implementation returns None (subclasses can override)
        result = phase._generate_checklist_file()
        assert result is None  # Default behavior - subclasses override as needed


class TestChecklistGenerationErrorHandling:
    """Test that checklist generation handles errors gracefully."""

    def test_checklist_generation_with_missing_directory(self):
        """Test that checklist generation creates parent directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Path to a file in a non-existent directory
            checklist_path = Path(tmpdir) / "nonexistent" / "dir" / "checklist.md"

            # Generator should create parent directories
            generate_spec_checklist(
                iteration=1,
                agent_name="Roger",
                current_spec_file=".cafe/issues/test/spec/iteration_001/output.md",
                prev_spec_file=None,
                checklist_file_path=checklist_path,
            )

            assert checklist_path.exists()
            assert checklist_path.parent.exists()

    def test_checklist_generation_overwrites_existing_file(self):
        """Test that checklist generation overwrites existing checklists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            checklist_path = Path(tmpdir) / "checklist.md"

            # Create initial checklist
            checklist_path.write_text("Old content")

            # Generate new checklist (should overwrite)
            generate_spec_checklist(
                iteration=1,
                agent_name="Roger",
                current_spec_file=".cafe/issues/test/spec/iteration_001/output.md",
                prev_spec_file=None,
                checklist_file_path=checklist_path,
            )

            # Verify it was overwritten
            content = checklist_path.read_text()
            assert "Old content" not in content
            assert "## Execution Steps Checklist" in content
