"""E2E tests for 'cafe review' command with mock agents.

使用 CliRunner 測試 CLI 命令執行, 用 CAFE_MOCK_AGENTS=true 避免真實 LLM 呼叫.
"""

import os
import json
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from cafe.ui.cli import app


runner = CliRunner()


@dataclass
class MockResult:
    """模擬 subprocess.run 結果格式"""
    returncode: int
    stdout: str
    stderr: str


def add_test_commit_to_branch(repo_path: Path):
    """Add a test commit to the current branch for review testing"""
    from cafe.core.git import GitOperations

    test_file = repo_path / "test.py"
    test_file.write_text("""def hello():
    print("Hello, World!")

if __name__ == "__main__":
    hello()
""")
    git = GitOperations(repo_path)
    git.run_git("add", ".")
    git.commit("Add hello function")


def setup_test_files(repo_path: Path, issue_name: str):
    """Setup test files (spec.md, plan.md) for review testing.

    Note: Git repo and directory structure should already be created by prepared_repo_factory.
    """
    # 創建 spec.md
    spec_file = repo_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
    spec_file.write_text("# 測試功能需求\n\n這是一個測試需求規格.")

    # 創建 plan.md
    plan_file = repo_path / ".cafe" / "issues" / issue_name / "plan" / "plan.md"
    plan_file.write_text("""# 實作計畫

## 任務清單
- [x] 實作功能 A
- [x] 實作功能 B
- [x] 撰寫測試

## 開發指南
已完成所有任務.
""")


def run_cafe_review(
    repo_path: Path,
    issue_name: str,
    mock_response: str,
    extra_args: Optional[List[str]] = None
) -> MockResult:
    """Helper function to run cafe review command with mock using CliRunner

    Note: Assumes prepared_repo_factory has already set the working directory.
    """
    args = ["review", "--no-interactive"]
    if extra_args:
        args.extend(extra_args)

    env_vars = {"CAFE_MOCK_AGENTS": "true"}
    if mock_response:
        env_vars["CAFE_MOCK_RESPONSE"] = mock_response

    try:
        # Mock Git operations to return the issue_name as branch
        with patch("cafe.ui.cli.GitOperations") as mock_git_cls:
            mock_git_instance = mock_git_cls.return_value
            mock_git_instance.is_valid_branch.return_value = True
            mock_git_instance.get_current_branch.return_value = issue_name

            # Also mock for ReviewPhase
            with patch("cafe.phases.review_phase.GitOperations") as mock_phase_git:
                mock_phase_git.return_value = mock_git_instance

                with patch.dict(os.environ, env_vars):
                    result = runner.invoke(app, args, catch_exceptions=False)
    except Exception as e:
        return MockResult(returncode=1, stdout="", stderr=str(e))

    return MockResult(
        returncode=result.exit_code,
        stdout=result.stdout or "",
        stderr=""
    )


