"""Tests for Task 3: Update _generate_pr_content() for unified output"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from cafe.phases.pr_phase import PRPhase
from cafe.core.types import WorkflowMode, PhaseResult, PhaseStatus


class TestGeneratePRContentWithOutputMd:
    """Test _generate_pr_content() with output.md format"""

    def test_creates_placeholder_with_correct_format(self, tmp_path):
        """Test that placeholder file is created with correct H1 format"""
        issue_dir = tmp_path / ".cafe" / "issues" / "test_issue"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True)
        iteration_spec_dir = spec_dir / "iteration_001"
        iteration_spec_dir.mkdir(parents=True)
        spec_file = iteration_spec_dir / "output.md"
        spec_file.write_text("# Spec")
        
        with patch('cafe.phases.pr_phase.PRPhase._get_issue_dir', return_value=issue_dir):
            with patch('cafe.phases.pr_phase.PRPhase._get_latest_versioned_file', return_value=spec_file):
                phase = PRPhase(
                    agent_manager=Mock(),
                    permission_handler=Mock(),
                    git_ops=Mock(),
                    github_ops=Mock(),
                    spec_file=str(spec_file),
                    workflow_mode=WorkflowMode.LOCAL,
                )
                phase.iteration = 1
                phase.base_branch = "main"
                phase._get_current_branch_commits = Mock(return_value="commit1\ncommit2")
                phase._execute_and_handle_agent_response = Mock(return_value=(None, "response"))
                
                # Execute
                phase._generate_pr_content()
                
                # Verify placeholder file was created
                pr_dir = issue_dir / "pr"
                iteration_dir = pr_dir / "iteration_001"  # File created before iteration increment
                output_file = iteration_dir / "output.md"
                
                assert output_file.exists()
                content = output_file.read_text()
                assert content == "# TODO: Write PR title\n\nTODO: Write PR body\n"

    def test_prompt_references_single_output_file(self, tmp_path):
        """Test that agent prompt references only output.md"""
        issue_dir = tmp_path / ".cafe" / "issues" / "test_issue"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True)
        iteration_spec_dir = spec_dir / "iteration_001"
        iteration_spec_dir.mkdir(parents=True)
        spec_file = iteration_spec_dir / "output.md"
        spec_file.write_text("# Spec")
        
        with patch('cafe.phases.pr_phase.PRPhase._get_issue_dir', return_value=issue_dir):
            with patch('cafe.phases.pr_phase.PRPhase._get_latest_versioned_file', return_value=spec_file):
                phase = PRPhase(
                    agent_manager=Mock(),
                    permission_handler=Mock(),
                    git_ops=Mock(),
                    github_ops=Mock(),
                    spec_file=str(spec_file),
                    workflow_mode=WorkflowMode.LOCAL,
                )
                phase.iteration = 1
                phase.base_branch = "main"
                phase._get_current_branch_commits = Mock(return_value="commit1\ncommit2")
                phase._execute_and_handle_agent_response = Mock(return_value=(None, "response"))
                
                # Execute
                phase._generate_pr_content()
                
                # Verify prompt mentions output.md
                assert "output.md" in phase._current_prompt
                assert "title.txt" not in phase._current_prompt
                assert "body.md" not in phase._current_prompt

    def test_allowed_tools_includes_only_output_md(self, tmp_path):
        """Test that allowed_tools includes only edit(output.md)"""
        issue_dir = tmp_path / ".cafe" / "issues" / "test_issue"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True)
        iteration_spec_dir = spec_dir / "iteration_001"
        iteration_spec_dir.mkdir(parents=True)
        spec_file = iteration_spec_dir / "output.md"
        spec_file.write_text("# Spec")
        
        with patch('cafe.phases.pr_phase.PRPhase._get_issue_dir', return_value=issue_dir):
            with patch('cafe.phases.pr_phase.PRPhase._get_latest_versioned_file', return_value=spec_file):
                phase = PRPhase(
                    agent_manager=Mock(),
                    permission_handler=Mock(),
                    git_ops=Mock(),
                    github_ops=Mock(),
                    spec_file=str(spec_file),
                    workflow_mode=WorkflowMode.LOCAL,
                )
                phase.iteration = 1
                phase.base_branch = "main"
                phase._get_current_branch_commits = Mock(return_value="commit1\ncommit2")
                
                # Mock _execute_and_handle_agent_response to capture allowed_tools
                captured_tools = []
                def capture_tools(**kwargs):
                    captured_tools.extend(kwargs.get('allowed_tools', []))
                    return (None, "response")
                
                phase._execute_and_handle_agent_response = Mock(side_effect=capture_tools)
                
                # Execute
                phase._generate_pr_content()
                
                # Verify allowed_tools
                edit_tools = [t for t in captured_tools if t.startswith("edit(")]
                assert len(edit_tools) == 1
                assert "output.md" in edit_tools[0]
