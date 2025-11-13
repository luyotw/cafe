"""測試 Phase 基礎類別的共用功能."""
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from cafe.core.phase import Phase
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import PhaseResult, PhaseStatus


class ConcretePhase(Phase):
    """用於測試的具體 Phase 實作."""

    def __init__(self, history_dir: Path) -> None:
        """初始化測試用的 Phase.

        Args:
            history_dir: History 儲存目錄
        """
        self.history_dir = history_dir
        self.iteration = 1

    def execute(self) -> PhaseResult:
        """空實作."""
        return PhaseResult(
            status=PhaseStatus.SUCCESS,
            message="Test phase completed"
        )


class TestSaveIterationHistory:
    """測試 _save_iteration_history 共用方法."""

    def test_save_basic_history(self, tmp_path: Path) -> None:
        """測試儲存基本的 history 資料."""
        phase = ConcretePhase(history_dir=tmp_path / "history")

        phase._save_iteration_history(
            phase_specific_data={
                "user_input": "Test input",
                "response": "Test response"
            }
        )

        # 驗證檔案被建立
        history_file = tmp_path / "history" / "iteration_001.json"
        assert history_file.exists()

        # 驗證內容
        with open(history_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        assert data["iteration"] == 1
        assert "timestamp" in data
        assert data["user_input"] == "Test input"
        assert data["response"] == "Test response"

    def test_save_history_with_agent_metadata(self, tmp_path: Path) -> None:
        """測試儲存包含 agent metadata 的 history."""
        phase = ConcretePhase(history_dir=tmp_path / "history")

        phase._save_iteration_history(
            phase_specific_data={
                "user_input": "Test input",
                "response": "Test response"
            },
            prompt="Test prompt",
            agent_cli="copilot",
            agent_session_id="session-123",
            allowed_tools=["write", "read"],
            denied_tools=["bash"]
        )

        history_file = tmp_path / "history" / "iteration_001.json"
        with open(history_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        assert data["prompt"] == "Test prompt"
        assert data["cli"] == "copilot"
        assert data["session_id"] == "session-123"
        assert data["allowed_tools"] == ["write", "read"]
        assert data["denied_tools"] == ["bash"]

    def test_save_history_with_status_code(self, tmp_path: Path) -> None:
        """測試儲存包含 status_code 的 history."""
        phase = ConcretePhase(history_dir=tmp_path / "history")

        phase._save_iteration_history(
            phase_specific_data={
                "user_input": "Test input"
            },
            status_code=PhaseStatusCode.CONFIRMED
        )

        history_file = tmp_path / "history" / "iteration_001.json"
        with open(history_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        assert data["status_code"] == "CAFE_CONFIRMED"

    def test_save_history_with_optional_fields(self, tmp_path: Path) -> None:
        """測試儲存時 optional 欄位可以是 None."""
        phase = ConcretePhase(history_dir=tmp_path / "history")

        phase._save_iteration_history(
            phase_specific_data={"user_input": "Test"},
            prompt=None,
            agent_cli=None,
            agent_session_id=None,
            allowed_tools=None,
            denied_tools=None,
            status_code=None
        )

        history_file = tmp_path / "history" / "iteration_001.json"
        with open(history_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        assert data["prompt"] is None
        assert data["cli"] is None
        assert data["session_id"] is None
        assert data["allowed_tools"] is None
        assert data["denied_tools"] is None
        assert data["status_code"] is None

    def test_save_history_creates_directory(self, tmp_path: Path) -> None:
        """測試 history 目錄不存在時會自動建立."""
        history_dir = tmp_path / "deep" / "nested" / "history"
        assert not history_dir.exists()

        phase = ConcretePhase(history_dir=history_dir)
        phase._save_iteration_history(
            phase_specific_data={"test": "data"}
        )

        assert history_dir.exists()
        assert (history_dir / "iteration_001.json").exists()

    def test_save_history_incremental_iterations(self, tmp_path: Path) -> None:
        """測試多次儲存會產生不同的 iteration 檔案."""
        phase = ConcretePhase(history_dir=tmp_path / "history")

        # Iteration 1
        phase.iteration = 1
        phase._save_iteration_history(
            phase_specific_data={"input": "first"}
        )

        # Iteration 2
        phase.iteration = 2
        phase._save_iteration_history(
            phase_specific_data={"input": "second"}
        )

        # 驗證兩個檔案都存在
        assert (tmp_path / "history" / "iteration_001.json").exists()
        assert (tmp_path / "history" / "iteration_002.json").exists()

        # 驗證內容不同
        with open(tmp_path / "history" / "iteration_001.json", 'r') as f:
            data1 = json.load(f)
        with open(tmp_path / "history" / "iteration_002.json", 'r') as f:
            data2 = json.load(f)

        assert data1["iteration"] == 1
        assert data2["iteration"] == 2
        assert data1["input"] == "first"
        assert data2["input"] == "second"


class TestLoadIterationCounter:
    """測試 _load_iteration_counter 共用方法."""

    def test_load_counter_with_no_history(self, tmp_path: Path) -> None:
        """測試當沒有 history 時返回 0."""
        phase = ConcretePhase(history_dir=tmp_path / "history")

        counter = phase._load_iteration_counter()

        assert counter == 0

    def test_load_counter_with_complete_iterations(self, tmp_path: Path) -> None:
        """測試載入完整 iteration 的 counter（有 response）."""
        history_dir = tmp_path / "history"
        history_dir.mkdir(parents=True)

        # 創建兩個完整的 iteration（都有 response）
        iteration1 = history_dir / "iteration_001.json"
        iteration1.write_text(json.dumps({
            "iteration": 1,
            "user_input": "Input 1",
            "response": "Response 1"
        }))

        iteration2 = history_dir / "iteration_002.json"
        iteration2.write_text(json.dumps({
            "iteration": 2,
            "user_input": "Input 2",
            "response": "Response 2"
        }))

        phase = ConcretePhase(history_dir=history_dir)
        counter = phase._load_iteration_counter()

        # 應該返回最後一個完整 iteration 的數字
        assert counter == 2

    def test_load_counter_with_incomplete_iteration(self, tmp_path: Path) -> None:
        """測試當最後一個 iteration 沒有 response（被中斷），應該重用該 iteration 編號."""
        history_dir = tmp_path / "history"
        history_dir.mkdir(parents=True)

        # iteration 1: 完整（有 response）
        iteration1 = history_dir / "iteration_001.json"
        iteration1.write_text(json.dumps({
            "iteration": 1,
            "user_input": "Input 1",
            "response": "Response 1"
        }))

        # iteration 2: 不完整（沒有 response - 被中斷）
        iteration2 = history_dir / "iteration_002.json"
        iteration2.write_text(json.dumps({
            "iteration": 2,
            "user_input": "Input 2"
            # 注意：沒有 "response" 欄位
        }))

        phase = ConcretePhase(history_dir=history_dir)
        counter = phase._load_iteration_counter()

        # 應該返回 1（前一個完整的 iteration），這樣下次執行時會重用 iteration 2
        assert counter == 1
