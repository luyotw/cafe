"""測試 SpecPhase 和 PlanPhase 使用相對路徑（git ignore 格式）。

確保 allowed_tools 中的檔案路徑使用：
- 相對於 repository root 的路徑
- Git ignore 格式（以 / 開頭）
- 支援 worktree 環境
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cafe.core.types import PhaseStatus, PhaseResult, WorkflowMode, TokenUsage
from cafe.phases.spec_phase import SpecPhase
from cafe.phases.plan_phase import PlanPhase


def create_template_file(tmp_path: Path) -> str:
    """Create a dummy template file for tests."""
    template_file = tmp_path / "template.md"
    template_file.write_text("# Plan Template\n\nTemplate content")
    return str(template_file)


@pytest.fixture
def mock_agent_manager():
    """Mock AgentManager"""
    manager = MagicMock()
    # Return proper TokenUsage object
    manager.execute.return_value = (
        "CAFE_READY_FOR_REVIEW\n\nRequirements confirmed",
        TokenUsage(),
        [],
        [],
        ""
    )

    # Mock get_agent to return a proper mock executor with config
    mock_executor = MagicMock()
    mock_config = MagicMock()
    mock_config.cli.value = "copilot"
    mock_config.session_id = "test-session-123"
    mock_executor.config = mock_config
    manager.get_agent.return_value = mock_executor

    # Mock get_total_token_usage to return proper TokenUsage object
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


class TestSpecPhaseAllowedToolsPaths:
    """測試 SpecPhase 的 allowed_tools 使用相對路徑"""

    def test_spec_phase_uses_relative_paths_in_normal_repo(self, tmp_path, mock_agent_manager, mock_permission, mock_git, monkeypatch):
        """一般 repo：allowed_tools 應使用相對路徑（git ignore 格式）"""
        # Setup: 創建一般 repo 結構
        repo_root = tmp_path / "my-repo"
        git_dir = repo_root / ".git"
        git_dir.mkdir(parents=True)

        issue_dir = repo_root / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.chdir(repo_root)

        # Capture the allowed_tools passed to agent
        captured_tools = []

        def capture_execute(*args, **kwargs):
            if "allowed_tools" in kwargs:
                captured_tools.append(kwargs["allowed_tools"])
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

        # Patch the analysis method
        with patch.object(phase, '_analyze_missing_status_code_with_logging', return_value=("", None)):
            # Execute
            result = phase.execute()

        # Assert
        assert result.status == PhaseStatus.COMPLETED
        assert len(captured_tools) > 0

        # 檢查最後一次呼叫的 allowed_tools (write removed, only edit remains)
        tools = captured_tools[-1]
        assert any("edit(" in tool for tool in tools), f"No edit tool found in {tools}"

        # 找出 edit 工具的路徑
        edit_tool = next(t for t in tools if "edit(" in t)

        # 關鍵斷言：路徑應該是相對路徑（git ignore 格式：以 / 開頭但不包含絕對路徑）
        # 格式應該是：edit(/.cafe/issues/test-issue/spec/spec_001.md)
        # 不應該是：edit(/var/folders/.../my-repo/.cafe/issues/test-issue/spec/spec_001.md)

        # 提取路徑（去掉 edit() 包裝）
        edit_path = edit_tool.replace("edit(", "").replace(")", "")

        # 路徑應該以 .cafe 開頭（普通相對路徑，不帶 / 前綴）
        assert edit_path.startswith(".cafe"), f"Expected path to start with '.cafe' but got: {edit_path}"

        # 路徑不應該以 / 開頭（不是 git ignore 格式）
        assert not edit_path.startswith("/"), f"Path should not start with '/' but got: {edit_path}"

        # 路徑應該包含正確的 issue 路徑
        assert ".cafe/issues/test-issue/spec/" in edit_path, f"Expected .cafe/issues/test-issue/spec/ in {edit_path}"

    @pytest.mark.skip(reason="Worktree support requires Phase base class refactor - tracked in issue #TODO")
    def test_spec_phase_uses_relative_paths_in_worktree(self, tmp_path, mock_agent_manager, mock_permission, mock_git, monkeypatch):
        """Worktree：allowed_tools 應使用相對於主 repo 的路徑"""
        # Setup: 創建主 repo 和 worktree 結構
        main_repo = tmp_path / "main-repo"
        main_git_dir = main_repo / ".git"
        main_git_dir.mkdir(parents=True)

        # 在主 repo 中創建 .cafe 目錄
        issue_dir = main_repo / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)

        # 創建 worktree
        worktree_dir = tmp_path / "worktrees" / "test-issue"
        worktree_dir.mkdir(parents=True)

        git_file = worktree_dir / ".git"
        git_file.write_text(f"gitdir: {main_git_dir}/worktrees/test-issue\n")

        worktree_git_dir = main_git_dir / "worktrees" / "test-issue"
        worktree_git_dir.mkdir(parents=True)

        # 切換到 worktree 目錄執行
        monkeypatch.chdir(worktree_dir)

        # Capture the allowed_tools passed to agent
        captured_tools = []

        def capture_execute(*args, **kwargs):
            if "allowed_tools" in kwargs:
                captured_tools.append(kwargs["allowed_tools"])
            return (
                "CAFE_READY_FOR_REVIEW\n\nTest response",
                TokenUsage(),
                [],
                [],
                ""
            )

        mock_agent_manager.execute.side_effect = capture_execute

        # In worktree, spec_file is in the main repo's .cafe directory
        spec_file = spec_dir / "spec_001.md"

        phase = SpecPhase(
            agent_manager=mock_agent_manager,
            permission_handler=mock_permission,
            git_ops=mock_git,
            workflow_mode=WorkflowMode.LOCAL,
            pm_agent="Roger",
            interactive=False,
            issue_name="test-issue",
            user_input="Test requirements",
            spec_file=str(spec_file),  # Explicitly pass spec_file in main repo
        )

        # Patch the analysis method
        with patch.object(phase, '_analyze_missing_status_code_with_logging', return_value=("", None)):
            # Execute
            result = phase.execute()

        # Assert
        if result.status != PhaseStatus.COMPLETED:
            print(f"\nUnexpected status: {result.status}")
            print(f"Message: {result.message}")
            print(f"Data: {result.data}")
        assert result.status == PhaseStatus.COMPLETED
        assert len(captured_tools) > 0

        tools = captured_tools[-1]
        write_tool = next(t for t in tools if "write(" in t)
        edit_tool = next(t for t in tools if "edit(" in t)

        # 提取路徑
        write_path = write_tool.replace("write(", "").replace(")", "")
        edit_path = edit_tool.replace("edit(", "").replace(")", "")

        # 關鍵斷言：即使在 worktree 中，路徑也應該是相對於主 repo 的 git ignore 格式
        # 格式應該是：write(/.cafe/issues/test-issue/spec/spec_001.md)
        assert write_path.startswith("/."), f"Expected path to start with '/.' but got: {write_path}"
        assert edit_path.startswith("/."), f"Expected path to start with '/.' but got: {edit_path}"

        assert "/.cafe/issues/test-issue/spec/" in write_path, f"Expected /.cafe/issues/test-issue/spec/ in {write_path}"
        assert "/.cafe/issues/test-issue/spec/" in edit_path, f"Expected /.cafe/issues/test-issue/spec/ in {edit_path}"


class TestPlanPhaseAllowedToolsPaths:
    """測試 PlanPhase 的 allowed_tools 使用相對路徑"""

    def test_plan_phase_uses_relative_paths_in_normal_repo(self, tmp_path, mock_agent_manager, mock_permission, mock_git, monkeypatch):
        """一般 repo：allowed_tools 應使用相對路徑（git ignore 格式）"""
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

        # Capture the allowed_tools passed to agent
        captured_tools = []

        def capture_execute(*args, **kwargs):
            if "allowed_tools" in kwargs:
                captured_tools.append(kwargs["allowed_tools"])
            return (
                "CAFE_READY_FOR_REVIEW\n\nPlan ready for review",
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
            template_path=create_template_file(tmp_path),
        )

        # Execute
        result = phase.execute()

        # Assert
        if result.status != PhaseStatus.COMPLETED:
            print(f"\nUnexpected status: {result.status}")
            print(f"Message: {result.message}")
            print(f"Data: {result.data}")
        assert result.status == PhaseStatus.COMPLETED
        assert len(captured_tools) > 0

        tools = captured_tools[-1]
        # Only check for edit (write removed since no CLI supports file-specific write)
        edit_tool = next(t for t in tools if "edit(" in t)

        # 提取路徑
        edit_path = edit_tool.replace("edit(", "").replace(")", "")

        # 關鍵斷言：路徑應該是普通相對路徑（不帶 / 前綴）
        assert edit_path.startswith(".cafe"), f"Expected path to start with '.cafe' but got: {edit_path}"

        # 路徑不應該以 / 開頭（不是 git ignore 格式）
        assert not edit_path.startswith("/"), f"Path should not start with '/' but got: {edit_path}"

        assert ".cafe/issues/test-issue/plan/" in edit_path, f"Expected .cafe/issues/test-issue/plan/ in {edit_path}"

    @pytest.mark.skip(reason="Worktree support requires Phase base class refactor - tracked in issue #TODO")
    def test_plan_phase_uses_relative_paths_in_worktree(self, tmp_path, mock_agent_manager, mock_permission, mock_git, monkeypatch):
        """Worktree：allowed_tools 應使用相對於主 repo 的路徑"""
        # Setup: 創建主 repo 和 worktree 結構
        main_repo = tmp_path / "main-repo"
        main_git_dir = main_repo / ".git"
        main_git_dir.mkdir(parents=True)

        # 在主 repo 中創建 .cafe 目錄
        issue_dir = main_repo / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        plan_dir = issue_dir / "plan"
        spec_dir.mkdir(parents=True, exist_ok=True)
        plan_dir.mkdir(parents=True, exist_ok=True)

        # Create spec file
        spec_file = spec_dir / "spec_001.md"
        spec_file.write_text("# Test Spec")

        # 創建 worktree
        worktree_dir = tmp_path / "worktrees" / "test-issue"
        worktree_dir.mkdir(parents=True)

        git_file = worktree_dir / ".git"
        git_file.write_text(f"gitdir: {main_git_dir}/worktrees/test-issue\n")

        worktree_git_dir = main_git_dir / "worktrees" / "test-issue"
        worktree_git_dir.mkdir(parents=True)

        # 切換到 worktree 目錄執行
        monkeypatch.chdir(worktree_dir)

        # Capture the allowed_tools passed to agent
        captured_tools = []

        def capture_execute(*args, **kwargs):
            if "allowed_tools" in kwargs:
                captured_tools.append(kwargs["allowed_tools"])
            return (
                "CAFE_READY_FOR_REVIEW\n\nPlan ready for review",
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
            template_path=create_template_file(tmp_path),
        )

        # Execute
        result = phase.execute()

        # Assert
        if result.status != PhaseStatus.COMPLETED:
            print(f"\nUnexpected status: {result.status}")
            print(f"Message: {result.message}")
            print(f"Data: {result.data}")
        assert result.status == PhaseStatus.COMPLETED
        assert len(captured_tools) > 0

        tools = captured_tools[-1]
        write_tool = next(t for t in tools if "write(" in t)
        edit_tool = next(t for t in tools if "edit(" in t)

        # 提取路徑
        write_path = write_tool.replace("write(", "").replace(")", "")
        edit_path = edit_tool.replace("edit(", "").replace(")", "")

        # 關鍵斷言：即使在 worktree 中，路徑也應該是相對於主 repo 的 git ignore 格式
        assert write_path.startswith("/."), f"Expected path to start with '/.' but got: {write_path}"
        assert edit_path.startswith("/."), f"Expected path to start with '/.' but got: {edit_path}"

        assert "/.cafe/issues/test-issue/plan/" in write_path, f"Expected /.cafe/issues/test-issue/plan/ in {write_path}"
        assert "/.cafe/issues/test-issue/plan/" in edit_path, f"Expected /.cafe/issues/test-issue/plan/ in {edit_path}"
