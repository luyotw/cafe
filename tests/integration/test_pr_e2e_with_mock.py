"""E2E tests for 'cafe pr' command with mocked git/gh operations.

使用 subprocess.run() 測試實際 CLI 命令執行，mock git 和 gh 操作。
"""

import subprocess
import os
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock


def setup_test_environment(tmp_path: Path, issue_name: str):
    """設置測試環境：創建 spec.md 和 plan.md"""
    # 創建 spec.md
    spec_dir = tmp_path / ".cafe" / "issues" / issue_name / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_file = spec_dir / "spec.md"
    spec_file.write_text("# 測試功能需求\n\n這是一個測試需求規格。")

    # 創建 plan.md
    plan_dir = tmp_path / ".cafe" / "issues" / issue_name / "plan"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_file = plan_dir / "plan.md"
    plan_file.write_text("""# 實作計畫

## 任務清單
- [x] 實作功能 A
- [x] 實作功能 B
- [x] 撰寫測試

## 開發指南
已完成所有任務。
""")


def mock_subprocess_run(gh_pr_url="https://github.com/user/repo/pull/1"):
    """Mock subprocess.run for git and gh commands"""
    def side_effect(*args, **kwargs):
        cmd = args[0] if args else []
        result = MagicMock()

        # Mock gh pr create
        if "gh" in cmd and "pr" in cmd and "create" in cmd:
            result.returncode = 0
            result.stdout = gh_pr_url
            result.stderr = ""
        else:
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""

        return result

    return side_effect


@pytest.mark.e2e
class TestPRE2EMockDraftFlag:
    """測試 draft flag 功能"""

    @patch("cafe.utils.github.subprocess.run")
    @patch("cafe.phases.pr_phase.subprocess.run")
    @patch("cafe.core.git.subprocess.run")
    def test_draft_pr_by_default(self, mock_git_run, mock_pr_run, mock_ghops_run, tmp_path):
        """測試預設創建 draft PR"""
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        # Mock subprocess calls
        def git_side_effect(*args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "main"
            result.stderr = ""
            return result

        def gh_side_effect(*args, **kwargs):
            cmd = args[0] if args else []
            result = MagicMock()

            # Mock gh --version check
            if "--version" in cmd:
                result.returncode = 0
                result.stdout = "gh version 2.0.0"
                result.stderr = ""
            # Mock gh pr list (no PR exists)
            elif "pr" in cmd and "list" in cmd:
                result.returncode = 0
                result.stdout = '[]'
                result.stderr = ""
            else:
                result.returncode = 0
                result.stdout = "https://github.com/user/repo/pull/1"
                result.stderr = ""
            return result

        mock_git_run.side_effect = git_side_effect
        mock_pr_run.side_effect = gh_side_effect
        mock_ghops_run.side_effect = gh_side_effect

        # Import here to use patched subprocess
        from cafe.agents.manager import AgentManager
        from cafe.core.permission import PermissionHandler
        from cafe.core.git import GitOperations
        from cafe.utils.github import GitHubOps
        from cafe.core.types import WorkflowMode
        from cafe.phases.pr_phase import PRPhase

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            spec_file = f".cafe/issues/{issue_name}/spec/spec.md"

            agent_manager = AgentManager()
            permission_handler = PermissionHandler()
            git_ops = GitOperations()
            github_ops = GitHubOps()

            phase = PRPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=git_ops,
                github_ops=github_ops,
                spec_file=spec_file,
                workflow_mode=WorkflowMode.LOCAL,
                issue_name=issue_name,
                draft=True,  # Default
                custom_title="Test PR",
                custom_body="Test body",
                interactive=False,
            )

            result = phase.execute()

            # Verify
            assert result.status.value == "completed"
            assert result.data["pr_number"] == "1"

            # Check that --draft was used (gh pr create is the last call)
            gh_calls = [call for call in mock_pr_run.call_args_list]
            assert len(gh_calls) > 0
            # Find the gh pr create call
            gh_pr_create_cmd = None
            for call in gh_calls:
                cmd = call[0][0]
                if "gh" in cmd and "pr" in cmd:
                    gh_pr_create_cmd = cmd
                    break
            assert gh_pr_create_cmd is not None
            assert "--draft" in gh_pr_create_cmd
        finally:
            os.chdir(original_cwd)

    @patch("cafe.utils.github.subprocess.run")
    @patch("cafe.phases.pr_phase.subprocess.run")
    @patch("cafe.core.git.subprocess.run")
    def test_non_draft_pr(self, mock_git_run, mock_pr_run, mock_ghops_run, tmp_path):
        """測試創建非 draft PR"""
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        # Mock subprocess calls
        def git_side_effect(*args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "main"
            result.stderr = ""
            return result

        def gh_side_effect(*args, **kwargs):
            cmd = args[0] if args else []
            result = MagicMock()
            if "--version" in cmd:
                result.returncode = 0
                result.stdout = "gh version 2.0.0"
                result.stderr = ""
            elif "pr" in cmd and "list" in cmd:
                result.returncode = 0
                result.stdout = '[]'
                result.stderr = ""
            else:
                result.returncode = 0
                result.stdout = "https://github.com/user/repo/pull/2"
                result.stderr = ""
            return result

        mock_git_run.side_effect = git_side_effect
        mock_pr_run.side_effect = gh_side_effect
        mock_ghops_run.side_effect = gh_side_effect

        # Import here to use patched subprocess
        from cafe.agents.manager import AgentManager
        from cafe.core.permission import PermissionHandler
        from cafe.core.git import GitOperations
        from cafe.utils.github import GitHubOps
        from cafe.core.types import WorkflowMode
        from cafe.phases.pr_phase import PRPhase

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            spec_file = f".cafe/issues/{issue_name}/spec/spec.md"

            agent_manager = AgentManager()
            permission_handler = PermissionHandler()
            git_ops = GitOperations()
            github_ops = GitHubOps()

            phase = PRPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=git_ops,
                github_ops=github_ops,
                spec_file=spec_file,
                workflow_mode=WorkflowMode.LOCAL,
                issue_name=issue_name,
                draft=False,
                custom_title="Test PR",
                custom_body="Test body",
                interactive=False,
            )

            result = phase.execute()

            # Verify
            assert result.status.value == "completed"
            assert result.data["pr_number"] == "2"

            # Check that --draft was NOT used
            gh_calls = [call for call in mock_pr_run.call_args_list]
            assert len(gh_calls) > 0
            # Find the gh pr create call
            gh_pr_create_cmd = None
            for call in gh_calls:
                cmd = call[0][0]
                if "gh" in cmd and "pr" in cmd:
                    gh_pr_create_cmd = cmd
                    break
            assert gh_pr_create_cmd is not None
            assert "--draft" not in gh_pr_create_cmd
        finally:
            os.chdir(original_cwd)


