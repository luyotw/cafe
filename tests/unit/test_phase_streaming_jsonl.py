"""Tests for Phase streaming JSONL save functionality."""

import json
import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from cafe.core.phase import Phase
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import PhaseResult, PhaseStatus


class ConcretePhaseWithHistory(Phase):
    """具備 history_dir 的測試用 Phase 實作"""

    def __init__(self, issue_dir: Path, phase_name: str = "test_phase"):
        self.issue_dir = issue_dir
        self.phase_dir = issue_dir / phase_name
        self.history_dir = self.phase_dir / "history"
        self.iteration = 1

    def execute(self) -> PhaseResult:
        """測試用簡單實作"""
        return PhaseResult(status=PhaseStatus.COMPLETED, message="Test phase completed")


class TestSaveStreamingJsonl:
    """測試 streaming JSONL 儲存功能"""

    def test_save_streaming_jsonl_with_data(self, tmp_path: Path) -> None:
        """測試當 streaming_log 有資料時，應建立對應的 response_XXX.jsonl 檔案"""
        # 建立測試 phase
        phase = ConcretePhaseWithHistory(tmp_path)
        phase.iteration = 1

        # 模擬 streaming_log 資料
        streaming_log = [
            "First fragment",
            "Second fragment",
            "Third fragment"
        ]

        # 呼叫儲存方法（尚未實作，預期會失敗）
        phase._save_streaming_jsonl(streaming_log)

        # 驗證檔案存在
        jsonl_file = phase.history_dir / "response_001.jsonl"
        assert jsonl_file.exists(), "JSONL 檔案應該被建立"

        # 驗證檔案內容
        with open(jsonl_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        assert len(lines) == 3, "應該有 3 行 JSONL 資料"

    def test_save_streaming_jsonl_empty_log(self, tmp_path: Path) -> None:
        """測試當 streaming_log 為空時，不應建立 JSONL 檔案"""
        # 建立測試 phase
        phase = ConcretePhaseWithHistory(tmp_path)
        phase.iteration = 1

        # 空的 streaming_log
        streaming_log = []

        # 呼叫儲存方法
        phase._save_streaming_jsonl(streaming_log)

        # 驗證檔案不存在
        jsonl_file = phase.history_dir / "response_001.jsonl"
        assert not jsonl_file.exists(), "空的 streaming_log 不應建立 JSONL 檔案"

    def test_jsonl_format_correctness(self, tmp_path: Path) -> None:
        """測試 JSONL 檔案格式正確性（每行一個有效 JSON，包含 index、timestamp、content 欄位）"""
        # 建立測試 phase
        phase = ConcretePhaseWithHistory(tmp_path)
        phase.iteration = 2

        # 模擬 streaming_log 資料
        streaming_log = [
            "Fragment A",
            "Fragment B"
        ]

        # 呼叫儲存方法
        phase._save_streaming_jsonl(streaming_log)

        # 讀取並驗證 JSONL 格式
        jsonl_file = phase.history_dir / "response_002.jsonl"
        with open(jsonl_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        for i, line in enumerate(lines):
            # 驗證每行都是有效的 JSON
            data = json.loads(line.strip())

            # 驗證必要欄位存在
            assert "index" in data, f"第 {i} 行應包含 index 欄位"
            assert "timestamp" in data, f"第 {i} 行應包含 timestamp 欄位"
            assert "content" in data, f"第 {i} 行應包含 content 欄位"

            # 驗證 index 正確
            assert data["index"] == i, f"第 {i} 行的 index 應為 {i}"

            # 驗證 timestamp 格式（ISO 8601）
            datetime.fromisoformat(data["timestamp"])  # 應該可以成功解析

    def test_jsonl_content_matches_streaming_log(self, tmp_path: Path) -> None:
        """測試 JSONL 檔案內容與 streaming_log 一致"""
        # 建立測試 phase
        phase = ConcretePhaseWithHistory(tmp_path)
        phase.iteration = 3

        # 模擬 streaming_log 資料
        streaming_log = [
            "CAFE_READY_FOR_REVIEW\n",
            "Starting implementation...",
            "Task completed."
        ]

        # 呼叫儲存方法
        phase._save_streaming_jsonl(streaming_log)

        # 讀取並驗證內容
        jsonl_file = phase.history_dir / "response_003.jsonl"
        with open(jsonl_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        for i, line in enumerate(lines):
            data = json.loads(line.strip())
            assert data["content"] == streaming_log[i], \
                f"第 {i} 行的 content 應與 streaming_log[{i}] 一致"

    def test_jsonl_file_naming(self, tmp_path: Path) -> None:
        """測試檔案命名格式：response_{iteration:03d}.jsonl"""
        phase = ConcretePhaseWithHistory(tmp_path)

        # 測試不同的 iteration 編號
        test_cases = [
            (1, "response_001.jsonl"),
            (5, "response_005.jsonl"),
            (42, "response_042.jsonl"),
            (100, "response_100.jsonl"),
        ]

        for iteration, expected_filename in test_cases:
            phase.iteration = iteration
            streaming_log = ["test content"]

            phase._save_streaming_jsonl(streaming_log)

            jsonl_file = phase.history_dir / expected_filename
            assert jsonl_file.exists(), \
                f"iteration {iteration} 應建立檔案 {expected_filename}"

    def test_history_dir_created_if_not_exists(self, tmp_path: Path) -> None:
        """測試當 history 目錄不存在時會自動建立"""
        # 建立測試 phase（故意不建立 history_dir）
        phase = ConcretePhaseWithHistory(tmp_path)
        phase.iteration = 1

        # 確保 history_dir 不存在
        assert not phase.history_dir.exists(), "history_dir 初始應不存在"

        # 呼叫儲存方法
        streaming_log = ["test"]
        phase._save_streaming_jsonl(streaming_log)

        # 驗證目錄被建立
        assert phase.history_dir.exists(), "history_dir 應該被自動建立"
        assert phase.history_dir.is_dir(), "history_dir 應該是目錄"
