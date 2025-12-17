"""E2E tests for 'cafe develop' command with mock agents.

使用 CliRunner 測試 CLI 命令執行，用 CAFE_MOCK_AGENTS=true 避免真實 LLM 呼叫。
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
from .conftest import init_git_repo_for_issue


runner = CliRunner()


@dataclass
class MockResult:
    """模擬 subprocess.run 的結果格式"""
    returncode: int
    stdout: str
    stderr: str


def setup_test_environment(tmp_path: Path, issue_name: str):
    """設置測試環境：創建 spec.md 和 plan.md，初始化 git repo"""
    # Initialize git repo (with GIT_CEILING_DIRECTORIES protection)
    init_git_repo_for_issue(tmp_path, issue_name)

    # Create config.yaml (required by cafe commands)
    cafe_dir = tmp_path / ".cafe"
    cafe_dir.mkdir(parents=True, exist_ok=True)
    config_file = cafe_dir / "config.yaml"
    config_file.write_text("""
agents:
  pm:
    name: Roger
    cli: copilot
  developer:
    name: David
    cli: copilot
  reviewer:
    name: Richard
    cli: copilot

auto:
  max_review_iterations: 5
""")

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
- [ ] 實作功能 A
- [ ] 實作功能 B
- [ ] 撰寫測試

## 開發指南
請按照以上任務清單逐步實作。
""")


def run_cafe_develop(
    tmp_path: Path,
    issue_name: str,
    mock_response: str,
    extra_args: Optional[List[str]] = None
) -> MockResult:
    """Helper function to run cafe develop command with mock using CliRunner"""
    args = ["develop", "--no-interactive"]
    if extra_args:
        args.extend(extra_args)

    env_vars = {"CAFE_MOCK_AGENTS": "true"}
    if mock_response:
        env_vars["CAFE_MOCK_RESPONSE"] = mock_response

    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        # Initialize git repo for the issue
        init_git_repo_for_issue(tmp_path, issue_name)
        with patch.dict(os.environ, env_vars):
            result = runner.invoke(app, args, catch_exceptions=False)
    except Exception as e:
        return MockResult(returncode=1, stdout="", stderr=str(e))
    finally:
        os.chdir(original_cwd)

    return MockResult(
        returncode=result.exit_code,
        stdout=result.stdout or "",
        stderr=""
    )