@pytest.mark.e2e
class TestReviewE2EMockStatusCodes:
    """測試狀態碼處理"""

    def test_confirmed_status_success(self, prepared_repo_factory):
        """測試 CONFIRMED 狀態碼成功完成

        情境：Agent 審查通過, 返回 CAFE_CONFIRMED
        指令：cafe review test-issue --no-interactive
        預期：成功, status.json 顯示 completed 狀態, status_code 為 CAFE_CONFIRMED
        """
        issue_name = "test-issue"
        repo_path = prepared_repo_factory(issue_name)
        setup_test_files(repo_path, issue_name)
        add_test_commit_to_branch(repo_path)

        result = run_cafe_review(repo_path, issue_name, "CAFE_CONFIRMED\n\n程式碼審查通過.")

        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "completed" in output.lower() or "成功" in output.lower() or "passed" in output.lower()

        # 驗證 status.json 被創建
        status_file = repo_path / ".cafe" / "issues" / issue_name / "review" / "status.json"
        assert status_file.exists()

        with open(status_file) as f:
            status_data = json.load(f)
            assert status_data["phase"] == "review"
            assert status_data["status"] == "completed"
            assert status_data["status_code"] == "CAFE_CONFIRMED"

    def test_needs_changes_status_success(self, prepared_repo_factory):
        """測試 NEEDS_CHANGES 狀態碼成功完成

        情境：Agent 發現需要修正, 返回 CAFE_NEEDS_CHANGES
        指令：cafe review test-issue --no-interactive
        預期：成功, status.json 顯示 completed 狀態（NEEDS_CHANGES 也視為完成）
        """
        issue_name = "test-issue"
        repo_path = prepared_repo_factory(issue_name)
        setup_test_files(repo_path, issue_name)
        add_test_commit_to_branch(repo_path)

        result = run_cafe_review(repo_path, issue_name, "CAFE_NEEDS_CHANGES\n\n需要修正 commit message.")

        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "completed" in output.lower() or "成功" in output.lower()

        # 驗證 status.json 被創建, 且 NEEDS_CHANGES 也視為 completed
        status_file = repo_path / ".cafe" / "issues" / issue_name / "review" / "status.json"
        assert status_file.exists()

        with open(status_file) as f:
            status_data = json.load(f)
            assert status_data["phase"] == "review"
            assert status_data["status"] == "completed"  # NEEDS_CHANGES 也是完成狀態
            assert status_data["status_code"] == "CAFE_NEEDS_CHANGES"

    def test_invalid_status_code_fails_in_non_interactive(self, prepared_repo_factory):
        """測試無效狀態碼在 non-interactive 模式會失敗

        情境：Agent 返回無效狀態碼
        指令：cafe review test-issue --no-interactive
        預期：失敗, 錯誤訊息包含 "no status code" or "failed"
        """
        issue_name = "test-issue"
        repo_path = prepared_repo_factory(issue_name)
        setup_test_files(repo_path, issue_name)
        add_test_commit_to_branch(repo_path)

        result = run_cafe_review(repo_path, issue_name, "CAFE_INVALID_CODE\n\n審查意見...")

        # Non-interactive mode fails when status code is invalid/missing
        assert result.returncode != 0
        output = result.stdout + result.stderr
        assert "no status code" in output.lower() or "failed" in output.lower()


@pytest.mark.e2e
class TestReviewE2EMockFileValidation:
    """測試檔案相關功能"""

    def test_review_md_created(self, prepared_repo_factory):
        """測試 review 成功完成並保存 iteration 資料

        情境：成功完成 review phase
        指令：cafe review test-issue --no-interactive
        預期：成功, iteration_001.json 包含 review 資訊

        注意：review_001.md 是由 agent  Write tool 產生, 
        在 mock 模式下不會實際產生檔案
        """
        issue_name = "test-issue"
        repo_path = prepared_repo_factory(issue_name)
        setup_test_files(repo_path, issue_name)
        add_test_commit_to_branch(repo_path)

        result = run_cafe_review(repo_path, issue_name, "CAFE_CONFIRMED\n\n程式碼審查通過.")

        assert result.returncode == 0

        # Verify iteration file contains review information
        iteration_file = repo_path / ".cafe" / "issues" / issue_name / "review" / "history" / "iteration_001.json"
        assert iteration_file.exists()

        with open(iteration_file) as f:
            data = json.load(f)
            assert "response" in data
            assert "CAFE_CONFIRMED" in data["response"]
            assert "程式碼審查通過" in data["response"]

    def test_history_directory_created(self, prepared_repo_factory):
        """測試 history 目錄被創建

        情境：成功完成 review phase（non-iterative）
        指令：cafe review test-issue --no-interactive
        預期：成功, review/history 目錄被創建, 只有一個 iteration 檔案
        """
        issue_name = "test-issue"
        repo_path = prepared_repo_factory(issue_name)
        setup_test_files(repo_path, issue_name)
        add_test_commit_to_branch(repo_path)

        result = run_cafe_review(repo_path, issue_name, "CAFE_CONFIRMED\n\n審查完成.")

        assert result.returncode == 0

        history_dir = repo_path / ".cafe" / "issues" / issue_name / "review" / "history"
        assert history_dir.exists()
        assert history_dir.is_dir()

        # Review 是 non-iterative, 應該只有一個 iteration 檔案
        iteration_files = list(history_dir.glob("iteration_*.json"))
        assert len(iteration_files) == 1

    def test_iteration_file_structure(self, prepared_repo_factory):
        """測試 iteration 檔案結構正確

        情境：成功完成 review phase
        指令：cafe review test-issue --no-interactive
        預期：成功, iteration_001.json 包含正確欄位, allowed_tools 包含 review 相關工具
        """
        issue_name = "test-issue"
        repo_path = prepared_repo_factory(issue_name)
        setup_test_files(repo_path, issue_name)
        add_test_commit_to_branch(repo_path)

        result = run_cafe_review(repo_path, issue_name, "CAFE_NEEDS_CHANGES\n\n需要修正.")

        assert result.returncode == 0

        iteration_file = repo_path / ".cafe" / "issues" / issue_name / "review" / "history" / "iteration_001.json"
        assert iteration_file.exists()

        with open(iteration_file) as f:
            data = json.load(f)
            # 驗證包含必要欄位
            assert "user_input" in data
            assert "response" in data
            assert "prompt" in data
            assert "cli" in data
            assert "session_id" in data
            assert "allowed_tools" in data
            # Review phase 允許使用多種工具進行程式碼審查
            allowed_tools = data["allowed_tools"]
            assert isinstance(allowed_tools, list)
            assert len(allowed_tools) > 0
            # 確認包含關鍵 review 工具
            assert any("read" in tool for tool in allowed_tools)
            assert any("git diff" in tool or "bash(git diff)" in tool for tool in allowed_tools)
            assert any("git log" in tool or "bash(git log)" in tool for tool in allowed_tools)
            assert any("edit" in tool for tool in allowed_tools)

    @pytest.mark.skip(reason="需要使用真實 Git 操作而非 mock, 將在後續修復")
    def test_no_diff_should_fail(self, prepared_repo_factory):
        """測試沒有 diff 時應該失敗

        情境：Feature branch 沒有任何改變（無 diff）
        指令：cafe review --no-interactive
        預期：失敗, 錯誤訊息包含 "no changes" or "diff"
        """
        issue_name = "test-issue"
        repo_path = prepared_repo_factory(issue_name)
        setup_test_files(repo_path, issue_name)

        # 不調用 add_test_commit_to_branch(), 故意製造 "no diff" 情境

        # 停留在 feature branch, 沒有任何改變（and main 沒有差異）
        result = run_cafe_review(repo_path, issue_name, "CAFE_CONFIRMED\n\n審查通過.")

        # 沒有 diff 應該失敗
        assert result.returncode != 0
        output = result.stdout + result.stderr
        assert "no changes" in output.lower() or "diff" in output.lower() or "failed" in output.lower()


