"""Tests for Display."""

import pytest
from io import StringIO
from unittest.mock import MagicMock, patch

from aaf.ui.display import Display


class TestDisplayBasics:
    """Test basic Display functionality."""

    def test_init_display(self) -> None:
        """測試初始化 Display"""
        display = Display()

        assert display is not None
        assert hasattr(display, 'console')

    def test_show_phase_status_completed(self, capsys) -> None:
        """測試顯示 phase 完成狀態"""
        display = Display()

        display.show_phase_status("spec", "completed", "Requirements clarified")

        captured = capsys.readouterr()
        assert "spec" in captured.out.lower()
        assert "completed" in captured.out.lower() or "✅" in captured.out


class TestMultilineInput:
    """Test multiline input functionality."""

    def test_get_multiline_input_basic(self) -> None:
        """測試基本多行輸入"""
        display = Display()

        with patch('aaf.ui.display.pt_prompt', return_value="Line 1\nLine 2"):
            result = display.get_multiline_input("Enter text:")

        assert result == "Line 1\nLine 2"

    def test_get_multiline_input_chinese(self) -> None:
        """測試中文多行輸入"""
        display = Display()

        with patch('aaf.ui.display.pt_prompt', return_value="你好世界\n測試中文"):
            result = display.get_multiline_input("輸入文字:")

        assert result == "你好世界\n測試中文"

    def test_get_multiline_input_empty_lines(self) -> None:
        """測試包含空行的輸入"""
        display = Display()

        with patch('aaf.ui.display.pt_prompt', return_value="Line 1\n\nLine 3"):
            result = display.get_multiline_input("Enter text:")

        assert result == "Line 1\n\nLine 3"

    def test_get_multiline_input_case_insensitive_end(self) -> None:
        """測試不需要 END marker"""
        display = Display()

        with patch('aaf.ui.display.pt_prompt', return_value="Line 1"):
            result = display.get_multiline_input("Enter text:")

        assert result == "Line 1"

    def test_get_multiline_input_eof(self) -> None:
        """測試 EOF 處理"""
        display = Display()

        with patch('aaf.ui.display.pt_prompt', side_effect=EOFError()):
            try:
                result = display.get_multiline_input("Enter text:")
                assert False, "Should have raised EOFError"
            except EOFError:
                pass  # Expected


class TestAgentResponseFormatting:
    """Test agent response formatting."""

    def test_format_agent_response_basic(self) -> None:
        """測試格式化 agent 回應"""
        display = Display()

        formatted = display.format_agent_response("Roger", "This is a response")

        assert "Roger" in formatted
        assert "This is a response" in formatted

    def test_format_agent_response_with_status(self) -> None:
        """測試格式化帶狀態碼的回應"""
        display = Display()

        formatted = display.format_agent_response(
            "Roger",
            "CONFIRMED\nRequirements are clear",
            status_code="CONFIRMED"
        )

        assert "Roger" in formatted
        assert "Requirements are clear" in formatted or "CONFIRMED" in formatted
