"""測試 git_utils.py  repo root and路徑轉換功能.

確保可以正確處理：
1. 一般 Git repository
2. Git worktree 環境
3. 路徑轉換為 git ignore 格式
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from cafe.utils.git_utils import get_git_dir, get_git_toplevel, get_repo_root, to_git_ignore_path


class TestGetRepoRoot:
    """測試 get_repo_root() 函數"""

    def test_normal_repo_returns_repo_root(self, tmp_path):
        """一般 repo：返回包含 .git 目錄路徑"""
        # Setup: 創建 .git 目錄
        repo_root = tmp_path / "my-repo"
        git_dir = repo_root / ".git"
        git_dir.mkdir(parents=True)

        # 在子目錄中呼叫
        subdir = repo_root / "src" / "cafe"
        subdir.mkdir(parents=True)

        # Execute
        result = get_repo_root(subdir)

        # Assert
        assert result == repo_root
        assert result.name == "my-repo"

    def test_worktree_returns_main_repo_root(self, tmp_path):
        """Worktree：返回主 repo  root, 不是 worktree 目錄"""
        # Setup: 創建主 repo 結構
        main_repo = tmp_path / "main-repo"
        main_git_dir = main_repo / ".git"
        main_git_dir.mkdir(parents=True)

        # 創建 worktree 結構
        worktree_dir = tmp_path / "worktrees" / "feature-branch"
        worktree_dir.mkdir(parents=True)

        # 創建 .git 檔案（worktree  .git 是檔案不是目錄）
        git_file = worktree_dir / ".git"
        git_file.write_text(f"gitdir: {main_git_dir}/worktrees/feature-branch\n")

        # 創建對應 worktree git 目錄
        worktree_git_dir = main_git_dir / "worktrees" / "feature-branch"
        worktree_git_dir.mkdir(parents=True)

        # Execute：從 worktree 目錄呼叫
        result = get_repo_root(worktree_dir)

        # Assert：應該返回主 repo  root
        assert result == main_repo
        assert result.name == "main-repo"

    def test_from_nested_worktree_subdirectory(self, tmp_path):
        """從 worktree 子目錄呼叫, 仍然返回主 repo root"""
        # Setup
        main_repo = tmp_path / "main-repo"
        main_git_dir = main_repo / ".git"
        main_git_dir.mkdir(parents=True)

        worktree_dir = tmp_path / "worktrees" / "my-feature"
        worktree_dir.mkdir(parents=True)

        git_file = worktree_dir / ".git"
        git_file.write_text(f"gitdir: {main_git_dir}/worktrees/my-feature\n")

        worktree_git_dir = main_git_dir / "worktrees" / "my-feature"
        worktree_git_dir.mkdir(parents=True)

        # 創建 worktree 子目錄
        subdir = worktree_dir / "src" / "cafe" / "phases"
        subdir.mkdir(parents=True)

        # Execute：從深層子目錄呼叫
        result = get_repo_root(subdir)

        # Assert
        assert result == main_repo

    def test_current_directory_default(self, tmp_path, monkeypatch):
        """不傳入路徑時, 使用當前目錄"""
        # Setup
        repo_root = tmp_path / "repo"
        git_dir = repo_root / ".git"
        git_dir.mkdir(parents=True)

        monkeypatch.chdir(repo_root)

        # Execute：不傳入參數
        result = get_repo_root()

        # Assert
        assert result == repo_root

    def test_not_in_git_repo_raises_error(self, tmp_path):
        """不在 Git repo 中時拋出 ValueError"""
        # Setup：沒有 .git 目錄
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()

        # Execute & Assert
        with pytest.raises(ValueError, match="Not in a Git repository"):
            get_repo_root(non_repo)


class TestGetGitDir:
    """測試 get_git_dir() 函數"""

    def test_normal_repo_returns_dot_git_dir(self, tmp_path):
        repo_root = tmp_path / "repo"
        git_dir = repo_root / ".git"
        git_dir.mkdir(parents=True)

        assert get_git_dir(repo_root) == git_dir

    def test_worktree_returns_worktree_git_dir(self, tmp_path):
        main_repo = tmp_path / "main-repo"
        main_git_dir = main_repo / ".git"
        main_git_dir.mkdir(parents=True)

        worktree_dir = tmp_path / "worktrees" / "feature-branch"
        worktree_dir.mkdir(parents=True)
        worktree_git_dir = main_git_dir / "worktrees" / "feature-branch"
        worktree_git_dir.mkdir(parents=True)

        (worktree_dir / ".git").write_text(f"gitdir: {worktree_git_dir}\n")

        assert get_git_dir(worktree_dir) == worktree_git_dir


class TestGetGitToplevel:
    """測試 get_git_toplevel() 函數"""

    def test_normal_repo_returns_repo_root(self, tmp_path):
        repo_root = tmp_path / "repo"
        (repo_root / ".git").mkdir(parents=True)

        with patch("cafe.utils.git_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=str(repo_root), returncode=0)
            assert get_git_toplevel(repo_root) == repo_root

    def test_worktree_returns_worktree_root(self, tmp_path):
        main_repo = tmp_path / "main-repo"
        main_git_dir = main_repo / ".git"
        main_git_dir.mkdir(parents=True)

        worktree_dir = tmp_path / "worktrees" / "feature-branch"
        worktree_dir.mkdir(parents=True)
        worktree_git_dir = main_git_dir / "worktrees" / "feature-branch"
        worktree_git_dir.mkdir(parents=True)

        (worktree_dir / ".git").write_text(f"gitdir: {worktree_git_dir}\n")

        with patch("cafe.utils.git_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=str(worktree_dir), returncode=0)
            assert get_git_toplevel(worktree_dir) == worktree_dir


class TestToGitIgnorePath:
    """測試 to_git_ignore_path() 函數"""

    def test_convert_absolute_to_relative_with_slash_prefix(self):
        """轉換絕對路徑為相對路徑, 並加上 / 前綴"""
        repo_root = Path("/Users/me/projects/my-repo")
        file_path = Path("/Users/me/projects/my-repo/.cafe/issues/issue26/spec/spec_001.md")

        result = to_git_ignore_path(file_path, repo_root)

        assert result == "/.cafe/issues/issue26/spec/spec_001.md"

    def test_handles_string_inputs(self):
        """接受字串輸入"""
        repo_root = "/Users/me/repo"
        file_path = "/Users/me/repo/src/main.py"

        result = to_git_ignore_path(file_path, repo_root)

        assert result == "/src/main.py"

    def test_file_not_under_repo_raises_error(self):
        """檔案不在 repo 下時拋出 ValueError"""
        repo_root = Path("/Users/me/repo")
        file_path = Path("/Users/other/different-repo/file.txt")

        with pytest.raises(ValueError, match="not under repository root"):
            to_git_ignore_path(file_path, repo_root)

    def test_handles_path_with_spaces(self):
        """處理包含空格路徑"""
        repo_root = Path("/Users/me/my projects/repo")
        file_path = Path("/Users/me/my projects/repo/src/my file.py")

        result = to_git_ignore_path(file_path, repo_root)

        assert result == "/src/my file.py"

    def test_file_at_repo_root(self):
        """檔案在 repo 根目錄"""
        repo_root = Path("/Users/me/repo")
        file_path = Path("/Users/me/repo/README.md")

        result = to_git_ignore_path(file_path, repo_root)

        assert result == "/README.md"

    def test_preserves_case_sensitivity(self):
        """保持大小寫"""
        repo_root = Path("/Users/me/MyRepo")
        file_path = Path("/Users/me/MyRepo/SRC/MyFile.PY")

        result = to_git_ignore_path(file_path, repo_root)

        assert result == "/SRC/MyFile.PY"
