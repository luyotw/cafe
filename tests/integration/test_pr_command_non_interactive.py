"""Integration tests for 'cafe pr --no-interactive' command.

使用 mock git and gh CLI 測試完整 PR command flow.
"""

import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cafe.agents.manager import AgentManager
from cafe.core.permission import PermissionHandler
from cafe.core.git import GitOperations
from cafe.core.types import AgentConfig, AgentCLI, WorkflowMode, PhaseStatus
from cafe.phases.pr_phase import PRPhase
from cafe.utils.github import GitHubOps


@pytest.fixture
def mock_github_ops():
    """Mock GitHubOps"""
    github_ops = MagicMock(spec=GitHubOps)
    github_ops.get_pr_for_branch.return_value = None  # Assume no existing PR by default
    github_ops.create_pr.return_value = "https://github.com/user/repo/pull/123"
    return github_ops


@pytest.fixture
def temp_pr_dir(tmp_path):
    """創建臨時 PR 目錄結構"""
    # 創建完整目錄結構: {tmp_path}/.cafe/issues/test-issue/
    issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"

    # 創建 spec 目錄and spec.md
    spec_dir = issue_dir / "spec"
    spec_dir.mkdir(parents=True)
    spec_file = spec_dir / "spec.md"
    spec_file.write_text("# 測試功能需求\n\n這是一個測試需求規格.")

    # 創建 plan 目錄and plan.md
    plan_dir = issue_dir / "plan"
    plan_dir.mkdir(parents=True)
    plan_file = plan_dir / "plan.md"
    plan_file.write_text("""# 實作計畫

## 任務清單
- [x] 實作功能 A
- [x] 實作功能 B
- [x] 撰寫測試

## 開發指南
已按照以上任務清單完成實作.
""")

    return issue_dir


@pytest.fixture
def mock_git_ops():
    """Mock GitOperations"""
    git_ops = MagicMock(spec=GitOperations)
    git_ops.get_main_branch.return_value = "main"
    git_ops.get_commits_between.return_value = """commit1: Add feature A
commit2: Add feature B
commit3: Add tests"""
    return git_ops


class TestPRCommandNonInteractiveBasics:
    """測試 PR --no-interactive 基本功能"""

    def test_draft_pr_creation(self, temp_pr_dir, mock_git_ops, mock_github_ops, tmp_path):
        """測試創建 draft PR"""
        # Arrange
        spec_file = str(temp_pr_dir / "spec" / "spec.md")

        # Override mock to return specific PR URL
        mock_github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            agent_manager = AgentManager()
            agent_manager.register_agent(
                AgentConfig(name="David", cli=AgentCLI.CLAUDE)
            )

            permission_handler = PermissionHandler()

            # Act
            phase = PRPhase(
                    agent_manager=agent_manager,
                    permission_handler=permission_handler,
                    git_ops=mock_git_ops,
                    github_ops=mock_github_ops,
                    spec_file=spec_file,
                    workflow_mode=WorkflowMode.LOCAL,
                    issue_name="test-issue",
                    draft=True,
                    interactive=False,
                    custom_title="Test PR Title",
                    custom_body="Test PR Body",
                )

            result = phase.execute()

            # Assert
            assert result.status == PhaseStatus.COMPLETED
            assert result.data["pr_number"] == "1"
            assert result.data["branch"] == "test-issue"

            # 驗證 git push 被呼叫
            mock_git_ops.push.assert_called_once_with("test-issue", set_upstream=True, force=False)

            # 驗證 github_ops.create_pr 被呼叫, 並包含 draft flag
            mock_github_ops.create_pr.assert_called_once()
            call_kwargs = mock_github_ops.create_pr.call_args[1]
            assert call_kwargs["draft"] == True
        finally:
            os.chdir(original_cwd)

    def test_non_draft_pr_creation(self, temp_pr_dir, mock_git_ops, mock_github_ops, tmp_path):
        """測試創建非 draft PR"""
        # Arrange
        spec_file = str(temp_pr_dir / "spec" / "spec.md")

        # Override mock to return specific PR URL
        mock_github_ops.create_pr.return_value = "https://github.com/user/repo/pull/2"

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            agent_manager = AgentManager()
            agent_manager.register_agent(
                AgentConfig(name="David", cli=AgentCLI.CLAUDE)
            )

            permission_handler = PermissionHandler()

            # Act
            phase = PRPhase(
                    agent_manager=agent_manager,
                    permission_handler=permission_handler,
                    git_ops=mock_git_ops,
                    github_ops=mock_github_ops,
                    spec_file=spec_file,
                    workflow_mode=WorkflowMode.LOCAL,
                    issue_name="test-issue",
                    draft=False,
                    interactive=False,
                    custom_title="Test PR Title",
                    custom_body="Test PR Body",
                )

            result = phase.execute()

            # Assert
            assert result.status == PhaseStatus.COMPLETED
            assert result.data["pr_number"] == "2"

            # 驗證 github_ops.create_pr 被呼叫, 不包含 draft flag
            mock_github_ops.create_pr.assert_called_once()
            call_kwargs = mock_github_ops.create_pr.call_args[1]
            assert call_kwargs["draft"] == False
        finally:
            os.chdir(original_cwd)


