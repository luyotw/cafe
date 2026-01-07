"""Tests for Task 2: Update _prepare_pr_content() to use unified file"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch
from cafe.phases.pr_phase import PRPhase
from cafe.core.types import WorkflowMode


class TestPreparePRContentWithOutputMd:
    """Test _prepare_pr_content() with output.md format"""

    def test_read_output_md_when_exists(self, tmp_path):
        """Test reading from output.md when it exists"""
        issue_dir = tmp_path / ".cafe" / "issues" / "test_issue"
        
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True)
        spec_file = spec_dir / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Spec")
        
        # Create PR output.md in the location the method will look for
        pr_dir = issue_dir / "pr"
        iteration_dir = pr_dir / "iteration_001"
        iteration_dir.mkdir(parents=True)
        output_file = iteration_dir / "output.md"
        output_file.write_text("# Test PR Title\n\nThis is the PR body content")
        
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
                phase._generate_pr_content = Mock(return_value=None)
                phase._get_latest_iteration_dir = Mock(return_value=iteration_dir)
                
                result, content = phase._prepare_pr_content()
                
                assert result is None
                assert content is not None
                title, body = content
                assert title == "Test PR Title"
                assert body == "This is the PR body content"
                phase._generate_pr_content.assert_not_called()

    def test_custom_title_and_body_writes_to_output_md(self, tmp_path):
        """Test custom title/body writes to output.md in unified format"""
        issue_dir = tmp_path / ".cafe" / "issues" / "test_issue"
        
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True)
        spec_file = spec_dir / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
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
                    custom_title="Custom Title",
                    custom_body="Custom Body",
                )
                phase.iteration = 1
                phase._generate_pr_content = Mock(return_value=None)
                
                # Compute expected paths
                pr_dir = issue_dir / "pr"
                iteration_dir = pr_dir / "iteration_001"
                output_file = iteration_dir / "output.md"
                
                phase._get_latest_iteration_dir = Mock(return_value=iteration_dir)
                
                result, content = phase._prepare_pr_content()
                
                assert output_file.exists()
                content_text = output_file.read_text()
                assert content_text == "# Custom Title\n\nCustom Body"
                
                assert result is None
                assert content is not None
                title, body = content
                assert title == "Custom Title"
                assert body == "Custom Body"
                phase._generate_pr_content.assert_not_called()

    def test_no_custom_values_calls_generate(self, tmp_path):
        """Test that agent is called when no custom values and file doesn't exist"""
        issue_dir = tmp_path / ".cafe" / "issues" / "test_issue"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True)
        spec_file = spec_dir / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
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
                phase._generate_pr_content = Mock(return_value=None)
                phase._get_pr_title = Mock(return_value="Generated Title")
                phase._get_pr_body = Mock(return_value="Generated Body")
                
                result, content = phase._prepare_pr_content()
                
                phase._generate_pr_content.assert_called_once()
                assert result is None
                assert content == ("Generated Title", "Generated Body")