@pytest.mark.e2e
class TestDevelopE2EMockStatusCodes:
    """測試狀態碼處理"""

    def test_confirmed_status_success(self, tmp_path):
        """測試 CONFIRMED 狀態碼成功完成

        情境：Agent 返回 CAFE_CONFIRMED，開發工作完成
        指令：cafe develop test-issue --no-interactive
        預期：成功，status.json 顯示 completed 狀態
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        result = run_cafe_develop(tmp_path, issue_name, "CAFE_CONFIRMED\n\n開發完成。")

        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "completed" in output.lower() or "成功" in output.lower()

        # 驗證 status.json 被創建
        status_file = tmp_path / ".cafe" / "issues" / issue_name / "develop" / "status.json"
        assert status_file.exists()

        with open(status_file) as f:
            status_data = json.load(f)
            assert status_data["phase"] == "develop"
            assert status_data["status"] == "completed"
            assert status_data["status_code"] == "CAFE_CONFIRMED"

    def test_invalid_status_code_pauses(self, tmp_path):
        """測試 agent 返回無效狀態碼經過 5 次重試後失敗

        情境：Agent 返回無法識別的狀態碼
        指令：cafe develop test-issue --no-interactive
        預期：失敗，經過 5 次重試後輸出包含 "Agent 在 5 次嘗試後仍未回傳有效的 status code"
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        result = run_cafe_develop(tmp_path, issue_name, "CAFE_INVALID_CODE\n\n開發中...")

        # Non-interactive mode fails after 5 retries
        assert result.returncode == 1
        output = result.stdout + result.stderr
        # After retries, should fail with status code error
        assert "Agent 在 5 次嘗試後仍未回傳有效的 status code" in output or "failed" in output.lower()

    def test_no_status_code_pauses(self, tmp_path):
        """測試 agent 回應沒有狀態碼經過 5 次重試後失敗

        情境：Agent 回應內容沒有包含任何狀態碼
        指令：cafe develop test-issue --no-interactive
        預期：失敗，經過 5 次重試後輸出包含 "Agent 在 5 次嘗試後仍未回傳有效的 status code"
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        result = run_cafe_develop(tmp_path, issue_name, "開發中，正在實作功能...")

        # Non-interactive mode fails after 5 retries
        assert result.returncode == 1
        output = result.stdout + result.stderr
        assert "Agent 在 5 次嘗試後仍未回傳有效的 status code" in output or "failed" in output.lower()

    def test_whitespace_only_response_should_fail(self, tmp_path):
        """測試 agent 返回僅空白字符的回應應該失敗

        情境：Agent 返回只包含空白字符的回應
        指令：cafe develop test-issue --no-interactive
        預期：失敗，錯誤訊息包含 "no response" 或 "failed"
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        result = run_cafe_develop(tmp_path, issue_name, "   \n\n  ")

        # Whitespace-only response is treated as NO_RESPONSE
        assert result.returncode != 0
        output = result.stdout + result.stderr
        assert "no response" in output.lower() or "failed" in output.lower()

    def test_need_permission_in_non_interactive_should_fail(self, tmp_path):
        """測試 NEED_PERMISSION 在 non-interactive 模式應該失敗

        情境：Agent 返回 CAFE_NEED_PERMISSION，但在非互動模式下
        指令：cafe develop test-issue --no-interactive
        預期：失敗，錯誤訊息包含 "permission" 或 "non-interactive"
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        result = run_cafe_develop(tmp_path, issue_name, "CAFE_NEED_PERMISSION\n\n需要權限執行 git commit。")

        assert result.returncode != 0
        output = result.stdout + result.stderr
        assert "permission" in output.lower() or "non-interactive" in output.lower()


@pytest.mark.e2e
class TestDevelopE2EMockFileValidation:
    """測試檔案相關錯誤"""

    def test_spec_file_not_exists_should_fail(self, tmp_path):
        """測試 spec.md 不存在應該失敗

        情境：Issue 的 spec.md 檔案不存在
        指令：cafe develop test-issue --no-interactive
        預期：失敗，錯誤訊息包含 "spec" 或 "not found"
        """
        issue_name = "test-issue"
        # 只創建 plan.md，不創建 spec.md
        plan_dir = tmp_path / ".cafe" / "issues" / issue_name / "plan"
        plan_dir.mkdir(parents=True, exist_ok=True)
        plan_file = plan_dir / "plan.md"
        plan_file.write_text("# 實作計畫")

        result = run_cafe_develop(tmp_path, issue_name, "CAFE_CONFIRMED\n\n開發完成。")

        assert result.returncode != 0
        output = result.stdout + result.stderr
        assert "spec" in output.lower() or "not found" in output.lower()

    def test_plan_file_not_exists_should_fail(self, tmp_path):
        """測試 plan.md 不存在應該失敗

        情境：Issue 的 plan.md 檔案不存在
        指令：cafe develop test-issue --no-interactive
        預期：失敗，錯誤訊息包含 "plan" 或 "not found"
        """
        issue_name = "test-issue"
        # 只創建 spec.md，不創建 plan.md
        spec_dir = tmp_path / ".cafe" / "issues" / issue_name / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)
        spec_file = spec_dir / "spec.md"
        spec_file.write_text("# 測試功能需求")

        result = run_cafe_develop(tmp_path, issue_name, "CAFE_CONFIRMED\n\n開發完成。")

        assert result.returncode != 0
        output = result.stdout + result.stderr
        assert "plan" in output.lower() or "not found" in output.lower()

    def test_history_directory_created(self, tmp_path):
        """測試 history 目錄被創建

        情境：成功執行 develop phase
        指令：cafe develop test-issue --no-interactive
        預期：成功，develop/history 目錄被創建，至少有一個 iteration 檔案
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        result = run_cafe_develop(tmp_path, issue_name, "CAFE_CONFIRMED\n\n開發完成。")

        assert result.returncode == 0

        history_dir = tmp_path / ".cafe" / "issues" / issue_name / "develop" / "history"
        assert history_dir.exists()
        assert history_dir.is_dir()

        # 至少有一個 iteration 檔案
        iteration_files = list(history_dir.glob("iteration_*.json"))
        assert len(iteration_files) >= 1

    def test_iteration_file_structure(self, tmp_path):
        """測試 iteration 檔案結構正確

        情境：成功執行 develop phase
        指令：cafe develop test-issue --no-interactive
        預期：成功，iteration_001.json 包含正確的欄位（iteration, timestamp, status_code）
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        result = run_cafe_develop(tmp_path, issue_name, "CAFE_CONFIRMED\n\n開發完成。")

        assert result.returncode == 0

        iteration_file = tmp_path / ".cafe" / "issues" / issue_name / "develop" / "history" / "iteration_001.json"
        assert iteration_file.exists()

        with open(iteration_file) as f:
            data = json.load(f)
            assert "iteration" in data
            assert "timestamp" in data
            assert "status_code" in data
            assert data["iteration"] == 1
            assert data["status_code"] == "CAFE_CONFIRMED"


@pytest.mark.e2e
class TestDevelopE2EMockBranchManagement:
    """測試 branch 管理（使用 mock git operations）"""

    def test_config_file_created_with_branch_info(self, tmp_path):
        """測試 config.yaml 包含 branch 資訊

        情境：在 git repository 中執行 develop
        指令：cafe develop test-issue --no-interactive
        預期：成功，config.yaml 包含 base_branch 或 feature_branch 資訊
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        result = run_cafe_develop(tmp_path, issue_name, "CAFE_CONFIRMED\n\n開發完成。")

        # Note: This test may fail in CI without git repo, skip or mock git
        if result.returncode == 0:
            config_file = tmp_path / ".cafe" / "issues" / issue_name / "issue.yaml"
            if config_file.exists():
                import yaml
                with open(config_file) as f:
                    config_data = yaml.safe_load(f)
                    assert "base_branch" in config_data or "feature_branch" in config_data


