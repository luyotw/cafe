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


class TestLoadIterationCounter:
    """Tests for _load_iteration_counter() fallback behavior."""

    def test_uses_completed_context_with_end_time_when_status_missing(self, tmp_path):
        phase_dir = tmp_path / "spec"
        iteration_dir = phase_dir / "iteration_001"
        iteration_dir.mkdir(parents=True)
        phase = ConcretePhase(phase_dir=phase_dir)

        (iteration_dir / "context.json").write_text(
            json.dumps(
                {
                    "iteration": 1,
                    "response": "CAFE_CONFIRMED",
                    "end_time": "2026-04-29T10:00:00+08:00",
                }
            ),
            encoding="utf-8",
        )

        assert phase._load_iteration_counter() == 1

    def test_ignores_incomplete_context_without_end_time(self, tmp_path):
        phase_dir = tmp_path / "spec"
        iteration_dir = phase_dir / "iteration_001"
        iteration_dir.mkdir(parents=True)
        phase = ConcretePhase(phase_dir=phase_dir)

        (iteration_dir / "context.json").write_text(
            json.dumps(
                {
                    "iteration": 1,
                    "response": "draft only",
                }
            ),
            encoding="utf-8",
        )

        assert phase._load_iteration_counter() == 0

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
        # user_input is no longer duplicated into context.json;
        # it lives in user_input.md instead
        assert "user_input" not in data

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

    def test_creates_user_input_md_file(self, tmp_path):
        """驗證會建立 user_input.md 檔案"""
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir()
        phase = ConcretePhase(phase_dir=phase_dir)
        phase.iteration = 1

        phase._save_user_input("Test user input content")

        # 驗證 user_input.md 存在
        user_input_file = phase_dir / "iteration_001" / "user_input.md"
        assert user_input_file.exists()

    def test_user_input_md_contains_correct_content(self, tmp_path):
        """驗證 user_input.md 包含正確內容"""
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir()
        phase = ConcretePhase(phase_dir=phase_dir)
        phase.iteration = 1

        user_input_content = "# Test Requirements\n\nThis is a test."
        phase._save_user_input(user_input_content)

        user_input_file = phase_dir / "iteration_001" / "user_input.md"
        content = user_input_file.read_text(encoding="utf-8")
        assert content == user_input_content

    def test_user_input_no_longer_in_context_json(self, tmp_path):
        """驗證 context.json 不再包含 user_input（改用 user_input.md）"""
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir()
        phase = ConcretePhase(phase_dir=phase_dir)
        phase.iteration = 1

        user_input_content = "Test input for backward compatibility"
        phase._save_user_input(user_input_content)

        # user_input is now stored exclusively in user_input.md
        context_file = phase_dir / "iteration_001" / "context.json"
        data = json.loads(context_file.read_text())
        assert "user_input" not in data

        user_input_file = phase_dir / "iteration_001" / "user_input.md"
        assert user_input_file.exists()
        assert user_input_file.read_text() == user_input_content


class TestLoadUserInput:
    """測試 _load_user_input() 方法"""

    def test_loads_from_user_input_md_first(self, tmp_path):
        """驗證優先從 user_input.md 載入"""
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir()
        phase = ConcretePhase(phase_dir=phase_dir)

        # 建立 iteration_001 目錄和 user_input.md
        iteration_dir = phase_dir / "iteration_001"
        iteration_dir.mkdir()
        user_input_file = iteration_dir / "user_input.md"
        user_input_file.write_text("Content from user_input.md", encoding="utf-8")

        # 同時建立 context.json（但內容不同，以驗證優先讀取 .md）
        context_file = iteration_dir / "context.json"
        context_data = {
            "iteration": 1,
            "timestamp": "2025-12-27T10:00:00Z",
            "user_input": "Content from context.json",
        }
        context_file.write_text(json.dumps(context_data), encoding="utf-8")

        # 載入並驗證
        result = phase._load_user_input(1)
        assert result == "Content from user_input.md"

    def test_returns_empty_string_when_md_not_found(self, tmp_path):
        """驗證當 user_input.md 不存在時返回空字串"""
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir()
        phase = ConcretePhase(phase_dir=phase_dir)

        # 只建立 context.json，不建立 user_input.md
        iteration_dir = phase_dir / "iteration_001"
        iteration_dir.mkdir()
        context_file = iteration_dir / "context.json"
        context_data = {
            "iteration": 1,
            "timestamp": "2025-12-27T10:00:00Z",
            "user_input": "Content from context.json only",
        }
        context_file.write_text(json.dumps(context_data), encoding="utf-8")

        # _load_user_input no longer falls back to context.json
        result = phase._load_user_input(1)
        assert result == ""

    def test_returns_empty_string_when_no_iteration_dir(self, tmp_path):
        """驗證當 iteration 目錄不存在時返回空字串"""
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir()
        phase = ConcretePhase(phase_dir=phase_dir)

        # iteration_001 目錄不存在
        result = phase._load_user_input(1)
        assert result == ""

    def test_returns_empty_string_when_user_input_key_missing_in_context(self, tmp_path):
        """驗證當 context.json 存在但缺少 user_input 欄位時返回空字串"""
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir()
        phase = ConcretePhase(phase_dir=phase_dir)

        # 建立 context.json 但不包含 user_input 欄位
        iteration_dir = phase_dir / "iteration_001"
        iteration_dir.mkdir()
        context_file = iteration_dir / "context.json"
        context_data = {
            "iteration": 1,
            "timestamp": "2025-12-27T10:00:00Z",
        }
        context_file.write_text(json.dumps(context_data), encoding="utf-8")

        # 載入並驗證
        result = phase._load_user_input(1)
        assert result == ""


class TestIterationIndexIncludesEndTime:
    """Test that iterations.jsonl includes end_time field"""

    def test_append_iteration_index_includes_end_time(self, tmp_path):
        """Verify _append_iteration_index includes end_time in the iteration record"""
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir()
        phase = ConcretePhase(phase_dir=phase_dir)

        # Prepare iteration data with end_time
        iteration_data = {
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "status": "CAFE_CONFIRMED",
            "has_error": False,
            "end_time": "2026-01-27T10:05:00+08:00"
        }

        # Append the iteration index
        phase._append_iteration_index(iteration_data)

        # Verify the iterations.jsonl file contains end_time
        iterations_file = phase_dir / "iterations.jsonl"
        assert iterations_file.exists()

        with open(iterations_file, "r", encoding="utf-8") as f:
            line = f.readline()
            data = json.loads(line)

        assert data["iteration"] == 1
        assert data["timestamp"] == "2026-01-27T10:00:00+08:00"
        assert data["end_time"] == "2026-01-27T10:05:00+08:00"
        assert data["status"] == "CAFE_CONFIRMED"
        assert data["has_error"] is False