@pytest.mark.e2e
class TestPRE2EMockCustomTitleAndBody:
    """測試自訂 title 和 body"""

    @patch("cafe.utils.github.subprocess.run")
    @patch("cafe.phases.pr_phase.subprocess.run")
    @patch("cafe.core.git.subprocess.run")
    def test_custom_title_and_body(self, mock_git_run, mock_pr_run, mock_ghops_run, tmp_path):
        """測試使用自訂 title 和 body"""
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        # Mock subprocess calls
        def git_side_effect(*args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "main"
            result.stderr = ""
            return result

        def gh_side_effect(*args, **kwargs):
            cmd = args[0] if args else []
            result = MagicMock()
            if "--version" in cmd:
                result.returncode = 0
                result.stdout = "gh version 2.0.0"
                result.stderr = ""
            elif "pr" in cmd and "list" in cmd:
                result.returncode = 0
                result.stdout = '[]'
                result.stderr = ""
            else:
                result.returncode = 0
                result.stdout = "https://github.com/user/repo/pull/3"
                result.stderr = ""
            return result

        mock_git_run.side_effect = git_side_effect
        mock_pr_run.side_effect = gh_side_effect
        mock_ghops_run.side_effect = gh_side_effect

        # Import here to use patched subprocess
        from cafe.agents.manager import AgentManager
        from cafe.core.permission import PermissionHandler
        from cafe.core.git import GitOperations
        from cafe.utils.github import GitHubOps
        from cafe.core.types import WorkflowMode
        from cafe.phases.pr_phase import PRPhase

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            spec_file = f".cafe/issues/{issue_name}/spec/spec.md"
            custom_title = "My Custom PR Title"
            custom_body = "My custom PR body\nwith details"

            agent_manager = AgentManager()
            permission_handler = PermissionHandler()
            git_ops = GitOperations()
            github_ops = GitHubOps()

            phase = PRPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=git_ops,
                github_ops=github_ops,
                spec_file=spec_file,
                workflow_mode=WorkflowMode.LOCAL,
                issue_name=issue_name,
                draft=True,
                custom_title=custom_title,
                custom_body=custom_body,
                interactive=False,
            )

            result = phase.execute()

            # Verify
            assert result.status.value == "completed"
            assert result.data["pr_number"] == "3"

            # Check custom title and body were used
            gh_calls = [call for call in mock_pr_run.call_args_list]
            assert len(gh_calls) > 0
            # Find the gh pr create call
            gh_pr_create_cmd = None
            for call in gh_calls:
                cmd = call[0][0]
                if "gh" in cmd and "pr" in cmd:
                    gh_pr_create_cmd = cmd
                    break
            assert gh_pr_create_cmd is not None
            assert custom_title in gh_pr_create_cmd
            body_index = gh_pr_create_cmd.index("--body") + 1
            assert custom_body == gh_pr_create_cmd[body_index]
        finally:
            os.chdir(original_cwd)

    @patch("cafe.agents.manager.AgentManager.execute")
    @patch("cafe.utils.github.subprocess.run")
    @patch("cafe.phases.pr_phase.subprocess.run")
    @patch("cafe.core.git.subprocess.run")
    def test_auto_generate_title_and_body(self, mock_git_run, mock_pr_run, mock_ghops_run, mock_agent_execute, tmp_path):
        """測試自動產生 title 和 body"""
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        # Mock subprocess calls
        def git_side_effect(*args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "main"
            result.stderr = ""
            return result

        def gh_side_effect(*args, **kwargs):
            cmd = args[0] if args else []
            result = MagicMock()
            if "--version" in cmd:
                result.returncode = 0
                result.stdout = "gh version 2.0.0"
                result.stderr = ""
            elif "pr" in cmd and "list" in cmd:
                result.returncode = 0
                result.stdout = '[]'
                result.stderr = ""
            else:
                result.returncode = 0
                result.stdout = "https://github.com/user/repo/pull/4"
                result.stderr = ""
            return result

        mock_git_run.side_effect = git_side_effect
        mock_pr_run.side_effect = gh_side_effect
        mock_ghops_run.side_effect = gh_side_effect

        # Mock agent to write title and body files
        def agent_execute_side_effect(agent_name, prompt, allowed_tools):
            pr_dir = tmp_path / ".cafe" / "issues" / issue_name / "pr"
            pr_dir.mkdir(parents=True, exist_ok=True)
            (pr_dir / "title.txt").write_text("測試功能需求")
            (pr_dir / "body.md").write_text("## Summary\nAuto-generated PR description\n\n## Changes\n- Feature implementation")
            return "CAFE_CONFIRMED", [], [], []

        mock_agent_execute.side_effect = agent_execute_side_effect

        # Import here to use patched subprocess
        from cafe.agents.manager import AgentManager
        from cafe.core.permission import PermissionHandler
        from cafe.core.git import GitOperations
        from cafe.utils.github import GitHubOps
        from cafe.core.types import WorkflowMode
        from cafe.phases.pr_phase import PRPhase

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            spec_file = f".cafe/issues/{issue_name}/spec/spec.md"

            agent_manager = AgentManager()
            permission_handler = PermissionHandler()
            git_ops = GitOperations()
            github_ops = GitHubOps()

            phase = PRPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=git_ops,
                github_ops=github_ops,
                spec_file=spec_file,
                workflow_mode=WorkflowMode.LOCAL,
                issue_name=issue_name,
                draft=True,
                custom_title=None,  # Auto-generate
                custom_body=None,   # Auto-generate
                interactive=False,
            )

            result = phase.execute()

            # Verify
            assert result.status.value == "completed"
            assert result.data["pr_number"] == "4"

            # Verify agent was called
            mock_agent_execute.assert_called_once()

            # Check title was auto-generated
            gh_calls = [call for call in mock_pr_run.call_args_list]
            assert len(gh_calls) > 0
            # Find the gh pr create call
            gh_pr_create_cmd = None
            for call in gh_calls:
                cmd = call[0][0]
                if "gh" in cmd and "pr" in cmd:
                    gh_pr_create_cmd = cmd
                    break
            assert gh_pr_create_cmd is not None
            assert "測試功能需求" in gh_pr_create_cmd
        finally:
            os.chdir(original_cwd)


