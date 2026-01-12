"""Tests for Task 5: Update status analysis prompt"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch
from cafe.phases.pr_phase import PRPhase


class TestStatusAnalysisPrompt:
    """Test _get_status_analysis_prompt() with output.md format"""

    def test_status_analysis_prompt_references_output_md(self, tmp_path):
        """Test that status analysis prompt references output.md"""
        issue_dir = tmp_path / ".cafe" / "issues" / "test_issue"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True)
        iteration_spec_dir = spec_dir / "iteration_001"
        iteration_spec_dir.mkdir(parents=True)
        spec_file = iteration_spec_dir / "output.md"
        spec_file.write_text("# Spec")
        
        # Create PR output.md
        pr_dir = issue_dir / "pr"
        iteration_dir = pr_dir / "iteration_001"
        iteration_dir.mkdir(parents=True)
        output_file = iteration_dir / "output.md"
        output_file.write_text("# Test PR\n\nBody content")
        
        with patch('cafe.phases.pr_phase.PRPhase._get_issue_dir', return_value=issue_dir):
            with patch('cafe.phases.pr_phase.PRPhase._get_latest_versioned_file', return_value=spec_file):
                phase = PRPhase(
                    agent_manager=Mock(),
                    permission_handler=Mock(),
                    git_ops=Mock(),
                    github_ops=Mock(),
                    spec_file=str(spec_file),
                )
                
                prompt = phase._get_status_analysis_prompt()
                
                # Verify prompt mentions output.md
                assert "output.md" in prompt
                assert str(output_file) in prompt
                
                # Verify prompt doesn't mention old files
                assert "title.txt" not in prompt
                assert "body.md" not in prompt

    def test_status_analysis_prompt_describes_content_requirements(self, tmp_path):
        """Test that prompt describes what content should be in output.md"""
        issue_dir = tmp_path / ".cafe" / "issues" / "test_issue"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True)
        iteration_spec_dir = spec_dir / "iteration_001"
        iteration_spec_dir.mkdir(parents=True)
        spec_file = iteration_spec_dir / "output.md"
        spec_file.write_text("# Spec")
        
        # Create PR output.md
        pr_dir = issue_dir / "pr"
        iteration_dir = pr_dir / "iteration_001"
        iteration_dir.mkdir(parents=True)
        output_file = iteration_dir / "output.md"
        output_file.write_text("# Test PR\n\nBody content")
        
        with patch('cafe.phases.pr_phase.PRPhase._get_issue_dir', return_value=issue_dir):
            with patch('cafe.phases.pr_phase.PRPhase._get_latest_versioned_file', return_value=spec_file):
                phase = PRPhase(
                    agent_manager=Mock(),
                    permission_handler=Mock(),
                    git_ops=Mock(),
                    github_ops=Mock(),
                    spec_file=str(spec_file),
                )
                
                prompt = phase._get_status_analysis_prompt()
                
                # Verify prompt describes expected content
                assert "PR title" in prompt or "title" in prompt.lower()
                assert "PR body" in prompt or "body" in prompt.lower()
                assert "H1 heading" in prompt or "#" in prompt
                assert "CAFE_CONFIRMED" in prompt

    def test_status_analysis_prompt_format(self, tmp_path):
        """Test that prompt has correct structure"""
        issue_dir = tmp_path / ".cafe" / "issues" / "test_issue"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True)
        iteration_spec_dir = spec_dir / "iteration_001"
        iteration_spec_dir.mkdir(parents=True)
        spec_file = iteration_spec_dir / "output.md"
        spec_file.write_text("# Spec")
        
        # Create PR output.md
        pr_dir = issue_dir / "pr"
        iteration_dir = pr_dir / "iteration_001"
        iteration_dir.mkdir(parents=True)
        output_file = iteration_dir / "output.md"
        output_file.write_text("# Test PR\n\nBody content")
        
        with patch('cafe.phases.pr_phase.PRPhase._get_issue_dir', return_value=issue_dir):
            with patch('cafe.phases.pr_phase.PRPhase._get_latest_versioned_file', return_value=spec_file):
                phase = PRPhase(
                    agent_manager=Mock(),
                    permission_handler=Mock(),
                    git_ops=Mock(),
                    github_ops=Mock(),
                    spec_file=str(spec_file),
                )
                
                prompt = phase._get_status_analysis_prompt()
                
                # Verify basic prompt structure
                assert len(prompt) > 0
                assert "check" in prompt.lower() or "verify" in prompt.lower()
                assert "complete" in prompt.lower()
