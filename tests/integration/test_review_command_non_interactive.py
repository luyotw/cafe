"""Integration tests for 'cafe review --no-interactive' command.

使用 MockAgentExecutor 測試完整的 review command flow，不呼叫真實 LLM API。
"""

import os
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from cafe.agents.manager import AgentManager
from cafe.core.permission import PermissionHandler
from cafe.core.git import GitOperations
from cafe.core.types import AgentConfig, AgentCLI, WorkflowMode, PhaseStatus
from cafe.phases.review_phase import ReviewPhase


@pytest.fixture
def mock_env(monkeypatch):
    """啟用 mock agent mode"""
    monkeypatch.setenv("CAFE_MOCK_AGENTS", "true")


@pytest.fixture
def temp_review_dir(tmp_path):
    """創建臨時 review 目錄結構"""
    # 創建完整的目錄結構: {tmp_path}/.cafe/issues/test-issue/review/
    issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"

    # 創建 spec 目錄和 spec.md（review 需要 spec 已存在）
    spec_dir = issue_dir / "spec"
    spec_dir.mkdir(parents=True)
    spec_file = spec_dir / "spec.md"
    spec_file.write_text("# 測試功能需求\n\n這是一個測試需求規格。")

    # 創建 plan 目錄和 plan.md（review 需要 plan 已存在）
    plan_dir = issue_dir / "plan"
    plan_dir.mkdir(parents=True)
    plan_file = plan_dir / "plan.md"
    plan_file.write_text("""# 實作計畫

## 任務清單
- [x] 實作功能 A
- [x] 實作功能 B
- [x] 撰寫測試

## 開發指南
已按照以上任務清單完成實作。
""")

    # 創建 review 目錄
    review_dir = issue_dir / "review"
    review_dir.mkdir(parents=True)

    return review_dir


@pytest.fixture
def mock_git_ops():
    """Mock GitOperations"""
    git_ops = MagicMock(spec=GitOperations)
    git_ops.get_diff.return_value = """diff --git a/test.py b/test.py
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/test.py
@@ -0,0 +1,5 @@
+def hello():
+    print("Hello, World!")
+
+if __name__ == "__main__":
+    hello()
"""
    return git_ops


