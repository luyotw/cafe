"""Tests for cafe show command."""

import json
import pytest
from pathlib import Path
from typer.testing import CliRunner
from unittest.mock import patch

from cafe.ui.cli import app, _resolve_iteration_number, _get_show_file_path


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
            (iteration_dir / "iteration.json").write_text("{}")
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
            (iteration_dir / "iteration.json").write_text("{}")
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
            (iteration_dir / "iteration.json").write_text("{}")
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
            (iteration_dir / "iteration.json").write_text("{}")
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
            (iteration_dir / "iteration.json").write_text("{}")
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
        # 準備測試資料：iteration 1-3 有 output.md，iteration 4 只有 iteration.json
        phase_dir = tmp_path / "spec"
        phase_dir.mkdir(parents=True)
        for i in [1, 2, 3, 4]:
            iteration_dir = phase_dir / f"iteration_{i:03d}"
            iteration_dir.mkdir()
            (iteration_dir / "iteration.json").write_text("{}")
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
        assert _get_show_file_path(phase_dir, 1, "context") == phase_dir / "iteration_001" / "iteration.json"
        assert _get_show_file_path(phase_dir, 2, "output") == phase_dir / "iteration_002" / "output.md"
        assert _get_show_file_path(phase_dir, 3, "streaming") == phase_dir / "iteration_003" / "streaming.jsonl"
        assert _get_show_file_path(phase_dir, 1, "error") == phase_dir / "iteration_001" / "error.json"
        assert _get_show_file_path(phase_dir, 1, "checklist") == phase_dir / "iteration_001" / "checklist.md"

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
        (iteration_dir / "iteration.json").write_text("{}")
        (iteration_dir / "output.md").write_text("# Test Output")

        # Mock git operations 和 config
        with patch("cafe.ui.cli.GitOperations") as mock_git_cls, \
             patch("cafe.ui.cli.ConfigManager") as mock_config_cls, \
             patch("cafe.ui.cli.Path.cwd", return_value=tmp_path):

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
        (iteration_dir / "iteration.json").write_text('{"test": "context"}')

        # Mock git operations 和 config
        with patch("cafe.ui.cli.GitOperations") as mock_git_cls, \
             patch("cafe.ui.cli.ConfigManager") as mock_config_cls, \
             patch("cafe.ui.cli.Path.cwd", return_value=tmp_path):

            mock_git = mock_git_cls.return_value
            mock_git.get_current_branch.return_value = "test-issue"

            # 執行命令
            result = runner.invoke(app, ["show", "spec", "context"])

            # 驗證輸出
            assert result.exit_code == 0
            assert "context" in result.stdout

    def test_show_command_accepts_custom_playbook_step(self, tmp_path):
        """測試 show 命令接受 workflow playbook 中的自訂 step."""
        cafe_dir = tmp_path / ".cafe"
        issues_dir = cafe_dir / "issues" / "test-issue" / "qa"
        issues_dir.mkdir(parents=True)
        blackboard = cafe_dir / "issues" / "test-issue" / "blackboard.json"
        blackboard.write_text('{"schema_version":1,"playbook_id":"custom","current_step":"qa","artifacts":{},"events":[],"decisions":[]}')

        playbook_dir = cafe_dir / "playbooks"
        playbook_dir.mkdir(parents=True)
        (playbook_dir / "custom.yaml").write_text(
            """
playbook:
  id: custom
steps:
  qa:
    skill: cafe-review
    role: reviewer
    allowed_tools: ["Bash(cafe verification check:*)"]
    valid_intents: [confirmed]
    on:
      await_agent: _done
""".strip(),
            encoding="utf-8",
        )

        iteration_dir = issues_dir / "iteration_001"
        iteration_dir.mkdir()
        (iteration_dir / "iteration.json").write_text("{}")
        (iteration_dir / "output.md").write_text("# QA Output")

        with patch("cafe.ui.cli.GitOperations") as mock_git_cls, \
             patch("cafe.ui.cli.ConfigManager") as mock_config_cls, \
             patch("cafe.ui.cli.Path.cwd", return_value=tmp_path):

            mock_git = mock_git_cls.return_value
            mock_git.get_current_branch.return_value = "test-issue"

            result = runner.invoke(app, ["show", "qa"])

            assert result.exit_code == 0
            assert "QA Output" in result.stdout

    def test_show_status_synthesizes_when_status_file_missing(self, tmp_path):
        """status view falls back to synthesized workflow state."""
        cafe_dir = tmp_path / ".cafe"
        issue_dir = cafe_dir / "issues" / "test-issue"
        phase_dir = issue_dir / "develop"
        iteration_dir = phase_dir / "iteration_001"
        iteration_dir.mkdir(parents=True)
        (iteration_dir / "iteration.json").write_text(
            json.dumps(
                {
                    "iteration": 1,
                    "timestamp": "2026-04-27T10:00:00+08:00",
                    "end_time": "2026-04-27T10:05:00+08:00",
                    "status_code": "confirmed",
                }
            ),
            encoding="utf-8",
        )
        baton = {
            "version": 1,
            "from_step": "develop",
            "to_owner": "agent",
            "to_step": "review",
            "intent": "await_agent",
            "status_code": "confirmed",
            "created_at": "2026-04-27T10:05:00+08:00",
            "source": "test",
        }
        (issue_dir / "blackboard.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "current_step": "review",
                    "playbook_id": "default",
                    "artifacts": {},
                    "events": [],
                    "decisions": [],
                    "handoff_contract": baton,
                }
            ),
            encoding="utf-8",
        )
        (issue_dir / "next_step.txt").write_text(json.dumps(baton), encoding="utf-8")

        with patch("cafe.ui.cli.GitOperations") as mock_git_cls, \
             patch("cafe.ui.cli.ConfigManager") as mock_config_cls, \
             patch("cafe.ui.cli.Path.cwd", return_value=tmp_path):

            mock_git = mock_git_cls.return_value
            mock_git.get_current_branch.return_value = "test-issue"

            result = runner.invoke(app, ["show", "develop", "status"])

            assert result.exit_code == 0
            assert "confirmed" in result.stdout
            assert "completed" in result.stdout

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
            (iteration_dir / "iteration.json").write_text("{}")
            (iteration_dir / "output.md").write_text(f"# Iteration {i}")

        # Mock git operations 和 config
        with patch("cafe.ui.cli.GitOperations") as mock_git_cls, \
             patch("cafe.ui.cli.ConfigManager") as mock_config_cls, \
             patch("cafe.ui.cli.Path.cwd", return_value=tmp_path):

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
            (iteration_dir / "iteration.json").write_text("{}")
            (iteration_dir / "output.md").write_text(f"# Iteration {i}")

        # Mock git operations 和 config
        with patch("cafe.ui.cli.GitOperations") as mock_git_cls, \
             patch("cafe.ui.cli.ConfigManager") as mock_config_cls, \
             patch("cafe.ui.cli.Path.cwd", return_value=tmp_path):

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
        (iteration_dir / "iteration.json").write_text("{}")

        # Mock git operations 和 config
        with patch("cafe.ui.cli.GitOperations") as mock_git_cls, \
             patch("cafe.ui.cli.ConfigManager") as mock_config_cls, \
             patch("cafe.ui.cli.Path.cwd", return_value=tmp_path):

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
        (iteration_dir / "iteration.json").write_text("{}")

        # Mock git operations 和 config
        with patch("cafe.ui.cli.GitOperations") as mock_git_cls, \
             patch("cafe.ui.cli.ConfigManager") as mock_config_cls, \
             patch("cafe.ui.cli.Path.cwd", return_value=tmp_path):

            mock_git = mock_git_cls.return_value
            mock_git.get_current_branch.return_value = "test-issue"

            # 執行命令，請求不存在的迭代號碼
            result = runner.invoke(app, ["show", "spec", "output", "-i", "5"])

            # 驗證返回錯誤
            assert result.exit_code != 0

    def test_show_command_invalid_phase(self, tmp_path):
        """測試無效的階段名稱"""
        # Mock git operations 和 config
        with patch("cafe.ui.cli.GitOperations") as mock_git_cls, \
             patch("cafe.ui.cli.ConfigManager") as mock_config_cls, \
             patch("cafe.ui.cli.Path.cwd", return_value=tmp_path):

            mock_git = mock_git_cls.return_value
            mock_git.get_current_branch.return_value = "test-issue"

            # 執行命令，使用無效的階段名稱
            result = runner.invoke(app, ["show", "invalid-phase"])

            # 驗證返回錯誤
            assert result.exit_code != 0


