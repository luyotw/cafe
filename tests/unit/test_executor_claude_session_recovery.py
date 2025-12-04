"""測試 Claude executor 在 session 找不到時自動恢復"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from cafe.agents.executor import AgentExecutor
from cafe.core.types import AgentConfig, AgentCLI


class TestClaudeSessionRecovery:
    """測試 Claude session 自動恢復功能"""

    def test_creates_new_session_when_session_not_found(self):
        """當 session 不存在時，應該自動建立新 session"""
        config = AgentConfig(
            name="TestAgent",
            cli=AgentCLI.CLAUDE,
            session_id="nonexistent-session-id"  # 不存在的 session
        )
        executor = AgentExecutor(config)

        # Mock subprocess calls
        popen_calls = []

        def mock_popen(*args, **kwargs):
            cmd = args[0]
            popen_calls.append(cmd)

            mock_proc = MagicMock()

            # 第一次呼叫：使用不存在的 session，回傳錯誤
            if "--resume" in cmd and "nonexistent-session-id" in cmd:
                mock_proc.stdout.readline.side_effect = ['']
                mock_proc.stderr = MagicMock()
                mock_proc.stderr.read.return_value = "No conversation found with session ID: nonexistent-session-id"
                mock_proc.wait.return_value = 1  # 失敗
                return mock_proc

            # 第二次呼叫：使用新 session，成功
            mock_proc.stdout.readline.side_effect = [
                '{"message":{"content":[{"type":"text","text":"Test response"}]}}\n',
                ''
            ]
            mock_proc.stderr = MagicMock()
            mock_proc.stderr.read.return_value = ""
            mock_proc.wait.return_value = 0
            return mock_proc

        with patch("subprocess.Popen", side_effect=mock_popen):
            with patch("subprocess.run") as mock_run:
                # Mock session creation
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout='{"session_id": "new-session-123"}',
                    stderr=""
                )
                with patch("select.select", return_value=([], [], [])):
                    # 應該成功執行，不拋出錯誤
                    response = executor.execute("Test prompt")

        # 驗證：執行成功，Session ID 被更新
        assert response is not None
        assert executor.config.session_id == "new-session-123"

        # 至少應該有兩次 Popen 調用（第一次失敗，第二次成功）
        assert len(popen_calls) >= 2


    def test_handles_session_locked_error(self):
        """當 session 被鎖定時，應該拋出明確的錯誤"""
        config = AgentConfig(
            name="TestAgent",
            cli=AgentCLI.CLAUDE,
            session_id="locked-session"
        )
        executor = AgentExecutor(config)

        def mock_popen(*args, **kwargs):
            mock_proc = MagicMock()
            mock_proc.stdout.readline.side_effect = ['']
            mock_proc.stderr = MagicMock()
            mock_proc.stderr.read.return_value = "Session locked error: already in use"
            mock_proc.wait.return_value = 1
            return mock_proc

        with patch("subprocess.Popen", side_effect=mock_popen):
            with patch("select.select", return_value=([], [], [])):
                with pytest.raises(Exception) as exc_info:
                    executor.execute("Test prompt")

                # 其他錯誤應該正常拋出
                assert "locked" in str(exc_info.value).lower() or "already in use" in str(exc_info.value).lower()


    def test_normal_execution_without_session_error(self):
        """正常執行時不應該嘗試恢復 session"""
        config = AgentConfig(
            name="TestAgent",
            cli=AgentCLI.CLAUDE,
        )
        executor = AgentExecutor(config)

        session_creation_count = [0]

        def mock_run(*args, **kwargs):
            session_creation_count[0] += 1
            return MagicMock(
                returncode=0,
                stdout='{"session_id": "session-123"}',
                stderr=""
            )

        def mock_popen(*args, **kwargs):
            mock_proc = MagicMock()
            mock_proc.stdout.readline.side_effect = [
                '{"message":{"content":[{"type":"text","text":"Success"}]}}\n',
                ''
            ]
            mock_proc.stderr = MagicMock()
            mock_proc.stderr.read.return_value = ""
            mock_proc.wait.return_value = 0
            return mock_proc

        with patch("subprocess.Popen", side_effect=mock_popen):
            with patch("subprocess.run", side_effect=mock_run):
                with patch("select.select", return_value=([], [], [])):
                    response = executor.execute("Test prompt")

        # 應該只建立一次 session（warmup）
        assert session_creation_count[0] == 1
