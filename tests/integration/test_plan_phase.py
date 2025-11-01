#!/usr/bin/env python3
"""Integration test for plan phase with real agent execution."""

import pytest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from aaf.agents.manager import AgentManager
from aaf.core.permission import PermissionHandler
from aaf.core.types import AgentConfig, AgentCLI, WorkflowMode, PhaseStatus
from aaf.phases.plan_phase import PlanPhase


@pytest.mark.integration
def test_plan_phase_first_iteration_with_dev_guide():
    """Test first iteration of plan phase with mocked user input.

    This test verifies that:
    - PlanPhase can start with a dev guide
    - Developer agent processes the spec and creates implementation plan
    - plan.md and status.json are created correctly
    """
    # Setup
    agent_manager = AgentManager()
    agent_manager.register_agent(
        AgentConfig(name="David", cli=AgentCLI.CLAUDE)
    )

    permission_handler = PermissionHandler()

    # Create test directory structure
    test_dir = Path(".aaf/issues/test-integration")
    spec_dir = test_dir / "spec"
    plan_dir = test_dir / "plan"

    # Clean up previous test files
    import shutil
    if test_dir.exists():
        shutil.rmtree(test_dir)

    # Create spec file
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_file = spec_dir / "spec.md"
    spec_file.write_text("""## 使用者故事

身為開發者，我想要有一個指令 `myip` 可以顯示我的外網 IP，以便我可以快速複製貼上到別的地方

## 需求規格

### 核心功能
- 提供一個指令工具叫做 myip
- 查詢並顯示當前的外網 IP 位址

### 輸出格式
- 只顯示 IP 位址本身（例如：`203.0.113.42`）
- 不需要任何說明文字或額外資訊

### 平台支援
- 支援 Linux 環境

### 錯誤處理
- 如果沒有網路連線，顯示空行
""")

    # Create plan phase
    phase = PlanPhase(
        agent_manager=agent_manager,
        permission_handler=permission_handler,
        spec_file=str(spec_file),
        workflow_mode=WorkflowMode.LOCAL,
        issue_name="test-integration",
        interactive=True,
        dev_agent="David",
    )

    # Prepare user input for dev guide
    dev_guide = """可以使用 curl 或 wget 查詢外網 IP API 服務，例如：
- https://api.ipify.org
- https://ifconfig.me

建議使用 bash script 或 Python 腳本實作。
輸出要簡潔，只顯示 IP 位址。"""

    # Mock display.get_multiline_input to provide dev guide
    with patch.object(phase.display, 'get_multiline_input', return_value=dev_guide):
        result = phase.execute()

        # Verify result
        assert result.status in [PhaseStatus.COMPLETED, PhaseStatus.IN_PROGRESS]
        assert phase.iteration >= 1

    # Check plan.md was created
    plan_file = plan_dir / "plan.md"
    assert plan_file.exists(), "plan.md should be created"

    content = plan_file.read_text()

    # Verify plan.md has all required sections
    print(f"\n📄 Generated plan.md:")
    print("=" * 70)
    print(content)
    print("=" * 70)

    assert "## 開發指南" in content, "Should contain dev guide section"
    # Dev guide content should be preserved
    assert ("api.ipify.org" in content or "ifconfig.me" in content or
            "curl" in content or "wget" in content), "Should contain dev guide info"

    # Check if agent wrote the implementation plan
    # Agent should write either:
    # - "## 技術分析", "## 實作步驟", "## 技術細節" (if CONFIRMED)
    # - "## 實作計畫", "## 待確認問題" (if NEED_CLARIFICATION)
    has_tech_analysis = "## 技術分析" in content or "## 技術可行性" in content
    has_impl_steps = "## 實作步驟" in content
    has_impl_plan = "## 實作計畫" in content
    has_questions = "## 待確認問題" in content

    if result.status == PhaseStatus.COMPLETED:
        assert has_tech_analysis or has_impl_steps or has_impl_plan, \
            "COMPLETED status should have implementation content (技術分析/實作步驟/實作計畫)"
    else:
        assert has_impl_plan or has_questions, \
            "IN_PROGRESS status should have plan or questions"

    # Check status.json was created with correct content
    status_file = plan_dir / "status.json"
    assert status_file.exists(), "status.json should be created"

    import json
    with open(status_file, 'r', encoding='utf-8') as f:
        status_data = json.load(f)

    assert status_data["phase"] == "plan", "status.json should have phase=plan"
    assert "status_code" in status_data, "status.json should have status_code"
    assert "iteration" in status_data, "status.json should have iteration"
    print(f"\n📊 Status: {status_data['status_code']} (iteration {status_data['iteration']})")

    # Check history was created with correct structure
    history_dir = plan_dir / "history"
    assert history_dir.exists(), "history directory should be created"
    history_files = sorted(history_dir.glob("*.json"))
    assert len(history_files) >= 1, "Should have at least one history file"

    # Verify first history file has correct structure
    with open(history_files[0], 'r', encoding='utf-8') as f:
        history_data = json.load(f)

    assert "iteration" in history_data, "history should have iteration"
    assert "timestamp" in history_data, "history should have timestamp"
    assert "prompt" in history_data, "history should have prompt"
    assert "response" in history_data, "history should have response"
    assert "status_code" in history_data, "history should have status_code"
    print(f"📝 History: {len(history_files)} file(s), status_code={history_data['status_code']}")

    print(f"\n✅ Test passed!")
    print(f"   - Plan file: {plan_file}")
    print(f"   - All required sections present: ✓")
    print(f"   - Status tracking: ✓")
    print(f"   - History tracking: ✓")

    # Cleanup
    shutil.rmtree(test_dir)


if __name__ == "__main__":
    # Allow running directly for manual testing
    pytest.main([__file__, "-v", "-s"])
