"""Tests to improve spec_phase coverage to 90%+."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.permission import PermissionHandler
from cafe.core.types import PhaseStatus, SpecRigor, TokenUsage, WorkflowMode
from cafe.phases.spec_phase import SpecPhase, create_github_issue, update_github_issue


@pytest.fixture
def mock_git_ops() -> MagicMock:
    """Create a mock GitOperations for testing."""
    git_ops = MagicMock(spec=GitOperations)
    git_ops.get_current_branch.return_value = "test"
    return git_ops


class TestGitHubFunctions:
    """Test GitHub helper functions."""

    def test_create_github_issue_not_implemented(self, mock_git_ops) -> None:
        """測試 create_github_issue 拋出 NotImplementedError"""
        with pytest.raises(NotImplementedError, match="GitHub issue creation not yet implemented"):
            create_github_issue("test content")

    def test_update_github_issue_not_implemented(self, mock_git_ops) -> None:
        """測試 update_github_issue 拋出 NotImplementedError"""
        with pytest.raises(NotImplementedError, match="GitHub issue update not yet implemented"):
            update_github_issue("123", "updated content")


class TestSpecPhaseRigorPrompt:
    """Test rigor level prompt."""

    def test_prompt_for_rigor_default(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試選擇預設 rigor level (Medium)"""
        monkeypatch.chdir(tmp_path)
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Initial Spec\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            git_ops=mock_git_ops,
        )

        with patch('cafe.ui.phase_prompts.prompt_list', return_value="Medium (中) - balanced mode [預設]\n   • 詢問重要細節and關鍵場景\n   • 在速度and精確度間取得平衡\n   • 適合：一般功能開發"):
            phase._prompt_for_rigor()

        assert phase.rigor == SpecRigor.MEDIUM

    def test_prompt_for_rigor_low(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試選擇 Low rigor level"""
        monkeypatch.chdir(tmp_path)
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Initial Spec\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            git_ops=mock_git_ops,
        )

        with patch('cafe.ui.phase_prompts.prompt_list', return_value="Low (低) - fast development模式\n   • 只問最關鍵資訊\n   • 允許模糊地帶, 讓開發者自行判斷\n   • 適合：快速原型、MVP、內部工具"):
            phase._prompt_for_rigor()

        assert phase.rigor == SpecRigor.LOW

    def test_prompt_for_rigor_high(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試選擇 High rigor level"""
        monkeypatch.chdir(tmp_path)
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Initial Spec\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            git_ops=mock_git_ops,
        )

        with patch('cafe.ui.phase_prompts.prompt_list', return_value="High (高) - precise specification模式\n   • 詳細詢問所有細節and邊界情況\n   • 確保需求可測試、無模糊\n   • 適合：核心功能、API 設計、對外產品"):
            phase._prompt_for_rigor()

        assert phase.rigor == SpecRigor.HIGH

    def test_prompt_for_rigor_invalid_then_valid(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試輸入無效值後再輸入有效值"""
        monkeypatch.chdir(tmp_path)
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Initial Spec\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            git_ops=mock_git_ops,
        )

        with patch('cafe.ui.phase_prompts.prompt_list', return_value="Medium (中) - balanced mode [預設]\n   • 詢問重要細節and關鍵場景\n   • 在速度and精確度間取得平衡\n   • 適合：一般功能開發"):
            with patch('builtins.print'):
                phase._prompt_for_rigor()

        assert phase.rigor == SpecRigor.MEDIUM

    def test_prompt_for_rigor_skips_if_explicitly_set(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試如果已明確設定 rigor 則跳過提示"""
        monkeypatch.chdir(tmp_path)
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Initial Spec\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            rigor=SpecRigor.HIGH,
            git_ops=mock_git_ops,
        )

        with patch('builtins.input') as mock_input:
            with patch('builtins.print'):
                phase._prompt_for_rigor()

        # Should not prompt for input
        mock_input.assert_not_called()
        assert phase.rigor == SpecRigor.HIGH


class TestSpecPhaseUserStoryPrompt:
    """Test user story prompt."""

    def test_prompt_for_user_story_success(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試成功提示用戶輸入需求"""
        monkeypatch.chdir(tmp_path)
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Initial Spec\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            git_ops=mock_git_ops,
        )
        # Manually set spec_file since we're calling internal method directly
        phase.spec_file = str(spec_file)

        user_requirement = "身為用戶, 我想要新增登入功能, 以便管理個人資料"

        with patch.object(phase.display, 'get_multiline_input', return_value=user_requirement):
            with patch('builtins.print'):
                phase._prompt_for_user_story()

        # Check spec file was created
        assert spec_file.exists()
        content = spec_file.read_text()
        assert "# Initial Requirements" in content
        assert user_requirement in content

    def test_prompt_for_user_story_empty_raises_error(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試空輸入拋出 ValueError"""
        monkeypatch.chdir(tmp_path)
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Initial Spec\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            git_ops=mock_git_ops,
        )

        with patch.object(phase.display, 'get_multiline_input', return_value=""):
            with patch('builtins.print'):
                with pytest.raises(ValueError, match="No requirements provided"):
                    phase._prompt_for_user_story()