class TestShowCommandChecklist:
    """Tests for cafe show checklist command."""

    def test_show_checklist_content_type_valid(self, tmp_path):
        """Test checklist is a valid content type."""
        from cafe.ui.cli import VALID_CONTENT_TYPES
        assert "checklist" in VALID_CONTENT_TYPES

    def test_show_checklist_file_mapping(self, tmp_path):
        """Test checklist maps to checklist.md file."""
        from cafe.ui.cli import CONTENT_TYPE_FILE_MAP
        assert CONTENT_TYPE_FILE_MAP["checklist"] == "checklist.md"

    def test_show_spec_checklist(self, tmp_path):
        """Test showing spec phase checklist."""
        # Prepare test environment
        cafe_dir = tmp_path / ".cafe"
        issues_dir = cafe_dir / "issues" / "test-issue" / "spec"
        issues_dir.mkdir(parents=True)

        # Create iteration with checklist
        iteration_dir = issues_dir / "iteration_001"
        iteration_dir.mkdir()
        (iteration_dir / "iteration.json").write_text("{}")
        checklist_file = iteration_dir / "checklist.md"
        checklist_file.write_text("""## Execution Steps Checklist

[ ] Read agent file
[ ] Read spec file
[ ] Return status code
""")

        # Mock git operations and config
        with patch("cafe.ui.cli.GitOperations") as mock_git_cls, \
             patch("cafe.ui.cli.ConfigManager") as mock_config_cls, \
             patch("cafe.ui.cli.Path.cwd", return_value=tmp_path):

            mock_git = mock_git_cls.return_value
            mock_git.get_current_branch.return_value = "test-issue"

            # Execute command
            result = runner.invoke(app, ["show", "spec", "checklist"])

            # Verify output
            assert result.exit_code == 0
            assert "Execution Steps Checklist" in result.stdout
            assert "Read agent file" in result.stdout

    def test_show_plan_checklist(self, tmp_path):
        """Test showing plan phase checklist."""
        # Prepare test environment
        cafe_dir = tmp_path / ".cafe"
        issues_dir = cafe_dir / "issues" / "test-issue" / "plan"
        issues_dir.mkdir(parents=True)

        # Create iteration with checklist
        iteration_dir = issues_dir / "iteration_001"
        iteration_dir.mkdir()
        (iteration_dir / "iteration.json").write_text("{}")
        checklist_file = iteration_dir / "checklist.md"
        checklist_file.write_text("""## Execution Steps Checklist

[ ] Read plan file
[ ] Write implementation plan
""")

        # Mock git operations and config
        with patch("cafe.ui.cli.GitOperations") as mock_git_cls, \
             patch("cafe.ui.cli.ConfigManager") as mock_config_cls, \
             patch("cafe.ui.cli.Path.cwd", return_value=tmp_path):

            mock_git = mock_git_cls.return_value
            mock_git.get_current_branch.return_value = "test-issue"

            # Execute command
            result = runner.invoke(app, ["show", "plan", "checklist"])

            # Verify output
            assert result.exit_code == 0
            assert "Execution Steps Checklist" in result.stdout

    def test_show_checklist_with_iteration_flag(self, tmp_path):
        """Test showing checklist with specific iteration."""
        # Prepare test environment
        cafe_dir = tmp_path / ".cafe"
        issues_dir = cafe_dir / "issues" / "test-issue" / "spec"
        issues_dir.mkdir(parents=True)

        # Create two iterations with different checklists
        for i in [1, 2]:
            iteration_dir = issues_dir / f"iteration_{i:03d}"
            iteration_dir.mkdir()
            (iteration_dir / "iteration.json").write_text("{}")
            checklist_file = iteration_dir / "checklist.md"
            checklist_file.write_text(f"## Checklist for iteration {i}")

        # Mock git operations and config
        with patch("cafe.ui.cli.GitOperations") as mock_git_cls, \
             patch("cafe.ui.cli.ConfigManager") as mock_config_cls, \
             patch("cafe.ui.cli.Path.cwd", return_value=tmp_path):

            mock_git = mock_git_cls.return_value
            mock_git.get_current_branch.return_value = "test-issue"

            # Execute command with iteration 1
            result = runner.invoke(app, ["show", "spec", "checklist", "-i", "1"])

            # Verify output
            assert result.exit_code == 0
            assert "iteration 1" in result.stdout

    def test_show_checklist_file_not_found(self, tmp_path):
        """Test error when checklist file doesn't exist."""
        # Prepare test environment
        cafe_dir = tmp_path / ".cafe"
        issues_dir = cafe_dir / "issues" / "test-issue" / "spec"
        issues_dir.mkdir(parents=True)

        # Create iteration WITHOUT checklist
        iteration_dir = issues_dir / "iteration_001"
        iteration_dir.mkdir()
        (iteration_dir / "iteration.json").write_text("{}")
        # No checklist.md file

        # Mock git operations and config
        with patch("cafe.ui.cli.GitOperations") as mock_git_cls, \
             patch("cafe.ui.cli.ConfigManager") as mock_config_cls, \
             patch("cafe.ui.cli.Path.cwd", return_value=tmp_path):

            mock_git = mock_git_cls.return_value
            mock_git.get_current_branch.return_value = "test-issue"

            # Execute command
            result = runner.invoke(app, ["show", "spec", "checklist"])

            # Should show error
            assert result.exit_code != 0


