"""Integration tests for checklist workflow."""

import tempfile
from pathlib import Path

from cafe.utils.checklist_generator import (
    generate_spec_checklist,
    generate_plan_checklist,
    generate_develop_checklist,
    generate_review_checklist,
    generate_pr_checklist,
)


class TestChecklistWorkflowIntegration:
    """Test complete checklist generation workflow."""

    def test_full_workflow_checklist_generation(self):
        """Test generating checklists for all phases in a workflow."""
        with tempfile.TemporaryDirectory() as tmpdir:
            issue_dir = Path(tmpdir) / ".cafe" / "issues" / "test_issue"

            # Generate spec phase checklist
            spec_dir = issue_dir / "spec" / "iteration_001"
            spec_dir.mkdir(parents=True)
            spec_checklist = spec_dir / "checklist.md"

            generate_spec_checklist(
                iteration=1,
                agent_name="Roger",
                current_spec_file=str(spec_dir / "output.md"),
                prev_spec_file=None,
                checklist_file_path=spec_checklist,
            )

            assert spec_checklist.exists()
            spec_content = spec_checklist.read_text()
            assert "## Execution Steps Checklist" in spec_content
            assert "Roger.md" in spec_content

            # Generate plan phase checklist
            plan_dir = issue_dir / "plan" / "iteration_001"
            plan_dir.mkdir(parents=True)
            plan_checklist = plan_dir / "checklist.md"

            generate_plan_checklist(
                agent_name="Nick",
                plan_file_path=str(plan_dir / "output.md"),
                spec_file_path=str(spec_dir / "output.md"),
                checklist_file_path=plan_checklist,
            )

            assert plan_checklist.exists()
            plan_content = plan_checklist.read_text()
            assert "## Execution Steps Checklist" in plan_content
            assert "Nick.md" in plan_content

            # Generate develop phase checklist
            develop_dir = issue_dir / "develop" / "iteration_001"
            develop_dir.mkdir(parents=True)
            develop_checklist = develop_dir / "checklist.md"

            generate_develop_checklist(
                agent_name="Nick",
                spec_file_path=str(spec_dir / "output.md"),
                plan_file_path=str(plan_dir / "output.md"),
                develop_file=None,
                checklist_file_path=develop_checklist,
                correction_mode=False,
            )

            assert develop_checklist.exists()
            develop_content = develop_checklist.read_text()
            assert "## Execution Steps Checklist" in develop_content

            # Generate review phase checklist
            review_dir = issue_dir / "review" / "iteration_001"
            review_dir.mkdir(parents=True)
            review_checklist = review_dir / "checklist.md"

            generate_review_checklist(
                agent_name="Richard",
                spec_file_path=str(spec_dir / "output.md"),
                review_file_path=str(review_dir / "output.md"),
                base_branch="develop",
                checklist_file_path=review_checklist,
            )

            assert review_checklist.exists()
            review_content = review_checklist.read_text()
            assert "## Execution Steps Checklist" in review_content
            assert "Richard.md" in review_content

            # Generate PR phase checklist
            pr_dir = issue_dir / "pr" / "iteration_001"
            pr_dir.mkdir(parents=True)
            pr_checklist = pr_dir / "checklist.md"

            generate_pr_checklist(
                agent_name="Nick",
                spec_file_path=str(spec_dir / "output.md"),
                plan_file_path=str(plan_dir / "output.md"),
                pr_title_file=str(pr_dir / "title.txt"),
                pr_body_file=str(pr_dir / "body.md"),
                checklist_file_path=pr_checklist,
            )

            assert pr_checklist.exists()
            pr_content = pr_checklist.read_text()
            assert "## Execution Steps Checklist" in pr_content

            # Verify all checklists were created
            assert spec_checklist.exists()
            assert plan_checklist.exists()
            assert develop_checklist.exists()
            assert review_checklist.exists()
            assert pr_checklist.exists()

    def test_checklist_file_path_resolution(self):
        """Test that checklist files resolve placeholders correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            checklist_path = Path(tmpdir) / "checklist.md"
            spec_file = ".cafe/issues/myissue/spec/iteration_001/output.md"

            generate_spec_checklist(
                iteration=1,
                agent_name="Roger",
                current_spec_file=spec_file,
                prev_spec_file=None,
                checklist_file_path=checklist_path,
            )

            content = checklist_path.read_text()
            # Verify placeholder was resolved
            assert spec_file in content
            assert "{current_spec_file}" not in content
            assert ".cafe/agents/pm/Roger.md" in content

    def test_multiple_iteration_checklists(self):
        """Test generating checklists for multiple iterations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            issue_dir = Path(tmpdir) / ".cafe" / "issues" / "test_issue" / "spec"

            # Iteration 1
            iter1_dir = issue_dir / "iteration_001"
            iter1_dir.mkdir(parents=True)
            iter1_checklist = iter1_dir / "checklist.md"

            generate_spec_checklist(
                iteration=1,
                agent_name="Roger",
                current_spec_file=str(iter1_dir / "output.md"),
                prev_spec_file=None,
                checklist_file_path=iter1_checklist,
            )

            # Iteration 2
            iter2_dir = issue_dir / "iteration_002"
            iter2_dir.mkdir(parents=True)
            iter2_checklist = iter2_dir / "checklist.md"

            generate_spec_checklist(
                iteration=2,
                agent_name="Roger",
                current_spec_file=str(iter2_dir / "output.md"),
                prev_spec_file=str(iter1_dir / "output.md"),
                checklist_file_path=iter2_checklist,
            )

            # Verify both checklists exist and have different content
            assert iter1_checklist.exists()
            assert iter2_checklist.exists()

            iter1_content = iter1_checklist.read_text()
            iter2_content = iter2_checklist.read_text()

            # Both should have execution steps
            assert "## Execution Steps Checklist" in iter1_content
            assert "## Execution Steps Checklist" in iter2_content

            # Iteration 2 should reference previous iteration
            assert "iteration_001" in iter2_content
