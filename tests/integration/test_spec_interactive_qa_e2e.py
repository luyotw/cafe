"""E2E 測試 spec phase 互動式問答完整流程

測試 agent 產出 XML → 驗證 → 互動式問答 → 格式化回答 → agent 處理的完整流程，
以及 fallback 流程。
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.core.questions_schema import Question, parse_questions_xml, validate_questions_xml
from cafe.phases.spec_phase import SpecPhase
from cafe.ui.interactive_qa import interactive_qa_flow


VALID_QUESTIONS_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<questions>
  <question id="1">
    <title>What is the expected error behavior?</title>
    <options>
      <option>Return error code and log message</option>
      <option>Throw exception to caller</option>
      <option>Silently retry up to 3 times</option>
    </options>
  </question>
  <question id="2">
    <title>Should it support concurrent access?</title>
    <options>
      <option>Yes, with mutex lock</option>
      <option>No, single-threaded only</option>
    </options>
  </question>
</questions>
"""

INVALID_QUESTIONS_XML_NO_OPTIONS = """\
<?xml version="1.0" encoding="UTF-8"?>
<questions>
  <question id="1">
    <title>Missing options question</title>
  </question>
</questions>
"""


@pytest.fixture
def mock_dependencies():
    """建立 SpecPhase 的 mock 相依物件"""
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
def spec_phase(tmp_path, mock_dependencies):
    """建立用於測試的 SpecPhase 實例"""
    phase = SpecPhase(
        agent_manager=mock_dependencies["agent_manager"],
        permission_handler=mock_dependencies["permission_handler"],
        git_ops=mock_dependencies["git_ops"],
        pm_agent="Roger",
        interactive=True,
        issue_name="test-issue",
    )

    phase.phase_dir = tmp_path / ".cafe" / "issues" / "test-issue" / "spec"
    phase.phase_dir.mkdir(parents=True, exist_ok=True)
    return phase


class TestFullFlowXmlToInteractiveQA:
    """測試完整流程：agent 產出 XML → 驗證通過 → 互動式問答 → 格式化回答"""

    def test_agent_writes_valid_xml_then_interactive_qa_is_used(
        self, spec_phase, tmp_path
    ):
        """測試 agent 寫出有效 XML 後，下一輪使用互動式問答介面"""
        # 模擬第 1 輪：agent 寫出 questions.xml
        spec_phase.iteration = 1
        iter1_dir = spec_phase._get_iteration_dir(1)
        iter1_dir.mkdir(parents=True, exist_ok=True)
        xml_path = iter1_dir / "questions.xml"
        xml_path.write_text(VALID_QUESTIONS_XML)

        # 驗證 XML
        assert validate_questions_xml(xml_path) is True

        # 驗證 XML 能被解析出正確問題
        questions = parse_questions_xml(xml_path)
        assert len(questions) == 2
        assert questions[0].title == "What is the expected error behavior?"
        assert len(questions[0].options) == 3
        assert questions[1].title == "Should it support concurrent access?"
        assert len(questions[1].options) == 2

        # 模擬第 2 輪：_ask_user_for_clarification 應使用互動式問答
        spec_phase.iteration = 2

        with patch("cafe.phases.spec_phase.interactive_qa_flow") as mock_qa_flow:
            mock_qa_flow.return_value = (
                "Q1: What is the expected error behavior?\n"
                "A1: Throw exception to caller\n\n"
                "Q2: Should it support concurrent access?\n"
                "A2: Yes, with mutex lock"
            )

            result = spec_phase._ask_user_for_clarification()

        # 驗證互動式問答被呼叫
        mock_qa_flow.assert_called_once()
        # 驗證傳入的 questions 正確
        passed_questions = mock_qa_flow.call_args[0][0]
        assert len(passed_questions) == 2
        assert passed_questions[0].id == "1"
        assert passed_questions[1].id == "2"

        # 驗證回傳的格式化回答
        assert "Q1:" in result
        assert "A1: Throw exception to caller" in result
        assert "Q2:" in result
        assert "A2: Yes, with mutex lock" in result

    def test_validation_retry_then_interactive_qa(self, spec_phase, tmp_path):
        """測試 XML 驗證失敗 → 重試修正 → 修正後互動式問答正常使用"""
        spec_phase.iteration = 1
        iter1_dir = spec_phase._get_iteration_dir(1)
        iter1_dir.mkdir(parents=True, exist_ok=True)
        xml_path = iter1_dir / "questions.xml"

        # 先寫入無效 XML
        xml_path.write_text(INVALID_QUESTIONS_XML_NO_OPTIONS)

        # Mock agent 在第一次重試時修正 XML
        def mock_execute(agent_name, prompt, **kwargs):
            xml_path.write_text(VALID_QUESTIONS_XML)
            return ("Fixed", MagicMock(), [], None, [], None)

        spec_phase.agent_manager.execute = MagicMock(side_effect=mock_execute)

        # 執行驗證和重試
        result = spec_phase._validate_and_retry_questions_xml(
            xml_path=xml_path,
            agent_name="Roger",
            allowed_tools=["read", "edit(questions.xml)"],
        )
        assert result is True

        # 驗證修正後的 XML 可以用於互動式問答
        spec_phase.iteration = 2
        with patch("cafe.phases.spec_phase.interactive_qa_flow") as mock_qa_flow:
            mock_qa_flow.return_value = "Q1: ...\nA1: ..."
            spec_phase._ask_user_for_clarification()

        mock_qa_flow.assert_called_once()
        passed_questions = mock_qa_flow.call_args[0][0]
        assert len(passed_questions) == 2


