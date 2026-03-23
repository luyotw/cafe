"""Tests for _ask_user_for_review_decision() with chat option."""

from unittest.mock import MagicMock, call, patch

import pytest

from cafe.core.phase import Phase


class ConcretePhase(Phase):
    """Minimal concrete Phase subclass for testing."""

    def execute(self):
        pass


def _make_phase(issue_name: str = "test-issue", interactive: bool = True) -> ConcretePhase:
    """Create a ConcretePhase instance with issue_name set."""
    phase = ConcretePhase.__new__(ConcretePhase)
    phase.interactive = interactive
    phase.issue_name = issue_name
    return phase


class TestAskUserForReviewDecisionChat:
    """Tests for chat option in _ask_user_for_review_decision()."""

    @patch("cafe.core.phase.launch_chat_session")
    @patch("cafe.core.phase.prompt_list")
    def test_confirm_choice_returns_confirm(self, mock_prompt_list, mock_launch_chat):
        """Test that selecting Confirm returns 'confirm'."""
        mock_prompt_list.return_value = "c"
        phase = _make_phase()

        result = phase._ask_user_for_review_decision("plan", agent_name="David", role="developer")

        assert result == "confirm"
        mock_launch_chat.assert_not_called()

    @patch("cafe.core.phase.prompt_multiline")
    @patch("cafe.core.phase.launch_chat_session")
    @patch("cafe.core.phase.prompt_list")
    def test_modify_choice_returns_modification_text(
        self, mock_prompt_list, mock_launch_chat, mock_multiline
    ):
        """Test that selecting Modify returns user's modification text."""
        mock_prompt_list.return_value = "m"
        mock_multiline.return_value = "Please add error handling"
        phase = _make_phase()

        result = phase._ask_user_for_review_decision("plan", agent_name="David", role="developer")

        assert result == "Please add error handling"
        mock_launch_chat.assert_not_called()

    @patch("cafe.core.phase.prompt_list")
    def test_choices_include_chat_option(self, mock_prompt_list):
        """Test that the choices list includes a chat option and a separator."""
        mock_prompt_list.return_value = "c"
        phase = _make_phase(issue_name="test-issue")

        phase._ask_user_for_review_decision("plan", agent_name="David", role="developer")

        args, kwargs = mock_prompt_list.call_args
        choices = args[1]

        # Find the chat choice
        chat_choices = [c for c in choices if isinstance(c, dict) and c.get("value") == "chat"]
        assert len(chat_choices) == 1
        assert "David" in chat_choices[0]["name"]

    @patch("cafe.core.phase.prompt_multiline")
    @patch("cafe.core.phase.launch_chat_session")
    @patch("cafe.core.phase.prompt_list")
    def test_selecting_chat_launches_session_then_re_prompts(
        self, mock_prompt_list, mock_launch_chat, mock_multiline
    ):
        """Test that selecting chat launches session and then re-displays prompt."""
        # First call: select chat; Second call: confirm
        mock_prompt_list.side_effect = ["chat", "c"]
        phase = _make_phase(issue_name="my-issue")

        result = phase._ask_user_for_review_decision("plan", agent_name="David", role="developer")

        assert result == "confirm"
        mock_launch_chat.assert_called_once_with("developer", "my-issue")
        assert mock_prompt_list.call_count == 2

    @patch("cafe.core.phase.prompt_multiline")
    @patch("cafe.core.phase.launch_chat_session")
    @patch("cafe.core.phase.prompt_list")
    def test_selecting_chat_multiple_times_then_confirm(
        self, mock_prompt_list, mock_launch_chat, mock_multiline
    ):
        """Test that user can chat multiple times before confirming."""
        mock_prompt_list.side_effect = ["chat", "chat", "c"]
        phase = _make_phase(issue_name="my-issue")

        result = phase._ask_user_for_review_decision("plan", agent_name="David", role="developer")

        assert result == "confirm"
        assert mock_launch_chat.call_count == 2
        assert mock_prompt_list.call_count == 3

    @patch("cafe.core.phase.prompt_list")
    def test_default_role_is_developer(self, mock_prompt_list):
        """Test that role defaults to 'developer' when not specified."""
        mock_prompt_list.return_value = "c"
        phase = _make_phase(issue_name="my-issue")

        # Call without role parameter
        result = phase._ask_user_for_review_decision("plan", agent_name="David")

        assert result == "confirm"

    @patch("cafe.core.phase.prompt_multiline")
    @patch("cafe.core.phase.launch_chat_session")
    @patch("cafe.core.phase.prompt_list")
    def test_re_displays_output_file_after_chat(
        self, mock_prompt_list, mock_launch_chat, mock_multiline, tmp_path
    ):
        """Test that output file content is printed after returning from chat."""
        output_file = tmp_path / "output.md"
        output_file.write_text("## Spec Content\nSome agent output here.")

        mock_prompt_list.side_effect = ["chat", "c"]
        phase = _make_phase(issue_name="my-issue")

        with patch("builtins.print") as mock_print:
            result = phase._ask_user_for_review_decision(
                "plan", agent_name="David", role="developer", output_file=output_file
            )

        assert result == "confirm"
        # Verify output file content was printed after chat returned
        printed_text = " ".join(str(a) for call in mock_print.call_args_list for a in call[0])
        assert "Some agent output here" in printed_text

    @patch("cafe.core.phase.prompt_list")
    def test_no_output_file_no_error(self, mock_prompt_list):
        """Test that skipping output_file works fine (no error)."""
        mock_prompt_list.side_effect = ["chat", "c"]
        phase = _make_phase(issue_name="my-issue")

        with patch("cafe.core.phase.launch_chat_session"):
            # Should not raise even without output_file
            result = phase._ask_user_for_review_decision("plan", agent_name="David", role="developer")

        assert result == "confirm"

    @patch("cafe.core.phase.prompt_multiline")
    @patch("cafe.core.phase.launch_chat_session")
    @patch("cafe.core.phase.prompt_list")
    def test_re_displays_display_content_after_chat(
        self, mock_prompt_list, mock_launch_chat, mock_multiline
    ):
        """Test that display_content string is printed after returning from chat."""
        mock_prompt_list.side_effect = ["chat", "c"]
        phase = _make_phase(issue_name="my-issue")

        with patch("builtins.print") as mock_print:
            result = phase._ask_user_for_review_decision(
                "code changes",
                agent_name="David",
                role="developer",
                display_content="diff --git a/foo.py b/foo.py\n+new line",
            )

        assert result == "confirm"
        printed_text = " ".join(str(a) for call in mock_print.call_args_list for a in call[0])
        assert "diff --git a/foo.py b/foo.py" in printed_text
