"""Tests for cafe show command."""

import pytest
from pathlib import Path
from typer.testing import CliRunner
from unittest.mock import patch

from cafe.ui.cli import app
from cafe.ui.commands.show_commands import _resolve_iteration_number, _get_show_file_path


runner = CliRunner()


class TestResolveIterationNumber:
    """測試迭代號碼解析功能"""

    def test_resolve_iteration_number_positive(self, tmp_path):
        """測試正數迭代號碼解析"""
        # 準備測試資料：建立 iteration_001, iteration_002, iteration_003
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir(parents=True)
        for i in [1, 2, 3]:
            iteration_dir = phase_dir / f"iteration_{i:03d}"
            iteration_dir.mkdir()
            (iteration_dir / "context.json").write_text("{}")
            (iteration_dir / "output.md").write_text(f"# Output {i}")

        # 測試正數迭代號碼
        assert _resolve_iteration_number(phase_dir, 1, "output") == 1
        assert _resolve_iteration_number(phase_dir, 2, "output") == 2
        assert _resolve_iteration_number(phase_dir, 3, "output") == 3

    def test_resolve_iteration_number_zero(self, tmp_path):
        """測試零（最新迭代）解析"""
        # 準備測試資料
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir(parents=True)
        for i in [1, 2, 3]:
            iteration_dir = phase_dir / f"iteration_{i:03d}"
            iteration_dir.mkdir()
            (iteration_dir / "context.json").write_text("{}")
            (iteration_dir / "output.md").write_text(f"# Output {i}")

        # 零應該返回最新的有該檔案的迭代號碼（3）
        assert _resolve_iteration_number(phase_dir, 0, "output") == 3

    def test_resolve_iteration_number_negative(self, tmp_path):
        """測試負數（相對索引）解析"""
        # 準備測試資料
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir(parents=True)
        for i in [1, 2, 3]:
            iteration_dir = phase_dir / f"iteration_{i:03d}"
            iteration_dir.mkdir()
            (iteration_dir / "context.json").write_text("{}")
            (iteration_dir / "output.md").write_text(f"# Output {i}")

        # -1 應該返回最新迭代的前一個（2）
        assert _resolve_iteration_number(phase_dir, -1, "output") == 2
        # -2 應該返回最新迭代的前兩個（1）
        assert _resolve_iteration_number(phase_dir, -2, "output") == 1

    def test_resolve_iteration_number_invalid_positive(self, tmp_path):
        """測試不存在的正數迭代號碼"""
        # 準備測試資料
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir(parents=True)
        for i in [1, 2]:
            iteration_dir = phase_dir / f"iteration_{i:03d}"
            iteration_dir.mkdir()
            (iteration_dir / "context.json").write_text("{}")
            (iteration_dir / "output.md").write_text(f"# Output {i}")

        # 迭代號碼 5 不存在，應該拋出 ValueError
        with pytest.raises(ValueError):
            _resolve_iteration_number(phase_dir, 5, "output")

    def test_resolve_iteration_number_invalid_negative(self, tmp_path):
        """測試超出範圍的負數迭代號碼"""
        # 準備測試資料
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir(parents=True)
        for i in [1, 2]:
            iteration_dir = phase_dir / f"iteration_{i:03d}"
            iteration_dir.mkdir()
            (iteration_dir / "context.json").write_text("{}")
            (iteration_dir / "output.md").write_text(f"# Output {i}")

        # -5 超出範圍，應該拋出 ValueError
        with pytest.raises(ValueError):
            _resolve_iteration_number(phase_dir, -5, "output")

    def test_resolve_iteration_number_no_iterations(self, tmp_path):
        """測試沒有任何迭代時"""
        # 準備空的階段目錄
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir(parents=True)

        # 沒有迭代時應該拋出 ValueError
        with pytest.raises(ValueError):
            _resolve_iteration_number(phase_dir, 0, "output")

    def test_resolve_iteration_number_partial_files(self, tmp_path):
        """測試部分迭代有特定檔案的情況"""
        # 準備測試資料：iteration 1-3 有 output.md，iteration 4 只有 context.json
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir(parents=True)
        for i in [1, 2, 3, 4]:
            iteration_dir = phase_dir / f"iteration_{i:03d}"
            iteration_dir.mkdir()
            (iteration_dir / "context.json").write_text("{}")
            if i <= 3:  # 只有 1-3 有 output.md
                (iteration_dir / "output.md").write_text(f"# Output {i}")

        # 0 應該返回最後一個有 output.md 的迭代（3）
        assert _resolve_iteration_number(phase_dir, 0, "output") == 3
        # -1 應該返回倒數第二個有 output.md 的迭代（2）
        assert _resolve_iteration_number(phase_dir, -1, "output") == 2
        # -2 應該返回倒數第三個有 output.md 的迭代（1）
        assert _resolve_iteration_number(phase_dir, -2, "output") == 1