class TestFallbackFlow:
    """測試 fallback 流程：agent 產出無效 XML → 重試失敗 → 回退到原始問答"""

    def test_invalid_xml_retries_fail_then_fallback_to_multiline(
        self, spec_phase, tmp_path
    ):
        """測試 XML 重試全部失敗後，下一輪 fallback 到 prompt_multiline"""
        spec_phase.iteration = 1
        iter1_dir = spec_phase._get_iteration_dir(1)
        iter1_dir.mkdir(parents=True, exist_ok=True)
        xml_path = iter1_dir / "questions.xml"
        xml_path.write_text(INVALID_QUESTIONS_XML_NO_OPTIONS)

        # Mock agent 每次都無法修正
        def mock_execute(agent_name, prompt, **kwargs):
            return ("Still broken", MagicMock(), [], None, [], None)

        spec_phase.agent_manager.execute = MagicMock(side_effect=mock_execute)

        # 驗證和重試 → 失敗
        result = spec_phase._validate_and_retry_questions_xml(
            xml_path=xml_path,
            agent_name="Roger",
            allowed_tools=["read", "edit(questions.xml)"],
        )
        assert result is False
        assert not xml_path.exists()  # 無效 XML 被刪除

        # 模擬第 2 輪：由於 XML 被刪除，應 fallback
        spec_phase.iteration = 2
        with patch("cafe.phases.spec_phase.interactive_qa_flow") as mock_qa_flow, \
             patch("cafe.core.phase.prompt_list", return_value="answer"), \
             patch("cafe.core.phase.prompt_multiline", return_value="manual answer"):

            answer = spec_phase._ask_user_for_clarification()

        # 互動式問答不應被呼叫
        mock_qa_flow.assert_not_called()
        assert answer == "manual answer"

    def test_no_xml_file_at_all_uses_multiline(self, spec_phase, tmp_path):
        """測試完全沒有 questions.xml 時使用 prompt_multiline"""
        spec_phase.iteration = 2
        # 建立前一輪目錄但不放 questions.xml
        iter1_dir = spec_phase._get_iteration_dir(1)
        iter1_dir.mkdir(parents=True, exist_ok=True)

        with patch("cafe.phases.spec_phase.interactive_qa_flow") as mock_qa_flow, \
             patch("cafe.core.phase.prompt_list", return_value="answer"), \
             patch("cafe.core.phase.prompt_multiline", return_value="typed answer"):

            answer = spec_phase._ask_user_for_clarification()

        mock_qa_flow.assert_not_called()
        assert answer == "typed answer"