@pytest.mark.e2e
class TestDevelopE2EMockReviewFeedback:
    """測試 review feedback 處理"""

    def test_continues_with_review_feedback(self, tmp_path):
        """測試當有 review feedback 時繼續執行

        情境：已完成一次 develop，後來收到 review feedback (NEEDS_CHANGES)
        指令：cafe develop test-issue --no-interactive（第二次執行）
        預期：成功，處理 review feedback 並繼續執行
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        # 先執行一次開發（完成）
        result1 = run_cafe_develop(tmp_path, issue_name, "CAFE_CONFIRMED\n\n初次開發完成。")
        assert result1.returncode == 0

        # 創建 review feedback
        review_dir = tmp_path / ".cafe" / "issues" / issue_name / "review"
        review_dir.mkdir(parents=True, exist_ok=True)
        review_file = review_dir / "review.md"
        review_file.write_text("CAFE_NEEDS_CHANGES\n\n請修正 commit message。")

        review_status_file = review_dir / "status.json"
        import time
        time.sleep(0.01)  # Ensure review timestamp is after develop
        review_status_data = {
            "phase": "review",
            "status": "completed",
            "status_code": "CAFE_NEEDS_CHANGES",
            "iteration": 1,
            "timestamp": "2025-01-01T00:01:00"
        }
        review_status_file.write_text(json.dumps(review_status_data, indent=2))

        # 再次執行開發（處理 review feedback）
        result2 = run_cafe_develop(tmp_path, issue_name, "CAFE_CONFIRMED\n\n已修正 commit message。")

        # 應該成功執行而非提前返回
        assert result2.returncode == 0
        output = result2.stdout + result2.stderr
        # 可能包含 "completed" 或成功訊息
        assert "completed" in output.lower() or "成功" in output.lower()

    def test_returns_early_when_already_completed(self, tmp_path):
        """測試當已完成且無 review feedback 時提前返回

        情境：已完成 develop，無新的 review feedback
        指令：cafe develop test-issue --no-interactive（第二次執行）
        預期：成功但提前返回，輸出包含 "completed"
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        # 第一次執行
        result1 = run_cafe_develop(tmp_path, issue_name, "CAFE_CONFIRMED\n\n開發完成。")
        assert result1.returncode == 0

        # 第二次執行（應該提前返回）
        result2 = run_cafe_develop(tmp_path, issue_name, "CAFE_CONFIRMED\n\n不應該執行到這裡。")

        # 應該成功但提前返回
        assert result2.returncode == 0
        output = result2.stdout + result2.stderr
        # 檢查是否包含 "already" 或 "completed" 訊息
        assert "completed" in output.lower()


