"""Tests for phase cache system."""

import json
import pytest
from pathlib import Path
from datetime import datetime
from cafe.core.phase_cache import PhaseCache, CacheEntry
from cafe.core.status_codes import PhaseStatusCode


class TestCacheEntry:
    """Test CacheEntry data structure."""

    def test_cache_entry_creation(self) -> None:
        """測試建立 CacheEntry"""
        entry = CacheEntry(
            phase_name="requirements",
            status_code=PhaseStatusCode.CONFIRMED,
            response="Requirements are clear",
            content_hash="abc123",
            timestamp=datetime.now()
        )

        assert entry.phase_name == "requirements"
        assert entry.status_code == PhaseStatusCode.CONFIRMED
        assert entry.response == "Requirements are clear"
        assert entry.content_hash == "abc123"
        assert isinstance(entry.timestamp, datetime)

    def test_cache_entry_to_dict(self) -> None:
        """測試 CacheEntry 轉換為 dict"""
        timestamp = datetime.now()
        entry = CacheEntry(
            phase_name="analysis",
            status_code=PhaseStatusCode.CONFIRMED,
            response="Analysis complete",
            content_hash="def456",
            timestamp=timestamp
        )

        data = entry.to_dict()

        assert data["phase_name"] == "analysis"
        assert data["status_code"] == "confirmed"  # Serialized as plain outcome token (no CAFE_ prefix)
        assert data["response"] == "Analysis complete"
        assert data["content_hash"] == "def456"
        assert data["timestamp"] == timestamp.isoformat()

    def test_cache_entry_from_dict(self) -> None:
        """測試從 dict 建立 CacheEntry"""
        timestamp = datetime.now()
        data = {
            "phase_name": "review",
            "status_code": "confirmed",  # Plain outcome token in persisted cache entries
            "response": "Code looks good",
            "content_hash": "ghi789",
            "timestamp": timestamp.isoformat()
        }

        entry = CacheEntry.from_dict(data)

        assert entry.phase_name == "review"
        assert entry.status_code == PhaseStatusCode.CONFIRMED
        assert entry.response == "Code looks good"
        assert entry.content_hash == "ghi789"
        assert entry.timestamp.isoformat() == timestamp.isoformat()