@pytest.mark.e2e
class TestPRE2EMockErrorHandling:
    """測試錯誤處理"""

    def test_missing_spec_file_fails(self, tmp_path):
        """測試缺少 spec 檔案時失敗"""
        issue_name = "test-issue"
        # Don't setup test environment - missing spec file

        # Import here
        from cafe.agents.manager import AgentManager
        from cafe.core.permission import PermissionHandler
        from cafe.core.git import GitOperations
        from cafe.utils.github import GitHubOps
        from cafe.core.types import WorkflowMode
        from cafe.phases.pr_phase import PRPhase

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            spec_file = f".cafe/issues/{issue_name}/spec/spec.md"

            agent_manager = AgentManager()
            permission_handler = PermissionHandler()
            git_ops = GitOperations()
            github_ops = GitHubOps()

            phase = PRPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=git_ops,
                github_ops=github_ops,
                spec_file=spec_file,
                workflow_mode=WorkflowMode.LOCAL,
                issue_name=issue_name,
                draft=True,
                interactive=False,
            )

            result = phase.execute()

            # Verify failure
            assert result.status.value == "failed"
            assert "not found" in result.message.lower()
        finally:
            os.chdir(original_cwd)

    @patch("cafe.utils.github.subprocess.run")
    @patch("cafe.phases.pr_phase.subprocess.run")
    @patch("cafe.core.git.subprocess.run")
    def test_gh_pr_create_failure(self, mock_git_run, mock_pr_run, mock_ghops_run, tmp_path):
        """測試 gh pr create 失敗"""
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        # Mock subprocess calls
        def git_side_effect(*args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "main"
            result.stderr = ""
            return result

        def gh_side_effect(*args, **kwargs):
            cmd = args[0] if args else []
            result = MagicMock()
            if "--version" in cmd:
                result.returncode = 0
                result.stdout = "gh version 2.0.0"
                result.stderr = ""
            elif "pr" in cmd and "list" in cmd:
                result.returncode = 0
                result.stdout = '[]'
                result.stderr = ""
            else:
                result = MagicMock()
                result.returncode = 1
                result.stdout = ""
                result.stderr = "PR creation failed"
            return result

        mock_git_run.side_effect = git_side_effect
        mock_pr_run.side_effect = gh_side_effect
        mock_ghops_run.side_effect = gh_side_effect

        # Import here to use patched subprocess
        from cafe.agents.manager import AgentManager
        from cafe.core.permission import PermissionHandler
        from cafe.core.git import GitOperations
        from cafe.utils.github import GitHubOps
        from cafe.core.types import WorkflowMode
        from cafe.phases.pr_phase import PRPhase

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            spec_file = f".cafe/issues/{issue_name}/spec/spec.md"

            agent_manager = AgentManager()
            permission_handler = PermissionHandler()
            git_ops = GitOperations()
            github_ops = GitHubOps()

            phase = PRPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=git_ops,
                github_ops=github_ops,
                spec_file=spec_file,
                workflow_mode=WorkflowMode.LOCAL,
                issue_name=issue_name,
                draft=True,
                interactive=False,
            )

            result = phase.execute()

            # Verify failure
            assert result.status.value == "failed"
            assert "failed" in result.message.lower()
        finally:
            os.chdir(original_cwd)