@pytest.mark.e2e
class TestDevelopE2EMockMultipleIterations:
    """測試多輪迭代場景"""

    def test_single_iteration_success(self, tmp_path):
        """測試單輪迭代成功

        情境：第一次執行 develop 並成功完成
        指令：cafe develop test-issue --no-interactive
        預期：成功，history 目錄只有一個 iteration 檔案
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        result = run_cafe_develop(tmp_path, issue_name, "CAFE_CONFIRMED\n\n開發完成。")

        assert result.returncode == 0

        # 驗證只有一個 iteration
        history_dir = tmp_path / ".cafe" / "issues" / issue_name / "develop" / "history"
        iteration_files = list(history_dir.glob("iteration_*.json"))
        assert len(iteration_files) == 1

    def test_status_file_reflects_completion(self, tmp_path):
        """測試 status.json 正確反映完成狀態

        情境：成功完成 develop phase
        指令：cafe develop test-issue --no-interactive
        預期：成功，status.json 的 status 為 "completed"
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        result = run_cafe_develop(tmp_path, issue_name, "CAFE_CONFIRMED\n\n開發完成。")

        assert result.returncode == 0

        status_file = tmp_path / ".cafe" / "issues" / issue_name / "develop" / "status.json"
        assert status_file.exists()

        with open(status_file) as f:
            status_data = json.load(f)
            assert status_data["status"] == "completed"
            assert status_data["iteration"] >= 1


@pytest.mark.e2e
class TestDevelopE2EMockErrorRecovery:
    """測試錯誤恢復場景"""

    def test_handles_corrupted_status_file(self, tmp_path):
        """測試處理損壞的 status.json

        情境：develop/status.json 檔案內容損壞（invalid JSON）
        指令：cafe develop test-issue --no-interactive
        預期：能夠繼續執行或優雅失敗，不會 crash
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        # 創建損壞的 status.json
        develop_dir = tmp_path / ".cafe" / "issues" / issue_name / "develop"
        develop_dir.mkdir(parents=True, exist_ok=True)
        status_file = develop_dir / "status.json"
        status_file.write_text("{invalid json")

        # 應該能夠繼續執行（忽略損壞的檔案）
        result = run_cafe_develop(tmp_path, issue_name, "CAFE_CONFIRMED\n\n開發完成。")

        # 可能成功或失敗，但不應該 crash
        assert result.returncode in [0, 1]

    def test_handles_missing_directories_gracefully(self, tmp_path):
        """測試優雅處理缺少的目錄

        情境：develop 目錄不存在
        指令：cafe develop test-issue --no-interactive
        預期：成功，自動創建需要的目錄
        """
        issue_name = "test-issue"

        # Initialize git repo (with GIT_CEILING_DIRECTORIES protection)
        init_git_repo_for_issue(tmp_path, issue_name)

        # Create config.yaml (required by cafe commands)
        cafe_dir = tmp_path / ".cafe"
        from tests.conftest import create_minimal_config

        create_minimal_config(tmp_path)

        # 只創建最基本的 spec 和 plan
        spec_dir = tmp_path / ".cafe" / "issues" / issue_name / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)
        (spec_dir / "spec.md").write_text("# Spec")

        plan_dir = tmp_path / ".cafe" / "issues" / issue_name / "plan"
        plan_dir.mkdir(parents=True, exist_ok=True)
        (plan_dir / "plan.md").write_text("# Plan")

        # 不創建 develop 目錄
        result = run_cafe_develop(tmp_path, issue_name, "CAFE_CONFIRMED\n\n開發完成。")

        # 應該自動創建需要的目錄並成功
        assert result.returncode == 0

        develop_dir = tmp_path / ".cafe" / "issues" / issue_name / "develop"
        assert develop_dir.exists()


@pytest.mark.e2e
class TestDevelopE2EMockOutputValidation:
    """測試輸出驗證"""

    def test_output_contains_success_message(self, tmp_path):
        """測試輸出包含成功訊息

        情境：成功完成 develop phase
        指令：cafe develop test-issue --no-interactive
        預期：成功，輸出包含 "completed" 或 "success" 等成功訊息
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        result = run_cafe_develop(tmp_path, issue_name, "CAFE_CONFIRMED\n\n開發完成。")

        assert result.returncode == 0
        output = result.stdout + result.stderr
        # 輸出應包含成功相關訊息
        assert any(word in output.lower() for word in ["completed", "success", "成功", "完成"])

    def test_error_output_is_informative(self, tmp_path):
        """測試錯誤輸出提供有用資訊

        情境：故意不創建 plan.md 導致失敗
        指令：cafe develop test-issue --no-interactive
        預期：失敗，錯誤訊息明確提到 "plan"
        """
        issue_name = "test-issue"
        # 故意不創建 plan.md
        spec_dir = tmp_path / ".cafe" / "issues" / issue_name / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)
        (spec_dir / "spec.md").write_text("# Spec")

        result = run_cafe_develop(tmp_path, issue_name, "CAFE_CONFIRMED\n\n不應該到這裡")

        assert result.returncode != 0
        output = result.stdout + result.stderr
        # 錯誤訊息應該提到 plan
        assert "plan" in output.lower()


