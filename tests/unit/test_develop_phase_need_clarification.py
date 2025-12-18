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
    git_ops.get_current_branch.return_value = "test-issue"

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
    """測試 prompt 中包含 CAFE_NEED_CLARIFICATION 說明文字."""
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


def test_save_develop_clarification_file(mock_deps, monkeypatch, tmp_path):
    """測試當 agent 回應 CAFE_NEED_CLARIFICATION 時, 檔案正確儲存到 develop_{it_num}.md."""
    # Change working directory to tmp_path
    monkeypatch.chdir(tmp_path)

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

    # Set iteration to 1
    phase.iteration = 1

    # Sample agent response with status code
    agent_response = """CAFE_NEED_CLARIFICATION

I need clarification on the expected behavior of the test case.
The test is failing but I'm not sure what the expected result should be."""

    # Call the save method
    phase._save_develop_clarification(agent_response)

    # Verify file was created (use relative path now since we changed dir)
    develop_file = Path(".cafe/issues/test-issue/develop/develop_001.md")
    assert develop_file.exists()

    # Verify content (should not include status code line)
    content = develop_file.read_text()
    assert "CAFE_NEED_CLARIFICATION" not in content
    assert "I need clarification on the expected behavior" in content


def test_develop_file_path_format(mock_deps):
    """測試檔案路徑格式為 .cafe/issues/{issue-name}/develop/develop_001.md."""
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

    phase.iteration = 1

    # Get the file path
    develop_dir = mock_deps["issue_dir"] / "develop"
    develop_file = phase._get_versioned_file_path("develop", phase.iteration, develop_dir)

    # Verify path format
    assert str(develop_file).endswith(".cafe/issues/test-issue/develop/develop_001.md")


def test_develop_file_numbering_increments(mock_deps):
    """測試多輪迭代時檔案編號正確遞增（001, 002, 003...）."""
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

    develop_dir = mock_deps["issue_dir"] / "develop"

    # Test iteration 1
    phase.iteration = 1
    file1 = phase._get_versioned_file_path("develop", phase.iteration, develop_dir)
    assert str(file1).endswith("develop_001.md")

    # Test iteration 2
    phase.iteration = 2
    file2 = phase._get_versioned_file_path("develop", phase.iteration, develop_dir)
    assert str(file2).endswith("develop_002.md")

    # Test iteration 3
    phase.iteration = 3
    file3 = phase._get_versioned_file_path("develop", phase.iteration, develop_dir)
    assert str(file3).endswith("develop_003.md")


def test_prompt_includes_develop_file_when_exists(mock_deps, monkeypatch, tmp_path):
    """測試當存在前一輪 develop_{it_num-1}.md 時, prompt 包含該檔案路徑."""
    monkeypatch.chdir(tmp_path)

    # Create a develop file
    develop_dir = mock_deps["issue_dir"] / "develop"
    develop_file = develop_dir / "develop_001.md"
    develop_file.write_text("Previous question: What should we do?")

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

    phase.iteration = 2

    prompt = phase._generate_prompt()

    # Verify prompt includes develop file path
    assert "develop_001.md" in prompt
    # Verify prompt includes instruction to read develop file
    assert "first read" in prompt.lower() or "read" in prompt.lower()


def test_prompt_includes_read_instruction_when_develop_file_exists(mock_deps, monkeypatch, tmp_path):
    """測試 prompt 中包含「首先閱讀 develop_XXX.md 中問題」指示."""
    monkeypatch.chdir(tmp_path)

    # Create a develop file
    develop_dir = mock_deps["issue_dir"] / "develop"
    develop_file = develop_dir / "develop_001.md"
    develop_file.write_text("Need help with this issue.")

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

    phase.iteration = 2

    prompt = phase._generate_prompt()

    # Verify prompt includes instruction to read develop file
    assert "develop_001.md" in prompt
    assert "question" in prompt


def test_prompt_excludes_develop_file_when_not_exists(mock_deps, monkeypatch, tmp_path):
    """測試當不存在 develop file 時, prompt 不包含相關指示."""
    monkeypatch.chdir(tmp_path)

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

    phase.iteration = 1

    prompt = phase._generate_prompt()

    # Verify prompt does not include develop file references
    assert "develop_" not in prompt or "develop_001.md" not in prompt
