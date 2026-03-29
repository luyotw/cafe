"""Tests for interactive menu system."""

import sys
import subprocess
from enum import Enum
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from cafe.ui.menu import MenuState, MenuStateDetector, InteractiveMenu


# ---------------------------------------------------------------------------
# MenuStateDetector tests
# ---------------------------------------------------------------------------

class TestMenuStateDetector:
    """Tests for MenuStateDetector state detection."""

    def test_not_initialized_when_no_config(self, tmp_path):
        """測試沒有 config.yaml 時回傳 NOT_INITIALIZED 狀態"""
        detector = MenuStateDetector()
        with patch("cafe.ui.menu.Path") as mock_path_cls:
            # .cafe/config.yaml does not exist
            mock_config = MagicMock()
            mock_config.exists.return_value = False
            mock_path_cls.return_value = mock_config

            with patch.object(detector, "_config_exists", return_value=False):
                state = detector.detect_state()

        assert state == MenuState.NOT_INITIALIZED

    def test_not_initialized_real_fs(self, tmp_path, monkeypatch):
        """測試在沒有 config.yaml 的目錄中偵測到 NOT_INITIALIZED"""
        monkeypatch.chdir(tmp_path)
        detector = MenuStateDetector()
        state = detector.detect_state()
        assert state == MenuState.NOT_INITIALIZED

    def test_no_active_issue_when_config_exists_but_branch_not_initialized(self, tmp_path, monkeypatch):
        """測試 config 存在但目前分支未初始化時回傳 NO_ACTIVE_ISSUE"""
        monkeypatch.chdir(tmp_path)
        # Create .cafe/config.yaml
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        (cafe_dir / "config.yaml").write_text("agents: {}")

        detector = MenuStateDetector()
        with (
            patch("cafe.ui.menu.GitOperations") as mock_git_cls,
            patch("cafe.ui.menu.is_branch_initialized") as mock_is_init,
        ):
            mock_git = MagicMock()
            mock_git.get_current_branch.return_value = "main"
            mock_git_cls.return_value = mock_git
            mock_is_init.return_value = False

            state = detector.detect_state()

        assert state == MenuState.NO_ACTIVE_ISSUE

    def test_active_issue_when_config_and_branch_initialized(self, tmp_path, monkeypatch):
        """測試 config 存在且分支已初始化時回傳 ACTIVE_ISSUE"""
        monkeypatch.chdir(tmp_path)
        # Create .cafe/config.yaml
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        (cafe_dir / "config.yaml").write_text("agents: {}")

        detector = MenuStateDetector()
        with (
            patch("cafe.ui.menu.GitOperations") as mock_git_cls,
            patch("cafe.ui.menu.is_branch_initialized") as mock_is_init,
        ):
            mock_git = MagicMock()
            mock_git.get_current_branch.return_value = "feature/my-issue"
            mock_git_cls.return_value = mock_git
            mock_is_init.return_value = True

            state = detector.detect_state()

        assert state == MenuState.ACTIVE_ISSUE
        mock_is_init.assert_called_once_with("feature/my-issue")

    def test_no_active_issue_when_issues_dir_missing(self, tmp_path, monkeypatch):
        """測試 .cafe/issues 目錄不存在時回傳 NO_ACTIVE_ISSUE"""
        monkeypatch.chdir(tmp_path)
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        (cafe_dir / "config.yaml").write_text("agents: {}")
        # No .cafe/issues directory

        detector = MenuStateDetector()
        with (
            patch("cafe.ui.menu.GitOperations") as mock_git_cls,
            patch("cafe.ui.menu.is_branch_initialized") as mock_is_init,
        ):
            mock_git = MagicMock()
            mock_git.get_current_branch.return_value = "main"
            mock_git_cls.return_value = mock_git
            mock_is_init.return_value = False

            state = detector.detect_state()

        assert state == MenuState.NO_ACTIVE_ISSUE

    def test_no_active_issue_when_git_fails(self, tmp_path, monkeypatch):
        """測試 git 指令失敗時回傳 NO_ACTIVE_ISSUE"""
        monkeypatch.chdir(tmp_path)
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        (cafe_dir / "config.yaml").write_text("agents: {}")

        detector = MenuStateDetector()
        with patch("cafe.ui.menu.GitOperations") as mock_git_cls:
            mock_git = MagicMock()
            mock_git.get_current_branch.side_effect = Exception("not a git repo")
            mock_git_cls.return_value = mock_git

            state = detector.detect_state()

        assert state == MenuState.NO_ACTIVE_ISSUE

    def test_get_current_issue_name(self, tmp_path, monkeypatch):
        """測試取得目前 issue 名稱"""
        monkeypatch.chdir(tmp_path)
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        (cafe_dir / "config.yaml").write_text("agents: {}")

        detector = MenuStateDetector()
        with (
            patch("cafe.ui.menu.GitOperations") as mock_git_cls,
            patch("cafe.ui.menu.is_branch_initialized") as mock_is_init,
        ):
            mock_git = MagicMock()
            mock_git.get_current_branch.return_value = "feature/chat-web-ui"
            mock_git_cls.return_value = mock_git
            mock_is_init.return_value = True

            state = detector.detect_state()
            issue_name = detector.get_current_issue_name()

        assert state == MenuState.ACTIVE_ISSUE
        assert issue_name == "feature/chat-web-ui"

    def test_get_current_issue_name_returns_none_when_no_active_issue(self, tmp_path, monkeypatch):
        """測試沒有活躍 issue 時 get_current_issue_name 回傳 None"""
        monkeypatch.chdir(tmp_path)
        detector = MenuStateDetector()
        assert detector.get_current_issue_name() is None