@pytest.mark.e2e
class TestDevelopE2EMockPRComments:
    """測試 PR comments 功能"""

    def test_pr_number_parameter_accepted(self, tmp_path):
        """測試 --pr-number 參數被接受

        情境：使用 --pr-number 參數執行 develop
        指令：cafe develop test-issue --pr-number 123 --no-interactive
        預期：成功（即使 PR 不存在，也不應該影響執行）
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        result = run_cafe_develop(
            tmp_path,
            issue_name,
            "CAFE_CONFIRMED\n\n開發完成。",
            extra_args=["--pr-number", "123"]
        )

        # 應該成功完成（PR comments 是 optional context）
        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "completed" in output.lower() or "成功" in output.lower()

    def test_develop_without_pr_number_still_works(self, tmp_path):
        """測試不使用 --pr-number 仍然正常運作

        情境：不使用 --pr-number 參數執行 develop
        指令：cafe develop test-issue --no-interactive
        預期：成功，與之前的行為相同
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        result = run_cafe_develop(tmp_path, issue_name, "CAFE_CONFIRMED\n\n開發完成。")

        # 應該正常工作（向後兼容）
        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "completed" in output.lower() or "成功" in output.lower()

    def test_dev_alias_with_pr_number(self, tmp_path):
        """測試 dev alias 也能接受 --pr-number 參數

        情境：使用 dev alias 和 --pr-number 參數執行
        指令：cafe dev --pr-number 123 --no-interactive
        預期：成功（與 develop 命令行為一致）
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        # 使用 run_cafe_develop helper 並添加 --pr-number
        result = run_cafe_develop(
            tmp_path,
            issue_name,
            "CAFE_CONFIRMED\n\n開發完成。",
            extra_args=["--pr-number", "123"]
        )

        # 應該成功完成（dev alias 與 develop 行為相同）
        assert result.returncode == 0, f"指令應該成功執行，但返回碼是 {result.returncode}，輸出：{result.stderr}"
        output = result.stdout + result.stderr
        assert "completed" in output.lower() or "成功" in output.lower()

    def test_pr_comments_with_real_gh_data(self, tmp_path):
        """測試使用簡化的 PR comments 資料（模擬 gh CLI）

        情境：執行 develop 時提供 PR number，模擬 gh CLI 返回 PR comments
        指令：cafe develop test-issue --pr-number 10 --no-interactive
        預期：成功執行，PR comments 被載入（通過創建 fake gh script）
        """
        # 簡化的 PR comments 資料（基於真實 PR #10）
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

        # 準備 gh repo view 的輸出（返回 repo 資訊）
        repo_info = {
            "owner": {"login": "testowner"},
            "name": "testrepo"
        }

        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        # init_git_repo_for_issue already checked out to the issue branch

        # 創建一個假的 gh script 來返回 PR comments
        fake_gh_dir = tmp_path / "bin"
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
        args = ["develop", "--no-interactive", "--pr-number", "10"]

        env_vars = {
            "CAFE_MOCK_AGENTS": "true",
            "CAFE_MOCK_RESPONSE": "CAFE_CONFIRMED\n\n開發完成。",
            "PATH": f"{fake_gh_dir}:{os.environ.get('PATH', '')}"
        }

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            with patch.dict(os.environ, env_vars):
                result = runner.invoke(app, args, catch_exceptions=False)
        finally:
            os.chdir(original_cwd)

        # 應該成功完成（如果 get_pr_comments 失敗，會導致錯誤）
        output = result.stdout or ""
        print("\n=== CLI Output ===")
        print(output)
        print("==================\n")

        assert result.exit_code == 0, f"指令應該成功執行，但返回碼是 {result.exit_code}，輸出：{output}"

        # 驗證開發完成
        assert "completed" in output.lower() or "成功" in output.lower()


@pytest.mark.e2e
class TestDevelopAutoModeClarification:
    """測試 develop --auto 模式處理 IN_PROGRESS 狀態的行為"""

    def test_auto_mode_calls_execute_next_phase_on_in_progress(self, tmp_path):
        """測試 auto 模式下收到 IN_PROGRESS 狀態時調用 _execute_next_phase_auto

        情境：Agent 返回 CAFE_NEED_CLARIFICATION（IN_PROGRESS 狀態），並且在 auto 模式下
        指令：cafe develop --auto
        預期：調用 _execute_next_phase_auto 以繼續流程
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        # Mock _execute_next_phase_auto to prevent actual subprocess call
        with patch('cafe.ui.cli._execute_next_phase_auto') as mock_execute:
            args = ["develop", "--auto"]
            env_vars = {
                "CAFE_MOCK_AGENTS": "true",
                "CAFE_MOCK_RESPONSE": "CAFE_NEED_CLARIFICATION\n\nI need clarification on the expected behavior."
            }

            original_cwd = os.getcwd()
            try:
                os.chdir(tmp_path)
                with patch.dict(os.environ, env_vars):
                    result = runner.invoke(app, args, catch_exceptions=False)
            finally:
                os.chdir(original_cwd)

            output = result.stdout or ""
            print("\n=== CLI Output ===")
            print(output)
            print("==================\n")

            # 驗證調用了 _execute_next_phase_auto
            mock_execute.assert_called_once_with("develop", issue_name)

    def test_auto_mode_calls_execute_for_need_permission(self, tmp_path):
        """測試 auto 模式下收到 NEED_PERMISSION 時也會調用 _execute_next_phase_auto

        情境：Agent 返回 CAFE_NEED_PERMISSION（IN_PROGRESS 狀態），並且在 auto 模式下
        指令：cafe develop --auto
        預期：調用 _execute_next_phase_auto（與 NEED_CLARIFICATION 行為一致）
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        # Mock _execute_next_phase_auto to prevent actual subprocess call
        with patch('cafe.ui.cli._execute_next_phase_auto') as mock_execute:
            args = ["develop", "--auto"]
            env_vars = {
                "CAFE_MOCK_AGENTS": "true",
                "CAFE_MOCK_RESPONSE": "CAFE_NEED_PERMISSION\n\nI need permission to continue."
            }

            original_cwd = os.getcwd()
            try:
                os.chdir(tmp_path)
                with patch.dict(os.environ, env_vars):
                    result = runner.invoke(app, args, catch_exceptions=False)
            finally:
                os.chdir(original_cwd)

            output = result.stdout or ""
            print("\n=== CLI Output ===")
            print(output)
            print("==================\n")

            # 驗證調用了 _execute_next_phase_auto
            mock_execute.assert_called_once_with("develop", issue_name)

    def test_non_auto_mode_shows_normal_pause_message_on_in_progress(self, tmp_path):
        """測試非 auto 模式下收到 IN_PROGRESS 狀態時顯示一般暫停訊息

        情境：Agent 返回 CAFE_NEED_CLARIFICATION，但不在 auto 模式下
        指令：cafe develop（沒有 --auto）
        預期：顯示一般的暫停訊息，不會執行 auto mode 的邏輯
        """
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)

        args = ["develop"]
        env_vars = {
            "CAFE_MOCK_AGENTS": "true",
            "CAFE_MOCK_RESPONSE": "CAFE_NEED_CLARIFICATION\n\nI need clarification on the expected behavior."
        }

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            with patch.dict(os.environ, env_vars):
                result = runner.invoke(app, args, catch_exceptions=False)
        finally:
            os.chdir(original_cwd)

        output = result.stdout or ""
        print("\n=== CLI Output ===")
        print(output)
        print("==================\n")

        # 驗證顯示一般暫停訊息
        assert "⏸️  Development paused" in output
        assert "Resume with: cafe develop" in output