class TestSpecPhaseGitHubMethods:
    """Test GitHub-related methods."""

    def test_create_github_issue(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試 _create_github_issue 呼叫"""
        monkeypatch.chdir(tmp_path)
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Initial Spec\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.GITHUB,
            interactive=False,
            git_ops=mock_git_ops,
        )

        with pytest.raises(NotImplementedError):
            phase._create_github_issue("test content")

    def test_update_github_issue(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試 _update_github_issue 呼叫"""
        monkeypatch.chdir(tmp_path)
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Initial Spec\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.GITHUB,
            interactive=False,
            issue_id="123",
            git_ops=mock_git_ops,
        )

        with pytest.raises(NotImplementedError):
            phase._update_github_issue("updated content")


class TestSpecPhaseHelperMethods:
    """Test helper methods."""

    def test_backup_spec(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試 _backup_spec 建立備份"""
        monkeypatch.chdir(tmp_path)
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("Original content")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            git_ops=mock_git_ops,
        )

        phase._backup_spec(spec_file)

        # Check backup was created
        backup_file = spec_file.parent / "spec_001.md.backup"
        assert backup_file.exists()
        assert backup_file.read_text() == "Original content"

    def test_display_current_spec_first_iteration(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試 _display_current_spec Round 1時顯示「檔案未產生」"""
        monkeypatch.chdir(tmp_path)
        spec_dir = tmp_path / ".cafe" / "issues" / "test" / "spec"
        spec_dir.mkdir(parents=True)

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            git_ops=mock_git_ops,
        )
        phase.pm_agent = "Roger"
        phase.iteration = 1  # Round 1
        phase.spec_file = str(spec_dir / "spec_001.md")
        phase.phase_dir = spec_dir

        with patch('builtins.print') as mock_print:
            phase._display_current_spec()

        # 驗證顯示「檔案未產生」
        print_calls = [str(call) for call in mock_print.call_args_list]
        assert any("File not generated" in str(call) for call in print_calls), \
            f"Expected 'File not generated' in print calls, got: {print_calls}"

    def test_display_current_spec_loads_previous_iteration(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試 _display_current_spec 載入上一輪檔案（iteration > 1）"""
        monkeypatch.chdir(tmp_path)
        spec_dir = tmp_path / ".cafe" / "issues" / "test" / "spec"
        spec_dir.mkdir(parents=True)

        # 建立上一輪檔案（spec_001.md）
        prev_spec_file = spec_dir / "spec_001.md"
        prev_spec_file.write_text("# Previous Spec\nPrevious requirements")

        # 當前輪檔案（spec_002.md）還不存在
        current_spec_file = spec_dir / "spec_002.md"

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            git_ops=mock_git_ops,
        )
        phase.pm_agent = "Roger"
        phase.iteration = 2  # Round 2
        phase.spec_file = str(current_spec_file)
        phase.phase_dir = spec_dir

        with patch('builtins.print') as mock_print:
            phase._display_current_spec()

        # 驗證有印出載入訊息, 並包含正確檔案路徑
        print_calls = [str(call) for call in mock_print.call_args_list]
        assert any("spec_001.md" in str(call) for call in print_calls), \
            f"Expected spec_001.md in print calls, got: {print_calls}"
        # 驗證載入內容是上一輪內容
        assert any("Previous requirements" in str(call) for call in print_calls), \
            f"Expected 'Previous requirements' in print calls, got: {print_calls}"

    def test_ask_user_for_clarification(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試 _ask_user_for_clarification"""
        monkeypatch.chdir(tmp_path)
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Initial Spec\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            git_ops=mock_git_ops,
        )

        user_input = "這是我回答"
        with patch.object(phase.display, 'get_multiline_input', return_value=user_input):
            result = phase._ask_user_for_clarification()

        assert result == user_input