class TestPhaseCache:
    """Test PhaseCache class."""

    def test_init_creates_cache_directory(self, tmp_path: Path) -> None:
        """測試初始化時會建立 cache 目錄"""
        cache_dir = tmp_path / "cache"
        session_id = "test_session"

        cache = PhaseCache(session_id, str(cache_dir))

        expected_dir = cache_dir / f"session_{session_id}"
        assert expected_dir.exists()
        assert expected_dir.is_dir()

    def test_get_cache_file_returns_correct_path(self, tmp_path: Path) -> None:
        """測試 get_cache_file 回傳正確檔案路徑"""
        cache = PhaseCache("session_123", str(tmp_path))

        cache_file = cache.get_cache_file("requirements")

        expected = tmp_path / "session_session_123" / "phase_0_requirements.json"
        assert cache_file == expected

    def test_save_cache_creates_file(self, tmp_path: Path) -> None:
        """測試 save 會建立 cache 檔案"""
        cache = PhaseCache("test_save", str(tmp_path))

        cache.save(
            phase_name="requirements",
            status_code=PhaseStatusCode.CONFIRMED,
            response="Requirements confirmed",
            content_hash="hash123"
        )

        cache_file = cache.get_cache_file("requirements")
        assert cache_file.exists()

    def test_save_and_load_cache(self, tmp_path: Path) -> None:
        """測試可以成功儲存並載入 cache"""
        cache = PhaseCache("test_load", str(tmp_path))

        cache.save(
            phase_name="analysis",
            status_code=PhaseStatusCode.CONFIRMED,
            response="Analysis looks good",
            content_hash="hash456"
        )

        entry = cache.load("analysis")

        assert entry is not None
        assert entry.phase_name == "analysis"
        assert entry.status_code == PhaseStatusCode.CONFIRMED
        assert entry.response == "Analysis looks good"
        assert entry.content_hash == "hash456"

    def test_load_returns_none_when_cache_not_exists(self, tmp_path: Path) -> None:
        """測試當 cache 不存在時, load 回傳 None"""
        cache = PhaseCache("test_noexist", str(tmp_path))

        entry = cache.load("nonexistent_phase")

        assert entry is None

    def test_is_valid_returns_true_for_matching_hash(self, tmp_path: Path) -> None:
        """測試當 hash 匹配時, is_valid 回傳 True"""
        cache = PhaseCache("test_valid", str(tmp_path))

        cache.save(
            phase_name="requirements",
            status_code=PhaseStatusCode.CONFIRMED,
            response="OK",
            content_hash="same_hash"
        )

        assert cache.is_valid("requirements", "same_hash") is True

    def test_is_valid_returns_false_for_different_hash(self, tmp_path: Path) -> None:
        """測試當 hash 不匹配時, is_valid 回傳 False"""
        cache = PhaseCache("test_invalid", str(tmp_path))

        cache.save(
            phase_name="requirements",
            status_code=PhaseStatusCode.CONFIRMED,
            response="OK",
            content_hash="old_hash"
        )

        assert cache.is_valid("requirements", "new_hash") is False

    def test_is_valid_returns_false_when_cache_not_exists(self, tmp_path: Path) -> None:
        """測試當 cache 不存在時, is_valid 回傳 False"""
        cache = PhaseCache("test_nofile", str(tmp_path))

        assert cache.is_valid("nonexistent", "any_hash") is False

    def test_clear_removes_cache_file(self, tmp_path: Path) -> None:
        """測試 clear 會刪除指定 cache 檔案"""
        cache = PhaseCache("test_clear", str(tmp_path))

        cache.save(
            phase_name="requirements",
            status_code=PhaseStatusCode.CONFIRMED,
            response="OK",
            content_hash="hash"
        )

        cache_file = cache.get_cache_file("requirements")
        assert cache_file.exists()

        cache.clear("requirements")

        assert not cache_file.exists()

    def test_clear_does_not_raise_if_file_not_exists(self, tmp_path: Path) -> None:
        """測試 clear 不存在檔案時不會報錯"""
        cache = PhaseCache("test_clear_safe", str(tmp_path))

        # Should not raise
        cache.clear("nonexistent")

    def test_clear_all_removes_all_cache_files(self, tmp_path: Path) -> None:
        """測試 clear_all 會刪除所有 cache 檔案"""
        cache = PhaseCache("test_clear_all", str(tmp_path))

        # Create multiple cache files
        cache.save("requirements", PhaseStatusCode.CONFIRMED, "OK", "h1")
        cache.save("analysis", PhaseStatusCode.CONFIRMED, "OK", "h2")
        cache.save("review", PhaseStatusCode.CONFIRMED, "OK", "h3")

        cache_dir = tmp_path / "session_test_clear_all"
        assert len(list(cache_dir.glob("*.json"))) == 3

        cache.clear_all()

        assert len(list(cache_dir.glob("*.json"))) == 0

    def test_clear_all_does_not_raise_if_directory_empty(self, tmp_path: Path) -> None:
        """測試 clear_all 在目錄為空時不會報錯"""
        cache = PhaseCache("test_empty_dir", str(tmp_path))

        # Should not raise
        cache.clear_all()

    def test_save_overwrites_existing_cache(self, tmp_path: Path) -> None:
        """測試 save 會覆寫既有 cache"""
        cache = PhaseCache("test_overwrite", str(tmp_path))

        cache.save("requirements", PhaseStatusCode.NEED_CLARIFICATION, "Need info", "h1")
        cache.save("requirements", PhaseStatusCode.CONFIRMED, "OK now", "h2")

        entry = cache.load("requirements")

        assert entry is not None
        assert entry.status_code == PhaseStatusCode.CONFIRMED
        assert entry.response == "OK now"
        assert entry.content_hash == "h2"

    def test_cache_preserves_timestamp(self, tmp_path: Path) -> None:
        """測試 cache 保留時間戳記"""
        cache = PhaseCache("test_timestamp", str(tmp_path))

        before_save = datetime.now()
        cache.save("requirements", PhaseStatusCode.CONFIRMED, "OK", "hash")
        after_save = datetime.now()

        entry = cache.load("requirements")

        assert entry is not None
        assert before_save <= entry.timestamp <= after_save

    def test_get_phase_number_returns_correct_index(self, tmp_path: Path) -> None:
        """測試 get_phase_number 回傳正確 phase 索引"""
        cache = PhaseCache("test_index", str(tmp_path))

        assert cache.get_phase_number("requirements") == 0
        assert cache.get_phase_number("analysis") == 1
        assert cache.get_phase_number("implementation") == 2
        assert cache.get_phase_number("review") == 3
        assert cache.get_phase_number("pr") == 4

    def test_get_phase_number_returns_minus_one_for_unknown(self, tmp_path: Path) -> None:
        """測試 get_phase_number 對未知 phase 回傳 -1"""
        cache = PhaseCache("test_unknown", str(tmp_path))

        assert cache.get_phase_number("unknown_phase") == -1