# ---------------------------------------------------------------------------
# InteractiveMenu main menu tests
# ---------------------------------------------------------------------------

class TestInteractiveMenuMainMenu:
    """Tests for InteractiveMenu main menu display."""

    def test_main_menu_shows_correct_options_for_no_active_issue(self):
        """測試 NO_ACTIVE_ISSUE 狀態時顯示正確的主選單選項"""
        detector = MagicMock(spec=MenuStateDetector)
        detector.detect_state.return_value = MenuState.NO_ACTIVE_ISSUE
        detector.get_current_issue_name.return_value = None

        menu = InteractiveMenu(state_detector=detector)
        choices = menu._build_main_menu_choices(MenuState.NO_ACTIVE_ISSUE)

        values = [c["value"] for c in choices]
        assert "prepare" in values
        assert "ls" in values
        assert "restore" in values
        assert "settings" in values
        assert "exit" in values
        # Init project should NOT be shown in NO_ACTIVE_ISSUE state
        assert "init" not in values

    def test_main_menu_shows_init_option_for_not_initialized(self):
        """測試 NOT_INITIALIZED 狀態時顯示 Init project 選項"""
        detector = MagicMock(spec=MenuStateDetector)
        detector.detect_state.return_value = MenuState.NOT_INITIALIZED
        detector.get_current_issue_name.return_value = None

        menu = InteractiveMenu(state_detector=detector)
        choices = menu._build_main_menu_choices(MenuState.NOT_INITIALIZED)

        values = [c["value"] for c in choices]
        assert "init" in values
        assert "exit" in values

    def test_main_menu_does_not_show_init_for_no_active_issue(self):
        """測試 NO_ACTIVE_ISSUE 狀態時不顯示 Init project 選項"""
        detector = MagicMock(spec=MenuStateDetector)
        menu = InteractiveMenu(state_detector=detector)
        choices = menu._build_main_menu_choices(MenuState.NO_ACTIVE_ISSUE)

        values = [c["value"] for c in choices]
        assert "init" not in values

    def test_exit_terminates_menu_loop(self):
        """測試選擇 Exit 時結束選單迴圈"""
        detector = MagicMock(spec=MenuStateDetector)
        detector.detect_state.return_value = MenuState.NO_ACTIVE_ISSUE
        detector.get_current_issue_name.return_value = None

        menu = InteractiveMenu(state_detector=detector)

        with patch("cafe.ui.menu.prompt_list") as mock_prompt:
            mock_prompt.return_value = "exit"
            menu.run()

        # Should have called detect_state and prompt_list, then exited
        detector.detect_state.assert_called()
        mock_prompt.assert_called()

    def test_keyboard_interrupt_exits_cleanly(self):
        """測試 Ctrl+C 能夠乾淨退出選單"""
        detector = MagicMock(spec=MenuStateDetector)
        detector.detect_state.return_value = MenuState.NO_ACTIVE_ISSUE
        detector.get_current_issue_name.return_value = None

        menu = InteractiveMenu(state_detector=detector)

        with patch("cafe.ui.menu.prompt_list") as mock_prompt:
            mock_prompt.side_effect = KeyboardInterrupt
            # Should not raise exception
            menu.run()

    def test_menu_choices_include_descriptions(self):
        """測試選單項目包含描述文字"""
        detector = MagicMock(spec=MenuStateDetector)
        menu = InteractiveMenu(state_detector=detector)
        choices = menu._build_main_menu_choices(MenuState.NO_ACTIVE_ISSUE)

        for choice in choices:
            assert "name" in choice
            assert "value" in choice
            # Name should be non-empty
            assert len(choice["name"]) > 0


