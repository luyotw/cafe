"""測試 Plan Phase 的 XML 問答驗證、重試機制和互動式問答整合"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.core.questions_schema import Question
from cafe.phases.plan_phase import PlanPhase


@pytest.fixture
def mock_dependencies():
    """建立 PlanPhase 的 mock 相依物件"""
    mock_agent_manager = MagicMock()
    mock_permission_handler = MagicMock()
    mock_git_ops = MagicMock()
    mock_git_ops.get_current_branch.return_value = "test-branch"

    return {
        "agent_manager": mock_agent_manager,
        "permission_handler": mock_permission_handler,
        "git_ops": mock_git_ops,
    }


@pytest.fixture
def plan_phase(tmp_path, mock_dependencies):
    """建立用於測試的 PlanPhase 實例"""
    spec_file = tmp_path / "spec.md"
    spec_file.write_text("# Test Spec")

    phase = PlanPhase(
        agent_manager=mock_dependencies["agent_manager"],
        permission_handler=mock_dependencies["permission_handler"],
        git_ops=mock_dependencies["git_ops"],
        spec_file=str(spec_file),
        issue_name="test-issue",
        dev_agent="David",
        interactive=False,
        user_input="test input",
    )

    phase.phase_dir = tmp_path / ".cafe" / "issues" / "test-issue" / "plan"
    phase.phase_dir.mkdir(parents=True, exist_ok=True)
    phase.iteration = 1
    return phase


VALID_QUESTIONS_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<questions>
  <question id="1">
    <title>What is the preferred error handling approach?</title>
    <options>
      <option>Return error code</option>
      <option>Throw exception</option>
    </options>
  </question>
</questions>
"""

INVALID_QUESTIONS_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<questions>
  <question id="1">
    <title>Missing options question</title>
  </question>