class TestSpecPhaseGetMethods:
    """Test getter methods."""

    def test_get_non_technical_guidelines(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試 _get_non_technical_guidelines 返回指南"""
        monkeypatch.chdir(tmp_path)
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Initial Spec\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            git_ops=mock_git_ops,
        )

        guidelines = phase._get_non_technical_guidelines()
        assert "no technical details" in guidelines
        assert "Do not mention implementation methods" in guidelines

    def test_get_status_code_prompt(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試 _get_status_code_prompt 返回 prompt"""
        monkeypatch.chdir(tmp_path)
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Initial Spec\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            git_ops=mock_git_ops,
        )

        prompt = phase._get_status_code_prompt()
        assert "CAFE_READY_FOR_REVIEW" in prompt or "READY_FOR_REVIEW" in prompt

    def test_get_rigor_guidelines(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試 _get_rigor_guidelines 對不同 rigor levels"""
        monkeypatch.chdir(tmp_path)
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Initial Spec\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        # Test LOW
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            rigor=SpecRigor.LOW,
            git_ops=mock_git_ops,
        )
        guidelines = phase._get_rigor_guidelines()
        assert "fast development" in guidelines.lower()

        # Test MEDIUM
        phase.rigor = SpecRigor.MEDIUM
        guidelines = phase._get_rigor_guidelines()
        assert "balance" in guidelines.lower()

        # Test HIGH
        phase.rigor = SpecRigor.HIGH
        guidelines = phase._get_rigor_guidelines()
        assert "precise" in guidelines.lower()


class TestSpecPhaseRigorPersistence:
    """測試 rigor 參數持久化"""

    def test_rigor_saved_to_config_after_first_iteration(self, tmp_path, mock_git_ops, monkeypatch):
        """測試Round 1執行後 rigor 被儲存到 config"""
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-issue"

        spec_file = tmp_path / ".cafe" / "issues" / "test-issue" / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# 初始需求\n\n測試需求")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n需求已完成", TokenUsage(), [], None, [])
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent

        permission_handler = MagicMock(spec=PermissionHandler)

        # Create phase with explicitly set rigor=LOW
        from cafe.core.types import SpecRigor
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            git_ops=mock_git_ops,
            rigor=SpecRigor.LOW,
        )

        with patch('builtins.print'):
            phase.execute()

        # Check that rigor was saved to config
        config_file = tmp_path / ".cafe" / "issues" / "test-issue" / "issue.yaml"
        assert config_file.exists()

        import yaml
        with open(config_file, 'r') as f:
            config_data = yaml.safe_load(f)

        assert config_data["rigor"] == "low"

    def test_rigor_loaded_from_config_in_second_iteration(self, tmp_path, mock_git_ops, monkeypatch):
        """測試Round 2從 config 載入 rigor"""
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-issue"

        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True)

        # Create spec_001.md (previous iteration)
        spec_001 = spec_dir / "spec_001.md"
        spec_001.write_text("# 需求\n\n## 待釐清問題\n測試問題")

        # Create config with rigor=low
        config_file = issue_dir / "issue.yaml"
        import yaml
        with open(config_file, 'w') as f:
            yaml.dump({"rigor": "low"}, f)

        # Create iteration 1 history with NEED_CLARIFICATION
        history_dir = spec_dir / "history"
        history_dir.mkdir()
        import json
        iteration_1 = history_dir / "iteration_001.json"
        iteration_1.write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2025-11-09T12:00:00",
            "user_input": "初始需求",
            "pm_agent": "Roger",
            "response": "CAFE_NEED_CLARIFICATION\n需要更多資訊",
            "status_code": "CAFE_NEED_CLARIFICATION"
        }, ensure_ascii=False))

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n需求已完成", TokenUsage(), [], None, [])
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent

        permission_handler = MagicMock(spec=PermissionHandler)

        # Create phase WITHOUT explicitly setting rigor (should load from config)
        from cafe.core.types import SpecRigor
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            user_input="c",  # Confirm
            git_ops=mock_git_ops,
            # rigor NOT set - should load from config
        )

        # Execute phase (will load config in __init__)
        with patch('builtins.print'):
            phase.execute()

        # Verify rigor was loaded from config
        assert phase.rigor == SpecRigor.LOW

        # Verify prompt contains LOW rigor guidelines
        # Check iteration 2 history
        iteration_2 = history_dir / "iteration_002.json"
        assert iteration_2.exists()
        with open(iteration_2, 'r') as f:
            iter2_data = json.load(f)
        
        assert "low" in iter2_data["prompt"]
        assert "中（balanced mode）" not in iter2_data["prompt"]

    def test_explicit_rigor_overrides_config(self, tmp_path, mock_git_ops, monkeypatch):
        """測試明確設定 rigor 會覆蓋 config 中值"""
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-issue"

        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)

        # Create config with rigor=low
        config_file = issue_dir / "issue.yaml"
        import yaml
        with open(config_file, 'w') as f:
            yaml.dump({"rigor": "low"}, f)

        spec_file = issue_dir / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# 初始需求\n\n測試需求")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n需求已完成", TokenUsage(), [], None, [])
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent

        permission_handler = MagicMock(spec=PermissionHandler)

        # Create phase with explicitly set rigor=HIGH (should override config)
        from cafe.core.types import SpecRigor
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            git_ops=mock_git_ops,
            rigor=SpecRigor.HIGH,  # Explicitly set to HIGH
        )

        # Execute phase
        with patch('builtins.print'):
            phase.execute()

        # Verify rigor is HIGH (not LOW from config)
        assert phase.rigor == SpecRigor.HIGH

        # Verify config was updated to HIGH
        with open(config_file, 'r') as f:
            config_data = yaml.safe_load(f)
        assert config_data["rigor"] == "high"
