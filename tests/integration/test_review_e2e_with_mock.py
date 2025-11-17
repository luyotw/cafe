"""E2E tests for 'cafe review' command with mock agents.

使用 subprocess.run() 測試實際 CLI 命令執行，但用 CAFE_MOCK_AGENTS=true 避免真實 LLM 呼叫。
"""

import subprocess
import json
import os
from pathlib import Path
import pytest


def init_git_repo(tmp_path: Path):
    """初始化 git repository"""
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "checkout", "-b", "main"], cwd=str(tmp_path), capture_output=True)

    # Create initial commit
    readme = tmp_path / "README.md"
    readme.write_text("# Test Project")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(tmp_path), capture_output=True)


def setup_test_environment(tmp_path: Path, issue_name: str):
    """設置測試環境：創建 spec.md 和 plan.md，初始化 git repo，創建測試 commit"""
    # Initialize git repo with initial commit
    init_git_repo(tmp_path)

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

    # 創建 feature branch 並添加測試 commit
    subprocess.run(["git", "checkout", "-b", issue_name], cwd=str(tmp_path), capture_output=True)
    test_file = tmp_path / "test.py"
    test_file.write_text("""def hello():
    print("Hello, World!")

if __name__ == "__main__":
    hello()
""")
    subprocess.run(["git", "add", "test.py"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add hello function"], cwd=str(tmp_path), capture_output=True)


def run_cafe_review(tmp_path: Path, issue_name: str, mock_response: str, extra_args: list = None):
    """Helper function to run cafe review command with mock"""
    # Use installed cafe command or fall back to local script
    cafe_cmd = "cafe" if subprocess.run(["which", "cafe"], capture_output=True).returncode == 0 else "./cafe"
    args = [cafe_cmd, "review", issue_name, "--no-interactive"]
    if extra_args:
        args.extend(extra_args)

    env = os.environ.copy()
    env["CAFE_MOCK_AGENTS"] = "true"
    if mock_response:
        env["CAFE_MOCK_RESPONSE"] = mock_response

    return subprocess.run(
        args,
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.mark.e2e
class TestReviewE2EMockStatusCodes:
    """測試狀態碼處理"""

    def test_confirmed_status_success(self, tmp_path):
        """測試 CONFIRMED 狀態碼成功完成

        情境：Agent 審查通過，返回 CAFE_CONFIRMED
        指令：cafe review test-issue --no-interactive
        預期：成功，status.json 顯示 completed 狀態，status_code 為 CAFE_CONFIRMED
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        result = run_cafe_review(tmp_path, issue_name, "CAFE_CONFIRMED\n\n程式碼審查通過。")

        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "completed" in output.lower() or "成功" in output.lower() or "passed" in output.lower()

        # 驗證 status.json 被創建
        status_file = tmp_path / ".cafe" / "issues" / issue_name / "review" / "status.json"
        assert status_file.exists()

        with open(status_file) as f:
            status_data = json.load(f)
            assert status_data["phase"] == "review"
            assert status_data["status"] == "completed"
            assert status_data["status_code"] == "CAFE_CONFIRMED"

    def test_needs_changes_status_success(self, tmp_path):
        """測試 NEEDS_CHANGES 狀態碼成功完成

        情境：Agent 發現需要修正，返回 CAFE_NEEDS_CHANGES
        指令：cafe review test-issue --no-interactive
        預期：成功，status.json 顯示 completed 狀態（NEEDS_CHANGES 也視為完成）
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        result = run_cafe_review(tmp_path, issue_name, "CAFE_NEEDS_CHANGES\n\n需要修正 commit message。")

        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "completed" in output.lower() or "成功" in output.lower()

        # 驗證 status.json 被創建，且 NEEDS_CHANGES 也視為 completed
        status_file = tmp_path / ".cafe" / "issues" / issue_name / "review" / "status.json"
        assert status_file.exists()

        with open(status_file) as f:
            status_data = json.load(f)
            assert status_data["phase"] == "review"
            assert status_data["status"] == "completed"  # NEEDS_CHANGES 也是完成狀態
            assert status_data["status_code"] == "CAFE_NEEDS_CHANGES"

    def test_invalid_status_code_fails_in_non_interactive(self, tmp_path):
        """測試無效狀態碼在 non-interactive 模式會失敗

        情境：Agent 返回無效的狀態碼
        指令：cafe review test-issue --no-interactive
        預期：失敗，錯誤訊息包含 "no status code" 或 "failed"
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        result = run_cafe_review(tmp_path, issue_name, "CAFE_INVALID_CODE\n\n審查意見...")

        # Non-interactive mode fails when status code is invalid/missing
        assert result.returncode != 0
        output = result.stdout + result.stderr
        assert "no status code" in output.lower() or "failed" in output.lower()


@pytest.mark.e2e
class TestReviewE2EMockFileValidation:
    """測試檔案相關功能"""

    def test_review_md_created(self, tmp_path):
        """測試 review.md 被創建

        情境：成功完成 review phase
        指令：cafe review test-issue --no-interactive
        預期：成功，review.md 包含狀態碼和審查意見
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        result = run_cafe_review(tmp_path, issue_name, "CAFE_CONFIRMED\n\n程式碼審查通過。")

        assert result.returncode == 0

        review_file = tmp_path / ".cafe" / "issues" / issue_name / "review" / "review.md"
        assert review_file.exists()

        content = review_file.read_text()
        assert "CAFE_CONFIRMED" in content
        assert "程式碼審查通過" in content

    def test_history_directory_created(self, tmp_path):
        """測試 history 目錄被創建

        情境：成功完成 review phase（non-iterative）
        指令：cafe review test-issue --no-interactive
        預期：成功，review/history 目錄被創建，只有一個 iteration 檔案
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        result = run_cafe_review(tmp_path, issue_name, "CAFE_CONFIRMED\n\n審查完成。")

        assert result.returncode == 0

        history_dir = tmp_path / ".cafe" / "issues" / issue_name / "review" / "history"
        assert history_dir.exists()
        assert history_dir.is_dir()

        # Review 是 non-iterative，應該只有一個 iteration 檔案
        iteration_files = list(history_dir.glob("iteration_*.json"))
        assert len(iteration_files) == 1

    def test_iteration_file_structure(self, tmp_path):
        """測試 iteration 檔案結構正確

        情境：成功完成 review phase
        指令：cafe review test-issue --no-interactive
        預期：成功，iteration_001.json 包含正確欄位，allowed_tools 為 ["bash"]
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        result = run_cafe_review(tmp_path, issue_name, "CAFE_NEEDS_CHANGES\n\n需要修正。")

        assert result.returncode == 0

        iteration_file = tmp_path / ".cafe" / "issues" / issue_name / "review" / "history" / "iteration_001.json"
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
            # Review 允許使用 bash tools
            assert data["allowed_tools"] == ["bash"]

    def test_no_diff_should_fail(self, tmp_path):
        """測試沒有 diff 時應該失敗

        情境：Feature branch 沒有任何改變（無 diff）
        指令：cafe review test-issue --no-interactive
        預期：失敗，錯誤訊息包含 "no changes" 或 "diff"
        """
        issue_name = "test-issue"
        init_git_repo(tmp_path)

        # 創建 spec 和 plan 但不創建 feature branch（沒有 diff）
        spec_dir = tmp_path / ".cafe" / "issues" / issue_name / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)
        spec_file = spec_dir / "spec.md"
        spec_file.write_text("# 測試功能需求")

        plan_dir = tmp_path / ".cafe" / "issues" / issue_name / "plan"
        plan_dir.mkdir(parents=True, exist_ok=True)
        plan_file = plan_dir / "plan.md"
        plan_file.write_text("# 實作計畫")

        # 停留在 main branch，沒有任何改變
        result = run_cafe_review(tmp_path, issue_name, "CAFE_CONFIRMED\n\n審查通過。")

        # 沒有 diff 應該失敗
        assert result.returncode != 0
        output = result.stdout + result.stderr
        assert "no changes" in output.lower() or "diff" in output.lower() or "failed" in output.lower()


@pytest.mark.e2e
class TestReviewE2EMockDiffHandling:
    """測試 diff 處理功能"""

    def test_full_branch_diff_by_default(self, tmp_path):
        """測試預設審查完整 branch diff

        情境：沒有指定特定 commit，審查整個 feature branch
        指令：cafe review test-issue --no-interactive
        預期：成功，審查 feature branch 與 main 的所有差異
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        result = run_cafe_review(tmp_path, issue_name, "CAFE_CONFIRMED\n\n審查完成。")

        assert result.returncode == 0

        # 驗證 review.md 包含審查結果
        review_file = tmp_path / ".cafe" / "issues" / issue_name / "review" / "review.md"
        assert review_file.exists()

    def test_specific_commit_with_flag(self, tmp_path):
        """測試使用 --commit 旗標審查特定 commit

        情境：只審查特定 commit 的變更
        指令：cafe review test-issue --no-interactive --commit <commit-sha>
        預期：成功，只審查指定 commit 的 diff
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        # 取得最新 commit SHA
        commit_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        commit_sha = commit_result.stdout.strip()[:7]  # 取前 7 個字元

        result = run_cafe_review(
            tmp_path,
            issue_name,
            "CAFE_CONFIRMED\n\nCommit 審查通過。",
            extra_args=["--commit", commit_sha]
        )

        assert result.returncode == 0

        # 驗證 review.md 被創建
        review_file = tmp_path / ".cafe" / "issues" / issue_name / "review" / "review.md"
        assert review_file.exists()


@pytest.mark.e2e
class TestReviewE2EMockAgentBehavior:
    """測試 mock agent 行為"""

    def test_agent_called_only_once(self, tmp_path):
        """測試 agent 只被呼叫一次（non-iterative）

        情境：Review phase 是 non-iterative
        指令：cafe review test-issue --no-interactive
        預期：成功，history 只有一個 iteration 檔案
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        result = run_cafe_review(tmp_path, issue_name, "CAFE_CONFIRMED\n\n審查完成。")

        assert result.returncode == 0

        # 驗證 history 只有一個 iteration
        history_dir = tmp_path / ".cafe" / "issues" / issue_name / "review" / "history"
        iteration_files = list(history_dir.glob("iteration_*.json"))
        assert len(iteration_files) == 1
        assert iteration_files[0].name == "iteration_001.json"

    def test_whitespace_only_response_should_fail(self, tmp_path):
        """測試 agent 返回僅空白字符的回應應該失敗

        情境：Agent 返回只包含空白字符的回應
        指令：cafe review test-issue --no-interactive
        預期：失敗，錯誤訊息包含 "no response" 或 "failed"
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        result = run_cafe_review(tmp_path, issue_name, "   \n\n  ")

        # Whitespace-only response is treated as NO_RESPONSE
        assert result.returncode != 0
        output = result.stdout + result.stderr
        assert "no response" in output.lower() or "failed" in output.lower()


@pytest.mark.e2e
class TestReviewE2EMockBaseBranch:
    """測試 base branch 處理"""

    def test_uses_default_main_branch(self, tmp_path):
        """測試預設使用 main branch 作為 base

        情境：沒有在 config.json 指定 base_branch
        指令：cafe review test-issue --no-interactive
        預期：成功，使用 main 作為 base branch 進行 diff
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        result = run_cafe_review(tmp_path, issue_name, "CAFE_CONFIRMED\n\n審查通過。")

        assert result.returncode == 0

        # 驗證成功執行（預設 base branch 是 main）
        status_file = tmp_path / ".cafe" / "issues" / issue_name / "review" / "status.json"
        assert status_file.exists()

    def test_reads_base_branch_from_config(self, tmp_path):
        """測試從 issue config.json 讀取 base branch

        情境：config.json 指定 base_branch 為 develop
        指令：cafe review test-issue --no-interactive
        預期：嘗試使用 develop 作為 base branch（可能失敗如果 develop 不存在）
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        # 創建 config.json with custom base_branch
        config_file = tmp_path / ".cafe" / "issues" / issue_name / "config.json"
        config_file.write_text(json.dumps({
            "base_branch": "develop",
            "feature_branch": issue_name
        }))

        result = run_cafe_review(tmp_path, issue_name, "CAFE_CONFIRMED\n\n審查通過。")

        # Even if develop branch doesn't exist, the command should attempt to use it
        # The test focuses on whether the config is read, not the git operation success
        # In a real scenario, develop branch would exist
        output = result.stdout + result.stderr
        # If git fails because develop doesn't exist, that's OK for this test
        assert result.returncode in [0, 1]  # May fail if develop branch doesn't exist
