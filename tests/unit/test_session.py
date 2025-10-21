"""Tests for session management."""

import pytest
from pathlib import Path
from aaf.core.session import SessionManager
from aaf.core.types import AgentConfig, AgentCLI


class TestSessionManager:
    """Test SessionManager class."""

    def test_init_creates_sessions_directory(self, tmp_path: Path) -> None:
        """測試初始化時會建立 sessions 目錄"""
        sessions_dir = tmp_path / "sessions"
        manager = SessionManager(str(sessions_dir))

        assert sessions_dir.exists()
        assert sessions_dir.is_dir()

    def test_get_session_file_returns_correct_path(self, tmp_path: Path) -> None:
        """測試 get_session_file 回傳正確的檔案路徑"""
        sessions_dir = tmp_path / "sessions"
        manager = SessionManager(str(sessions_dir))

        session_file = manager.get_session_file("Roger")

        assert session_file == sessions_dir / "Roger_session_id"

    def test_load_session_returns_none_when_file_not_exists(self, tmp_path: Path) -> None:
        """測試當 session 檔案不存在時，load_session 回傳 None"""
        manager = SessionManager(str(tmp_path / "sessions"))

        session_id = manager.load_session("NonExistentAgent")

        assert session_id is None

    def test_save_and_load_session(self, tmp_path: Path) -> None:
        """測試可以成功儲存並載入 session ID"""
        manager = SessionManager(str(tmp_path / "sessions"))

        manager.save_session("David", "session-12345")
        loaded_session = manager.load_session("David")

        assert loaded_session == "session-12345"

    def test_save_session_creates_file(self, tmp_path: Path) -> None:
        """測試 save_session 會建立檔案"""
        sessions_dir = tmp_path / "sessions"
        manager = SessionManager(str(sessions_dir))

        manager.save_session("Richard", "session-abc")

        session_file = sessions_dir / "Richard_session_id"
        assert session_file.exists()
        assert session_file.read_text().strip() == "session-abc"

    def test_delete_session_removes_file(self, tmp_path: Path) -> None:
        """測試 delete_session 會刪除檔案"""
        manager = SessionManager(str(tmp_path / "sessions"))
        manager.save_session("Roger", "session-xyz")

        session_file = manager.get_session_file("Roger")
        assert session_file.exists()

        manager.delete_session("Roger")

        assert not session_file.exists()

    def test_delete_session_when_file_not_exists(self, tmp_path: Path) -> None:
        """測試刪除不存在的 session 不會拋出錯誤"""
        manager = SessionManager(str(tmp_path / "sessions"))

        # Should not raise any error
        manager.delete_session("NonExistent")

    def test_init_or_resume_session_returns_existing_session(self, tmp_path: Path) -> None:
        """測試當 session 已存在時，init_or_resume_session 回傳現有的 session ID"""
        manager = SessionManager(str(tmp_path / "sessions"))
        manager.save_session("David", "existing-session")

        agent_config = AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        session_id = manager.init_or_resume_session(agent_config)

        assert session_id == "existing-session"

    def test_init_or_resume_session_creates_new_when_not_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """測試當 session 不存在時，init_or_resume_session 會建立新的 session"""
        manager = SessionManager(str(tmp_path / "sessions"))

        # Mock _create_new_session to return a test session ID
        def mock_create_session(config: AgentConfig) -> str:
            return "new-session-456"

        monkeypatch.setattr(manager, "_create_new_session", mock_create_session)

        agent_config = AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        session_id = manager.init_or_resume_session(agent_config)

        assert session_id == "new-session-456"
        # Verify it was saved
        assert manager.load_session("Roger") == "new-session-456"

    def test_create_new_session_raises_not_implemented(self, tmp_path: Path) -> None:
        """測試 _create_new_session 預設會拋出 NotImplementedError"""
        manager = SessionManager(str(tmp_path / "sessions"))
        agent_config = AgentConfig(name="Test", cli=AgentCLI.CLAUDE)

        with pytest.raises(NotImplementedError, match="Session creation must be implemented"):
            manager._create_new_session(agent_config)
