"""E2E tests for 'aaf plan' command with mock agents.

使用 subprocess.run() 測試實際 CLI 命令執行，但用 AAF_MOCK_AGENTS=true 避免真實 LLM 呼叫。
"""

import subprocess
import json
import os
from pathlib import Path
import pytest


def setup_test_environment(tmp_path: Path, issue_name: str):
    """設置測試環境：創建 spec.md"""
    spec_dir = tmp_path / ".aaf" / "issues" / issue_name / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_file = spec_dir / "spec.md"
    spec_file.write_text("# 測試功能需求\n\n這是一個測試需求規格。")


def create_default_template(tmp_path: Path):
    """創建 default template"""
    template_dir = tmp_path / ".aaf" / "templates" / "plan"
    template_dir.mkdir(parents=True, exist_ok=True)
    template_file = template_dir / "default.md"
    template_file.write_text("""# 實作計畫

## 概要
{summary}

## 技術方案
{technical_approach}

## 開發指南
{development_guide}
""")


def run_aaf_plan(tmp_path: Path, issue_name: str, mock_response: str, extra_args: list = None, template: str = "default"):
    """Helper function to run aaf plan command with mock"""
    # Use installed aaf command or fall back to local script
    aaf_cmd = "aaf" if subprocess.run(["which", "aaf"], capture_output=True).returncode == 0 else "./aaf"
    args = [aaf_cmd, "plan", issue_name, "--no-interactive"]
    if template:
        args.extend(["--template", template])
    if extra_args:
        args.extend(extra_args)
    
    env = os.environ.copy()
    env["AAF_MOCK_AGENTS"] = "true"
    if mock_response:
        env["AAF_MOCK_RESPONSE"] = mock_response
    
    return subprocess.run(
        args,
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.mark.integration
class TestPlanE2EMockStatusCodes:
    """測試無效狀態碼處理"""

    def test_invalid_status_code_should_fail(self, tmp_path):
        """測試 agent 返回無效狀態碼應該失敗"""
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)
        create_default_template(tmp_path)
        
        result = run_aaf_plan(tmp_path, issue_name, "AAF_INVALID_CODE\n\n# 實作計畫")
        
        assert result.returncode != 0
        output = result.stdout + result.stderr
        # The parser treats invalid status codes as "no status code"
        assert "no status code" in output.lower() or "failed" in output.lower()

    def test_no_status_code_should_fail(self, tmp_path):
        """測試 agent 回應沒有狀態碼應該失敗"""
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)
        create_default_template(tmp_path)
        
        result = run_aaf_plan(tmp_path, issue_name, "# 實作計畫\n\n這是計畫內容但沒有狀態碼")
        
        assert result.returncode != 0
        output = result.stdout + result.stderr
        assert "no status code" in output.lower() or "failed" in output.lower()

    def test_empty_response_should_fail(self, tmp_path):
        """測試 agent 返回空回應應該失敗"""
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)
        create_default_template(tmp_path)
        
        result = run_aaf_plan(tmp_path, issue_name, "")
        
        assert result.returncode != 0
        output = result.stdout + result.stderr
        assert "empty" in output.lower() or "no status code" in output.lower() or "failed" in output.lower()


@pytest.mark.integration
class TestPlanE2EMockTemplateErrors:
    """測試 Template 檔案相關錯誤"""

    def test_template_not_exists_should_fail(self, tmp_path):
        """測試 template 不存在應該失敗"""
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)
        
        result = run_aaf_plan(tmp_path, issue_name, None, template="nonexistent-template")
        
        assert result.returncode != 0
        output = result.stdout + result.stderr
        assert "template" in output.lower()
        assert "not found" in output.lower() or "does not exist" in output.lower()
        
        plan_file = tmp_path / ".aaf" / "issues" / issue_name / "plan" / "plan.md"
        assert not plan_file.exists()

    def test_first_round_without_template_should_fail(self, tmp_path):
        """測試第一輪沒有提供 template 應該失敗"""
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)
        
        plan_file = tmp_path / ".aaf" / "issues" / issue_name / "plan" / "plan.md"
        if plan_file.exists():
            plan_file.unlink()
        
        result = run_aaf_plan(tmp_path, issue_name, None, template=None)
        
        assert result.returncode != 0
        output = result.stdout + result.stderr
        assert "template" in output.lower()
        assert "required" in output.lower() or "needed" in output.lower()


@pytest.mark.integration
class TestPlanE2EMockContentValidation:
    """測試 Plan 內容驗證"""

    def test_plan_content_excludes_status_code(self, tmp_path):
        """測試 plan.md 不包含狀態碼"""
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)
        create_default_template(tmp_path)
        
        result = run_aaf_plan(tmp_path, issue_name, "AAF_READY_FOR_REVIEW\n\n# 實作計畫\n\n計畫內容")
        
        assert result.returncode == 0
        
        plan_file = tmp_path / ".aaf" / "issues" / issue_name / "plan" / "plan.md"
        assert plan_file.exists()
        
        content = plan_file.read_text()
        assert "AAF_READY_FOR_REVIEW" not in content
        assert "# 實作計畫" in content
        assert "計畫內容" in content

    def test_plan_preserves_dev_guide_section(self, tmp_path):
        """測試第二輪更新時保留開發指南"""
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)
        
        plan_file = tmp_path / ".aaf" / "issues" / issue_name / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\n原始開發指南內容\n\n## 實作計畫\n\n初版計畫")
        
        result = run_aaf_plan(
            tmp_path, issue_name, 
            "AAF_READY_FOR_REVIEW\n\n## 開發指南\n\n原始開發指南內容\n\n## 實作計畫\n\n更新後的計畫",
            template=None
        )
        
        assert result.returncode == 0
        
        content = plan_file.read_text()
        assert "原始開發指南內容" in content
        assert "更新後的計畫" in content

    def test_plan_file_has_valid_structure(self, tmp_path):
        """測試 plan.md 有正確的 Markdown 結構"""
        issue_name = "test-issue"
        setup_test_environment(tmp_path, issue_name)
        create_default_template(tmp_path)
        
        result = run_aaf_plan(tmp_path, issue_name, "AAF_READY_FOR_REVIEW\n\n# 實作計畫\n\n## 步驟一\n內容")
        
        assert result.returncode == 0
        
        plan_file = tmp_path / ".aaf" / "issues" / issue_name / "plan" / "plan.md"
        content = plan_file.read_text()
        
        assert content.startswith("#")
        assert "## 步驟一" in content
        assert isinstance(content, str)
