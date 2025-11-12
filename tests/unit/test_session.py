"""Tests for session management."""

import json
import pytest
from pathlib import Path
from datetime import datetime
from cafe.core.session import SessionManager
from cafe.core.types import AgentConfig, AgentCLI, SessionData


class TestSessionManager:
    """Test SessionManager class."""

    def test_init_creates_sessions_directory(self, tmp_path: Path) -> None:
        """測試初始化時會建立 sessions 目錄"""
        sessions_dir = tmp_path / "sessions"
        manager = SessionManager(str(sessions_dir))

        assert sessions_dir.exists()
        assert sessions_dir.is_dir()

    def test_get_session_file_returns_correct_path(self, tmp_path: Path) -> None:
        """測試 get_session_file 回傳正確的檔案路徑（包含 CLI 類型）"""
        sessions_dir = tmp_path / "sessions"
        manager = SessionManager(str(sessions_dir))

        session_file = manager.get_session_file("Roger", AgentCLI.COPILOT)

        assert session_file == sessions_dir / "Roger_copilot.json"

    def test_get_session_file_with_issue_name(self, tmp_path: Path) -> None:
        """測試 get_session_file 在指定 issue 時回傳正確路徑（在 issues 目錄下）"""
        sessions_dir = tmp_path / "sessions"
        manager = SessionManager(str(sessions_dir))

        session_file = manager.get_session_file("David", AgentCLI.CLAUDE, "myip")

        # Issue-specific sessions 應該在 .cafe/issues/{issue_name}/sessions/
        expected_path = Path(".cafe/issues") / "myip" / "sessions" / "David_claude.json"
        assert session_file == expected_path

    def test_load_session_returns_none_when_file_not_exists(self, tmp_path: Path) -> None:
        """測試當 session 檔案不存在時，load_session 回傳 None"""
        manager = SessionManager(str(tmp_path / "sessions"))

        session_data = manager.load_session("NonExistentAgent", AgentCLI.CLAUDE)

        assert session_data is None

    def test_save_and_load_session(self, tmp_path: Path) -> None:
        """測試可以成功儲存並載入 session 資料"""
        manager = SessionManager(str(tmp_path / "sessions"))

        manager.save_session("David", AgentCLI.CLAUDE, "session-12345")
        loaded_session = manager.load_session("David", AgentCLI.CLAUDE)

        assert loaded_session is not None
        assert loaded_session.agent_name == "David"
        assert loaded_session.cli == AgentCLI.CLAUDE
        assert loaded_session.session_id == "session-12345"
        assert isinstance(loaded_session.created_at, datetime)
        assert isinstance(loaded_session.last_used_at, datetime)

    def test_save_session_creates_json_file(self, tmp_path: Path) -> None:
        """測試 save_session 會建立 JSON 檔案"""
        sessions_dir = tmp_path / "sessions"
        manager = SessionManager(str(sessions_dir))

        manager.save_session("Richard", AgentCLI.GEMINI, "session-abc")

        session_file = sessions_dir / "Richard_gemini.json"
        assert session_file.exists()

        # Verify JSON content
        with open(session_file) as f:
            data = json.load(f)
            assert data["agent_name"] == "Richard"
            assert data["cli"] == "gemini"
            assert data["session_id"] == "session-abc"

    def test_save_session_preserves_created_at(self, tmp_path: Path) -> None:
        """測試更新 session 時會保留 created_at 時間"""
        manager = SessionManager(str(tmp_path / "sessions"))

        # First save
        manager.save_session("Roger", AgentCLI.COPILOT, "session-1")
        first_save = manager.load_session("Roger", AgentCLI.COPILOT)
        assert first_save is not None

        # Second save (update)
        import time
        time.sleep(0.1)  # Ensure time difference
        manager.save_session("Roger", AgentCLI.COPILOT, "session-2")
        second_save = manager.load_session("Roger", AgentCLI.COPILOT)
        assert second_save is not None

        # created_at should be the same, last_used_at should be different
        assert second_save.created_at == first_save.created_at
        assert second_save.last_used_at > first_save.last_used_at
        assert second_save.session_id == "session-2"

    def test_delete_session_removes_file(self, tmp_path: Path) -> None:
        """測試 delete_session 會刪除檔案"""
        manager = SessionManager(str(tmp_path / "sessions"))
        manager.save_session("Roger", AgentCLI.CLAUDE, "session-xyz")

        session_file = manager.get_session_file("Roger", AgentCLI.CLAUDE)
        assert session_file.exists()

        manager.delete_session("Roger", AgentCLI.CLAUDE)

        assert not session_file.exists()

    def test_delete_session_when_file_not_exists(self, tmp_path: Path) -> None:
        """測試刪除不存在的 session 不會拋出錯誤"""
        manager = SessionManager(str(tmp_path / "sessions"))

        # Should not raise any error
        manager.delete_session("NonExistent", AgentCLI.CLAUDE)

    def test_different_cli_sessions_are_independent(self, tmp_path: Path) -> None:
        """測試同一個 agent 使用不同 CLI 的 session 是獨立的"""
        manager = SessionManager(str(tmp_path / "sessions"))

        # Save sessions with different CLIs
        manager.save_session("David", AgentCLI.CLAUDE, "claude-session-123")
        manager.save_session("David", AgentCLI.GEMINI, "gemini-session-456")

        # Load and verify they are independent
        claude_session = manager.load_session("David", AgentCLI.CLAUDE)
        gemini_session = manager.load_session("David", AgentCLI.GEMINI)

        assert claude_session is not None
        assert gemini_session is not None
        assert claude_session.session_id == "claude-session-123"
        assert gemini_session.session_id == "gemini-session-456"

    def test_issue_specific_sessions(self, tmp_path: Path) -> None:
        """測試 issue-specific sessions 會儲存在獨立目錄"""
        manager = SessionManager(str(tmp_path / "sessions"))

        manager.save_session("Roger", AgentCLI.COPILOT, "session-issue1", "myip")
        manager.save_session("Roger", AgentCLI.COPILOT, "session-issue2", "fix-bug")

        issue1_session = manager.load_session("Roger", AgentCLI.COPILOT, "myip")
        issue2_session = manager.load_session("Roger", AgentCLI.COPILOT, "fix-bug")

        assert issue1_session is not None
        assert issue2_session is not None
        assert issue1_session.session_id == "session-issue1"
        assert issue2_session.session_id == "session-issue2"

    def test_load_session_handles_invalid_json(self, tmp_path: Path) -> None:
        """測試載入無效的 JSON 檔案時回傳 None"""
        sessions_dir = tmp_path / "sessions"
        manager = SessionManager(str(sessions_dir))

        # Create invalid JSON file
        session_file = manager.get_session_file("Test", AgentCLI.CLAUDE)
        session_file.write_text("invalid json content")

        session_data = manager.load_session("Test", AgentCLI.CLAUDE)

        assert session_data is None
