"""GitOperations worktree 方法的單元測試 (TDD Red phase)."""

import pytest
from pathlib import Path
from cafe.core.git import GitOperations, GitError


class TestGitWorktree:
    """測試 GitOperations 的 worktree 相關操作."""

    def test_create_worktree(self, tmp_path, monkeypatch):
        """測試建立 worktree."""
        monkeypatch.chdir(tmp_path)

        # 初始化 git repo
        git_ops = GitOperations(str(tmp_path))
        git_ops.run_git("init", "-b", "main")
        git_ops.run_git("config", "user.email", "test@test.com")
        git_ops.run_git("config", "user.name", "Test User")

        # 建立初始 commit
        test_file = tmp_path / "test.txt"
        test_file.write_text("test")
        git_ops.run_git("add", "test.txt")
        git_ops.run_git("commit", "-m", "Initial commit")

        # 測試建立 worktree
        worktree_path = tmp_path / "worktrees" / "test-branch"
        git_ops.create_worktree(str(worktree_path), "test-branch", "main")

        # 驗證 worktree 目錄存在
        assert worktree_path.exists()
        assert worktree_path.is_dir()

        # 驗證 worktree 中有正確的 branch
        worktree_git = GitOperations(str(worktree_path))
        assert worktree_git.get_current_branch() == "test-branch"

    def test_create_worktree_creates_parent_dir(self, tmp_path, monkeypatch):
        """測試建立 worktree 時會自動建立父目錄."""
        monkeypatch.chdir(tmp_path)

        git_ops = GitOperations(str(tmp_path))
        git_ops.run_git("init", "-b", "main")
        git_ops.run_git("config", "user.email", "test@test.com")
        git_ops.run_git("config", "user.name", "Test User")

        test_file = tmp_path / "test.txt"
        test_file.write_text("test")
        git_ops.run_git("add", "test.txt")
        git_ops.run_git("commit", "-m", "Initial commit")

        # 父目錄不存在
        worktree_path = tmp_path / "a" / "b" / "c" / "worktree"
        git_ops.create_worktree(str(worktree_path), "test-branch", "main")

        assert worktree_path.exists()

    def test_remove_worktree(self, tmp_path, monkeypatch):
        """測試移除 worktree."""
        monkeypatch.chdir(tmp_path)

        git_ops = GitOperations(str(tmp_path))
        git_ops.run_git("init", "-b", "main")
        git_ops.run_git("config", "user.email", "test@test.com")
        git_ops.run_git("config", "user.name", "Test User")

        test_file = tmp_path / "test.txt"
        test_file.write_text("test")
        git_ops.run_git("add", "test.txt")
        git_ops.run_git("commit", "-m", "Initial commit")

        # 建立 worktree
        worktree_path = tmp_path / "worktrees" / "test-branch"
        git_ops.create_worktree(str(worktree_path), "test-branch", "main")
        assert worktree_path.exists()

        # 移除 worktree
        git_ops.remove_worktree(str(worktree_path))
        assert not worktree_path.exists()

    def test_remove_worktree_nonexistent(self, tmp_path, monkeypatch):
        """測試移除不存在的 worktree 應該拋出錯誤."""
        monkeypatch.chdir(tmp_path)

        git_ops = GitOperations(str(tmp_path))
        git_ops.run_git("init", "-b", "main")

        worktree_path = tmp_path / "nonexistent"
        with pytest.raises(GitError):
            git_ops.remove_worktree(str(worktree_path))

    def test_worktree_exists(self, tmp_path, monkeypatch):
        """測試檢查 worktree 是否存在."""
        monkeypatch.chdir(tmp_path)

        git_ops = GitOperations(str(tmp_path))
        git_ops.run_git("init", "-b", "main")
        git_ops.run_git("config", "user.email", "test@test.com")
        git_ops.run_git("config", "user.name", "Test User")

        test_file = tmp_path / "test.txt"
        test_file.write_text("test")
        git_ops.run_git("add", "test.txt")
        git_ops.run_git("commit", "-m", "Initial commit")

        worktree_path = tmp_path / "worktrees" / "test-branch"

        # 尚未建立時應返回 False
        assert not git_ops.worktree_exists(str(worktree_path))

        # 建立後應返回 True
        git_ops.create_worktree(str(worktree_path), "test-branch", "main")
        assert git_ops.worktree_exists(str(worktree_path))

    def test_list_worktrees(self, tmp_path, monkeypatch):
        """測試列出所有 worktrees."""
        monkeypatch.chdir(tmp_path)

        git_ops = GitOperations(str(tmp_path))
        git_ops.run_git("init", "-b", "main")
        git_ops.run_git("config", "user.email", "test@test.com")
        git_ops.run_git("config", "user.name", "Test User")

        test_file = tmp_path / "test.txt"
        test_file.write_text("test")
        git_ops.run_git("add", "test.txt")
        git_ops.run_git("commit", "-m", "Initial commit")

        # 初始只有主 repo
        worktrees = git_ops.list_worktrees()
        assert len(worktrees) >= 1  # 至少包含主 repo

        # 建立兩個 worktrees
        worktree1 = tmp_path / "worktrees" / "branch1"
        worktree2 = tmp_path / "worktrees" / "branch2"
        git_ops.create_worktree(str(worktree1), "branch1", "main")
        git_ops.create_worktree(str(worktree2), "branch2", "main")

        # 應該列出所有 worktrees（包含主 repo）
        worktrees = git_ops.list_worktrees()
        assert len(worktrees) >= 3

        # 驗證路徑在列表中
        worktree_paths = [wt["path"] for wt in worktrees]
        assert str(worktree1.resolve()) in worktree_paths
        assert str(worktree2.resolve()) in worktree_paths

    def test_list_worktrees_structure(self, tmp_path, monkeypatch):
        """測試 list_worktrees 返回的資料結構."""
        monkeypatch.chdir(tmp_path)

        git_ops = GitOperations(str(tmp_path))
        git_ops.run_git("init", "-b", "main")
        git_ops.run_git("config", "user.email", "test@test.com")
        git_ops.run_git("config", "user.name", "Test User")

        test_file = tmp_path / "test.txt"
        test_file.write_text("test")
        git_ops.run_git("add", "test.txt")
        git_ops.run_git("commit", "-m", "Initial commit")

        worktree_path = tmp_path / "worktrees" / "test-branch"
        git_ops.create_worktree(str(worktree_path), "test-branch", "main")

        worktrees = git_ops.list_worktrees()

        # 每個 worktree 應該包含 path 和 branch
        for wt in worktrees:
            assert "path" in wt
            assert "branch" in wt
            assert isinstance(wt["path"], str)
            assert isinstance(wt["branch"], str)
