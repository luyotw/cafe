"""測試 Phase base class 的 user_input 處理功能。"""

import json
from pathlib import Path

import pytest

from cafe.core.phase import Phase
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import PhaseResult, PhaseStatus


class ConcretePhase(Phase):
    """用於測試的具體 Phase 實作。"""

    def __init__(self, history_dir: Path):
        self.history_dir = history_dir
        self.iteration = 1

    def execute(self) -> PhaseResult:
        return PhaseResult(status=PhaseStatus.COMPLETED, message="test")


class TestSaveUserInput:
    """測試 _save_user_input 方法。"""

    def test_save_user_input_creates_file(self, tmp_path: Path) -> None:
        """測試 _save_user_input 建立 JSON 檔案"""
        history_dir = tmp_path / "history"
        phase = ConcretePhase(history_dir)

        user_input = "使用者的修改意見"
        phase._save_user_input(user_input)

        # 檢查檔案被建立
        iteration_file = history_dir / "iteration_001.json"
        assert iteration_file.exists()

        # 檢查內容
        with open(iteration_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["iteration"] == 1
        assert data["user_input"] == user_input
        assert "timestamp" in data

    def test_save_user_input_with_phase_specific_data(self, tmp_path: Path) -> None:
        """測試 _save_user_input 可以加入 phase 特定資料"""
        history_dir = tmp_path / "history"
        phase = ConcretePhase(history_dir)

        user_input = "使用者輸入"
        phase_data = {"custom_field": "custom_value"}
        phase._save_user_input(user_input, phase_specific_data=phase_data)

        iteration_file = history_dir / "iteration_001.json"
        with open(iteration_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["user_input"] == user_input
        assert data["custom_field"] == "custom_value"

    def test_save_user_input_without_history_dir_raises_error(self) -> None:
        """測試沒有 history_dir 屬性時會報錯"""

        class PhaseWithoutHistoryDir(Phase):
            def execute(self) -> PhaseResult:
                return PhaseResult(status=PhaseStatus.COMPLETED, message="test")

        phase = PhaseWithoutHistoryDir()
        with pytest.raises(AttributeError, match="history_dir"):
            phase._save_user_input("test")

    def test_save_user_input_without_iteration_raises_error(self, tmp_path: Path) -> None:
        """測試沒有 iteration 屬性時會報錯"""

        class PhaseWithoutIteration(Phase):
            def __init__(self, history_dir: Path):
                self.history_dir = history_dir

            def execute(self) -> PhaseResult:
                return PhaseResult(status=PhaseStatus.COMPLETED, message="test")

        phase = PhaseWithoutIteration(tmp_path / "history")
        with pytest.raises(AttributeError, match="iteration"):
            phase._save_user_input("test")


class TestUpdateIterationHistory:
    """測試 _update_iteration_history 方法。"""

    def test_update_existing_file(self, tmp_path: Path) -> None:
        """測試更新現有的 history 檔案"""
        history_dir = tmp_path / "history"
        phase = ConcretePhase(history_dir)

        # 先儲存 user_input
        user_input = "使用者輸入"
        phase._save_user_input(user_input)

        # 然後更新 agent response
        phase._update_iteration_history(
            phase_specific_data={"response": "Agent 回應"},
            prompt="Test prompt",
            agent_cli="copilot",
            status_code=PhaseStatusCode.READY_FOR_REVIEW,
        )

        # 檢查檔案內容
        iteration_file = history_dir / "iteration_001.json"
        with open(iteration_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 應該同時包含 user_input 和 response
        assert data["user_input"] == user_input
        assert data["response"] == "Agent 回應"
        assert data["prompt"] == "Test prompt"
        assert data["cli"] == "copilot"
        assert data["status_code"] == "CAFE_READY_FOR_REVIEW"

    def test_update_nonexistent_file_creates_new(self, tmp_path: Path) -> None:
        """測試更新不存在的檔案時會建立新檔案"""
        history_dir = tmp_path / "history"
        phase = ConcretePhase(history_dir)

        # 直接更新（沒有先呼叫 _save_user_input）
        phase._update_iteration_history(
            phase_specific_data={"response": "Agent 回應"},
            prompt="Test prompt",
        )

        # 檢查檔案被建立
        iteration_file = history_dir / "iteration_001.json"
        assert iteration_file.exists()

        with open(iteration_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["response"] == "Agent 回應"
        assert data["prompt"] == "Test prompt"


class TestTwoPhaseWorkflow:
    """測試兩階段儲存流程（先 _save_user_input，後 _update_iteration_history）。"""

    def test_complete_iteration_workflow(self, tmp_path: Path) -> None:
        """測試完整的迭代流程"""
        history_dir = tmp_path / "history"
        phase = ConcretePhase(history_dir)

        # 第 1 輪：先儲存 user_input
        phase.iteration = 1
        phase._save_user_input("第 1 輪使用者輸入")

        # 模擬 agent 執行後更新
        phase._update_iteration_history(
            phase_specific_data={"response": "第 1 輪 Agent 回應"},
            prompt="第 1 輪 prompt",
            status_code=PhaseStatusCode.NEED_CLARIFICATION,
        )

        # 第 2 輪：使用者提供修改意見
        phase.iteration = 2
        phase._save_user_input("第 2 輪使用者修改意見")

        # Agent 再次執行
        phase._update_iteration_history(
            phase_specific_data={"response": "第 2 輪 Agent 回應"},
            prompt="第 2 輪 prompt",
            status_code=PhaseStatusCode.READY_FOR_REVIEW,
        )

        # 檢查兩輪的 history 檔案
        iter1_file = history_dir / "iteration_001.json"
        iter2_file = history_dir / "iteration_002.json"

        assert iter1_file.exists()
        assert iter2_file.exists()

        with open(iter1_file, "r", encoding="utf-8") as f:
            data1 = json.load(f)
        with open(iter2_file, "r", encoding="utf-8") as f:
            data2 = json.load(f)

        # 第 1 輪
        assert data1["iteration"] == 1
        assert data1["user_input"] == "第 1 輪使用者輸入"
        assert data1["response"] == "第 1 輪 Agent 回應"
        assert data1["status_code"] == "CAFE_NEED_CLARIFICATION"

        # 第 2 輪
        assert data2["iteration"] == 2
        assert data2["user_input"] == "第 2 輪使用者修改意見"
        assert data2["response"] == "第 2 輪 Agent 回應"
        assert data2["status_code"] == "CAFE_READY_FOR_REVIEW"

    def test_user_input_preserved_on_agent_failure(self, tmp_path: Path) -> None:
        """測試 agent 執行失敗時，user_input 仍然被保存"""
        history_dir = tmp_path / "history"
        phase = ConcretePhase(history_dir)

        # 儲存 user_input
        phase._save_user_input("使用者輸入")

        # 檢查即使沒有呼叫 _update_iteration_history，user_input 也被保存了
        iteration_file = history_dir / "iteration_001.json"
        assert iteration_file.exists()

        with open(iteration_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["user_input"] == "使用者輸入"
        # 不應該有 response 或 status_code（因為沒有呼叫 _update_iteration_history）
        assert "response" not in data
        assert "status_code" not in data