@pytest.mark.e2e
class TestPRE2EMockExistingFiles:
    """測試 PR 檔案已存在的情境"""

    @patch("cafe.utils.github.subprocess.run")
    @patch("cafe.core.git.subprocess.run")
    def test_pr_exists_non_interactive_without_update_fails(self, mock_ghops_run, mock_git_run, tmp_path):
        """測試 PR 檔案已存在且非互動模式沒有 --update 會失敗"""
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        # Create existing PR files
        pr_dir = tmp_path / ".cafe" / "issues" / issue_name / "pr"
        pr_dir.mkdir(parents=True, exist_ok=True)
        (pr_dir / "title.txt").write_text("Existing Title")
        (pr_dir / "body.md").write_text("Existing Body")

        # Mock git operations
        def git_side_effect(*args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "main"
            result.stderr = ""
            return result

        mock_git_run.side_effect = git_side_effect

        # Import here to use patched subprocess
        from cafe.agents.manager import AgentManager
        from cafe.core.permission import PermissionHandler
        from cafe.core.git import GitOperations
        from cafe.utils.github import GitHubOps
        from cafe.core.types import WorkflowMode
        from cafe.phases.pr_phase import PRPhase

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            spec_file = f".cafe/issues/{issue_name}/spec/spec.md"

            agent_manager = AgentManager()
            permission_handler = PermissionHandler()
            git_ops = GitOperations()
            github_ops = GitHubOps()

            phase = PRPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=git_ops,
                github_ops=github_ops,
                spec_file=spec_file,
                workflow_mode=WorkflowMode.LOCAL,
                issue_name=issue_name,
                draft=True,
                custom_title=None,
                custom_body=None,
                update=False,  # No update flag
                interactive=False,  # Non-interactive
            )

            result = phase.execute()

            # Should fail with message about using --update
            assert result.status.value == "failed"
            assert "--update" in result.message
        finally:
            os.chdir(original_cwd)

    @patch("cafe.agents.manager.AgentManager.execute")
    @patch("cafe.utils.github.subprocess.run")
    @patch("cafe.phases.pr_phase.subprocess.run")
    @patch("cafe.core.git.subprocess.run")
    def test_pr_exists_with_update_flag_regenerates(self, mock_git_run, mock_pr_run, mock_ghops_run, mock_agent_execute, tmp_path):
        """測試 PR 檔案已存在且使用 --update 會重新生成"""
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        # Create existing PR files
        pr_dir = tmp_path / ".cafe" / "issues" / issue_name / "pr"
        pr_dir.mkdir(parents=True, exist_ok=True)
        (pr_dir / "title.txt").write_text("Old Title")
        (pr_dir / "body.md").write_text("Old Body")

        # Mock subprocess calls
        def git_side_effect(*args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "main"
            result.stderr = ""
            return result

        def gh_side_effect(*args, **kwargs):
            cmd = args[0] if args else []
            result = MagicMock()
            if "--version" in cmd:
                result.returncode = 0
                result.stdout = "gh version 2.0.0"
                result.stderr = ""
            elif "pr" in cmd and "list" in cmd:
                result.returncode = 0
                result.stdout = '[]'
                result.stderr = ""
            else:
                result.returncode = 0
                result.stdout = "https://github.com/user/repo/pull/1"
                result.stderr = ""
            return result

        mock_git_run.side_effect = git_side_effect
        mock_pr_run.side_effect = gh_side_effect
        mock_ghops_run.side_effect = gh_side_effect

        # Mock agent to write new files
        def agent_execute_side_effect(agent_name, prompt, allowed_tools):
            (pr_dir / "title.txt").write_text("New Generated Title")
            (pr_dir / "body.md").write_text("New Generated Body")
            return "CAFE_CONFIRMED", [], [], []

        mock_agent_execute.side_effect = agent_execute_side_effect

        # Import here to use patched subprocess
        from cafe.agents.manager import AgentManager
        from cafe.core.permission import PermissionHandler
        from cafe.core.git import GitOperations
        from cafe.utils.github import GitHubOps
        from cafe.core.types import WorkflowMode
        from cafe.phases.pr_phase import PRPhase

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            spec_file = f".cafe/issues/{issue_name}/spec/spec.md"

            agent_manager = AgentManager()
            permission_handler = PermissionHandler()
            git_ops = GitOperations()
            github_ops = GitHubOps()

            phase = PRPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=git_ops,
                github_ops=github_ops,
                spec_file=spec_file,
                workflow_mode=WorkflowMode.LOCAL,
                issue_name=issue_name,
                draft=True,
                custom_title=None,
                custom_body=None,
                update=True,  # Force update
                interactive=False,
            )

            result = phase.execute()

            # Should succeed and regenerate
            assert result.status.value == "completed"
            assert result.data["pr_number"] == "1"

            # Verify files were updated
            assert (pr_dir / "title.txt").read_text() == "New Generated Title"
            assert (pr_dir / "body.md").read_text() == "New Generated Body"

            # Verify agent was called
            mock_agent_execute.assert_called_once()
        finally:
            os.chdir(original_cwd)


