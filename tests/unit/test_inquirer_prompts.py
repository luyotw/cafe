"""測試 inquirer_prompts wrapper 函式"""

from unittest.mock import MagicMock, patch

import pytest

from cafe.ui.inquirer_prompts import prompt_confirm, prompt_list, prompt_text


class TestPromptList:
    """測試 prompt_list 函式"""

    @patch("cafe.ui.inquirer_prompts.inquirer.select")
    def test_prompt_list_calls_inquirer_select(self, mock_select):
        """測試 prompt_list 正確呼叫 inquirer.select"""
        mock_instance = MagicMock()
        mock_instance.execute.return_value = "choice1"
        mock_select.return_value = mock_instance

        result = prompt_list("Select option", ["choice1", "choice2"])

        assert result == "choice1"
        mock_select.assert_called_once_with(
            message="Select option",
            choices=["choice1", "choice2"],
        )
        mock_instance.execute.assert_called_once()

    @patch("cafe.ui.inquirer_prompts.inquirer.select")
    def test_prompt_list_with_default(self, mock_select):
        """測試 prompt_list 支援預設值"""
        mock_instance = MagicMock()
        mock_instance.execute.return_value = "default_choice"
        mock_select.return_value = mock_instance

        result = prompt_list("Select option", ["choice1", "choice2"], default="default_choice")

        assert result == "default_choice"
        mock_select.assert_called_once_with(
            message="Select option",
            choices=["choice1", "choice2"],
            default="default_choice",
        )


class TestPromptText:
    """測試 prompt_text 函式"""

    @patch("cafe.ui.inquirer_prompts.inquirer.text")
    def test_prompt_text_calls_inquirer_text(self, mock_text):
        """測試 prompt_text 正確呼叫 inquirer.text"""
        mock_instance = MagicMock()
        mock_instance.execute.return_value = "user input"
        mock_text.return_value = mock_instance

        result = prompt_text("Enter text")

        assert result == "user input"
        mock_text.assert_called_once_with(
            message="Enter text",
            default="",
        )
        mock_instance.execute.assert_called_once()

    @patch("cafe.ui.inquirer_prompts.inquirer.text")
    def test_prompt_text_with_default(self, mock_text):
        """測試 prompt_text 支援預設值"""
        mock_instance = MagicMock()
        mock_instance.execute.return_value = "default text"
        mock_text.return_value = mock_instance

        result = prompt_text("Enter text", default="default text")

        assert result == "default text"
        mock_text.assert_called_once_with(
            message="Enter text",
            default="default text",
        )


class TestPromptConfirm:
    """測試 prompt_confirm 函式"""

    @patch("cafe.ui.inquirer_prompts.inquirer.confirm")
    def test_prompt_confirm_calls_inquirer_confirm(self, mock_confirm):
        """測試 prompt_confirm 正確呼叫 inquirer.confirm"""
        mock_instance = MagicMock()
        mock_instance.execute.return_value = True
        mock_confirm.return_value = mock_instance

        result = prompt_confirm("Continue?")

        assert result is True
        mock_confirm.assert_called_once_with(
            message="Continue?",
            default=True,
        )
        mock_instance.execute.assert_called_once()

    @patch("cafe.ui.inquirer_prompts.inquirer.confirm")
    def test_prompt_confirm_with_default_false(self, mock_confirm):
        """測試 prompt_confirm 支援預設值為 False"""
        mock_instance = MagicMock()
        mock_instance.execute.return_value = False
        mock_confirm.return_value = mock_instance

        result = prompt_confirm("Continue?", default=False)

        assert result is False
        mock_confirm.assert_called_once_with(
            message="Continue?",
            default=False,
        )