class TestReviewCommandNonInteractiveBasics:
    """測試 review --no-interactive 基本功能"""

    def test_confirmed_status_success(
        self, mock_env, temp_review_dir, mock_git_ops, monkeypatch, tmp_path
    ):
        """測試返回 CONFIRMED 狀態成功"""
        # Arrange
        spec_file = str(temp_review_dir.parent / "spec" / "spec.md")
        plan_file = str(temp_review_dir.parent / "plan" / "plan.md")

        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_CONFIRMED\n\n程式碼審查通過，沒有問題。"
        )

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            agent_manager = AgentManager()
            agent_manager.register_agent(
                AgentConfig(name="Richard", cli=AgentCLI.CLAUDE)
            )

            permission_handler = PermissionHandler()

            # Act
            phase = ReviewPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=mock_git_ops,
                spec_file=spec_file,
                plan_file=plan_file,
                workflow_mode=WorkflowMode.LOCAL,
                interactive=False,
            )

            result = phase.execute()

            # Assert
            assert result.status == PhaseStatus.COMPLETED
            assert result.data["status_code"] == "CAFE_CONFIRMED"

            # 驗證 agent 被呼叫一次（review 是 non-iterative）
            executor = agent_manager.get_agent("Richard")
            assert executor.call_count == 1
        finally:
            os.chdir(original_cwd)

    def test_needs_changes_status_success(
        self, mock_env, temp_review_dir, mock_git_ops, monkeypatch, tmp_path
    ):
        """測試返回 NEEDS_CHANGES 狀態成功"""
        # Arrange
        spec_file = str(temp_review_dir.parent / "spec" / "spec.md")
        plan_file = str(temp_review_dir.parent / "plan" / "plan.md")

        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_NEEDS_CHANGES\n\n需要修正 commit message 格式。"
        )

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            agent_manager = AgentManager()
            agent_manager.register_agent(
                AgentConfig(name="Richard", cli=AgentCLI.CLAUDE)
            )

            permission_handler = PermissionHandler()

            # Act
            phase = ReviewPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=mock_git_ops,
                spec_file=spec_file,
                plan_file=plan_file,
                workflow_mode=WorkflowMode.LOCAL,
                interactive=False,
            )

            result = phase.execute()

            # Assert
            assert result.status == PhaseStatus.COMPLETED
            assert result.data["status_code"] == "CAFE_NEEDS_CHANGES"

            # 驗證 agent 被呼叫一次
            executor = agent_manager.get_agent("Richard")
            assert executor.call_count == 1
        finally:
            os.chdir(original_cwd)

    def test_no_diff_creates_review_with_needs_changes(
        self, mock_env, temp_review_dir, monkeypatch, tmp_path
    , mock_git_ops):
        """測試沒有 diff 時 agent 應該生成 NEEDS_CHANGES review"""
        # Arrange
        spec_file = str(temp_review_dir.parent / "spec" / "spec.md")
        plan_file = str(temp_review_dir.parent / "plan" / "plan.md")

        # Mock git_ops to return empty diff
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = ""

        # Mock response with NEEDS_CHANGES (agent should detect no changes)
        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_NEEDS_CHANGES\n\nNo code changes detected. Please implement the planned changes."
        )

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            agent_manager = AgentManager()
            agent_manager.register_agent(
                AgentConfig(name="Richard", cli=AgentCLI.CLAUDE)
            )

            permission_handler = PermissionHandler()

            # Act
            phase = ReviewPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=git_ops,
                spec_file=spec_file,
                plan_file=plan_file,
                workflow_mode=WorkflowMode.LOCAL,
                interactive=False,
            )

            result = phase.execute()

            # Assert - should complete successfully with NEEDS_CHANGES status
            assert result.status == PhaseStatus.COMPLETED
            assert result.data["status_code"] == "CAFE_NEEDS_CHANGES"

            # Verify review file was created
            review_file = temp_review_dir / "review_001.md"
            assert review_file.exists()
            review_content = review_file.read_text()
            assert "NEEDS_CHANGES" in review_content
        finally:
            os.chdir(original_cwd)


class TestReviewCommandNonInteractiveFiles:
    """測試檔案操作相關功能"""

    def test_review_md_created(
        self, mock_env, temp_review_dir, mock_git_ops, monkeypatch, tmp_path
    ):
        """測試 review_001.md 被創建"""
        # Arrange
        spec_file = str(temp_review_dir.parent / "spec" / "spec.md")
        plan_file = str(temp_review_dir.parent / "plan" / "plan.md")
        review_file = temp_review_dir / "review_001.md"  # First iteration creates review_001.md

        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_CONFIRMED\n\n程式碼審查通過。"
        )

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            agent_manager = AgentManager()
            agent_manager.register_agent(
                AgentConfig(name="Richard", cli=AgentCLI.CLAUDE)
            )

            permission_handler = PermissionHandler()

            phase = ReviewPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=mock_git_ops,
                spec_file=spec_file,
                plan_file=plan_file,
                workflow_mode=WorkflowMode.LOCAL,
                interactive=False,
            )

            # Act
            phase.execute()

            # Assert
            assert review_file.exists()
            content = review_file.read_text()
            assert "CAFE_CONFIRMED" in content
            assert "程式碼審查通過" in content
        finally:
            os.chdir(original_cwd)

    def test_history_created(
        self, mock_env, temp_review_dir, mock_git_ops, monkeypatch, tmp_path
    ):
        """測試 history 目錄和檔案被創建"""
        # Arrange
        spec_file = str(temp_review_dir.parent / "spec" / "spec.md")
        plan_file = str(temp_review_dir.parent / "plan" / "plan.md")
        history_dir = temp_review_dir / "history"

        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_CONFIRMED\n\n審查完成。"
        )

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            agent_manager = AgentManager()
            agent_manager.register_agent(
                AgentConfig(name="Richard", cli=AgentCLI.CLAUDE)
            )

            permission_handler = PermissionHandler()

            phase = ReviewPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=mock_git_ops,
                spec_file=spec_file,
                plan_file=plan_file,
                workflow_mode=WorkflowMode.LOCAL,
                interactive=False,
            )

            # Act
            phase.execute()

            # Assert
            assert history_dir.exists()
            assert history_dir.is_dir()

            # 應該有一個 iteration 檔案（review 只執行一次）
            iteration_files = list(history_dir.glob("iteration_*.json"))
            assert len(iteration_files) == 1
        finally:
            os.chdir(original_cwd)

    def test_status_json_created(
        self, mock_env, temp_review_dir, mock_git_ops, monkeypatch, tmp_path
    ):
        """測試 status.json 被創建"""
        # Arrange
        spec_file = str(temp_review_dir.parent / "spec" / "spec.md")
        plan_file = str(temp_review_dir.parent / "plan" / "plan.md")
        status_file = temp_review_dir / "status.json"

        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_NEEDS_CHANGES\n\n需要修正。"
        )

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            agent_manager = AgentManager()
            agent_manager.register_agent(
                AgentConfig(name="Richard", cli=AgentCLI.CLAUDE)
            )

            permission_handler = PermissionHandler()

            phase = ReviewPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=mock_git_ops,
                spec_file=spec_file,
                plan_file=plan_file,
                workflow_mode=WorkflowMode.LOCAL,
                interactive=False,
            )

            # Act
            phase.execute()

            # Assert
            assert status_file.exists()

            with open(status_file) as f:
                status_data = json.load(f)
                assert status_data["phase"] == "review"
                assert status_data["status"] == "completed"
                assert status_data["status_code"] == "CAFE_NEEDS_CHANGES"
        finally:
            os.chdir(original_cwd)