class TestGetShowFilePath:
    """測試檔案路徑解析功能"""

    def test_get_show_file_path_iteration_files(self, tmp_path):
        """測試迭代目錄內的檔案路徑"""
        phase_dir = tmp_path / "spec"

        # 測試各種內容類型
        assert _get_show_file_path(phase_dir, 1, "context") == phase_dir / "iteration_001" / "context.json"
        assert _get_show_file_path(phase_dir, 2, "output") == phase_dir / "iteration_002" / "output.md"
        assert _get_show_file_path(phase_dir, 3, "streaming") == phase_dir / "iteration_003" / "streaming.jsonl"
        assert _get_show_file_path(phase_dir, 1, "error") == phase_dir / "iteration_001" / "error.json"
        assert _get_show_file_path(phase_dir, 1, "title") == phase_dir / "iteration_001" / "title.txt"
        assert _get_show_file_path(phase_dir, 1, "body") == phase_dir / "iteration_001" / "body.md"

    def test_get_show_file_path_phase_files(self, tmp_path):
        """測試階段層級的檔案路徑"""
        phase_dir = tmp_path / "spec"

        # status 和 iterations 應該位於階段目錄根層
        assert _get_show_file_path(phase_dir, 1, "status") == phase_dir / "status.json"
        assert _get_show_file_path(phase_dir, 2, "iterations") == phase_dir / "iterations.jsonl"


