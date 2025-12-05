"""測試 get_repo_root() 是否正確遵守 GIT_CEILING_DIRECTORIES。

這個測試的目的是防止測試污染真實 repo。
當 pre-commit hook 執行測試時，測試 fixtures 會在 tmp_path 中創建 git repos。
如果 get_repo_root() 不尊重 GIT_CEILING_DIRECTORIES，它可能會向上搜索
並找到真實的 worktree，導致測試污染真實 repo。
"""
import os
import pytest
from pathlib import Path
from cafe.utils.git_utils import get_repo_root


class TestGetRepoRootWithCeiling:
    """測試 get_repo_root() 遵守 GIT_CEILING_DIRECTORIES 環境變數"""

    def test_respects_git_ceiling_directories_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """測試：當設置 GIT_CEILING_DIRECTORIES 時，不應該向上搜索超過邊界。

        這是防止測試污染的關鍵測試。

        場景：
        1. 我們在 tmp_path 中（沒有 .git）
        2. GIT_CEILING_DIRECTORIES 設置為 tmp_path 的父目錄
        3. 即使上層有真實的 .git，get_repo_root() 也不應該找到它

        預期行為：
        - 應該拋出 ValueError("Not in a Git repository")
        - 不應該逃逸到上層找到真實 repo
        """
        # 設置 GIT_CEILING_DIRECTORIES（這是 pre-commit hook 會設置的）
        ceiling = tmp_path.parent
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(ceiling))

        # 創建一個沒有 .git 的目錄
        test_dir = tmp_path / "no-git-here"
        test_dir.mkdir()
        monkeypatch.chdir(test_dir)

        # 確認 GIT_CEILING_DIRECTORIES 已設置
        assert os.environ.get("GIT_CEILING_DIRECTORIES") == str(ceiling)

        # 調用 get_repo_root() 應該失敗，而不是找到上層的真實 repo
        with pytest.raises(ValueError, match="Not in a Git repository"):
            get_repo_root()

    def test_respects_ceiling_even_with_explicit_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """測試：即使明確指定 cwd 參數，也應該遵守 GIT_CEILING_DIRECTORIES。"""
        ceiling = tmp_path.parent
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(ceiling))

        test_dir = tmp_path / "explicit-path"
        test_dir.mkdir()

        # 即使明確指定路徑，也不應該逃逸
        with pytest.raises(ValueError, match="Not in a Git repository"):
            get_repo_root(cwd=test_dir)

    def test_without_ceiling_can_find_parent_repo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """測試：當沒有設置 GIT_CEILING_DIRECTORIES 時，應該能向上搜索。

        這個測試確保我們的修復不會破壞正常的向上搜索功能。
        """
        # 確保沒有設置 GIT_CEILING_DIRECTORIES
        monkeypatch.delenv("GIT_CEILING_DIRECTORIES", raising=False)

        # 創建一個假的 git repo
        fake_repo = tmp_path / "fake-repo"
        fake_repo.mkdir()
        (fake_repo / ".git").mkdir()

        # 在子目錄中
        subdir = fake_repo / "subdir" / "deep"
        subdir.mkdir(parents=True)
        monkeypatch.chdir(subdir)

        # 應該能找到 fake_repo
        result = get_repo_root()
        assert result == fake_repo

    def test_ceiling_stops_search_at_boundary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """測試：GIT_CEILING_DIRECTORIES 應該停止在邊界處。

        場景：
        - tmp_path/repo/.git 存在
        - 我們在 tmp_path/repo/sub/deep
        - GIT_CEILING_DIRECTORIES 設置為 tmp_path/repo/sub

        預期：找不到 repo，因為 ceiling 阻止了向上搜索到 tmp_path/repo
        """
        # 創建 repo
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()

        # 創建子目錄
        sub = repo / "sub"
        sub.mkdir()
        deep = sub / "deep"
        deep.mkdir()

        # 設置 ceiling 為 sub（阻止搜索到 repo）
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(sub))
        monkeypatch.chdir(deep)

        # 應該找不到 repo（被 ceiling 阻止）
        with pytest.raises(ValueError, match="Not in a Git repository"):
            get_repo_root()

    def test_multiple_ceiling_directories(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """測試：GIT_CEILING_DIRECTORIES 可以包含多個路徑（用冒號分隔）。"""
        ceiling1 = tmp_path / "ceiling1"
        ceiling2 = tmp_path / "ceiling2"
        ceiling1.mkdir()
        ceiling2.mkdir()

        # GIT_CEILING_DIRECTORIES 支援多個路徑（類似 PATH）
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES", f"{ceiling1}:{ceiling2}"
        )

        test_dir = ceiling1 / "test"
        test_dir.mkdir()
        monkeypatch.chdir(test_dir)

        # 應該被 ceiling1 阻止
        with pytest.raises(ValueError, match="Not in a Git repository"):
            get_repo_root()