class TestReviewCommandNonInteractiveDiffHandling:
    """測試 diff 處理功能"""

    def test_full_branch_diff(
        self, mock_env, temp_review_dir, monkeypatch, tmp_path
    , mock_git_ops):
        """測試審查完整 branch diff"""
        # Arrange
        spec_file = str(temp_review_dir.parent / "spec" / "spec.md")
        plan_file = str(temp_review_dir.parent / "plan" / "plan.md")

        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_CONFIRMED\n\n審查完成。"
        )

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content for full branch"

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            agent_manager = AgentManager()
            agent_manager.register_agent(
                AgentConfig(name="Richard", cli=AgentCLI.CLAUDE)
            )

            permission_handler = PermissionHandler()

            phase = ReviewPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=git_ops,
                spec_file=spec_file,
                plan_file=plan_file,
                workflow_mode=WorkflowMode.LOCAL,
                interactive=False,
                base_branch="main",
            )

            # Act
            result = phase.execute()

            # Assert - verify review completes successfully
            assert result.status == PhaseStatus.COMPLETED
            assert result.data["status_code"] == "CAFE_CONFIRMED"

            # Verify review file was created
            review_file = temp_review_dir / "review_001.md"
            assert review_file.exists()
        finally:
            os.chdir(original_cwd)

    def test_specific_commit_diff(
        self, mock_env, temp_review_dir, monkeypatch, tmp_path
    , mock_git_ops):
        """測試審查特定 commit"""
        # Arrange
        spec_file = str(temp_review_dir.parent / "spec" / "spec.md")
        plan_file = str(temp_review_dir.parent / "plan" / "plan.md")
        target_commit = "abc1234"

        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_CONFIRMED\n\nCommit 審查完成。"
        )

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content for specific commit"

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            agent_manager = AgentManager()
            agent_manager.register_agent(
                AgentConfig(name="Richard", cli=AgentCLI.CLAUDE)
            )

            permission_handler = PermissionHandler()

            phase = ReviewPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=git_ops,
                spec_file=spec_file,
                plan_file=plan_file,
                workflow_mode=WorkflowMode.LOCAL,
                interactive=False,
                target_commit=target_commit,
            )

            # Act
            result = phase.execute()

            # Assert - verify review completes successfully
            assert result.status == PhaseStatus.COMPLETED
            assert result.data["status_code"] == "CAFE_CONFIRMED"
            assert result.data["target_commit"] == target_commit

            # Verify review file was created
            review_file = temp_review_dir / "review_001.md"
            assert review_file.exists()
        finally:
            os.chdir(original_cwd)