class TestShowCommand:
    """測試 show 命令整合功能"""

    def test_show_command_default(self, tmp_path):
        """測試預設參數（顯示最新迭代的 output.md）"""
        # 準備測試環境
        cafe_dir = tmp_path / ".cafe"
        issues_dir = cafe_dir / "issues" / "test-issue" / "spec"
        issues_dir.mkdir(parents=True)

        # 建立迭代目錄和檔案
        iteration_dir = issues_dir / "iteration_001"
        iteration_dir.mkdir()
        (iteration_dir / "context.json").write_text("{}")
        (iteration_dir / "output.md").write_text("# Test Output")

        # Mock git operations 和 config
        with patch("cafe.ui.commands.show_commands.GitOperations") as mock_git_cls, \
             patch("cafe.ui.commands.show_commands.Path.cwd", return_value=tmp_path):

            mock_git = mock_git_cls.return_value
            mock_git.get_current_branch.return_value = "test-issue"

            # 執行命令
            result = runner.invoke(app, ["show", "spec"])

            # 驗證輸出包含檔案內容
            assert result.exit_code == 0
            assert "Test Output" in result.stdout

    def test_show_command_with_content_type(self, tmp_path):
        """測試指定內容類型"""
        # 準備測試環境
        cafe_dir = tmp_path / ".cafe"
        issues_dir = cafe_dir / "issues" / "test-issue" / "spec"
        issues_dir.mkdir(parents=True)

        # 建立迭代目錄和檔案
        iteration_dir = issues_dir / "iteration_001"
        iteration_dir.mkdir()
        (iteration_dir / "context.json").write_text('{"test": "context"}')

        # Mock git operations 和 config
        with patch("cafe.ui.commands.show_commands.GitOperations") as mock_git_cls, \
             patch("cafe.ui.commands.show_commands.Path.cwd", return_value=tmp_path):

            mock_git = mock_git_cls.return_value
            mock_git.get_current_branch.return_value = "test-issue"

            # 執行命令
            result = runner.invoke(app, ["show", "spec", "context"])

            # 驗證輸出
            assert result.exit_code == 0
            assert "context" in result.stdout

    def test_show_command_with_iteration(self, tmp_path):
        """測試指定迭代號碼"""
        # 準備測試環境
        cafe_dir = tmp_path / ".cafe"
        issues_dir = cafe_dir / "issues" / "test-issue" / "spec"
        issues_dir.mkdir(parents=True)

        # 建立多個迭代
        for i in [1, 2]:
            iteration_dir = issues_dir / f"iteration_{i:03d}"
            iteration_dir.mkdir()
            (iteration_dir / "context.json").write_text("{}")
            (iteration_dir / "output.md").write_text(f"# Iteration {i}")

        # Mock git operations 和 config
        with patch("cafe.ui.commands.show_commands.GitOperations") as mock_git_cls, \
             patch("cafe.ui.commands.show_commands.Path.cwd", return_value=tmp_path):

            mock_git = mock_git_cls.return_value
            mock_git.get_current_branch.return_value = "test-issue"

            # 執行命令，指定迭代 1
            result = runner.invoke(app, ["show", "spec", "output", "-i", "1"])

            # 驗證輸出
            assert result.exit_code == 0
            assert "Iteration 1" in result.stdout

    def test_show_command_negative_iteration(self, tmp_path):
        """測試負數迭代號碼"""
        # 準備測試環境
        cafe_dir = tmp_path / ".cafe"
        issues_dir = cafe_dir / "issues" / "test-issue" / "spec"
        issues_dir.mkdir(parents=True)

        # 建立多個迭代
        for i in [1, 2, 3]:
            iteration_dir = issues_dir / f"iteration_{i:03d}"
            iteration_dir.mkdir()
            (iteration_dir / "context.json").write_text("{}")
            (iteration_dir / "output.md").write_text(f"# Iteration {i}")

        # Mock git operations 和 config
        with patch("cafe.ui.commands.show_commands.GitOperations") as mock_git_cls, \
             patch("cafe.ui.commands.show_commands.Path.cwd", return_value=tmp_path):

            mock_git = mock_git_cls.return_value
            mock_git.get_current_branch.return_value = "test-issue"

            # 執行命令，使用 -1 應該返回最新迭代的前一個（iteration 2）
            result = runner.invoke(app, ["show", "spec", "output", "-i", "-1"])

            # 驗證輸出
            assert result.exit_code == 0
            assert "Iteration 2" in result.stdout

    def test_show_command_file_not_found(self, tmp_path):
        """測試檔案不存在的情況"""
        # 準備測試環境
        cafe_dir = tmp_path / ".cafe"
        issues_dir = cafe_dir / "issues" / "test-issue" / "spec"
        issues_dir.mkdir(parents=True)

        # 建立迭代目錄但不建立 output.md
        iteration_dir = issues_dir / "iteration_001"
        iteration_dir.mkdir()
        (iteration_dir / "context.json").write_text("{}")

        # Mock git operations 和 config
        with patch("cafe.ui.commands.show_commands.GitOperations") as mock_git_cls, \
             patch("cafe.ui.commands.show_commands.Path.cwd", return_value=tmp_path):

            mock_git = mock_git_cls.return_value
            mock_git.get_current_branch.return_value = "test-issue"

            # 執行命令
            result = runner.invoke(app, ["show", "spec", "output"])

            # 驗證返回錯誤
            assert result.exit_code != 0

    def test_show_command_iteration_not_found(self, tmp_path):
        """測試迭代不存在的情況"""
        # 準備測試環境
        cafe_dir = tmp_path / ".cafe"
        issues_dir = cafe_dir / "issues" / "test-issue" / "spec"
        issues_dir.mkdir(parents=True)

        # 建立一個迭代
        iteration_dir = issues_dir / "iteration_001"
        iteration_dir.mkdir()
        (iteration_dir / "context.json").write_text("{}")

        # Mock git operations 和 config
        with patch("cafe.ui.commands.show_commands.GitOperations") as mock_git_cls, \
             patch("cafe.ui.commands.show_commands.Path.cwd", return_value=tmp_path):

            mock_git = mock_git_cls.return_value
            mock_git.get_current_branch.return_value = "test-issue"

            # 執行命令，請求不存在的迭代號碼
            result = runner.invoke(app, ["show", "spec", "output", "-i", "5"])

            # 驗證返回錯誤
            assert result.exit_code != 0

    def test_show_command_invalid_phase(self, tmp_path):
        """測試無效的階段名稱"""
        # Mock git operations 和 config
        with patch("cafe.ui.commands.show_commands.GitOperations") as mock_git_cls, \
             patch("cafe.ui.commands.show_commands.Path.cwd", return_value=tmp_path):

            mock_git = mock_git_cls.return_value
            mock_git.get_current_branch.return_value = "test-issue"

            # 執行命令，使用無效的階段名稱
            result = runner.invoke(app, ["show", "invalid-phase"])

            # 驗證返回錯誤
            assert result.exit_code != 0
