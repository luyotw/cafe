"""Tests to improve spec_phase coverage to 90%+."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.agents.manager import AgentManager
from cafe.core.permission import PermissionHandler
from cafe.core.types import PhaseStatus, SpecRigor, WorkflowMode
from cafe.phases.spec_phase import SpecPhase, create_github_issue, update_github_issue


class TestGitHubFunctions:
    """Test GitHub helper functions."""

    def test_create_github_issue_not_implemented(self) -> None:
        """測試 create_github_issue 拋出 NotImplementedError"""
        with pytest.raises(NotImplementedError, match="GitHub issue creation not yet implemented"):
            create_github_issue("test content")

    def test_update_github_issue_not_implemented(self) -> None:
        """測試 update_github_issue 拋出 NotImplementedError"""
        with pytest.raises(NotImplementedError, match="GitHub issue update not yet implemented"):
            update_github_issue("123", "updated content")


class TestSpecPhaseRigorPrompt:
    """Test rigor level prompt."""

    def test_prompt_for_rigor_default(self, tmp_path: Path) -> None:
        """測試選擇預設 rigor level (Medium)"""
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        with patch('builtins.input', return_value=""):
            with patch('builtins.print'):
                phase._prompt_for_rigor()

        assert phase.rigor == SpecRigor.MEDIUM

    def test_prompt_for_rigor_low(self, tmp_path: Path) -> None:
        """測試選擇 Low rigor level"""
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        with patch('builtins.input', return_value="1"):
            with patch('builtins.print'):
                phase._prompt_for_rigor()

        assert phase.rigor == SpecRigor.LOW

    def test_prompt_for_rigor_high(self, tmp_path: Path) -> None:
        """測試選擇 High rigor level"""
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        with patch('builtins.input', return_value="3"):
            with patch('builtins.print'):
                phase._prompt_for_rigor()

        assert phase.rigor == SpecRigor.HIGH

    def test_prompt_for_rigor_invalid_then_valid(self, tmp_path: Path) -> None:
        """測試輸入無效值後再輸入有效值"""
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        with patch('builtins.input', side_effect=["invalid", "4", "2"]):
            with patch('builtins.print'):
                phase._prompt_for_rigor()

        assert phase.rigor == SpecRigor.MEDIUM

    def test_prompt_for_rigor_skips_if_explicitly_set(self, tmp_path: Path) -> None:
        """測試如果已明確設定 rigor 則跳過提示"""
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            rigor=SpecRigor.HIGH,
        )

        with patch('builtins.input') as mock_input:
            with patch('builtins.print'):
                phase._prompt_for_rigor()

        # Should not prompt for input
        mock_input.assert_not_called()
        assert phase.rigor == SpecRigor.HIGH


class TestSpecPhaseUserStoryPrompt:
    """Test user story prompt."""

    def test_prompt_for_user_story_success(self, tmp_path: Path) -> None:
        """測試成功提示用戶輸入需求"""
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        user_requirement = "身為用戶，我想要新增登入功能，以便管理個人資料"

        with patch.object(phase.display, 'get_multiline_input', return_value=user_requirement):
            with patch('builtins.print'):
                phase._prompt_for_user_story()

        # Check spec file was created
        assert spec_file.exists()
        content = spec_file.read_text()
        assert "# 初始需求" in content
        assert user_requirement in content

    def test_prompt_for_user_story_empty_raises_error(self, tmp_path: Path) -> None:
        """測試空輸入拋出 ValueError"""
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        with patch.object(phase.display, 'get_multiline_input', return_value=""):
            with patch('builtins.print'):
                with pytest.raises(ValueError, match="未提供需求，無法繼續"):
                    phase._prompt_for_user_story()


class TestSpecPhaseGitHubMethods:
    """Test GitHub-related methods."""

    def test_create_github_issue(self, tmp_path: Path) -> None:
        """測試 _create_github_issue 呼叫"""
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.GITHUB,
            interactive=False,
        )

        with pytest.raises(NotImplementedError):
            phase._create_github_issue("test content")

    def test_update_github_issue(self, tmp_path: Path) -> None:
        """測試 _update_github_issue 呼叫"""
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.GITHUB,
            interactive=False,
            issue_id="123",
        )

        with pytest.raises(NotImplementedError):
            phase._update_github_issue("updated content")


class TestSpecPhaseHelperMethods:
    """Test helper methods."""

    def test_backup_spec(self, tmp_path: Path) -> None:
        """測試 _backup_spec 建立備份"""
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"
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
        )

        phase._backup_spec(spec_file)

        # Check backup was created
        backup_file = spec_file.parent / "spec.md.backup"
        assert backup_file.exists()
        assert backup_file.read_text() == "Original content"

    def test_display_current_spec(self, tmp_path: Path) -> None:
        """測試 _display_current_spec 顯示目前內容"""
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Current Spec\nSome requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )
        phase.pm_agent = "Roger"
        phase.iteration = 2

        with patch('builtins.print') as mock_print:
            phase._display_current_spec()

        # Verify print was called
        assert mock_print.called

    def test_ask_user_for_clarification(self, tmp_path: Path) -> None:
        """測試 _ask_user_for_clarification"""
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        user_input = "這是我的回答"
        with patch.object(phase.display, 'get_multiline_input', return_value=user_input):
            result = phase._ask_user_for_clarification()

        assert result == user_input


class TestSpecPhaseGetMethods:
    """Test getter methods."""

    def test_get_non_technical_guidelines(self, tmp_path: Path) -> None:
        """測試 _get_non_technical_guidelines 返回指南"""
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        guidelines = phase._get_non_technical_guidelines()
        assert "不可涉及技術細節" in guidelines
        assert "不要提及實作方式" in guidelines

    def test_get_status_code_prompt(self, tmp_path: Path) -> None:
        """測試 _get_status_code_prompt 返回 prompt"""
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        prompt = phase._get_status_code_prompt()
        assert "CAFE_CONFIRMED" in prompt or "CONFIRMED" in prompt

    def test_get_rigor_guidelines(self, tmp_path: Path) -> None:
        """測試 _get_rigor_guidelines 對不同 rigor levels"""
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)

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
        )
        guidelines = phase._get_rigor_guidelines()
        assert "快速開發" in guidelines or "快速" in guidelines.lower()

        # Test MEDIUM
        phase.rigor = SpecRigor.MEDIUM
        guidelines = phase._get_rigor_guidelines()
        assert "平衡" in guidelines

        # Test HIGH
        phase.rigor = SpecRigor.HIGH
        guidelines = phase._get_rigor_guidelines()
        assert "詳細" in guidelines or "精確" in guidelines
