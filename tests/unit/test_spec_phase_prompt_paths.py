"""測試 SpecPhase 和 PlanPhase 的 prompt 使用相對路徑而非絕對路徑。"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from cafe.core.types import PhaseStatus, WorkflowMode, TokenUsage
from cafe.phases.spec_phase import SpecPhase
from cafe.phases.plan_phase import PlanPhase


@pytest.fixture
def mock_agent_manager():
    """Mock AgentManager"""
    manager = MagicMock()
    manager.execute.return_value = (
        "CAFE_READY_FOR_REVIEW\n\nRequirements confirmed",
        TokenUsage(),
        [],
        [],
        ""
    )
    mock_executor = MagicMock()
    mock_config = MagicMock()
    mock_config.cli.value = "copilot"
    mock_config.session_id = "test-session-123"
    mock_executor.config = mock_config
    manager.get_agent.return_value = mock_executor
    manager.get_total_token_usage.return_value = TokenUsage()
    return manager


@pytest.fixture
def mock_permission():
    """Mock PermissionHandler"""
    permission = MagicMock()
    permission.check.return_value = (True, [])
    return permission


@pytest.fixture
def mock_git():
    """Mock GitOperations"""
    git_ops = MagicMock()
    git_ops.get_current_branch.return_value = "test-issue"
    return git_ops


class TestSpecPhasePromptPaths:
    """測試 SpecPhase 的 prompt 使用相對路徑"""

    def test_first_iteration_prompt_uses_relative_paths(self, tmp_path, mock_agent_manager, mock_permission, mock_git, monkeypatch):
        """第一輪 prompt 應使用相對路徑而非絕對路徑"""
        # Setup
        repo_root = tmp_path / "my-repo"
        git_dir = repo_root / ".git"
        git_dir.mkdir(parents=True)

        issue_dir = repo_root / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.chdir(repo_root)

        # Capture the prompt passed to agent
        captured_prompts = []

        def capture_execute(agent_name, prompt, *args, **kwargs):
            captured_prompts.append(prompt)
            return (
                "CAFE_READY_FOR_REVIEW\n\nTest response",
                TokenUsage(),
                [],
                [],
                ""
            )

        mock_agent_manager.execute.side_effect = capture_execute

        phase = SpecPhase(
            agent_manager=mock_agent_manager,
            permission_handler=mock_permission,
            git_ops=mock_git,
            workflow_mode=WorkflowMode.LOCAL,
            pm_agent="Roger",
            interactive=False,
            issue_name="test-issue",
            user_input="Test requirements",
        )

        # Execute
        result = phase.execute()

        # Assert
        assert result.status == PhaseStatus.COMPLETED
        assert len(captured_prompts) > 0

        # 檢查 prompt 內容
        prompt = captured_prompts[-1]

        # 關鍵斷言：prompt 不應包含絕對路徑
        # 絕對路徑會包含 tmp_path 的完整路徑（例如 /tmp/pytest-xxx/）
        assert str(tmp_path) not in prompt, f"Prompt should not contain absolute path {tmp_path}"

        # Prompt 應該包含相對路徑（.cafe/issues/...）
        assert ".cafe/issues/test-issue/spec/" in prompt, f"Prompt should contain relative path, got: {prompt[:500]}"

        # 確保不是 git ignore 格式（不應以 /. 開頭）- prompt 中的路徑不需要 git ignore 格式
        # 只要是相對路徑即可
        assert "/.cafe/issues/test-issue/spec/" not in prompt, f"Prompt should not use git ignore format (/.cafe)"



class TestPlanPhasePromptPaths:
    """測試 PlanPhase 的 prompt 使用相對路徑"""

    def create_template_file(self, repo_root: Path) -> str:
        """Create a dummy template file for tests inside the repo."""
        template_file = repo_root / "template.md"
        template_file.write_text("# Plan Template\n\nTemplate content")
        return str(template_file)

    def test_first_iteration_prompt_uses_relative_paths(self, tmp_path, mock_agent_manager, mock_permission, mock_git, monkeypatch):
        """PlanPhase 的 prompt 應使用相對路徑而非絕對路徑"""
        # Setup
        repo_root = tmp_path / "my-repo"
        git_dir = repo_root / ".git"
        git_dir.mkdir(parents=True)

        issue_dir = repo_root / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        plan_dir = issue_dir / "plan"
        spec_dir.mkdir(parents=True, exist_ok=True)
        plan_dir.mkdir(parents=True, exist_ok=True)

        # Create spec file
        spec_file = spec_dir / "spec_001.md"
        spec_file.write_text("# Test Spec")

        monkeypatch.chdir(repo_root)

        # Capture the prompt passed to agent
        captured_prompts = []

        def capture_execute(agent_name, prompt, *args, **kwargs):
            captured_prompts.append(prompt)
            return (
                "CAFE_READY_FOR_REVIEW\n\nPlan ready",
                TokenUsage(),
                [],
                [],
                ""
            )

        mock_agent_manager.execute.side_effect = capture_execute

        phase = PlanPhase(
            agent_manager=mock_agent_manager,
            permission_handler=mock_permission,
            git_ops=mock_git,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            dev_agent="David",
            interactive=False,
            issue_name="test-issue",
            template_path=self.create_template_file(repo_root),
        )

        # Execute
        result = phase.execute()

        # Assert
        assert result.status == PhaseStatus.COMPLETED
        assert len(captured_prompts) > 0

        # 檢查 prompt 內容
        prompt = captured_prompts[-1]

        # 關鍵斷言：prompt 不應包含絕對路徑
        assert str(tmp_path) not in prompt, f"Prompt should not contain absolute path {tmp_path}"

        # Prompt 應該包含相對路徑
        assert ".cafe/issues/test-issue/" in prompt, f"Prompt should contain relative path"

        # 確保 spec 和 plan 的路徑都是相對路徑
        # spec 路徑
        assert ".cafe/issues/test-issue/spec/" in prompt, f"Prompt should contain relative spec path"
        # plan 路徑
        assert ".cafe/issues/test-issue/plan/" in prompt, f"Prompt should contain relative plan path"
