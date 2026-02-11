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