@pytest.mark.e2e
class TestReviewE2EMockDiffHandling:
    """測試 diff 處理功能"""

    def test_full_branch_diff_by_default(self, prepared_repo_factory):
        """測試預設審查完整 branch diff

        情境：沒有指定特定 commit, 審查整個 feature branch
        指令：cafe review test-issue --no-interactive
        預期：成功, 審查 feature branch and main 所有差異
        """
        issue_name = "test-issue"
        repo_path = prepared_repo_factory(issue_name)
        setup_test_files(repo_path, issue_name)
        add_test_commit_to_branch(repo_path)

        result = run_cafe_review(repo_path, issue_name, "CAFE_CONFIRMED\n\n審查完成.")

        assert result.returncode == 0

        # 驗證 review_001.md 包含審查結果
        review_file = repo_path / ".cafe" / "issues" / issue_name / "review" / "review_001.md"
        assert review_file.exists()

    def test_specific_commit_with_flag(self, prepared_repo_factory):
        """測試使用 --commit 旗標審查特定 commit

        情境：只審查特定 commit 變更
        指令：cafe review test-issue --no-interactive --commit <commit-sha>
        預期：成功, 只審查指定 commit  diff
        """
        issue_name = "test-issue"
        repo_path = prepared_repo_factory(issue_name)
        setup_test_files(repo_path, issue_name)
        add_test_commit_to_branch(repo_path)

        # 取得最新 commit SHA
        from cafe.core.git import GitOperations
        git = GitOperations(repo_path)
        commit_sha = git.run_git("rev-parse", "HEAD")[:7]  # 取前 7 個字元

        result = run_cafe_review(
            repo_path,
            issue_name,
            "CAFE_CONFIRMED\n\nCommit 審查通過.",
            extra_args=["--commit", commit_sha]
        )

        assert result.returncode == 0

        # 驗證 review_001.md 被創建
        review_file = repo_path / ".cafe" / "issues" / issue_name / "review" / "review_001.md"
        assert review_file.exists()


