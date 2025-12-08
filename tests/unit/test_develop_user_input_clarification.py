"""測試 develop phase 在 NEED_CLARIFICATION 狀態下正確處理 user_input"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cafe.core.types import WorkflowMode
from cafe.phases.develop_phase import DevelopPhase


class TestDevelopUserInputForClarification:
    """測試 develop phase 在回應 NEED_CLARIFICATION 時保存 user_input"""

    @pytest.fixture
    def setup_develop_phase(self, tmp_path):
        """建立 develop phase 測試環境"""
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_file = issue_dir / "spec" / "spec_001.md"
        plan_file = issue_dir / "plan" / "plan_001.md"
        history_dir = issue_dir / "develop" / "history"
        
        # 建立目錄
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        history_dir.mkdir(parents=True, exist_ok=True)
        
        # 建立 spec 和 plan 檔案
        spec_file.write_text("# Requirements")
        plan_file.write_text("# Plan")
        
        # Mock objects
        agent_manager = MagicMock()
        permission_handler = MagicMock()
        git_ops = MagicMock()
        git_ops.get_current_branch.return_value = "test-issue"
        git_ops.branch_exists.return_value = True
        
        return {
            "issue_dir": issue_dir,
            "spec_file": str(spec_file),
            "plan_file": str(plan_file),
            "history_dir": history_dir,
            "agent_manager": agent_manager,
            "permission_handler": permission_handler,
            "git_ops": git_ops,
        }

    def test_user_input_saved_when_responding_to_clarification(self, setup_develop_phase):
        """測試當回應 NEED_CLARIFICATION 時，user_input 應該被保存到 iteration history"""
        # 建立上一輪的 iteration 檔案，狀態為 NEED_CLARIFICATION
        prev_iteration_file = setup_develop_phase["history_dir"] / "iteration_001.json"
        prev_iteration_data = {
            "iteration": 1,
            "timestamp": "2025-12-08T00:00:00",
            "user_input": "",
            "response": "CAFE_NEED_CLARIFICATION\n\n我需要澄清一些問題...",
            "status_code": "CAFE_NEED_CLARIFICATION",
        }
        with open(prev_iteration_file, "w") as f:
            json.dump(prev_iteration_data, f)
        
        # 建立 develop phase，提供 user_input
        user_response = "選項 A"
        phase = DevelopPhase(
            agent_manager=setup_develop_phase["agent_manager"],
            permission_handler=setup_develop_phase["permission_handler"],
            git_ops=setup_develop_phase["git_ops"],
            spec_file=setup_develop_phase["spec_file"],
            plan_file=setup_develop_phase["plan_file"],
            workflow_mode=WorkflowMode.LOCAL,
            issue_id=None,
            issue_name="test-issue",
            dev_agent="John",
            interactive=True,
            user_input=user_response,
        )
        
        # 設定 iteration 為 2，這樣會載入 iteration_001.json
        phase.iteration = 2
        # 確保 history_dir 指向測試環境的目錄
        phase.history_dir = setup_develop_phase["history_dir"]
        
        # 呼叫 _prepare_user_input_for_iteration
        result = phase._prepare_user_input_for_iteration()
        
        # 確認返回的是 user_input
        assert result == user_response
        
        # 確認 user_input 被清空（避免下次重複使用）
        assert phase.user_input == ""

    def test_empty_user_input_when_no_clarification_pending(self, setup_develop_phase):
        """測試當沒有 NEED_CLARIFICATION 狀態時，返回空字串"""
        # 建立上一輪的 iteration 檔案，狀態為 NEED_PERMISSION（不是 NEED_CLARIFICATION）
        prev_iteration_file = setup_develop_phase["history_dir"] / "iteration_001.json"
        prev_iteration_data = {
            "iteration": 1,
            "timestamp": "2025-12-08T00:00:00",
            "user_input": "",
            "response": "CAFE_NEED_PERMISSION",
            "status_code": "CAFE_NEED_PERMISSION",
            "permission_denials": [],
        }
        with open(prev_iteration_file, "w") as f:
            json.dump(prev_iteration_data, f)
        
        # 建立 develop phase，提供 user_input，但因為不是 NEED_CLARIFICATION 所以不應該使用
        phase = DevelopPhase(
            agent_manager=setup_develop_phase["agent_manager"],
            permission_handler=setup_develop_phase["permission_handler"],
            git_ops=setup_develop_phase["git_ops"],
            spec_file=setup_develop_phase["spec_file"],
            plan_file=setup_develop_phase["plan_file"],
            workflow_mode=WorkflowMode.LOCAL,
            issue_id=None,
            issue_name="test-issue",
            dev_agent="John",
            interactive=True,
            user_input="這不應該被使用",
        )
        
        # 設定 iteration 為 2，這樣會載入 iteration_001.json
        phase.iteration = 2
        # 確保 history_dir 指向測試環境的目錄
        phase.history_dir = setup_develop_phase["history_dir"]
        
        # 呼叫 _prepare_user_input_for_iteration
        result = phase._prepare_user_input_for_iteration()
        
        # 確認返回空字串（因為不是 NEED_CLARIFICATION 狀態）
        assert result == ""