class TestPRCommandCustomTitleAndBody:
    """測試自訂 title and body"""

    def test_custom_title_and_body(self, temp_pr_dir, mock_git_ops, mock_github_ops, tmp_path):
        """測試使用自訂 title and body 創建 PR"""
        # Arrange
        spec_file = str(temp_pr_dir / "spec" / "spec.md")
        custom_title = "Custom PR Title"
        custom_body = "Custom PR body\nWith multiple lines"

        # Override mock to return specific PR URL
        mock_github_ops.create_pr.return_value = "https://github.com/user/repo/pull/3"

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            agent_manager = AgentManager()
            agent_manager.register_agent(
                AgentConfig(name="David", cli=AgentCLI.CLAUDE)
            )

            permission_handler = PermissionHandler()

            # Act
            phase = PRPhase(
                    agent_manager=agent_manager,
                    permission_handler=permission_handler,
                    git_ops=mock_git_ops,
                    github_ops=mock_github_ops,
                    spec_file=spec_file,
                    workflow_mode=WorkflowMode.LOCAL,
                    issue_name="test-issue",
                    draft=True,
                    custom_title=custom_title,
                    custom_body=custom_body,
                    interactive=False,
                )

            result = phase.execute()

            # Assert
            assert result.status == PhaseStatus.COMPLETED
            assert result.data["pr_number"] == "3"

            # 驗證 github_ops.create_pr 使用自訂 title and body
            mock_github_ops.create_pr.assert_called_once()
            call_kwargs = mock_github_ops.create_pr.call_args[1]
            assert call_kwargs["title"] == custom_title
            assert call_kwargs["body"] == custom_body
            assert call_kwargs["draft"] == True
        finally:
            os.chdir(original_cwd)

    def test_auto_generate_title_and_body(self, temp_pr_dir, mock_git_ops, mock_github_ops, tmp_path, monkeypatch):
        """測試自動產生 title and body"""
        # Arrange
        spec_file = str(temp_pr_dir / "spec" / "spec.md")

        # Enable mock agent mode
        monkeypatch.setenv("CAFE_MOCK_AGENTS", "true")
        monkeypatch.setenv("CAFE_MOCK_RESPONSE", "CAFE_CONFIRMED\n\nPR content generated")

        # Override mock to return specific PR URL
        mock_github_ops.create_pr.return_value = "https://github.com/user/repo/pull/4"

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            agent_manager = AgentManager()
            agent_manager.register_agent(
                AgentConfig(name="David", cli=AgentCLI.CLAUDE)
            )

            permission_handler = PermissionHandler()

            # Create title.txt and body.md files (mock agent won't write them)
            pr_dir = temp_pr_dir / "pr"
            pr_dir.mkdir(parents=True, exist_ok=True)
            (pr_dir / "title.txt").write_text("測試功能需求")
            (pr_dir / "body.md").write_text("## Summary\n\nTest summary\n\n## Changes\n\n- commit1\n- commit2")

            # Act
            phase = PRPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=mock_git_ops,
                github_ops=mock_github_ops,
                spec_file=spec_file,
                workflow_mode=WorkflowMode.LOCAL,
                issue_name="test-issue",
                draft=True,
                custom_title=None,  # Auto-generate
                custom_body=None,   # Auto-generate
                interactive=False,
            )

            result = phase.execute()

            # Assert
            assert result.status == PhaseStatus.COMPLETED
            assert result.data["pr_number"] == "4"

            # 驗證 github_ops.create_pr 被調用
            mock_github_ops.create_pr.assert_called_once()
            call_kwargs = mock_github_ops.create_pr.call_args[1]
            assert "測試功能需求" in call_kwargs["title"]
            assert "commit1" in call_kwargs["body"]
        finally:
            os.chdir(original_cwd)


class TestPRCommandErrorHandling:
    """測試錯誤處理"""

    def test_missing_spec_file_fails(self, tmp_path, mock_git_ops, mock_github_ops):
        """測試缺少 spec 檔案時失敗"""
        # Arrange
        spec_file = str(tmp_path / ".cafe" / "issues" / "test-issue" / "spec" / "spec.md")

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            agent_manager = AgentManager()
            agent_manager.register_agent(
                AgentConfig(name="David", cli=AgentCLI.CLAUDE)
            )

            permission_handler = PermissionHandler()

            # Act
            phase = PRPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=mock_git_ops,
                github_ops=mock_github_ops,
                spec_file=spec_file,  # 不存在檔案
                workflow_mode=WorkflowMode.LOCAL,
                issue_name="test-issue",
                draft=True,
                interactive=False,
            )

            result = phase.execute()

            # Assert
            assert result.status == PhaseStatus.FAILED
            assert "not found" in result.message.lower()
        finally:
            os.chdir(original_cwd)

    def test_gh_pr_create_fails(self, temp_pr_dir, mock_git_ops, mock_github_ops, tmp_path):
        """測試 gh pr create 失敗時返回失敗"""
        # Arrange
        spec_file = str(temp_pr_dir / "spec" / "spec.md")

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            agent_manager = AgentManager()
            agent_manager.register_agent(
                AgentConfig(name="David", cli=AgentCLI.CLAUDE)
            )

            permission_handler = PermissionHandler()

            # Mock github_ops.create_pr to raise an exception
            mock_github_ops.create_pr.side_effect = RuntimeError("PR creation failed")

            phase = PRPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=mock_git_ops,
                github_ops=mock_github_ops,
                spec_file=spec_file,
                workflow_mode=WorkflowMode.LOCAL,
                issue_name="test-issue",
                draft=True,
                interactive=False,
            )

            result = phase.execute()

            # Assert
            assert result.status == PhaseStatus.FAILED
            assert "failed" in result.message.lower()
        finally:
            os.chdir(original_cwd)