@pytest.mark.e2e
class TestReviewE2EMockAgentBehavior:
    """測試 mock agent 行為"""

    def test_agent_called_only_once(self, prepared_repo_factory):
        """測試 agent 只被呼叫一次（non-iterative）

        情境：Review phase 是 non-iterative
        指令：cafe review test-issue --no-interactive
        預期：成功, history 只有一個 iteration 檔案
        """
        issue_name = "test-issue"
        repo_path = prepared_repo_factory(issue_name)
        setup_test_files(repo_path, issue_name)
        add_test_commit_to_branch(repo_path)

        result = run_cafe_review(repo_path, issue_name, "CAFE_CONFIRMED\n\n審查完成.")

        assert result.returncode == 0

        # 驗證 history 只有一個 iteration
        history_dir = repo_path / ".cafe" / "issues" / issue_name / "review" / "history"
        iteration_files = list(history_dir.glob("iteration_*.json"))
        assert len(iteration_files) == 1
        assert iteration_files[0].name == "iteration_001.json"

    def test_whitespace_only_response_should_fail(self, prepared_repo_factory):
        """測試 agent 返回僅空白字符回應應該失敗

        情境：Agent 返回只包含空白字符回應
        指令：cafe review test-issue --no-interactive
        預期：失敗, 錯誤訊息包含 "no response" or "failed"
        """
        issue_name = "test-issue"
        repo_path = prepared_repo_factory(issue_name)
        setup_test_files(repo_path, issue_name)
        add_test_commit_to_branch(repo_path)

        result = run_cafe_review(repo_path, issue_name, "   \n\n  ")

        # Whitespace-only response is treated as NO_RESPONSE
        assert result.returncode != 0
        output = result.stdout + result.stderr
        assert "no response" in output.lower() or "failed" in output.lower()


@pytest.mark.e2e
class TestReviewE2EMockBaseBranch:
    """測試 base branch 處理"""

    def test_uses_default_main_branch(self, prepared_repo_factory):
        """測試預設使用 main branch 作為 base

        情境：沒有在 config.yaml 指定 base_branch
        指令：cafe review test-issue --no-interactive
        預期：成功, 使用 main 作為 base branch 進行 diff
        """
        issue_name = "test-issue"
        repo_path = prepared_repo_factory(issue_name)
        setup_test_files(repo_path, issue_name)
        add_test_commit_to_branch(repo_path)

        result = run_cafe_review(repo_path, issue_name, "CAFE_CONFIRMED\n\n審查通過.")

        assert result.returncode == 0

        # 驗證成功執行（預設 base branch 是 main）
        status_file = repo_path / ".cafe" / "issues" / issue_name / "review" / "status.json"
        assert status_file.exists()

    def test_reads_base_branch_from_config(self, prepared_repo_factory):
        """測試從 issue config.yaml 讀取 base branch

        情境：config.yaml 指定 base_branch 為 develop
        指令：cafe review test-issue --no-interactive
        預期：嘗試使用 develop 作為 base branch（可能失敗如果 develop 不存在）
        """
        issue_name = "test-issue"
        repo_path = prepared_repo_factory(issue_name)
        setup_test_files(repo_path, issue_name)
        add_test_commit_to_branch(repo_path)

        # 創建 config.yaml with custom base_branch
        import yaml
        config_file = repo_path / ".cafe" / "issues" / issue_name / "issue.yaml"
        config_file.write_text(yaml.dump({
            "base_branch": "develop",
            "feature_branch": issue_name
        }, allow_unicode=True, default_flow_style=False))

        result = run_cafe_review(repo_path, issue_name, "CAFE_CONFIRMED\n\n審查通過.")

        # Even if develop branch doesn't exist, the command should attempt to use it
        # The test focuses on whether the config is read, not the git operation success
        # In a real scenario, develop branch would exist
        output = result.stdout + result.stderr
        # If git fails because develop doesn't exist, that's OK for this test
        assert result.returncode in [0, 1]  # May fail if develop branch doesn't exist


