"""Tests for _ask_user_for_no_changes_decision() with chat option."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.phases.develop_phase import DevelopPhase


def _make_develop_phase(tmp_path: Path) -> DevelopPhase:
    """Create a minimal DevelopPhase instance for testing."""
    issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
    issue_dir.mkdir(parents=True, exist_ok=True)

    git_ops = MagicMock()
    git_ops.get_current_branch.return_value = "test-issue"

    phase = DevelopPhase.__new__(DevelopPhase)
    phase.interactive = True
    phase.issue_name = "test-issue"
    phase.issue_dir = issue_dir
    phase.dev_agent = "David"
    phase.iteration = 1
    return phase


class TestAskUserForNoChangesDecisionChat:
    """Tests for chat option in _ask_user_for_no_changes_decision()."""

    @patch("cafe.phases.develop_phase.launch_chat_session")
    @patch("cafe.phases.develop_phase.prompt_list")
    def test_agree_choice_returns_confirm(self, mock_prompt_list, mock_launch_chat, tmp_path):
        """Test that selecting Agree returns 'confirm'."""
        mock_prompt_list.return_value = "c"
        phase = _make_develop_phase(tmp_path)

        result = phase._ask_user_for_no_changes_decision()

        assert result == "confirm"
        mock_launch_chat.assert_not_called()

    @patch("cafe.phases.develop_phase.prompt_multiline")
    @patch("cafe.phases.develop_phase.launch_chat_session")
    @patch("cafe.phases.develop_phase.prompt_list")
    def test_disagree_choice_returns_feedback(
        self, mock_prompt_list, mock_launch_chat, mock_multiline, tmp_path
    ):
        """Test that selecting Disagree returns user's feedback."""
        mock_prompt_list.return_value = "m"
        mock_multiline.return_value = "I think there are still issues"
        phase = _make_develop_phase(tmp_path)

        result = phase._ask_user_for_no_changes_decision()

        assert result == "I think there are still issues"
        mock_launch_chat.assert_not_called()

    @patch("cafe.phases.develop_phase.prompt_list")
    def test_choices_include_chat_option(self, mock_prompt_list, tmp_path):
        """Test that choices include a chat option with a separator."""
        mock_prompt_list.return_value = "c"
        phase = _make_develop_phase(tmp_path)

        phase._ask_user_for_no_changes_decision()

        args, kwargs = mock_prompt_list.call_args
        choices = args[1]

        chat_choices = [c for c in choices if isinstance(c, dict) and c.get("value") == "chat"]
        assert len(chat_choices) == 1
        assert "David" in chat_choices[0]["name"]

    @patch("cafe.phases.develop_phase.prompt_multiline")
    @patch("cafe.phases.develop_phase.launch_chat_session")
    @patch("cafe.phases.develop_phase.prompt_list")
    def test_selecting_chat_launches_session_then_re_prompts(
        self, mock_prompt_list, mock_launch_chat, mock_multiline, tmp_path
    ):
        """Test that selecting chat launches session and then re-displays prompt."""
        mock_prompt_list.side_effect = ["chat", "c"]
        phase = _make_develop_phase(tmp_path)

        result = phase._ask_user_for_no_changes_decision()

        assert result == "confirm"
        mock_launch_chat.assert_called_once_with("developer", "test-issue")
        assert mock_prompt_list.call_count == 2

    @patch("cafe.phases.develop_phase.prompt_multiline")
    @patch("cafe.phases.develop_phase.launch_chat_session")
    @patch("cafe.phases.develop_phase.prompt_list")
    def test_selecting_chat_multiple_times_then_agree(
        self, mock_prompt_list, mock_launch_chat, mock_multiline, tmp_path
    ):
        """Test that user can chat multiple times before deciding."""
        mock_prompt_list.side_effect = ["chat", "chat", "c"]
        phase = _make_develop_phase(tmp_path)

        result = phase._ask_user_for_no_changes_decision()

        assert result == "confirm"
        assert mock_launch_chat.call_count == 2
        assert mock_prompt_list.call_count == 3