class TestReviewCommandNonInteractiveAgentTracking:
    """測試 mock agent 的追蹤功能"""

    def test_agent_receives_diff_in_prompt(
        self, mock_env, temp_review_dir, mock_git_ops, monkeypatch, tmp_path
    ):
        """測試 agent 收到包含 diff 的 prompt"""
        # Arrange
        spec_file = str(temp_review_dir.parent / "spec" / "spec.md")
        plan_file = str(temp_review_dir.parent / "plan" / "plan.md")

        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_CONFIRMED\n\n審查完成。"
        )

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            agent_manager = AgentManager()
            agent_manager.register_agent(
                AgentConfig(name="Richard", cli=AgentCLI.CLAUDE)
            )

            permission_handler = PermissionHandler()

            phase = ReviewPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=mock_git_ops,
                spec_file=spec_file,
                plan_file=plan_file,
                workflow_mode=WorkflowMode.LOCAL,
                interactive=False,
            )

            # Act
            phase.execute()

            # Assert - 驗證 agent 被呼叫，且 prompt 包含審查任務說明
            executor = agent_manager.get_agent("Richard")
            assert executor.call_count == 1
            # 新版 prompt 包含審查任務指引，不再直接提供 git diff 內容
            assert "git 狀態檢查" in executor.last_prompt or "審查任務" in executor.last_prompt
            # 新設計：agent 自己執行 git 指令獲取內容，而不是在 prompt 中直接提供
        finally:
            os.chdir(original_cwd)

    def test_agent_called_only_once(
        self, mock_env, temp_review_dir, mock_git_ops, monkeypatch, tmp_path
    ):
        """測試 agent 只被呼叫一次（non-iterative）"""
        # Arrange
        spec_file = str(temp_review_dir.parent / "spec" / "spec.md")
        plan_file = str(temp_review_dir.parent / "plan" / "plan.md")

        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_NEEDS_CHANGES\n\n需要修正。"
        )

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            agent_manager = AgentManager()
            agent_manager.register_agent(
                AgentConfig(name="Richard", cli=AgentCLI.CLAUDE)
            )

            permission_handler = PermissionHandler()

            phase = ReviewPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=mock_git_ops,
                spec_file=spec_file,
                plan_file=plan_file,
                workflow_mode=WorkflowMode.LOCAL,
                interactive=False,
            )

            # Act
            phase.execute()

            # Assert - review 是 non-iterative，只執行一次
            executor = agent_manager.get_agent("Richard")
            assert executor.call_count == 1
        finally:
            os.chdir(original_cwd)

    def test_agent_has_bash_tools(
        self, mock_env, temp_review_dir, mock_git_ops, monkeypatch, tmp_path
    ):
        """測試 agent 可以使用 bash tools（用於執行 git 指令）"""
        # Arrange
        spec_file = str(temp_review_dir.parent / "spec" / "spec.md")
        plan_file = str(temp_review_dir.parent / "plan" / "plan.md")

        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_CONFIRMED\n\n審查完成。"
        )

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            agent_manager = AgentManager()
            agent_manager.register_agent(
                AgentConfig(name="Richard", cli=AgentCLI.CLAUDE)
            )

            permission_handler = PermissionHandler()

            phase = ReviewPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=mock_git_ops,
                spec_file=spec_file,
                plan_file=plan_file,
                workflow_mode=WorkflowMode.LOCAL,
                interactive=False,
            )

            # Act
            phase.execute()

            # Assert - 驗證 history 記錄了允許的 tools
            history_dir = temp_review_dir / "history"
            iteration_file = history_dir / "iteration_001.json"
            assert iteration_file.exists()

            with open(iteration_file) as f:
                history_data = json.load(f)
                assert "allowed_tools" in history_data
                # Review phase 允許使用多種工具進行程式碼審查
                allowed_tools = history_data["allowed_tools"]
                assert isinstance(allowed_tools, list)
                assert len(allowed_tools) > 0
                # 確認包含關鍵的 git 工具
                assert any("git diff" in tool or "bash(git diff)" in tool for tool in allowed_tools)
                assert any("git log" in tool or "bash(git log)" in tool for tool in allowed_tools)
        finally:
            os.chdir(original_cwd)