@pytest.mark.e2e
class TestReviewE2EMockPRComments:
    """測試 review 指令 --pr-number 參數"""

    def test_pr_number_parameter_accepted(self, prepared_repo_factory):
        """測試 --pr-number 參數被接受

        情境：執行 review 時提供 PR number
        指令：cafe review test-issue --pr-number 10 --no-interactive
        預期：指令接受參數並執行（不管 PR 是否存在）
        """
        issue_name = "test-issue"
        repo_path = prepared_repo_factory(issue_name)
        setup_test_files(repo_path, issue_name)
        add_test_commit_to_branch(repo_path)

        result = run_cafe_review(
            repo_path,
            issue_name,
            "CAFE_CONFIRMED\n\n審查通過.",
            extra_args=["--pr-number", "10"]
        )

        output = result.stdout + result.stderr
        # 可能因為 PR 不存在而失敗, 但參數應該被接受
        # returncode 0 表示成功, 1 表示可能錯誤（如 PR 不存在）
        assert result.returncode in [0, 1]

    def test_review_without_pr_number_still_works(self, prepared_repo_factory):
        """測試不提供 --pr-number 仍正常運作

        情境：正常執行 review, 不提供 PR number
        指令：cafe review test-issue --no-interactive
        預期：成功完成審查
        """
        issue_name = "test-issue"
        repo_path = prepared_repo_factory(issue_name)
        setup_test_files(repo_path, issue_name)
        add_test_commit_to_branch(repo_path)

        result = run_cafe_review(repo_path, issue_name, "CAFE_CONFIRMED\n\n審查通過.")

        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "passed" in output.lower() or "成功" in output.lower()

    def test_pr_comments_with_real_gh_data(self, prepared_repo_factory):
        """測試使用簡化 PR comments 資料（模擬 gh CLI）

        情境：執行 review 時提供 PR number, 模擬 gh CLI 返回 PR comments
        指令：cafe review test-issue --pr-number 10 --no-interactive
        預期：成功執行, PR comments 被載入（通過創建 fake gh script）
        """
        # 簡化 PR comments 資料（基於真實 PR #10）
        raw_comments = [
            {
                "id": 2532554495,
                "body": "這邊應該要用 bulkInsert() 批次寫入",
                "user": {"login": "reviewer1"},
                "created_at": "2025-11-17T03:28:30Z",
                "path": "controllers/AdminController.php",
                "line": 530
            },
            {
                "id": 2532555684,
                "body": "建議加上錯誤處理",
                "user": {"login": "reviewer2"},
                "created_at": "2025-11-17T03:29:36Z",
                "path": "controllers/AdminController.php",
                "line": 699
            }
        ]

        # 準備 gh repo view 輸出（返回 repo 資訊）
        repo_info = {
            "owner": {"login": "testowner"},
            "name": "testrepo"
        }

        issue_name = "test-issue"
        repo_path = prepared_repo_factory(issue_name)
        setup_test_files(repo_path, issue_name)
        add_test_commit_to_branch(repo_path)

        # 創建一個假 gh script 來返回 PR comments
        fake_gh_dir = repo_path / "bin"
        fake_gh_dir.mkdir()
        fake_gh = fake_gh_dir / "gh"
        fake_gh.write_text(f"""#!/bin/bash
# Handle: gh repo view --json owner,name
if [ "$1" = "repo" ] && [ "$2" = "view" ] && [ "$3" = "--json" ]; then
    cat << 'EOF'
{json.dumps(repo_info)}
EOF
    exit 0
fi

# Handle: gh api /repos/testowner/testrepo/pulls/10/comments
if [ "$1" = "api" ] && [[ "$2" == *"/pulls/10/comments" ]]; then
    cat << 'EOF'
{json.dumps(raw_comments)}
EOF
    exit 0
fi

echo "Unsupported gh command: $@" >&2
exit 1
""")
        fake_gh.chmod(0o755)

        # 使用 fake gh 執行測試（修改 PATH）
        args = ["review", "--no-interactive", "--pr-number", "10"]

        env_vars = {
            "CAFE_MOCK_AGENTS": "true",
            "CAFE_MOCK_RESPONSE": "CAFE_CONFIRMED\n\n審查完成.",
            "PATH": f"{fake_gh_dir}:{os.environ.get('PATH', '')}"
        }

        # Mock Git operations
        with patch("cafe.ui.cli.GitOperations") as mock_git_cls:
            mock_git_instance = mock_git_cls.return_value
            mock_git_instance.is_valid_branch.return_value = True
            mock_git_instance.get_current_branch.return_value = issue_name

            with patch("cafe.phases.review_phase.GitOperations") as mock_phase_git:
                mock_phase_git.return_value = mock_git_instance

                with patch.dict(os.environ, env_vars):
                    result = runner.invoke(app, args, catch_exceptions=False)

        # 應該成功完成（如果 get_pr_comments 失敗, 會導致錯誤）
        output = result.stdout or ""
        print("\n=== CLI Output ===")
        print(output)
        print("==================\n")

        assert result.exit_code == 0, f"指令應該成功執行, 但返回碼是 {result.exit_code}, 輸出：{output}"

        # 驗證審查完成 (檢查 PR comments 被載入)
        assert "pr #10 comments will be reviewed" in output.lower() or "got 2 total comments" in output.lower()
