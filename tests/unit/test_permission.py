"""Tests for Permission Handler."""

import pytest
from unittest.mock import MagicMock, patch

from aaf.core.permission import PermissionHandler, PermissionDeniedError
from aaf.core.types import PermissionRequest, PermissionAction


class TestPermissionHandlerBasics:
    """Test basic PermissionHandler functionality."""

    def test_init_permission_handler(self) -> None:
        """測試初始化 PermissionHandler"""
        handler = PermissionHandler()
        assert handler is not None

    def test_request_permission_with_tool_name_and_input(self) -> None:
        """測試使用工具名稱和輸入請求權限"""
        handler = PermissionHandler()
        request = PermissionRequest(
            tool_name="Bash",
            tool_input={"command": "git status"}
        )

        with patch.object(handler, "_prompt_user") as mock_prompt:
            mock_prompt.return_value = PermissionAction.AUTHORIZE

            action = handler.request_permission(request)

            assert action == PermissionAction.AUTHORIZE
            mock_prompt.assert_called_once_with(request)


class TestPermissionDisplay:
    """Test permission request display."""

    def test_format_permission_request_bash(self) -> None:
        """測試格式化 Bash 工具的權限請求"""
        handler = PermissionHandler()
        request = PermissionRequest(
            tool_name="Bash",
            tool_input={"command": "git status", "description": "Show git status"}
        )

        formatted = handler.format_request(request)

        assert "Bash" in formatted
        assert "git status" in formatted
        assert "Show git status" in formatted

    def test_format_permission_request_read(self) -> None:
        """測試格式化 Read 工具的權限請求"""
        handler = PermissionHandler()
        request = PermissionRequest(
            tool_name="Read",
            tool_input={"file_path": "/path/to/file.py"}
        )

        formatted = handler.format_request(request)

        assert "Read" in formatted
        assert "/path/to/file.py" in formatted

    def test_format_permission_request_write(self) -> None:
        """測試格式化 Write 工具的權限請求"""
        handler = PermissionHandler()
        request = PermissionRequest(
            tool_name="Write",
            tool_input={
                "file_path": "/path/to/new_file.py",
                "content": "print('hello')"
            }
        )

        formatted = handler.format_request(request)

        assert "Write" in formatted
        assert "/path/to/new_file.py" in formatted


class TestUserActions:
    """Test user action handling."""

    def test_authorize_action_grants_permission(self) -> None:
        """測試授權動作允許執行工具"""
        handler = PermissionHandler()
        request = PermissionRequest(
            tool_name="Bash",
            tool_input={"command": "ls"}
        )

        with patch.object(handler, "_prompt_user") as mock_prompt:
            mock_prompt.return_value = PermissionAction.AUTHORIZE

            action = handler.request_permission(request)

            assert action == PermissionAction.AUTHORIZE

    def test_dialog_action_returns_dialog(self) -> None:
        """測試對話動作回傳 DIALOG"""
        handler = PermissionHandler()
        request = PermissionRequest(
            tool_name="Bash",
            tool_input={"command": "rm -rf /"}
        )

        with patch.object(handler, "_prompt_user") as mock_prompt:
            mock_prompt.return_value = PermissionAction.DIALOG

            action = handler.request_permission(request)

            assert action == PermissionAction.DIALOG

    def test_skip_action_returns_skip(self) -> None:
        """測試跳過動作回傳 SKIP"""
        handler = PermissionHandler()
        request = PermissionRequest(
            tool_name="Bash",
            tool_input={"command": "git push"}
        )

        with patch.object(handler, "_prompt_user") as mock_prompt:
            mock_prompt.return_value = PermissionAction.SKIP

            action = handler.request_permission(request)

            assert action == PermissionAction.SKIP


class TestAutoApproveRules:
    """Test auto-approve rules."""

    def test_auto_approve_read_operations(self) -> None:
        """測試自動授權讀取操作"""
        handler = PermissionHandler(auto_approve_read=True)
        request = PermissionRequest(
            tool_name="Read",
            tool_input={"file_path": "/path/to/file.py"}
        )

        action = handler.request_permission(request)

        assert action == PermissionAction.AUTHORIZE

    def test_auto_approve_safe_git_commands(self) -> None:
        """測試自動授權安全的 git 指令"""
        handler = PermissionHandler(auto_approve_safe_git=True)
        request = PermissionRequest(
            tool_name="Bash",
            tool_input={"command": "git status"}
        )

        action = handler.request_permission(request)

        assert action == PermissionAction.AUTHORIZE

    def test_no_auto_approve_for_dangerous_commands(self) -> None:
        """測試危險指令不會被自動授權"""
        handler = PermissionHandler(auto_approve_safe_git=True)
        request = PermissionRequest(
            tool_name="Bash",
            tool_input={"command": "git push --force"}
        )

        with patch.object(handler, "_prompt_user") as mock_prompt:
            mock_prompt.return_value = PermissionAction.DIALOG

            action = handler.request_permission(request)

            # Should prompt user, not auto-approve
            mock_prompt.assert_called_once()


class TestAllowedToolsPattern:
    """Test allowed tools pattern matching."""

    def test_match_exact_tool_name(self) -> None:
        """測試精確匹配工具名稱"""
        handler = PermissionHandler(allowed_tools=["Read"])

        assert handler.is_tool_allowed("Read", {})

    def test_match_tool_with_wildcard(self) -> None:
        """測試使用萬用字元匹配工具"""
        handler = PermissionHandler(allowed_tools=["Bash(git:*)"])

        assert handler.is_tool_allowed("Bash", {"command": "git status"})
        assert not handler.is_tool_allowed("Bash", {"command": "rm -rf /"})

    def test_match_tool_with_specific_command(self) -> None:
        """測試匹配特定指令"""
        handler = PermissionHandler(allowed_tools=["Bash(git status)"])

        assert handler.is_tool_allowed("Bash", {"command": "git status"})
        assert not handler.is_tool_allowed("Bash", {"command": "git push"})

    def test_allowed_tool_skips_prompt(self) -> None:
        """測試允許的工具跳過提示直接授權"""
        handler = PermissionHandler(allowed_tools=["Read(*)"])
        request = PermissionRequest(
            tool_name="Read",
            tool_input={"file_path": "/any/file.py"}
        )

        action = handler.request_permission(request)

        assert action == PermissionAction.AUTHORIZE


class TestPermissionHistory:
    """Test permission request history."""

    def test_records_permission_request(self) -> None:
        """測試記錄權限請求"""
        handler = PermissionHandler()
        request = PermissionRequest(
            tool_name="Bash",
            tool_input={"command": "ls"}
        )

        with patch.object(handler, "_prompt_user") as mock_prompt:
            mock_prompt.return_value = PermissionAction.AUTHORIZE

            handler.request_permission(request)

            assert len(handler.history) == 1
            assert handler.history[0]["request"] == request
            assert handler.history[0]["action"] == PermissionAction.AUTHORIZE

    def test_get_permission_history(self) -> None:
        """測試取得權限歷史記錄"""
        handler = PermissionHandler()

        with patch.object(handler, "_prompt_user") as mock_prompt:
            mock_prompt.return_value = PermissionAction.AUTHORIZE

            handler.request_permission(
                PermissionRequest(tool_name="Read", tool_input={"file_path": "a.py"})
            )
            handler.request_permission(
                PermissionRequest(tool_name="Write", tool_input={"file_path": "b.py"})
            )

            history = handler.get_history()

            assert len(history) == 2
            assert history[0]["request"].tool_name == "Read"
            assert history[1]["request"].tool_name == "Write"
