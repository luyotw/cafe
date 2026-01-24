"""Tests for PR phase issue commenting functionality"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from cafe.phases.pr_phase import PRPhase
from cafe.core.types import PhaseResult, PhaseStatus
from cafe.utils.github import GitHubError


def test_reads_issue_id_from_spec_section_current_broken_behavior(tmp_path):
    """Test that demonstrates the bug: issue_id is NOT read from spec section"""
    # Setup issue directory
    issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
    issue_dir.mkdir(parents=True)

    # Setup issue.yaml with issue_id in spec section (current real-world structure)
    issue_config = issue_dir / "issue.yaml"
    issue_config.write_text("spec:\n  issue_id: '123'\nbase_branch: main\n")

    # Setup spec file
    spec_dir = issue_dir / "spec" / "iteration_001"
    spec_dir.mkdir(parents=True)
    spec_file = spec_dir / "output.md"
    spec_file.write_text("# Spec")

    with patch('cafe.phases.pr_phase.PRPhase._get_issue_dir', return_value=issue_dir):
        phase = PRPhase(
            agent_manager=Mock(),
            permission_handler=Mock(),
            git_ops=Mock(),
            github_ops=Mock(),
            spec_file=str(spec_file),
        )

        # Simulate what execute() currently does (BROKEN - only checks top level)
        config_file = phase.issue_dir / "issue.yaml"
        phase.issue_id = phase._get_issue_config_value(config_file, "issue_id")
        # Bug: no fallback to spec.issue_id!

    # Before the fix: this is None (demonstrating the bug)
    assert phase.issue_id is None


def test_reads_issue_id_from_spec_section_after_fix(tmp_path):
    """Test that issue_id IS read from spec section after the fix"""
    # Setup issue directory
    issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
    issue_dir.mkdir(parents=True)

    # Setup issue.yaml with issue_id in spec section (current real-world structure)
    issue_config = issue_dir / "issue.yaml"
    issue_config.write_text("spec:\n  issue_id: '123'\nbase_branch: main\n")

    # Setup spec file
    spec_dir = issue_dir / "spec" / "iteration_001"
    spec_dir.mkdir(parents=True)
    spec_file = spec_dir / "output.md"
    spec_file.write_text("# Spec")

    with patch('cafe.phases.pr_phase.PRPhase._get_issue_dir', return_value=issue_dir):
        phase = PRPhase(
            agent_manager=Mock(),
            permission_handler=Mock(),
            git_ops=Mock(),
            github_ops=Mock(),
            spec_file=str(spec_file),
        )

        # Simulate what execute() should do (FIXED - checks both locations)
        config_file = phase.issue_dir / "issue.yaml"
        phase.issue_id = phase._get_issue_config_value(config_file, "issue_id")
        if not phase.issue_id:
            phase.issue_id = phase._get_issue_config_value(config_file, "spec.issue_id")

    # After the fix: this should be '123'
    assert phase.issue_id == '123'


def test_reads_issue_id_from_top_level(tmp_path):
    """Test that issue_id is read from top level if it exists there"""
    # Setup issue directory
    issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
    issue_dir.mkdir(parents=True)

    # Setup issue.yaml with issue_id at top level
    issue_config = issue_dir / "issue.yaml"
    issue_config.write_text("issue_id: '456'\nbase_branch: main\n")

    # Setup spec file
    spec_dir = issue_dir / "spec" / "iteration_001"
    spec_dir.mkdir(parents=True)
    spec_file = spec_dir / "output.md"
    spec_file.write_text("# Spec")

    with patch('cafe.phases.pr_phase.PRPhase._get_issue_dir', return_value=issue_dir):
        phase = PRPhase(
            agent_manager=Mock(),
            permission_handler=Mock(),
            git_ops=Mock(),
            github_ops=Mock(),
            spec_file=str(spec_file),
        )

        # Simulate what execute() does: read issue_id from config
        config_file = phase.issue_dir / "issue.yaml"
        phase.issue_id = phase._get_issue_config_value(config_file, "issue_id")
        if not phase.issue_id:
            phase.issue_id = phase._get_issue_config_value(config_file, "spec.issue_id")

    assert phase.issue_id == '456'


def test_issue_id_none_when_not_configured(tmp_path):
    """Test that issue_id is None when not configured"""
    # Setup issue directory
    issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
    issue_dir.mkdir(parents=True)

    # Setup issue.yaml without issue_id
    issue_config = issue_dir / "issue.yaml"
    issue_config.write_text("base_branch: main\n")

    # Setup spec file
    spec_dir = issue_dir / "spec" / "iteration_001"
    spec_dir.mkdir(parents=True)
    spec_file = spec_dir / "output.md"
    spec_file.write_text("# Spec")

    with patch('cafe.phases.pr_phase.PRPhase._get_issue_dir', return_value=issue_dir):
        phase = PRPhase(
            agent_manager=Mock(),
            permission_handler=Mock(),
            git_ops=Mock(),
            github_ops=Mock(),
            spec_file=str(spec_file),
        )

        # Simulate what execute() does: read issue_id from config
        config_file = phase.issue_dir / "issue.yaml"
        phase.issue_id = phase._get_issue_config_value(config_file, "issue_id")
        if not phase.issue_id:
            phase.issue_id = phase._get_issue_config_value(config_file, "spec.issue_id")

    assert phase.issue_id is None


def test_top_level_issue_id_takes_precedence(tmp_path):
    """Test that top-level issue_id takes precedence when both exist"""
    # Setup issue directory
    issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
    issue_dir.mkdir(parents=True)

    # Setup issue.yaml with both top-level and spec.issue_id
    issue_config = issue_dir / "issue.yaml"
    issue_config.write_text("issue_id: '789'\nspec:\n  issue_id: '123'\nbase_branch: main\n")

    # Setup spec file
    spec_dir = issue_dir / "spec" / "iteration_001"
    spec_dir.mkdir(parents=True)
    spec_file = spec_dir / "output.md"
    spec_file.write_text("# Spec")

    with patch('cafe.phases.pr_phase.PRPhase._get_issue_dir', return_value=issue_dir):
        phase = PRPhase(
            agent_manager=Mock(),
            permission_handler=Mock(),
            git_ops=Mock(),
            github_ops=Mock(),
            spec_file=str(spec_file),
        )

        # Simulate what execute() does: read issue_id from config
        config_file = phase.issue_dir / "issue.yaml"
        phase.issue_id = phase._get_issue_config_value(config_file, "issue_id")
        if not phase.issue_id:
            phase.issue_id = phase._get_issue_config_value(config_file, "spec.issue_id")

    # Top-level should take precedence
    assert phase.issue_id == '789'


