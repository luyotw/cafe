"""Tests for develop phase CAFE_NEED_CLARIFICATION handling with questions.xml."""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cafe.phases.develop_phase import DevelopPhase
from cafe.core.types import PhaseStatus, PhaseResult
from cafe.core.status_codes import PhaseStatusCode


@pytest.fixture
def mock_deps():
    """Create mock dependencies for DevelopPhase."""
    agent_manager = MagicMock()
    permission_handler = MagicMock()
    git_ops = MagicMock()
    git_ops.get_current_branch.return_value = "feature/test"
    return {
        "agent_manager": agent_manager,
        "permission_handler": permission_handler,
        "git_ops": git_ops,
    }


@pytest.fixture
def phase(tmp_path, mock_deps):
    """Create a DevelopPhase instance with mocked dependencies."""
    spec_file = tmp_path / "spec.md"
    spec_file.write_text("# Test Spec")
    plan_file = tmp_path / "plan.md"
    plan_file.write_text("# Test Plan")
    return DevelopPhase(
        agent_manager=mock_deps["agent_manager"],
        permission_handler=mock_deps["permission_handler"],
        git_ops=mock_deps["git_ops"],
        spec_file=str(spec_file),
        plan_file=str(plan_file),
        issue_name="test-issue",
        dev_agent="David",
        interactive=False,
    )


class TestDevelopClarificationQuestionsXml:
    """Tests that CAFE_NEED_CLARIFICATION requires questions.xml."""

    def test_need_clarification_without_questions_xml_returns_failed(self, phase, tmp_path, monkeypatch):
        """CAFE_NEED_CLARIFICATION with no questions.xml should return FAILED."""
        monkeypatch.chdir(tmp_path)
        # questions_xml_path won't exist since no file is created
        with patch.object(phase, "_check_if_already_completed_with_review", return_value=None):
            with patch.object(phase, "_prepare_user_input_for_iteration", return_value=""):
                with patch.object(phase, "_handle_previous_permission_denials", return_value=([], "")):
                    with patch.object(phase, "_merge_allowed_tools", return_value=["read", "edit"]):
                        with patch.object(
                            phase,
                            "_execute_and_handle_agent_response",
                            return_value=(None, "CAFE_NEED_CLARIFICATION"),
                        ):
                            # _validate_and_retry_questions_xml does nothing (xml still doesn't exist)
                            with patch.object(phase, "_validate_and_retry_questions_xml", return_value=False):
                                with patch("cafe.utils.checklist_generator.generate_develop_checklist"):
                                    result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "questions.xml" in result.message

    def test_need_clarification_with_valid_questions_xml_returns_in_progress(self, phase, tmp_path, monkeypatch):
        """CAFE_NEED_CLARIFICATION with valid questions.xml should return IN_PROGRESS."""
        monkeypatch.chdir(tmp_path)
        # Pre-create the questions.xml in the iteration directory
        issue_dir = phase.issue_dir
        iter_dir = issue_dir / "develop" / "iteration_001"
        iter_dir.mkdir(parents=True, exist_ok=True)
        questions_xml = iter_dir / "questions.xml"
        questions_xml.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<questions>
  <question id="1">
    <title>Which approach?</title>
    <options>
      <option>Option A</option>
      <option>Option B</option>
    </options>
  </question>
</questions>""")

        with patch.object(phase, "_check_if_already_completed_with_review", return_value=None):
            with patch.object(phase, "_prepare_user_input_for_iteration", return_value=""):
                with patch.object(phase, "_handle_previous_permission_denials", return_value=([], "")):
                    with patch.object(phase, "_merge_allowed_tools", return_value=["read", "edit"]):
                        with patch.object(
                            phase,
                            "_execute_and_handle_agent_response",
                            return_value=(None, "CAFE_NEED_CLARIFICATION"),
                        ):
                            with patch.object(phase, "_validate_and_retry_questions_xml", return_value=True):
                                with patch("cafe.utils.checklist_generator.generate_develop_checklist"):
                                    result = phase.execute()

        assert result.status == PhaseStatus.IN_PROGRESS
        assert result.data.get("status_code") == PhaseStatusCode.NEED_CLARIFICATION.value


class TestDevelopAskUserForClarification:
    """Tests for _ask_user_for_clarification using questions.xml."""

    def test_uses_questions_xml_when_present(self, phase, tmp_path, monkeypatch):
        """Should use interactive_qa_flow when questions.xml exists in previous iteration."""
        monkeypatch.chdir(tmp_path)
        phase.iteration = 2
        prev_iter_dir = phase._get_iteration_dir(1)
        prev_iter_dir.mkdir(parents=True, exist_ok=True)
        xml_path = prev_iter_dir / "questions.xml"
        xml_path.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<questions>
  <question id="1">
    <title>Which approach?</title>
    <options>
      <option>Option A</option>
    </options>
  </question>
</questions>""")

        mock_questions = [MagicMock()]
        with patch("cafe.core.questions_schema.validate_questions_xml", return_value=True):
            with patch("cafe.core.questions_schema.parse_questions_xml", return_value=mock_questions):
                with patch("cafe.ui.interactive_qa.interactive_qa_flow", return_value="Option A") as mock_flow:
                    result = phase._ask_user_for_clarification()

        mock_flow.assert_called_once_with(mock_questions)
        assert result == "Option A"

    def test_falls_back_to_prompt_when_no_questions_xml(self, phase, monkeypatch, tmp_path):
        """Should fall back to plain prompt when no questions.xml exists."""
        monkeypatch.chdir(tmp_path)
        phase.iteration = 2
        # No questions.xml created

        with patch("cafe.ui.inquirer_prompts.prompt_multiline", return_value="user answer") as mock_prompt:
            result = phase._ask_user_for_clarification()

        mock_prompt.assert_called_once()
        assert result == "user answer"

    def test_falls_back_to_prompt_on_iteration_1(self, phase, monkeypatch, tmp_path):
        """Should use plain prompt on iteration 1 (no previous iteration)."""
        monkeypatch.chdir(tmp_path)
        phase.iteration = 1

        with patch("cafe.ui.inquirer_prompts.prompt_multiline", return_value="user answer") as mock_prompt:
            result = phase._ask_user_for_clarification()

        mock_prompt.assert_called_once()
        assert result == "user answer"


class TestDevelopChecklistQuestionsXml:
    """Tests that generate_develop_checklist receives questions_xml_file."""

    def test_execute_passes_questions_xml_to_checklist_generator(self, phase, monkeypatch, tmp_path):
        """execute() should pass questions_xml_file to generate_develop_checklist."""
        monkeypatch.chdir(tmp_path)
        with patch.object(phase, "_check_if_already_completed_with_review", return_value=None):
            with patch.object(phase, "_prepare_user_input_for_iteration", return_value=""):
                with patch.object(phase, "_handle_previous_permission_denials", return_value=([], "")):
                    with patch.object(phase, "_merge_allowed_tools", return_value=["read"]):
                        with patch.object(phase, "_execute_and_handle_agent_response", return_value=(MagicMock(), "")):
                            with patch("cafe.utils.checklist_generator.generate_develop_checklist") as mock_gen:
                                with patch.object(phase, "_generate_prompt", return_value="prompt"):
                                    phase.execute()

        assert mock_gen.called
        call_kwargs = mock_gen.call_args[1]
        assert "questions_xml_file" in call_kwargs
        assert call_kwargs["questions_xml_file"] is not None
        assert "questions.xml" in str(call_kwargs["questions_xml_file"])