class TestShowCommandUserInput:
    """Tests for cafe show user_input command."""

    def test_show_user_input_content_type_valid(self, tmp_path):
        """Test user_input is a valid content type."""
        from cafe.ui.cli import VALID_CONTENT_TYPES
        assert "user_input" in VALID_CONTENT_TYPES

    def test_show_user_input_file_mapping(self, tmp_path):
        """Test user_input maps to user_input.md file."""
        from cafe.ui.cli import CONTENT_TYPE_FILE_MAP
        assert CONTENT_TYPE_FILE_MAP["user_input"] == "user_input.md"

    def test_show_spec_user_input(self, tmp_path):
        """Test showing spec phase user_input."""
        # Prepare test environment
        cafe_dir = tmp_path / ".cafe"
        issues_dir = cafe_dir / "issues" / "test-issue" / "spec"
        issues_dir.mkdir(parents=True)

        # Create iteration with user_input.md
        iteration_dir = issues_dir / "iteration_001"
        iteration_dir.mkdir()
        (iteration_dir / "iteration.json").write_text("{}")
        user_input_file = iteration_dir / "user_input.md"
        user_input_file.write_text("# Initial Requirements\n\nAdd login feature")

        # Mock git operations and config
        with patch("cafe.ui.cli.GitOperations") as mock_git_cls, \
             patch("cafe.ui.cli.ConfigManager") as mock_config_cls, \
             patch("cafe.ui.cli.Path.cwd", return_value=tmp_path):

            mock_git = mock_git_cls.return_value
            mock_git.get_current_branch.return_value = "test-issue"

            # Execute command
            result = runner.invoke(app, ["show", "spec", "user_input"])

            # Verify output
            assert result.exit_code == 0
            assert "Initial Requirements" in result.stdout
            assert "Add login feature" in result.stdout

    def test_show_plan_user_input(self, tmp_path):
        """Test showing plan phase user_input."""
        # Prepare test environment
        cafe_dir = tmp_path / ".cafe"
        issues_dir = cafe_dir / "issues" / "test-issue" / "plan"
        issues_dir.mkdir(parents=True)

        # Create iteration with user_input.md
        iteration_dir = issues_dir / "iteration_001"
        iteration_dir.mkdir()
        (iteration_dir / "iteration.json").write_text("{}")
        user_input_file = iteration_dir / "user_input.md"
        user_input_file.write_text("Please add error handling")

        # Mock git operations and config
        with patch("cafe.ui.cli.GitOperations") as mock_git_cls, \
             patch("cafe.ui.cli.ConfigManager") as mock_config_cls, \
             patch("cafe.ui.cli.Path.cwd", return_value=tmp_path):

            mock_git = mock_git_cls.return_value
            mock_git.get_current_branch.return_value = "test-issue"

            # Execute command
            result = runner.invoke(app, ["show", "plan", "user_input"])

            # Verify output
            assert result.exit_code == 0
            assert "Please add error handling" in result.stdout

    def test_show_user_input_with_iteration_flag(self, tmp_path):
        """Test showing user_input with specific iteration."""
        # Prepare test environment
        cafe_dir = tmp_path / ".cafe"
        issues_dir = cafe_dir / "issues" / "test-issue" / "spec"
        issues_dir.mkdir(parents=True)

        # Create two iterations with different user_input
        for i in [1, 2]:
            iteration_dir = issues_dir / f"iteration_{i:03d}"
            iteration_dir.mkdir()
            (iteration_dir / "iteration.json").write_text("{}")
            user_input_file = iteration_dir / "user_input.md"
            user_input_file.write_text(f"User input for iteration {i}")

        # Mock git operations and config
        with patch("cafe.ui.cli.GitOperations") as mock_git_cls, \
             patch("cafe.ui.cli.ConfigManager") as mock_config_cls, \
             patch("cafe.ui.cli.Path.cwd", return_value=tmp_path):

            mock_git = mock_git_cls.return_value
            mock_git.get_current_branch.return_value = "test-issue"

            # Execute command with iteration 1
            result = runner.invoke(app, ["show", "spec", "user_input", "-i", "1"])

            # Verify output
            assert result.exit_code == 0
            assert "iteration 1" in result.stdout

    def test_show_user_input_file_not_found(self, tmp_path):
        """Test error when user_input.md file doesn't exist."""
        # Prepare test environment
        cafe_dir = tmp_path / ".cafe"
        issues_dir = cafe_dir / "issues" / "test-issue" / "spec"
        issues_dir.mkdir(parents=True)

        # Create iteration WITHOUT user_input.md
        iteration_dir = issues_dir / "iteration_001"
        iteration_dir.mkdir()
        (iteration_dir / "iteration.json").write_text("{}")
        # No user_input.md file

        # Mock git operations and config
        with patch("cafe.ui.cli.GitOperations") as mock_git_cls, \
             patch("cafe.ui.cli.ConfigManager") as mock_config_cls, \
             patch("cafe.ui.cli.Path.cwd", return_value=tmp_path):

            mock_git = mock_git_cls.return_value
            mock_git.get_current_branch.return_value = "test-issue"

            # Execute command
            result = runner.invoke(app, ["show", "spec", "user_input"])

            # Should show error with specific message
            assert result.exit_code != 0
            assert "No user input markdown file found for this iteration." in result.stdout