# ---------------------------------------------------------------------------
# InteractiveMenu issue menu tests
# ---------------------------------------------------------------------------

class TestInteractiveMenuIssueMenu:
    """Tests for InteractiveMenu issue menu display."""

    def test_issue_menu_shows_correct_options(self):
        """測試 ACTIVE_ISSUE 狀態時顯示正確的 issue 選單選項"""
        detector = MagicMock(spec=MenuStateDetector)
        detector.detect_state.return_value = MenuState.ACTIVE_ISSUE
        detector.get_current_issue_name.return_value = "feature/my-issue"

        menu = InteractiveMenu(state_detector=detector)
        choices = menu._build_issue_menu_choices()

        values = [c["value"] for c in choices]
        assert "make" in values
        assert "chat" in values
        assert "summary" in values
        assert "reset" in values
        assert "close" in values
        assert "settings" in values
        assert "exit" in values
        # Show output is removed as it's not needed in practice
        assert "show" not in values

    def test_issue_menu_dispatches_make_command(self):
        """測試選擇 Continue workflow 時執行 cafe make"""
        detector = MagicMock(spec=MenuStateDetector)
        detector.detect_state.side_effect = [
            MenuState.ACTIVE_ISSUE,
            MenuState.ACTIVE_ISSUE,  # after command returns
            MenuState.ACTIVE_ISSUE,  # loop continues
        ]
        detector.get_current_issue_name.return_value = "feature/my-issue"

        menu = InteractiveMenu(state_detector=detector)

        with (
            patch("cafe.ui.menu.prompt_list") as mock_prompt,
            patch("cafe.ui.menu.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            # First call selects "make", second call selects "exit"
            mock_prompt.side_effect = ["make", "exit"]
            menu.run()

        mock_run.assert_called_once()
        call_cmd = mock_run.call_args[0][0]
        assert "make" in call_cmd

    def test_issue_menu_dispatches_summary_command(self):
        """測試選擇 Show status 時執行 cafe summary"""
        detector = MagicMock(spec=MenuStateDetector)
        detector.detect_state.side_effect = [
            MenuState.ACTIVE_ISSUE,
            MenuState.ACTIVE_ISSUE,
        ]
        detector.get_current_issue_name.return_value = "my-issue"

        menu = InteractiveMenu(state_detector=detector)

        with (
            patch("cafe.ui.menu.prompt_list") as mock_prompt,
            patch("cafe.ui.menu.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            mock_prompt.side_effect = ["summary", "exit"]
            menu.run()

        mock_run.assert_called_once()
        call_cmd = mock_run.call_args[0][0]
        assert "summary" in call_cmd

    def test_issue_menu_dispatches_close_command(self):
        """測試選擇 Close issue 時執行 cafe close"""
        detector = MagicMock(spec=MenuStateDetector)
        # After close, state changes to NO_ACTIVE_ISSUE
        detector.detect_state.side_effect = [
            MenuState.ACTIVE_ISSUE,
            MenuState.NO_ACTIVE_ISSUE,
        ]
        detector.get_current_issue_name.return_value = "my-issue"

        menu = InteractiveMenu(state_detector=detector)

        with (
            patch("cafe.ui.menu.prompt_list") as mock_prompt,
            patch("cafe.ui.menu.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            mock_prompt.side_effect = ["close", "exit"]
            menu.run()

        mock_run.assert_called_once()
        call_cmd = mock_run.call_args[0][0]
        assert "close" in call_cmd

    def test_close_exits_menu_immediately(self):
        """測試 close 指令執行後直接退出，不回主選單"""
        detector = MagicMock(spec=MenuStateDetector)
        detector.detect_state.return_value = MenuState.ACTIVE_ISSUE
        detector.get_current_issue_name.return_value = "my-issue"

        menu = InteractiveMenu(state_detector=detector)

        with (
            patch("cafe.ui.menu.prompt_list") as mock_prompt,
            patch("cafe.ui.menu.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            mock_prompt.side_effect = ["close"]
            menu.run()

        # detect_state only called once — no re-entry after close
        assert detector.detect_state.call_count == 1
        # close command was executed
        call_cmd = mock_run.call_args[0][0]
        assert "close" in call_cmd


# ---------------------------------------------------------------------------
# InteractiveMenu settings submenu tests
# ---------------------------------------------------------------------------

class TestInteractiveMenuSettingsSubmenu:
    """Tests for settings submenu and back navigation."""

    def test_settings_menu_shows_all_options(self):
        """測試設定子選單顯示所有選項"""
        detector = MagicMock(spec=MenuStateDetector)
        menu = InteractiveMenu(state_detector=detector)
        choices = menu._build_settings_menu_choices()

        values = [c["value"] for c in choices]
        assert "setup" in values
        assert "config" in values
        assert "config_edit" in values
        assert "role_edit" in values
        assert "template_edit" in values
        assert "back" in values

    def test_settings_back_from_main_menu_returns_to_main(self):
        """測試從主選單進入設定後，Back 回到主選單"""
        detector = MagicMock(spec=MenuStateDetector)
        detector.detect_state.return_value = MenuState.NO_ACTIVE_ISSUE
        detector.get_current_issue_name.return_value = None

        menu = InteractiveMenu(state_detector=detector)

        prompt_calls = []

        def prompt_side_effect(msg, choices, **kwargs):
            values = [c["value"] for c in choices]
            if "settings" in values and "back" not in values:
                # Main menu
                prompt_calls.append("main")
                if len(prompt_calls) == 1:
                    return "settings"
                else:
                    return "exit"
            elif "back" in values:
                # Settings menu
                prompt_calls.append("settings")
                return "back"
            return "exit"

        with (
            patch("cafe.ui.menu.prompt_list", side_effect=prompt_side_effect),
        ):
            menu.run()

        assert "main" in prompt_calls
        assert "settings" in prompt_calls
        # After back from settings, should be in main again
        assert prompt_calls.count("main") >= 2

    def test_settings_back_from_issue_menu_returns_to_issue(self):
        """測試從 issue 選單進入設定後，Back 回到 issue 選單"""
        detector = MagicMock(spec=MenuStateDetector)
        detector.detect_state.return_value = MenuState.ACTIVE_ISSUE
        detector.get_current_issue_name.return_value = "my-issue"

        menu = InteractiveMenu(state_detector=detector)

        prompt_calls = []

        def prompt_side_effect(msg, choices, **kwargs):
            values = [c["value"] for c in choices]
            if "make" in values:
                # Issue menu
                prompt_calls.append("issue")
                if len([c for c in prompt_calls if c == "issue"]) == 1:
                    return "settings"
                else:
                    return "exit"
            elif "back" in values and "make" not in values:
                # Settings menu
                prompt_calls.append("settings")
                return "back"
            return "exit"

        with patch("cafe.ui.menu.prompt_list", side_effect=prompt_side_effect):
            menu.run()

        assert "issue" in prompt_calls
        assert "settings" in prompt_calls
        assert prompt_calls.count("issue") >= 2

    def test_settings_dispatches_setup_command(self):
        """測試選擇 Agent setup 時執行 cafe setup"""
        detector = MagicMock(spec=MenuStateDetector)
        detector.detect_state.return_value = MenuState.NO_ACTIVE_ISSUE
        detector.get_current_issue_name.return_value = None

        menu = InteractiveMenu(state_detector=detector)

        with (
            patch("cafe.ui.menu.prompt_list") as mock_prompt,
            patch("cafe.ui.menu.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            # main → settings → setup → back → exit
            mock_prompt.side_effect = ["settings", "setup", "back", "exit"]
            menu.run()

        # setup command should have been called
        called_cmds = [call[0][0] for call in mock_run.call_args_list]
        assert any("setup" in cmd for cmd in called_cmds)

    def test_settings_dispatches_config_command(self):
        """測試選擇 View config 時執行 cafe config"""
        detector = MagicMock(spec=MenuStateDetector)
        detector.detect_state.return_value = MenuState.NO_ACTIVE_ISSUE
        detector.get_current_issue_name.return_value = None

        menu = InteractiveMenu(state_detector=detector)

        with (
            patch("cafe.ui.menu.prompt_list") as mock_prompt,
            patch("cafe.ui.menu.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            mock_prompt.side_effect = ["settings", "config", "back", "exit"]
            menu.run()

        called_cmds = [call[0][0] for call in mock_run.call_args_list]
        assert any("config" in cmd for cmd in called_cmds)


# ---------------------------------------------------------------------------
# Chat with agent tests
# ---------------------------------------------------------------------------

class TestChatWithAgent:
    """Tests for context-aware chat with agent feature."""

    def test_get_available_agents_returns_all_configured_agents(self, tmp_path, monkeypatch):
        """測試無論目前 phase 為何，回傳所有已設定的 agents"""
        monkeypatch.chdir(tmp_path)
        issue_dir = tmp_path / ".cafe" / "issues" / "my-issue"
        (issue_dir / "spec").mkdir(parents=True)

        detector = MagicMock(spec=MenuStateDetector)
        detector.get_current_issue_name.return_value = "my-issue"

        mock_config = MagicMock()
        mock_config.get.side_effect = lambda key, default=None: {
            "agents.pm.name": "Roger",
            "agents.developer.name": "Nick",
            "agents.reviewer.name": "Richard",
        }.get(key, default)

        menu = InteractiveMenu(state_detector=detector)

        with patch("cafe.ui.menu.ConfigManager") as mock_config_cls:
            mock_config_cls.return_value = mock_config
            agents = menu._get_available_agents()

        roles = [a["role"] for a in agents]
        assert "pm" in roles
        assert "developer" in roles
        assert "reviewer" in roles

    def test_get_available_agents_returns_all_in_develop_phase(self, tmp_path, monkeypatch):
        """測試 develop 階段時仍回傳所有已設定的 agents"""
        monkeypatch.chdir(tmp_path)
        issue_dir = tmp_path / ".cafe" / "issues" / "my-issue"
        (issue_dir / "spec").mkdir(parents=True)
        (issue_dir / "plan").mkdir(parents=True)
        (issue_dir / "develop").mkdir(parents=True)

        detector = MagicMock(spec=MenuStateDetector)
        detector.get_current_issue_name.return_value = "my-issue"

        mock_config = MagicMock()
        mock_config.get.side_effect = lambda key, default=None: {
            "agents.pm.name": "Roger",
            "agents.developer.name": "Nick",
            "agents.reviewer.name": "Richard",
        }.get(key, default)

        menu = InteractiveMenu(state_detector=detector)

        with patch("cafe.ui.menu.ConfigManager") as mock_config_cls:
            mock_config_cls.return_value = mock_config
            agents = menu._get_available_agents()

        roles = [a["role"] for a in agents]
        assert "pm" in roles
        assert "developer" in roles
        assert "reviewer" in roles

    def test_get_available_agents_returns_all_in_review_phase(self, tmp_path, monkeypatch):
        """測試 review 階段時仍回傳所有已設定的 agents"""
        monkeypatch.chdir(tmp_path)
        issue_dir = tmp_path / ".cafe" / "issues" / "my-issue"
        (issue_dir / "spec").mkdir(parents=True)
        (issue_dir / "plan").mkdir(parents=True)
        (issue_dir / "develop").mkdir(parents=True)
        (issue_dir / "review").mkdir(parents=True)

        detector = MagicMock(spec=MenuStateDetector)
        detector.get_current_issue_name.return_value = "my-issue"

        mock_config = MagicMock()
        mock_config.get.side_effect = lambda key, default=None: {
            "agents.pm.name": "Roger",
            "agents.developer.name": "Nick",
            "agents.reviewer.name": "Richard",
        }.get(key, default)

        menu = InteractiveMenu(state_detector=detector)

        with patch("cafe.ui.menu.ConfigManager") as mock_config_cls:
            mock_config_cls.return_value = mock_config
            agents = menu._get_available_agents()

        roles = [a["role"] for a in agents]
        assert "pm" in roles
        assert "developer" in roles
        assert "reviewer" in roles

    def test_get_available_agents_falls_back_to_all_when_no_phase(self, tmp_path, monkeypatch):
        """測試沒有偵測到 phase 時，回傳所有已設定的 agents"""
        monkeypatch.chdir(tmp_path)
        # No phase directories

        detector = MagicMock(spec=MenuStateDetector)
        detector.get_current_issue_name.return_value = "my-issue"

        mock_config = MagicMock()
        mock_config.get.side_effect = lambda key, default=None: {
            "agents.pm.name": "Roger",
            "agents.developer.name": "Nick",
            "agents.reviewer.name": "Richard",
        }.get(key, default)

        menu = InteractiveMenu(state_detector=detector)

        with patch("cafe.ui.menu.ConfigManager") as mock_config_cls:
            mock_config_cls.return_value = mock_config
            agents = menu._get_available_agents()

        roles = [a["role"] for a in agents]
        assert "pm" in roles
        assert "developer" in roles
        assert "reviewer" in roles

    def test_chat_dispatches_chat_command(self):
        """測試選擇 Chat 後執行 cafe chat <role>"""
        detector = MagicMock(spec=MenuStateDetector)
        detector.detect_state.return_value = MenuState.ACTIVE_ISSUE
        detector.get_current_issue_name.return_value = "my-issue"

        menu = InteractiveMenu(state_detector=detector)

        mock_config = MagicMock()
        mock_config.get.side_effect = lambda key, default=None: {
            "agents.pm.name": "Roger",
            "agents.developer.name": "Nick",
            "agents.reviewer.name": "Richard",
        }.get(key, default)

        with (
            patch("cafe.ui.menu.prompt_list") as mock_prompt,
            patch("cafe.ui.menu.subprocess.run") as mock_run,
            patch("cafe.ui.menu.ConfigManager") as mock_config_cls,
        ):
            mock_config_cls.return_value = mock_config
            mock_run.return_value = MagicMock(returncode=0)
            # Select "chat" from issue menu, then select a role, then exit
            mock_prompt.side_effect = ["chat", "pm", "exit"]
            menu.run()

        called_cmds = [call[0][0] for call in mock_run.call_args_list]
        assert any("chat" in cmd for cmd in called_cmds)
