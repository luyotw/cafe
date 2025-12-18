"""測試通用 session recovery 機制, 適用於所有 CLI"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from cafe.agents.executor import AgentExecutor
from cafe.core.types import AgentConfig, AgentCLI


class TestGenericSessionRecovery:
    """測試通用 session recovery 機制"""

    def test_session_recovery_with_custom_error_message(self, monkeypatch):
        """測試能識別不同 session 錯誤訊息"""
        config = AgentConfig(
            name="TestAgent",
            cli=AgentCLI.CLAUDE,
            session_id="old-session"
        )
        executor = AgentExecutor(config)

        call_count = [0]

        def mock_popen(*args, **kwargs):
            call_count[0] += 1
            mock_proc = MagicMock()

            if call_count[0] == 1:
                # 第一次：session 不存在
                mock_proc.stdout.readline.side_effect = ['']
                mock_proc.stderr = MagicMock()
                mock_proc.stderr.read.return_value = "Conversation does not exist"
                mock_proc.wait.return_value = 1
            else:
                # 第二次：成功
                mock_proc.stdout.readline.side_effect = [
                    '{"message":{"content":[{"type":"text","text":"Success"}]}}\n',
                    ''
                ]
                mock_proc.stderr = MagicMock()
                mock_proc.stderr.read.return_value = ""
                mock_proc.wait.return_value = 0

            return mock_proc

        with patch("subprocess.Popen", side_effect=mock_popen):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout='{"session_id": "new-session"}',
                    stderr=""
                )
                with patch("select.select", return_value=([], [], [])):
                    response = executor.execute("Test prompt")

        # 驗證成功恢復
        assert response is not None
        assert executor.config.session_id == "new-session"
        assert call_count[0] == 2


    def test_copilot_session_recovery(self):
        """測試 Copilot  session recovery"""
        config = AgentConfig(
            name="TestAgent",
            cli=AgentCLI.COPILOT,
            session_id="old-copilot-session"
        )
        executor = AgentExecutor(config)

        call_count = [0]

        def mock_popen(*args, **kwargs):
            call_count[0] += 1
            mock_proc = MagicMock()

            if call_count[0] == 1:
                # 第一次：session 不存在
                mock_proc.stdout.readline.side_effect = ['']
                mock_proc.stderr = MagicMock()
                mock_proc.stderr.read.return_value = "Session not found: old-copilot-session"
                mock_proc.wait.return_value = 1
            else:
                # 第二次：成功
                mock_proc.stdout.readline.side_effect = [
                    "Response from Copilot\n",
                    ''
                ]
                mock_proc.stderr = MagicMock()
                mock_proc.stderr.read.return_value = ""
                mock_proc.wait.return_value = 0

            return mock_proc

        # Mock copilot session directory
        with patch("subprocess.Popen", side_effect=mock_popen):
            with patch("select.select", return_value=([], [], [])):
                with patch.object(Path, "exists", return_value=True):
                    with patch.object(Path, "iterdir", return_value=[
                        MagicMock(name="new-copilot-session.jsonl", is_file=lambda: True)
                    ]):
                        response = executor.execute("Test prompt")

        # 驗證成功恢復
        assert response is not None
        # Copilot 會自動偵測新 session
        assert call_count[0] == 2


    def test_non_session_error_still_raises(self):
        """測試非 session 錯誤仍然正常拋出"""
        config = AgentConfig(
            name="TestAgent",
            cli=AgentCLI.CLAUDE,
            session_id="test-session"
        )
        executor = AgentExecutor(config)

        def mock_popen(*args, **kwargs):
            mock_proc = MagicMock()
            mock_proc.stdout.readline.side_effect = ['']
            mock_proc.stderr = MagicMock()
            mock_proc.stderr.read.return_value = "Some other error"
            mock_proc.wait.return_value = 1
            return mock_proc

        with patch("subprocess.Popen", side_effect=mock_popen):
            with patch("select.select", return_value=([], [], [])):
                with pytest.raises(Exception) as exc_info:
                    executor.execute("Test prompt")

                # 應該直接拋出原始錯誤
                assert "some other error" in str(exc_info.value).lower()


    def test_session_recovery_updates_config(self):
        """測試 session recovery 會更新 config 中 session_id"""
        config = AgentConfig(
            name="TestAgent",
            cli=AgentCLI.CLAUDE,
            session_id="expired-session"
        )
        executor = AgentExecutor(config)

        # 確認初始 session ID
        assert executor.config.session_id == "expired-session"

        call_count = [0]

        def mock_popen(*args, **kwargs):
            call_count[0] += 1
            mock_proc = MagicMock()

            if call_count[0] == 1:
                mock_proc.stdout.readline.side_effect = ['']
                mock_proc.stderr = MagicMock()
                mock_proc.stderr.read.return_value = "No conversation found"
                mock_proc.wait.return_value = 1
            else:
                mock_proc.stdout.readline.side_effect = [
                    '{"message":{"content":[{"type":"text","text":"OK"}]}}\n',
                    ''
                ]
                mock_proc.stderr = MagicMock()
                mock_proc.stderr.read.return_value = ""
                mock_proc.wait.return_value = 0

            return mock_proc

        with patch("subprocess.Popen", side_effect=mock_popen):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout='{"session_id": "fresh-session"}',
                    stderr=""
                )
                with patch("select.select", return_value=([], [], [])):
                    executor.execute("Test prompt")

        # 驗證 session_id 已更新
        assert executor.config.session_id == "fresh-session"

    def test_multiple_session_recovery_attempts(self, monkeypatch):
        """測試多次 session recovery（當新 session 也失敗時）"""
        config = AgentConfig(
            name="TestAgent",
            cli=AgentCLI.CLAUDE,
            session_id="session-1"
        )
        executor = AgentExecutor(config)

        call_count = [0]
        session_creation_count = [0]

        def mock_popen(*args, **kwargs):
            call_count[0] += 1
            mock_proc = MagicMock()

            if call_count[0] <= 2:
                # 前兩次：session 不存在
                mock_proc.stdout.readline.side_effect = ['']
                mock_proc.stderr = MagicMock()
                mock_proc.stderr.read.return_value = "No conversation found"
                mock_proc.wait.return_value = 1
            else:
                # 第三次：成功
                mock_proc.stdout.readline.side_effect = [
                    '{"message":{"content":[{"type":"text","text":"Success"}]}}\n',
                    ''
                ]
                mock_proc.stderr = MagicMock()
                mock_proc.stderr.read.return_value = ""
                mock_proc.wait.return_value = 0

            return mock_proc

        def mock_run_for_session_creation(*args, **kwargs):
            session_creation_count[0] += 1
            return MagicMock(
                returncode=0,
                stdout=f'{{"session_id": "session-{session_creation_count[0] + 1}"}}',
                stderr=""
            )

        with patch("subprocess.Popen", side_effect=mock_popen):
            with patch("subprocess.run", side_effect=mock_run_for_session_creation):
                with patch("select.select", return_value=([], [], [])):
                    response = executor.execute("Test prompt")

        # 驗證成功執行
        assert response.response == "Success"

        # 驗證經過 2 次 session 創建（session-1 → session-2 → session-3）
        assert session_creation_count[0] == 2
        assert executor.config.session_id == "session-3"

        # 驗證執行了 3 次（2 次失敗 + 1 次成功）
        assert call_count[0] == 3

    def test_max_retries_exceeded(self, monkeypatch):
        """測試超過最大 retry 次數時會失敗"""
        config = AgentConfig(
            name="TestAgent",
            cli=AgentCLI.CLAUDE,
            session_id="session-1"
        )
        executor = AgentExecutor(config)

        def mock_popen(*args, **kwargs):
            # 永遠回傳 session not found
            mock_proc = MagicMock()
            mock_proc.stdout.readline.side_effect = ['']
            mock_proc.stderr = MagicMock()
            mock_proc.stderr.read.return_value = "No conversation found"
            mock_proc.wait.return_value = 1
            return mock_proc

        session_creation_count = [0]

        def mock_run_for_session_creation(*args, **kwargs):
            session_creation_count[0] += 1
            return MagicMock(
                returncode=0,
                stdout=f'{{"session_id": "session-{session_creation_count[0] + 1}"}}',
                stderr=""
            )

        from cafe.agents.executor import AgentExecutionError

        with patch("subprocess.Popen", side_effect=mock_popen):
            with patch("subprocess.run", side_effect=mock_run_for_session_creation):
                with patch("select.select", return_value=([], [], [])):
                    with pytest.raises(AgentExecutionError):
                        executor.execute("Test prompt")

        # 驗證達到最大 retry 次數（3 次, 因為 max_retries=3）
        assert session_creation_count[0] == 3
