"""Tests for interactive_qa_flow() summary confirmation with chat option."""

from unittest.mock import MagicMock, patch

import pytest

from cafe.core.questions_schema import Question
from cafe.ui.interactive_qa import interactive_qa_flow


def _make_questions(count=2):
    """Build a list of test questions."""
    return [
        Question(
            id=str(i + 1),
            title=f"Question {i + 1}?",
            options=[f"Option {i + 1}A", f"Option {i + 1}B"],
        )
        for i in range(count)
    ]


class TestInteractiveQaFlowChat:
    """Tests for chat option in the summary confirmation of interactive_qa_flow()."""

    @patch("cafe.ui.interactive_qa.launch_chat_session")
    @patch("cafe.ui.interactive_qa.inquirer")
    def test_chat_option_present_in_summary_confirmation(self, mock_inquirer, mock_launch_chat):
        """Test that chat option appears in the summary confirmation prompt."""
        questions = _make_questions(1)

        mock_select = MagicMock()
        # Q1 answer, then confirm
        mock_select.execute = MagicMock(side_effect=["Option 1A", "Confirm and continue"])
        mock_inquirer.select.return_value = mock_select

        interactive_qa_flow(questions, role="pm", issue_name="test-issue")

        # Check that the summary confirmation call includes a chat option
        # The last select call for summary should have more than 2 choices
        summary_call_args = mock_inquirer.select.call_args_list[-1]
        choices = summary_call_args[1]["choices"]

        chat_choices = [c for c in choices if isinstance(c, dict) and "Chat" in c.get("name", "")]
        assert len(chat_choices) == 1

    @patch("cafe.ui.interactive_qa.launch_chat_session")
    @patch("cafe.ui.interactive_qa.inquirer")
    def test_selecting_chat_in_summary_launches_session_and_re_prompts(
        self, mock_inquirer, mock_launch_chat
    ):
        """Test that selecting chat in summary launches session and re-displays summary."""
        questions = _make_questions(1)

        mock_select = MagicMock()
        # Q1 answer, then chat, then confirm
        mock_select.execute = MagicMock(
            side_effect=["Option 1A", "chat", "Confirm and continue"]
        )
        mock_inquirer.select.return_value = mock_select

        result = interactive_qa_flow(questions, role="pm", issue_name="my-issue")

        mock_launch_chat.assert_called_once_with("pm", "my-issue")
        assert "A1: Option 1A" in result

    @patch("cafe.ui.interactive_qa.launch_chat_session")
    @patch("cafe.ui.interactive_qa.inquirer")
    def test_selecting_chat_multiple_times_in_summary(self, mock_inquirer, mock_launch_chat):
        """Test that user can chat multiple times in summary before confirming."""
        questions = _make_questions(1)

        mock_select = MagicMock()
        # Q1 answer, then chat twice, then confirm
        mock_select.execute = MagicMock(
            side_effect=["Option 1A", "chat", "chat", "Confirm and continue"]
        )
        mock_inquirer.select.return_value = mock_select

        result = interactive_qa_flow(questions, role="developer", issue_name="my-issue")

        assert mock_launch_chat.call_count == 2
        assert "A1: Option 1A" in result

    @patch("cafe.ui.interactive_qa.launch_chat_session")
    @patch("cafe.ui.interactive_qa.inquirer")
    def test_no_chat_option_when_role_not_provided(self, mock_inquirer, mock_launch_chat):
        """Test backward compatibility: no chat option when role/issue_name not given."""
        questions = _make_questions(1)

        mock_select = MagicMock()
        mock_select.execute = MagicMock(side_effect=["Option 1A", "Confirm and continue"])
        mock_inquirer.select.return_value = mock_select

        # Call without role and issue_name (backward compatibility)
        result = interactive_qa_flow(questions)

        mock_launch_chat.assert_not_called()
        # Summary choices should NOT contain chat
        summary_call_args = mock_inquirer.select.call_args_list[-1]
        choices = summary_call_args[1]["choices"]
        chat_choices = [c for c in choices if isinstance(c, dict) and "Chat" in c.get("name", "")]
        assert len(chat_choices) == 0

    @patch("cafe.ui.interactive_qa.launch_chat_session")
    @patch("cafe.ui.interactive_qa.inquirer")
    def test_normal_flow_unaffected_with_role(self, mock_inquirer, mock_launch_chat):
        """Test that normal confirm flow still works when role is provided."""
        questions = _make_questions(2)

        mock_select = MagicMock()
        # Q1, Q2, confirm
        mock_select.execute = MagicMock(
            side_effect=["Option 1A", "Option 2B", "Confirm and continue"]
        )
        mock_inquirer.select.return_value = mock_select

        result = interactive_qa_flow(questions, role="developer", issue_name="test-issue")

        assert "Q1: Question 1?" in result
        assert "A1: Option 1A" in result
        assert "Q2: Question 2?" in result
        assert "A2: Option 2B" in result
        mock_launch_chat.assert_not_called()
