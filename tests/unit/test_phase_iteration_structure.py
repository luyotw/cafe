"""Tests for phase iteration directory structure management."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cafe.core.phase import Phase


class ConcretePhase(Phase):
    """Concrete phase implementation for testing."""

    def __init__(self, phase_dir: Path, **kwargs):
        super().__init__(**kwargs)
        self.phase_dir = phase_dir

    def execute(self):
        pass


class TestGetIterationDir:
    """測試 _get_iteration_dir() 方法"""

    def test_returns_correct_iteration_directory_path(self, tmp_path):
        """驗證返回正確的 iteration_XXX/ 路徑格式"""
        phase_dir = tmp_path / "spec"
        phase = ConcretePhase(phase_dir=phase_dir)

        # 測試 iteration 1
        result = phase._get_iteration_dir(1)
        assert result == phase_dir / "iteration_001"

        # 測試 iteration 10
        result = phase._get_iteration_dir(10)
        assert result == phase_dir / "iteration_010"

        # 測試 iteration 100
        result = phase._get_iteration_dir(100)
        assert result == phase_dir / "iteration_100"


class TestAppendIterationIndex:
    """測試 _append_iteration_index() 方法"""

    def test_creates_iterations_jsonl_if_not_exists(self, tmp_path):
        """驗證首次呼叫時會建立 iterations.jsonl 檔案"""
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir()
        phase = ConcretePhase(phase_dir=phase_dir)

        iteration_data = {
            "iteration": 1,
            "timestamp": "2025-12-27T10:00:00Z",
            "status": "CAFE_READY_FOR_REVIEW",
            "has_error": False,
        }

        phase._append_iteration_index(iteration_data)

        iterations_file = phase_dir / "iterations.jsonl"
        assert iterations_file.exists()

    def test_appends_correct_json_line(self, tmp_path):
        """驗證能正確新增一筆 JSON 行到 iterations.jsonl"""
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir()
        phase = ConcretePhase(phase_dir=phase_dir)

        iteration_data = {
            "iteration": 1,
            "timestamp": "2025-12-27T10:00:00Z",
            "status": "CAFE_READY_FOR_REVIEW",
            "has_error": False,
        }

        phase._append_iteration_index(iteration_data)

        iterations_file = phase_dir / "iterations.jsonl"
        content = iterations_file.read_text()
        lines = content.strip().split("\n")

        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed == iteration_data

    def test_appends_multiple_iterations(self, tmp_path):
        """驗證能連續新增多筆記錄"""
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir()
        phase = ConcretePhase(phase_dir=phase_dir)

        # 新增第一筆
        phase._append_iteration_index(
            {
                "iteration": 1,
                "timestamp": "2025-12-27T10:00:00Z",
                "status": "CAFE_NEED_CLARIFICATION",
                "has_error": False,
            }
        )

        # 新增第二筆
        phase._append_iteration_index(
            {
                "iteration": 2,
                "timestamp": "2025-12-27T10:05:00Z",
                "status": "CAFE_READY_FOR_REVIEW",
                "has_error": False,
            }
        )

        iterations_file = phase_dir / "iterations.jsonl"
        lines = iterations_file.read_text().strip().split("\n")

        assert len(lines) == 2
        assert json.loads(lines[0])["iteration"] == 1
        assert json.loads(lines[1])["iteration"] == 2


class TestReadIterationsIndex:
    """測試 _read_iterations_index() 方法"""

    def test_returns_empty_list_when_file_not_exists(self, tmp_path):
        """驗證檔案不存在時返回空列表"""
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir()
        phase = ConcretePhase(phase_dir=phase_dir)

        result = phase._read_iterations_index()

        assert result == []

    def test_reads_all_iteration_records(self, tmp_path):
        """驗證能讀取所有 iterations 記錄"""
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir()
        phase = ConcretePhase(phase_dir=phase_dir)

        # 準備測試資料
        iterations_file = phase_dir / "iterations.jsonl"
        iterations_file.write_text(
            '{"iteration":1,"timestamp":"2025-12-27T10:00:00Z","status":"CAFE_NEED_CLARIFICATION","has_error":false}\n'
            '{"iteration":2,"timestamp":"2025-12-27T10:05:00Z","status":"CAFE_READY_FOR_REVIEW","has_error":false}\n'
            '{"iteration":3,"timestamp":"2025-12-27T10:10:00Z","status":"CAFE_ERROR","has_error":true}\n'
        )

        result = phase._read_iterations_index()

        assert len(result) == 3
        assert result[0]["iteration"] == 1
        assert result[0]["status"] == "CAFE_NEED_CLARIFICATION"
        assert result[1]["iteration"] == 2
        assert result[1]["status"] == "CAFE_READY_FOR_REVIEW"
        assert result[2]["iteration"] == 3
        assert result[2]["has_error"] is True

    def test_handles_empty_file(self, tmp_path):
        """驗證處理空檔案的情況"""
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir()
        phase = ConcretePhase(phase_dir=phase_dir)

        # 建立空檔案
        iterations_file = phase_dir / "iterations.jsonl"
        iterations_file.write_text("")

        result = phase._read_iterations_index()

        assert result == []


class TestSaveUserInput:
    """測試重構後的 _save_user_input() 方法"""

    def test_creates_iteration_directory_and_context_json(self, tmp_path):
        """驗證會建立 iteration_XXX/context.json"""
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir()
        phase = ConcretePhase(phase_dir=phase_dir)
        phase.iteration = 1

        phase._save_user_input("Test user input")

        # 驗證目錄和檔案存在
        iteration_dir = phase_dir / "iteration_001"
        assert iteration_dir.exists()
        assert iteration_dir.is_dir()

        context_file = iteration_dir / "context.json"
        assert context_file.exists()

    def test_context_json_contains_required_fields(self, tmp_path):
        """驗證 context.json 包含所有必要欄位"""
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir()
        phase = ConcretePhase(phase_dir=phase_dir)
        phase.iteration = 1

        phase._save_user_input("Test user input")

        context_file = phase_dir / "iteration_001" / "context.json"
        data = json.loads(context_file.read_text())

        # 驗證必要欄位
        assert "iteration" in data
        assert data["iteration"] == 1
        assert "timestamp" in data
        assert "user_input" in data
        assert data["user_input"] == "Test user input"

    def test_saves_phase_specific_data(self, tmp_path):
        """驗證能儲存 phase_specific_data"""
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir()
        phase = ConcretePhase(phase_dir=phase_dir)
        phase.iteration = 2

        phase_specific_data = {
            "template": "default",
            "agent_name": "PM",
        }

        phase._save_user_input("Test input", phase_specific_data=phase_specific_data)

        context_file = phase_dir / "iteration_002" / "context.json"
        data = json.loads(context_file.read_text())

        assert data["template"] == "default"
        assert data["agent_name"] == "PM"
