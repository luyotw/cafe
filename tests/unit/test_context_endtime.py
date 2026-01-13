"""Tests for context.json end_time field saving."""

import json
from pathlib import Path
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from cafe.core.phase import Phase
from cafe.core.status_codes import PhaseStatusCode


class ConcretePhase(Phase):
    """Concrete phase implementation for testing."""

    def __init__(self, phase_dir: Path, **kwargs):
        super().__init__(**kwargs)
        self.phase_dir = phase_dir
        self.iteration = 1

    def execute(self):
        pass


class TestContextEndTime:
    """測試 context.json 包含 end_time 欄位"""

    def test_context_json_contains_end_time_field(self, tmp_path):
        """驗證 context.json 在 iteration 結束時包含 end_time 欄位"""
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir()
        phase = ConcretePhase(phase_dir=phase_dir)
        phase.iteration = 1

        # 儲存 user input（模擬 iteration 開始）
        phase._save_user_input("Test user input")

        # 模擬 agent 執行完成，更新 iteration history
        phase._update_iteration_history(
            phase_specific_data={"response": "Test response"},
            status_code=None,
        )

        # 驗證 context.json 包含 end_time
        context_file = phase._get_iteration_dir(1) / "context.json"
        assert context_file.exists()

        with open(context_file, "r", encoding="utf-8") as f:
            context_data = json.load(f)

        # 驗證 end_time 存在且格式正確
        assert "end_time" in context_data
        assert context_data["end_time"] is not None
        # 驗證是 ISO 格式時間字串
        from datetime import datetime
        datetime.fromisoformat(context_data["end_time"].replace('Z', '+00:00'))


class TestContextJsonEndTime:
    """測試 context.json 的 end_time 欄位"""

    def test_context_json_contains_end_time_after_update(self, tmp_path):
        """驗證 _update_iteration_history() 儲存 end_time 到 context.json"""
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir()

        phase = ConcretePhase(phase_dir=phase_dir)
        phase.iteration = 1

        # 模擬 agent 回應
        phase_specific_data = {
            "response": "Test response",
            "streaming_log": [],
        }

        # 呼叫 _update_iteration_history
        phase._update_iteration_history(
            phase_specific_data=phase_specific_data,
            status_code=MagicMock(value="CAFE_CONFIRMED"),
        )

        # 驗證 context.json 包含 end_time 欄位
        iteration_dir = phase._get_iteration_dir(1)
        context_file = iteration_dir / "context.json"
        assert context_file.exists()

        with open(context_file, "r", encoding="utf-8") as f:
            context_data = json.load(f)

        # 驗證 end_time 欄位存在且格式正確
        assert "end_time" in context_data
        assert context_data["end_time"] is not None
        # 驗證是 ISO 格式的時間字串
        from datetime import datetime
        datetime.fromisoformat(context_data["end_time"].replace("Z", "+00:00"))