@pytest.mark.e2e
class TestPRE2EMockGitHubPRExists:
    """測試 GitHub 上已存在 PR 的情境"""

    @patch("cafe.utils.github.subprocess.run")
    @patch("cafe.phases.pr_phase.subprocess.run")
    @patch("cafe.core.git.subprocess.run")
    def test_github_pr_exists_non_interactive_without_update_fails(self, mock_git_run, mock_pr_run, mock_ghops_run, tmp_path):
        """測試 GitHub PR 已存在且非互動模式沒有 --update 會失敗"""
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        # Mock git operations
        def git_side_effect(*args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "feature-branch"
            result.stderr = ""
            return result

        # Mock gh CLI operations
        def gh_side_effect(*args, **kwargs):
            cmd = args[0] if args else []
            result = MagicMock()

            # Mock gh --version check
            if "--version" in cmd:
                result.returncode = 0
                result.stdout = "gh version 2.0.0"
                result.stderr = ""
            # Mock gh pr list (PR exists)
            elif "pr" in cmd and "list" in cmd:
                result.returncode = 0
                result.stdout = '[{"number":10,"url":"https://github.com/user/repo/pull/10","title":"Existing PR","body":"Existing body"}]'
                result.stderr = ""
            else:
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""

            return result

        mock_git_run.side_effect = git_side_effect
        mock_pr_run.side_effect = gh_side_effect
        mock_ghops_run.side_effect = gh_side_effect
        mock_pr_run.side_effect = gh_side_effect

        # Import here to use patched subprocess
        from cafe.agents.manager import AgentManager
        from cafe.core.permission import PermissionHandler
        from cafe.core.git import GitOperations
        from cafe.utils.github import GitHubOps
        from cafe.core.types import WorkflowMode
        from cafe.phases.pr_phase import PRPhase

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            spec_file = f".cafe/issues/{issue_name}/spec/spec.md"

            agent_manager = AgentManager()
            permission_handler = PermissionHandler()
            git_ops = GitOperations()
            github_ops = GitHubOps()

            phase = PRPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=git_ops,
                github_ops=github_ops,
                spec_file=spec_file,
                workflow_mode=WorkflowMode.LOCAL,
                issue_name=issue_name,
                draft=True,
                custom_title="New Title",
                custom_body="New Body",
                update=False,  # No update flag
                interactive=False,  # Non-interactive
            )

            result = phase.execute()

            # Should fail with message about existing PR
            assert result.status.value == "failed"
            assert "already exists" in result.message.lower() or "pull request" in result.message.lower()
        finally:
            os.chdir(original_cwd)

    @patch("cafe.utils.github.subprocess.run")
    @patch("cafe.phases.pr_phase.subprocess.run")
    @patch("cafe.core.git.subprocess.run")
    def test_github_pr_exists_with_update_flag_updates_pr(self, mock_git_run, mock_pr_run, mock_ghops_run, tmp_path):
        """測試 GitHub PR 已存在且使用 --update 會更新 PR"""
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        # Mock git operations
        def git_side_effect(*args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "feature-branch"
            result.stderr = ""
            return result

        # Mock gh CLI operations
        def gh_side_effect(*args, **kwargs):
            cmd = args[0] if args else []
            result = MagicMock()

            # Mock gh --version check
            if "--version" in cmd:
                result.returncode = 0
                result.stdout = "gh version 2.0.0"
                result.stderr = ""
            # Mock gh pr list (PR exists)
            elif "pr" in cmd and "list" in cmd:
                result.returncode = 0
                result.stdout = '[{"number":10,"url":"https://github.com/user/repo/pull/10","title":"Old PR","body":"Old body"}]'
                result.stderr = ""
            # Mock gh pr edit (update PR)
            elif "pr" in cmd and "edit" in cmd:
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
            else:
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""

            return result

        mock_git_run.side_effect = git_side_effect
        mock_pr_run.side_effect = gh_side_effect
        mock_ghops_run.side_effect = gh_side_effect
        mock_pr_run.side_effect = gh_side_effect

        # Import here to use patched subprocess
        from cafe.agents.manager import AgentManager
        from cafe.core.permission import PermissionHandler
        from cafe.core.git import GitOperations
        from cafe.utils.github import GitHubOps
        from cafe.core.types import WorkflowMode
        from cafe.phases.pr_phase import PRPhase

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            spec_file = f".cafe/issues/{issue_name}/spec/spec.md"

            agent_manager = AgentManager()
            permission_handler = PermissionHandler()
            git_ops = GitOperations()
            github_ops = GitHubOps()

            phase = PRPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=git_ops,
                github_ops=github_ops,
                spec_file=spec_file,
                workflow_mode=WorkflowMode.LOCAL,
                issue_name=issue_name,
                draft=True,
                custom_title="Updated Title",
                custom_body="Updated Body",
                update=True,  # Force update
                interactive=False,
            )

            result = phase.execute()

            # Should succeed
            assert result.status.value == "completed"
            assert "updated" in result.message.lower()
            assert result.data["pr_number"] == "10"

            # Verify gh pr edit was called
            gh_calls = [call for call in mock_ghops_run.call_args_list]
            pr_edit_calls = [call for call in gh_calls if len(call[0]) > 0 and "pr" in call[0][0] and "edit" in call[0][0]]
            assert len(pr_edit_calls) > 0

            # Verify the edit command had correct arguments
            edit_cmd = pr_edit_calls[0][0][0]
            assert "10" in edit_cmd
            assert "--title" in edit_cmd
            assert "Updated Title" in edit_cmd
            assert "--body" in edit_cmd
        finally:
            os.chdir(original_cwd)

    @patch("cafe.utils.github.subprocess.run")
    @patch("cafe.phases.pr_phase.subprocess.run")
    @patch("cafe.core.git.subprocess.run")
    def test_github_pr_not_exists_creates_new_pr(self, mock_git_run, mock_pr_run, mock_ghops_run, tmp_path):
        """測試 GitHub PR 不存在時創建新 PR"""
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        # Mock git operations
        def git_side_effect(*args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "feature-branch"
            result.stderr = ""
            return result

        # Mock gh CLI operations
        def gh_side_effect(*args, **kwargs):
            cmd = args[0] if args else []
            result = MagicMock()

            # Mock gh --version check
            if "--version" in cmd:
                result.returncode = 0
                result.stdout = "gh version 2.0.0"
                result.stderr = ""
            # Mock gh pr list (no PR exists)
            elif "pr" in cmd and "list" in cmd:
                result.returncode = 0
                result.stdout = '[]'
                result.stderr = ""
            # Mock gh pr create (create new PR)
            elif "pr" in cmd and "create" in cmd:
                result.returncode = 0
                result.stdout = "https://github.com/user/repo/pull/20"
                result.stderr = ""
            else:
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""

            return result

        mock_git_run.side_effect = git_side_effect
        mock_pr_run.side_effect = gh_side_effect
        mock_ghops_run.side_effect = gh_side_effect
        mock_pr_run.side_effect = gh_side_effect

        # Import here to use patched subprocess
        from cafe.agents.manager import AgentManager
        from cafe.core.permission import PermissionHandler
        from cafe.core.git import GitOperations
        from cafe.utils.github import GitHubOps
        from cafe.core.types import WorkflowMode
        from cafe.phases.pr_phase import PRPhase

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            spec_file = f".cafe/issues/{issue_name}/spec/spec.md"

            agent_manager = AgentManager()
            permission_handler = PermissionHandler()
            git_ops = GitOperations()
            github_ops = GitHubOps()

            phase = PRPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=git_ops,
                github_ops=github_ops,
                spec_file=spec_file,
                workflow_mode=WorkflowMode.LOCAL,
                issue_name=issue_name,
                draft=True,
                custom_title="New PR Title",
                custom_body="New PR Body",
                update=False,
                interactive=False,
            )

            result = phase.execute()

            # Should succeed and create new PR
            assert result.status.value == "completed"
            assert "created" in result.message.lower()
            assert result.data["pr_number"] == "20"

            # Verify gh pr create was called
            gh_calls = [call for call in mock_ghops_run.call_args_list]
            pr_create_calls = [call for call in gh_calls if len(call[0]) > 0 and "pr" in call[0][0] and "create" in call[0][0]]
            assert len(pr_create_calls) > 0
        finally:
            os.chdir(original_cwd)