</questions>
"""


class TestValidateAndRetryQuestionsXml:
    """測試 _validate_and_retry_questions_xml() 方法 (從 base class 繼承)"""

    def test_returns_true_when_xml_is_valid(self, plan_phase, tmp_path):
        """測試 XML 格式正確時回傳 True"""
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text(VALID_QUESTIONS_XML)

        result = plan_phase._validate_and_retry_questions_xml(
            xml_path=xml_path,
            agent_name="David",
            allowed_tools=["read", "edit(questions.xml)"],
        )

        assert result is True

    def test_returns_false_when_xml_not_exists(self, plan_phase, tmp_path):
        """測試 XML 檔案不存在時回傳 False"""
        xml_path = tmp_path / "questions.xml"

        result = plan_phase._validate_and_retry_questions_xml(
            xml_path=xml_path,
            agent_name="David",
            allowed_tools=["read", "edit(questions.xml)"],
        )

        assert result is False

    def test_retries_on_invalid_xml_and_succeeds(self, plan_phase, tmp_path):
        """測試 XML 格式不正確時呼叫 agent 修正，修正後回傳 True"""
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text(INVALID_QUESTIONS_XML)

        call_count = 0

        def mock_execute(agent_name, prompt, **kwargs):
            nonlocal call_count
            call_count += 1
            # Agent fixes the XML on first retry
            xml_path.write_text(VALID_QUESTIONS_XML)
            return ("Fixed", MagicMock(), [], None, [], None)

        plan_phase.agent_manager.execute = MagicMock(side_effect=mock_execute)

        result = plan_phase._validate_and_retry_questions_xml(
            xml_path=xml_path,
            agent_name="David",
            allowed_tools=["read", "edit(questions.xml)"],
        )

        assert result is True
        assert call_count == 1

    def test_retries_up_to_3_times_then_returns_false(self, plan_phase, tmp_path):
        """測試重試 3 次後仍無效，刪除檔案並回傳 False"""
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text(INVALID_QUESTIONS_XML)

        call_count = 0

        def mock_execute(agent_name, prompt, **kwargs):
            nonlocal call_count
            call_count += 1
            # Agent fails to fix the XML every time
            return ("Still broken", MagicMock(), [], None, [], None)

        plan_phase.agent_manager.execute = MagicMock(side_effect=mock_execute)

        result = plan_phase._validate_and_retry_questions_xml(
            xml_path=xml_path,
            agent_name="David",
            allowed_tools=["read", "edit(questions.xml)"],
        )

        assert result is False
        assert call_count == 3
        # Invalid XML file should be deleted
        assert not xml_path.exists()


class TestAskUserForClarification:
    """測試 _ask_user_for_clarification() 的互動式問答整合"""

    def test_uses_interactive_qa_when_xml_exists(self, plan_phase, tmp_path):
        """測試當前一輪的 questions.xml 存在且有效時，使用互動式問答介面"""
        plan_phase.iteration = 2
        plan_phase.interactive = True

        # 在前一輪 (iteration 1) 的目錄建立 questions.xml
        prev_iter_dir = plan_phase._get_iteration_dir(1)
        prev_iter_dir.mkdir(parents=True, exist_ok=True)
        xml_path = prev_iter_dir / "questions.xml"
        xml_path.write_text(VALID_QUESTIONS_XML)

        with patch("cafe.phases.plan_phase.interactive_qa_flow") as mock_qa_flow:
            mock_qa_flow.return_value = "Q1: What is the preferred error handling approach?\nA1: Return error code"

            result = plan_phase._ask_user_for_clarification()

        assert "Q1:" in result
        assert "A1:" in result
        mock_qa_flow.assert_called_once()
        # 驗證傳入的 questions 是正確解析的 Question 物件
        questions_arg = mock_qa_flow.call_args[0][0]
        assert len(questions_arg) == 1
        assert questions_arg[0].title == "What is the preferred error handling approach?"

    def test_falls_back_to_prompt_when_no_xml(self, plan_phase, tmp_path):
        """測試 questions.xml 不存在時 fallback 到提示選單"""
        plan_phase.iteration = 2
        plan_phase.interactive = True

        # 確保前一輪目錄存在但沒有 questions.xml
        prev_iter_dir = plan_phase._get_iteration_dir(1)
        prev_iter_dir.mkdir(parents=True, exist_ok=True)

        with patch("cafe.phases.plan_phase.interactive_qa_flow") as mock_qa_flow, \
             patch("cafe.core.phase_review_mixin.prompt_list", return_value="answer"), \
             patch("cafe.core.phase_review_mixin.prompt_multiline", return_value="manual answer"):

            result = plan_phase._ask_user_for_clarification()

        mock_qa_flow.assert_not_called()
        assert result == "manual answer"

    def test_falls_back_to_prompt_when_xml_invalid(self, plan_phase, tmp_path):
        """測試 questions.xml 格式不正確時 fallback 到提示選單"""
        plan_phase.iteration = 2
        plan_phase.interactive = True

        # 在前一輪目錄建立無效的 questions.xml
        prev_iter_dir = plan_phase._get_iteration_dir(1)
        prev_iter_dir.mkdir(parents=True, exist_ok=True)
        xml_path = prev_iter_dir / "questions.xml"
        xml_path.write_text(INVALID_QUESTIONS_XML)

        with patch("cafe.phases.plan_phase.interactive_qa_flow") as mock_qa_flow, \
             patch("cafe.core.phase_review_mixin.prompt_list", return_value="answer"), \
             patch("cafe.core.phase_review_mixin.prompt_multiline", return_value="fallback answer"):

            result = plan_phase._ask_user_for_clarification()

        mock_qa_flow.assert_not_called()
        assert result == "fallback answer"

    def test_iteration_1_falls_back_to_prompt(self, plan_phase, tmp_path):
        """測試第一輪沒有前一輪目錄，fallback 到提示選單"""
        plan_phase.iteration = 1
        plan_phase.interactive = True

        with patch("cafe.phases.plan_phase.interactive_qa_flow") as mock_qa_flow, \
             patch("cafe.core.phase_review_mixin.prompt_list", return_value="answer"), \
             patch("cafe.core.phase_review_mixin.prompt_multiline", return_value="first iteration answer"):

            result = plan_phase._ask_user_for_clarification()

        mock_qa_flow.assert_not_called()
        assert result == "first iteration answer"


class TestReviewDecisionDisplayCallback:
    """測試 READY_FOR_REVIEW 時的 diff 顯示 callback 接線"""

    def test_ready_for_review_passes_diff_callback_to_edit_menu(self, plan_phase, tmp_path):
        """測試 plan review menu 會傳入 diff callback，供 chat/edit 返回後重顯"""
        plan_phase.iteration = 2
        plan_phase.interactive = True

        prev_plan_file = plan_phase._get_versioned_file_path("plan", 1, plan_phase.phase_dir)
        prev_plan_file.parent.mkdir(parents=True, exist_ok=True)
        prev_plan_file.write_text("## Plan\n")

        with patch.object(plan_phase, "_display_current_plan"), \
             patch.object(plan_phase, "_display_iteration_delta") as mock_display_delta, \
             patch.object(plan_phase, "_load_previous_iteration_data", return_value={"status_code": "CAFE_READY_FOR_REVIEW"}), \
             patch.object(plan_phase, "_ask_user_for_review_decision", return_value="confirm") as mock_review_decision, \
             patch.object(plan_phase, "_process_review_decision", return_value="confirm"):

            result = plan_phase._prepare_user_input_for_iteration()

        assert result == "confirm"
        mock_display_delta.assert_called_once()
        assert mock_review_decision.call_args.kwargs["display_callback"] is mock_display_delta
        assert mock_review_decision.call_args.kwargs["output_file"] == prev_plan_file

    def test_ready_for_review_response_without_status_code_still_prompts_review_menu(self, plan_phase, tmp_path):
        """測試上一輪只留下 response 時，仍能辨識 READY_FOR_REVIEW。"""
        plan_phase.iteration = 2
        plan_phase.interactive = True

        prev_plan_file = plan_phase._get_versioned_file_path("plan", 1, plan_phase.phase_dir)
        prev_plan_file.parent.mkdir(parents=True, exist_ok=True)
        prev_plan_file.write_text("## Plan\n")

        with patch.object(plan_phase, "_display_current_plan"), \
             patch.object(plan_phase, "_display_iteration_delta") as mock_display_delta, \
             patch.object(
                 plan_phase,
                 "_load_previous_iteration_data",
                 return_value={"response": "CAFE_READY_FOR_REVIEW"},
             ), \
             patch.object(plan_phase, "_ask_user_for_review_decision", return_value="confirm") as mock_review_decision, \
             patch.object(plan_phase, "_process_review_decision", return_value="confirm"):

            result = plan_phase._prepare_user_input_for_iteration()

        assert result == "confirm"
        mock_display_delta.assert_called_once()
        assert mock_review_decision.call_args.kwargs["display_callback"] is mock_display_delta
        assert mock_review_decision.call_args.kwargs["output_file"] == prev_plan_file