class TestGetRepoRootPreservesExistingBehavior:
    """測試：確保修復不會破壞現有功能"""

    def test_finds_git_directory_in_current_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """測試：在當前目錄有 .git 目錄時應該找到"""
        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)

        result = get_repo_root()
        assert result == tmp_path

    def test_finds_git_in_parent_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """測試：在父目錄有 .git 時應該向上找到（沒有 ceiling 時）"""
        monkeypatch.delenv("GIT_CEILING_DIRECTORIES", raising=False)

        (tmp_path / ".git").mkdir()
        subdir = tmp_path / "sub" / "deep"
        subdir.mkdir(parents=True)
        monkeypatch.chdir(subdir)

        result = get_repo_root()
        assert result == tmp_path

    def test_handles_worktree_git_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """測試：處理 worktree 的 .git 檔案（而不是目錄）"""
        monkeypatch.delenv("GIT_CEILING_DIRECTORIES", raising=False)

        # 模擬 worktree 的 .git 檔案
        main_repo = tmp_path / "main-repo"
        main_repo.mkdir()

        worktree = tmp_path / "worktree"
        worktree.mkdir()

        git_file = worktree / ".git"
        git_file.write_text(f"gitdir: {main_repo}/.git/worktrees/test\n")

        monkeypatch.chdir(worktree)

        result = get_repo_root()
        assert result == main_repo

    def test_raises_when_not_in_git_repo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """測試：不在 git repo 時應該拋出錯誤"""
        monkeypatch.delenv("GIT_CEILING_DIRECTORIES", raising=False)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(ValueError, match="Not in a Git repository"):
            get_repo_root()

    def test_worktree_respects_ceiling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """測試：當在 worktree 中且 worktree 指向 ceiling 之外的主 repo 時，應該被阻止。

        這是修復測試污染的關鍵測試：
        1. 在 tmp_path 中建立一個 worktree（.git 是檔案）
        2. .git 檔案指向真實 repo（在 ceiling 之外）
        3. 設置 ceiling 為 tmp_path.parent
        4. get_repo_root() 應該拋出錯誤，而不是返回真實 repo
        """
        # 建立主 repo（在 ceiling 之外 - 更上層的目錄）
        # tmp_path = /T/pytest-xxx/test0
        # ceiling = tmp_path.parent = /T/pytest-xxx
        # real_repo = ceiling.parent / "real_repo" = /T/real_repo (在 ceiling 之上！)
        real_repo = tmp_path.parent.parent / "real_repo"
        real_repo.mkdir(exist_ok=True)  # May already exist from other tests
        real_git = real_repo / ".git"
        real_git.mkdir(exist_ok=True)

        # 建立 worktree 目錄（在 ceiling 之內）
        worktree_dir = tmp_path / "test_worktree"
        worktree_dir.mkdir()

        # 建立 .git 檔案指向主 repo
        git_file = worktree_dir / ".git"
        gitdir_path = real_git / "worktrees" / "test_branch"
        gitdir_path.mkdir(parents=True, exist_ok=True)
        git_file.write_text(f"gitdir: {gitdir_path}\n")

        # 設置 ceiling（tmp_path.parent 阻止搜索到 real_repo）
        ceiling = tmp_path.parent
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(ceiling))

        # 從 worktree 目錄執行
        monkeypatch.chdir(worktree_dir)

        # 應該拋出錯誤，因為 worktree 指向的主 repo 在 ceiling 之外
        with pytest.raises(ValueError, match="worktree points to repo"):
            get_repo_root()