VALID_QUESTIONS_WITH_CHECKBOX_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<questions>
  <question id="1">
    <title>What is the expected error behavior?</title>
    <options>
      <option>Return error code and log message</option>
      <option>Throw exception to caller</option>
    </options>
  </question>
  <question id="2" type="checkbox">
    <title>Which DoD items should be verified?</title>
    <options>
      <option>All features working</option>
      <option>Error handling tested</option>
      <option>Edge cases covered</option>
    </options>
  </question>
</questions>
"""


class TestCheckboxXmlParsing:
    """測試 checkbox 類型問題的 XML 解析"""

    def test_parse_checkbox_question_from_xml(self, tmp_path):
        """測試從 XML 解析出 checkbox 問題"""
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text(VALID_QUESTIONS_WITH_CHECKBOX_XML)

        questions = parse_questions_xml(xml_path)

        assert len(questions) == 2
        assert questions[0].multi_select is False
        assert questions[1].multi_select is True
        assert questions[1].title == "Which DoD items should be verified?"

    def test_validate_xml_with_checkbox_passes(self, tmp_path):
        """測試含有 checkbox 類型的 XML 驗證通過"""
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text(VALID_QUESTIONS_WITH_CHECKBOX_XML)

        assert validate_questions_xml(xml_path) is True

    def test_checkbox_question_interactive_flow(self, tmp_path):
        """測試 checkbox 問題走互動式問答流程"""
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text(VALID_QUESTIONS_WITH_CHECKBOX_XML)

        questions = parse_questions_xml(xml_path)

        with patch("cafe.ui.interactive_qa.inquirer") as mock_inquirer:
            mock_select = MagicMock()
            mock_select.execute = MagicMock(
                side_effect=[
                    "Return error code and log message",  # Q1 single-select
                    "Confirm and continue",               # final summary confirmation
                ]
            )
            mock_inquirer.select.return_value = mock_select

            mock_checkbox = MagicMock()
            mock_checkbox.execute = MagicMock(
                return_value=["All features working", "Edge cases covered"]
            )
            mock_inquirer.checkbox.return_value = mock_checkbox

            mock_text = MagicMock()
            mock_text.execute = MagicMock(return_value="")  # skip custom input
            mock_inquirer.text.return_value = mock_text

            result = interactive_qa_flow(questions)

        assert "A1: Return error code and log message" in result
        assert "A2: All features working, Edge cases covered" in result


class TestInteractiveQaFlowIntegration:
    """測試 interactive_qa_flow 與 Question schema 的整合"""

    def test_parsed_questions_work_with_interactive_flow(self, tmp_path):
        """測試從 XML 解析出的 Question 物件能正確傳入 interactive_qa_flow"""
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text(VALID_QUESTIONS_XML)

        questions = parse_questions_xml(xml_path)

        with patch("cafe.ui.interactive_qa.inquirer") as mock_inquirer:
            mock_select = MagicMock()
            # Q1 選 "Return error code and log message"
            # Q2 選 "No, single-threaded only"
            # 確認
            mock_select.execute = MagicMock(
                side_effect=[
                    "Return error code and log message",
                    "No, single-threaded only",
                    "Confirm and continue",
                ]
            )
            mock_inquirer.select.return_value = mock_select

            result = interactive_qa_flow(questions)

        assert "Q1: What is the expected error behavior?" in result
        assert "A1: Return error code and log message" in result
        assert "Q2: Should it support concurrent access?" in result
        assert "A2: No, single-threaded only" in result
