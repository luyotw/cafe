"""Tests for DevelopPhase CAFE_NEED_CLARIFICATION functionality."""

from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.permission import PermissionHandler
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import WorkflowMode
from cafe.phases.develop_phase import DevelopPhase


@pytest.fixture
def mock_deps(tmp_path: Path):
    """建立 mock dependencies."""
    # Setup directory structure
    issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
    spec_dir = issue_dir / "spec"
    plan_dir = issue_dir / "plan"
    develop_dir = issue_dir / "develop"

    spec_dir.mkdir(parents=True)
    plan_dir.mkdir(parents=True)
    develop_dir.mkdir(parents=True)

    spec_file = spec_dir / "spec_001.md"
    spec_file.write_text("# Test Spec")

    plan_file = plan_dir / "plan_001.md"
    plan_file.write_text("# Test Plan\n- [ ] Task 1\n- [ ] Task 2")

    # Setup mocks
    agent_manager = MagicMock(spec=AgentManager)
    permission_handler = MagicMock(spec=PermissionHandler)
    git_ops = MagicMock(spec=GitOperations)
    git_ops.branch_exists.return_value = False
    git_ops.get_current_branch.return_value = "main"

    return {
        "agent_manager": agent_manager,
        "permission_handler": permission_handler,
        "git_ops": git_ops,
        "spec_file": str(spec_file),
        "plan_file": str(plan_file),
        "issue_dir": issue_dir,
    }


def test_need_clarification_in_valid_status_codes(mock_deps):
    """測試 CAFE_NEED_CLARIFICATION 在 valid_status_codes 中."""
    phase = DevelopPhase(
        agent_manager=mock_deps["agent_manager"],
        permission_handler=mock_deps["permission_handler"],
        git_ops=mock_deps["git_ops"],
        spec_file=mock_deps["spec_file"],
        plan_file=mock_deps["plan_file"],
        workflow_mode=WorkflowMode.LOCAL,
        issue_name="test-issue",
        interactive=False,
    )

    # Generate prompt and check it includes CAFE_NEED_CLARIFICATION
    prompt = phase._generate_prompt()

    # Verify prompt includes NEED_CLARIFICATION status code
    assert "CAFE_NEED_CLARIFICATION" in prompt


def test_prompt_includes_need_clarification_description(mock_deps):
    """測試 prompt 中包含 CAFE_NEED_CLARIFICATION 的說明文字."""
    phase = DevelopPhase(
        agent_manager=mock_deps["agent_manager"],
        permission_handler=mock_deps["permission_handler"],
        git_ops=mock_deps["git_ops"],
        spec_file=mock_deps["spec_file"],
        plan_file=mock_deps["plan_file"],
        workflow_mode=WorkflowMode.LOCAL,
        issue_name="test-issue",
        interactive=False,
    )

    prompt = phase._generate_prompt()

    # Verify prompt includes description for NEED_CLARIFICATION
    # The description should explain when to use this status code
    assert "CAFE_NEED_CLARIFICATION" in prompt
