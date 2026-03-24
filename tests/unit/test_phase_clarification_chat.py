"""Tests for _ask_user_for_clarification() fallback prompt with chat option."""

from unittest.mock import MagicMock, call, patch

import pytest

from cafe.core.phase import Phase


class ConcretePhase(Phase):
    """Minimal concrete Phase subclass for testing."""

    def execute(self):
        pass


def _make_phase(issue_name: str = "test-issue") -> ConcretePhase:
    """Create a minimal Phase instance for testing."""
    phase = ConcretePhase.__new__(ConcretePhase)
    phase.interactive = True
    phase.issue_name = issue_name
    return phase


class TestAskUserForClarificationChatFallback:
    """Tests for chat option in the base class _ask_user_for_clarification() fallback."""

    @patch("cafe.core.phase.prompt_multiline")
    @patch("cafe.core.phase.prompt_list")
    def test_without_role_calls_prompt_multiline_directly(
        self, mock_prompt_list, mock_multiline
    ):
        """Test backward compat: without role, calls prompt_multiline directly (no extra select)."""
        mock_multiline.return_value = "user answer"
        phase = _make_phase()

        result = phase._ask_user_for_clarification()

        assert result == "user answer"
        mock_prompt_list.assert_not_called()

    @patch("cafe.core.phase.prompt_multiline")
    @patch("cafe.core.phase.launch_chat_session")
    @patch("cafe.core.phase.prompt_list")
    def test_with_role_shows_select_with_chat_option(
        self, mock_prompt_list, mock_launch_chat, mock_multiline
    ):
        """Test that with role, a select prompt with chat option is shown first."""
        mock_prompt_list.return_value = "answer"
        mock_multiline.return_value = "user answer text"
        phase = _make_phase()

        result = phase._ask_user_for_clarification(role="pm")

        mock_prompt_list.assert_called_once()
        args, kwargs = mock_prompt_list.call_args
        choices = args[1]
        chat_choices = [c for c in choices if isinstance(c, dict) and c.get("value") == "chat"]
        assert len(chat_choices) == 1

    @patch("cafe.core.phase.prompt_multiline")
    @patch("cafe.core.phase.launch_chat_session")
    @patch("cafe.core.phase.prompt_list")
    def test_selecting_answer_proceeds_to_multiline(
        self, mock_prompt_list, mock_launch_chat, mock_multiline
    ):
        """Test that selecting 'answer' shows the multiline prompt."""
        mock_prompt_list.return_value = "answer"
        mock_multiline.return_value = "user's detailed answer"
        phase = _make_phase()

        result = phase._ask_user_for_clarification(role="developer")

        assert result == "user's detailed answer"
        mock_launch_chat.assert_not_called()

    @patch("cafe.core.phase.prompt_multiline")
    @patch("cafe.core.phase.launch_chat_session")
    @patch("cafe.core.phase.prompt_list")
    def test_selecting_chat_launches_session_then_re_prompts(
        self, mock_prompt_list, mock_launch_chat, mock_multiline
    ):
        """Test that selecting chat launches session and re-displays the prompt."""
        mock_prompt_list.side_effect = ["chat", "answer"]
        mock_multiline.return_value = "user answer"
        phase = _make_phase(issue_name="my-issue")

        result = phase._ask_user_for_clarification(role="pm")

        assert result == "user answer"
        mock_launch_chat.assert_called_once_with("pm", "my-issue")
        assert mock_prompt_list.call_count == 2

    @patch("cafe.core.phase.prompt_multiline")
    @patch("cafe.core.phase.launch_chat_session")
    @patch("cafe.core.phase.prompt_list")
    def test_selecting_chat_multiple_times(
        self, mock_prompt_list, mock_launch_chat, mock_multiline
    ):
        """Test that user can chat multiple times before answering."""
        mock_prompt_list.side_effect = ["chat", "chat", "answer"]
        mock_multiline.return_value = "user answer"
        phase = _make_phase(issue_name="my-issue")

        result = phase._ask_user_for_clarification(role="developer")

        assert result == "user answer"
        assert mock_launch_chat.call_count == 2
        assert mock_prompt_list.call_count == 3

    @patch("cafe.core.phase.prompt_multiline")
    @patch("cafe.core.phase.launch_chat_session")
    @patch("cafe.core.phase.prompt_list")
    def test_chat_label_uses_agent_name_not_role(
        self, mock_prompt_list, mock_launch_chat, mock_multiline
    ):
        """Test that chat option label shows agent_name (e.g. 'Roger') not role (e.g. 'pm')."""
        mock_prompt_list.return_value = "answer"
        mock_multiline.return_value = "answer text"
        phase = _make_phase()

        phase._ask_user_for_clarification(role="pm", agent_name="Roger")

        args, kwargs = mock_prompt_list.call_args
        choices = args[1]
        chat_choice = next(c for c in choices if isinstance(c, dict) and c.get("value") == "chat")
        assert "Roger" in chat_choice["name"]
        assert "pm" not in chat_choice["name"]