class TestShowCommandQuestions:
    """Tests for cafe show questions command."""

    def test_show_questions_content_type_valid(self, tmp_path):
        """Test questions is a valid content type."""
        from cafe.ui.cli import VALID_CONTENT_TYPES
        assert "questions" in VALID_CONTENT_TYPES

    def test_show_questions_file_mapping(self, tmp_path):
        """Test questions maps to questions.xml file."""
        from cafe.ui.cli import CONTENT_TYPE_FILE_MAP
        assert CONTENT_TYPE_FILE_MAP["questions"] == "questions.xml"

    def test_show_spec_questions(self, tmp_path):
        """Test showing spec phase questions."""
        # Prepare test environment
        cafe_dir = tmp_path / ".cafe"
        issues_dir = cafe_dir / "issues" / "test-issue" / "spec"
        issues_dir.mkdir(parents=True)

        # Create iteration with questions.xml
        iteration_dir = issues_dir / "iteration_001"
        iteration_dir.mkdir()
        (iteration_dir / "iteration.json").write_text("{}")
        questions_file = iteration_dir / "questions.xml"
        questions_file.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<questions>
  <question>
    <title>What is your preferred authentication method?</title>
    <option>OAuth 2.0</option>
    <option>JWT</option>
  </question>
</questions>""")

        # Mock git operations and config
        with patch("cafe.ui.cli.GitOperations") as mock_git_cls, \
             patch("cafe.ui.cli.ConfigManager") as mock_config_cls, \
             patch("cafe.ui.cli.Path.cwd", return_value=tmp_path):

            mock_git = mock_git_cls.return_value
            mock_git.get_current_branch.return_value = "test-issue"

            # Execute command
            result = runner.invoke(app, ["show", "spec", "questions"])

            # Verify output
            assert result.exit_code == 0
            assert "authentication method" in result.stdout
            assert "OAuth 2.0" in result.stdout

    def test_show_questions_with_iteration_flag(self, tmp_path):
        """Test showing questions with specific iteration."""
        # Prepare test environment
        cafe_dir = tmp_path / ".cafe"
        issues_dir = cafe_dir / "issues" / "test-issue" / "spec"
        issues_dir.mkdir(parents=True)

        # Create two iterations with different questions
        for i in [1, 2]:
            iteration_dir = issues_dir / f"iteration_{i:03d}"
            iteration_dir.mkdir()
            (iteration_dir / "iteration.json").write_text("{}")
            questions_file = iteration_dir / "questions.xml"
            questions_file.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<questions>
  <question>
    <title>Question from iteration {i}?</title>
    <option>Yes</option>
  </question>
</questions>""")

        # Mock git operations and config
        with patch("cafe.ui.cli.GitOperations") as mock_git_cls, \
             patch("cafe.ui.cli.ConfigManager") as mock_config_cls, \
             patch("cafe.ui.cli.Path.cwd", return_value=tmp_path):

            mock_git = mock_git_cls.return_value
            mock_git.get_current_branch.return_value = "test-issue"

            # Execute command with iteration 1
            result = runner.invoke(app, ["show", "spec", "questions", "-i", "1"])

            # Verify output
            assert result.exit_code == 0
            assert "iteration 1" in result.stdout

    def test_show_questions_file_not_found(self, tmp_path):
        """Test error when questions.xml file doesn't exist."""
        # Prepare test environment
        cafe_dir = tmp_path / ".cafe"
        issues_dir = cafe_dir / "issues" / "test-issue" / "spec"
        issues_dir.mkdir(parents=True)

        # Create iteration WITHOUT questions.xml
        iteration_dir = issues_dir / "iteration_001"
        iteration_dir.mkdir()
        (iteration_dir / "iteration.json").write_text("{}")
        # No questions.xml file

        # Mock git operations and config
        with patch("cafe.ui.cli.GitOperations") as mock_git_cls, \
             patch("cafe.ui.cli.ConfigManager") as mock_config_cls, \
             patch("cafe.ui.cli.Path.cwd", return_value=tmp_path):

            mock_git = mock_git_cls.return_value
            mock_git.get_current_branch.return_value = "test-issue"

            # Execute command
            result = runner.invoke(app, ["show", "spec", "questions"])

            # Should show error
            assert result.exit_code != 0
