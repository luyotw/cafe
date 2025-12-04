"""測試所有 agent executor 在 worktree 環境中使用主 repo root 作為工作目錄"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from cafe.agents.executor import AgentExecutor
from cafe.core.types import AgentConfig, AgentCLI


class TestExecutorWorktreeCwd:
    """測試 executor 在 worktree 環境中使用正確的工作目錄"""

    def test_claude_executes_in_repo_root_when_in_worktree(self, tmp_path, monkeypatch):
        """Claude 在 worktree 環境中應該在主 repo root 執行"""
        # Setup: 模擬 worktree 環境
        main_repo = tmp_path / "main-repo"
        main_repo.mkdir()
        git_dir = main_repo / ".git"
        git_dir.mkdir()

        worktree = tmp_path / "worktree"
        worktree.mkdir()
        # Worktree 的 .git 是檔案而非目錄
        worktree_git = worktree / ".git"
        worktree_git.write_text("gitdir: ../main-repo/.git/worktrees/worktree")

        # 切換到 worktree 目錄
        monkeypatch.chdir(worktree)

        # Mock get_repo_root 回傳主 repo
        with patch("cafe.agents.executor.get_repo_root", return_value=main_repo):
            config = AgentConfig(name="TestAgent", cli=AgentCLI.CLAUDE)
            executor = AgentExecutor(config)

            # Capture subprocess.Popen call
            captured_cwd = []

            def mock_popen(*args, **kwargs):
                captured_cwd.append(kwargs.get("cwd"))
                mock_proc = MagicMock()
                mock_proc.stdout.readline.side_effect = [
                    '{"message":{"content":[{"type":"text","text":"Test"}]}}\n',
                    ''
                ]
                mock_proc.stderr = MagicMock()
                mock_proc.stderr.read.return_value = ""
                mock_proc.wait.return_value = 0
                return mock_proc

            # Mock subprocess and create session
            with patch("subprocess.Popen", side_effect=mock_popen):
                with patch("subprocess.run") as mock_run:
                    # Mock session creation
                    mock_run.return_value = MagicMock(
                        returncode=0,
                        stdout='{"session_id": "test-session"}',
                        stderr=""
                    )
                    with patch("select.select", return_value=([], [], [])):
                        try:
                            executor.execute("Test prompt")
                        except Exception:
                            pass

            # 驗證：subprocess 應該在主 repo root 執行
            assert len(captured_cwd) > 0
            assert captured_cwd[-1] == str(main_repo), f"Expected cwd={main_repo}, got {captured_cwd[-1]}"


    def test_gemini_executes_in_repo_root_when_in_worktree(self, tmp_path, monkeypatch):
        """Gemini 在 worktree 環境中應該在主 repo root 執行"""
        # Setup
        main_repo = tmp_path / "main-repo"
        main_repo.mkdir()
        git_dir = main_repo / ".git"
        git_dir.mkdir()

        worktree = tmp_path / "worktree"
        worktree.mkdir()
        worktree_git = worktree / ".git"
        worktree_git.write_text("gitdir: ../main-repo/.git/worktrees/worktree")

        monkeypatch.chdir(worktree)

        with patch("cafe.agents.executor.get_repo_root", return_value=main_repo):
            config = AgentConfig(name="TestAgent", cli=AgentCLI.GEMINI)
            executor = AgentExecutor(config)

            captured_cwd = []

            def mock_popen(*args, **kwargs):
                captured_cwd.append(kwargs.get("cwd"))
                mock_proc = MagicMock()
                mock_proc.stdout.readline.side_effect = [
                    '{"type":"message","role":"assistant","content":"Test"}\n',
                    ''
                ]
                mock_proc.stderr = MagicMock()
                mock_proc.stderr.read.return_value = ""
                mock_proc.wait.return_value = 0
                return mock_proc

            with patch("subprocess.Popen", side_effect=mock_popen):
                with patch("select.select", return_value=([], [], [])):
                    try:
                        executor.execute("Test prompt")
                    except Exception:
                        pass

            assert len(captured_cwd) > 0
            assert captured_cwd[-1] == str(main_repo), f"Expected cwd={main_repo}, got {captured_cwd[-1]}"


    def test_copilot_executes_in_repo_root_when_in_worktree(self, tmp_path, monkeypatch):
        """Copilot 在 worktree 環境中應該在主 repo root 執行"""
        main_repo = tmp_path / "main-repo"
        main_repo.mkdir()
        git_dir = main_repo / ".git"
        git_dir.mkdir()

        worktree = tmp_path / "worktree"
        worktree.mkdir()
        worktree_git = worktree / ".git"
        worktree_git.write_text("gitdir: ../main-repo/.git/worktrees/worktree")

        monkeypatch.chdir(worktree)

        with patch("cafe.agents.executor.get_repo_root", return_value=main_repo):
            config = AgentConfig(name="TestAgent", cli=AgentCLI.COPILOT)
            executor = AgentExecutor(config)

            captured_cwd = []

            def mock_popen(*args, **kwargs):
                captured_cwd.append(kwargs.get("cwd"))
                mock_proc = MagicMock()
                mock_proc.stdout.readline.side_effect = ["Test response\n", '']
                mock_proc.stderr = MagicMock()
                mock_proc.stderr.read.return_value = ""
                mock_proc.wait.return_value = 0
                return mock_proc

            with patch("subprocess.Popen", side_effect=mock_popen):
                with patch("select.select", return_value=([], [], [])):
                    try:
                        executor.execute("Test prompt")
                    except Exception:
                        pass

            assert len(captured_cwd) > 0
            assert captured_cwd[-1] == str(main_repo), f"Expected cwd={main_repo}, got {captured_cwd[-1]}"


    def test_cursor_executes_in_repo_root_when_in_worktree(self, tmp_path, monkeypatch):
        """Cursor 在 worktree 環境中應該在主 repo root 執行"""
        main_repo = tmp_path / "main-repo"
        main_repo.mkdir()
        git_dir = main_repo / ".git"
        git_dir.mkdir()

        worktree = tmp_path / "worktree"
        worktree.mkdir()
        worktree_git = worktree / ".git"
        worktree_git.write_text("gitdir: ../main-repo/.git/worktrees/worktree")

        monkeypatch.chdir(worktree)

        with patch("cafe.agents.executor.get_repo_root", return_value=main_repo):
            config = AgentConfig(name="TestAgent", cli=AgentCLI.CURSOR)
            executor = AgentExecutor(config)

            captured_cwd = []

            def mock_popen(*args, **kwargs):
                captured_cwd.append(kwargs.get("cwd"))
                mock_proc = MagicMock()
                mock_proc.stdout.readline.side_effect = [
                    '{"response":"Test"}\n',
                    ''
                ]
                mock_proc.stderr = MagicMock()
                mock_proc.stderr.read.return_value = ""
                mock_proc.wait.return_value = 0
                return mock_proc

            with patch("subprocess.Popen", side_effect=mock_popen):
                with patch("select.select", return_value=([], [], [])):
                    try:
                        executor.execute("Test prompt")
                    except Exception:
                        pass

            assert len(captured_cwd) > 0
            assert captured_cwd[-1] == str(main_repo), f"Expected cwd={main_repo}, got {captured_cwd[-1]}"


    def test_executes_in_current_dir_when_not_in_worktree(self, tmp_path, monkeypatch):
        """不在 worktree 時應該使用預設 cwd (None)"""
        # Setup: 普通 repo（不是 worktree）
        repo = tmp_path / "normal-repo"
        repo.mkdir()
        git_dir = repo / ".git"
        git_dir.mkdir()  # .git 是目錄，不是檔案

        monkeypatch.chdir(repo)

        with patch("cafe.agents.executor.get_repo_root", return_value=repo):
            config = AgentConfig(name="TestAgent", cli=AgentCLI.GEMINI)
            executor = AgentExecutor(config)

            captured_cwd = []

            def mock_popen(*args, **kwargs):
                captured_cwd.append(kwargs.get("cwd"))
                mock_proc = MagicMock()
                mock_proc.stdout.readline.side_effect = [
                    '{"type":"message","role":"assistant","content":"Test"}\n',
                    ''
                ]
                mock_proc.stderr = MagicMock()
                mock_proc.stderr.read.return_value = ""
                mock_proc.wait.return_value = 0
                return mock_proc

            with patch("subprocess.Popen", side_effect=mock_popen):
                with patch("select.select", return_value=([], [], [])):
                    try:
                        executor.execute("Test prompt")
                    except Exception:
                        pass

            # 不在 worktree，cwd 應該是 None（使用當前目錄）
            assert len(captured_cwd) > 0
            assert captured_cwd[-1] is None, f"Expected cwd=None, got {captured_cwd[-1]}"
