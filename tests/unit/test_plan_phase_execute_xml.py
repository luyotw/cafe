"""測試 PlanPhase.execute() 整合 questions.xml 的流程"""

from pathlib import Path
from unittest.mock import MagicMock, patch, call
import pytest

from cafe.phases.plan_phase import PlanPhase
from cafe.core.status_codes import PhaseStatusCode


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


def test_execute_passes_questions_xml_path_to_checklist_generator(tmp_path, mock_dependencies):
    """測試 execute() 計算 questions_xml_path 並傳給 checklist generator"""
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
        template_mode="auto",
    )

    # Patch generate_plan_checklist to verify it receives questions_xml_file
    with patch("cafe.utils.checklist_generator.generate_plan_checklist") as mock_gen:
        with patch.object(phase, "_execute_and_handle_agent_response", return_value=(MagicMock(), "")):
            with patch.object(phase, "_prepare_user_input_for_iteration", return_value="test input"):
                with patch.object(phase, "_copy_previous_version"):
                    with patch.object(phase, "_check_if_already_completed", return_value=None):
                        with patch.object(phase, "_check_max_iterations", return_value=None):
                            phase.execute()

    # 驗證 generate_plan_checklist 被呼叫時包含 questions_xml_file 參數
    assert mock_gen.called
    call_kwargs = mock_gen.call_args[1]
    assert "questions_xml_file" in call_kwargs
    assert call_kwargs["questions_xml_file"] is not None
    assert "questions.xml" in call_kwargs["questions_xml_file"]


def test_execute_includes_questions_xml_in_allowed_tools(tmp_path, mock_dependencies):
    """測試 execute() 包含 edit(questions.xml) 在 allowed_tools 中"""
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
    )

    # Patch _execute_and_handle_agent_response to capture allowed_tools
    captured_tools = []

    def capture_tools(*args, **kwargs):
        if "allowed_tools" in kwargs:
            captured_tools.append(kwargs["allowed_tools"])
        return (MagicMock(), "")

    with patch.object(phase, "_execute_and_handle_agent_response", side_effect=capture_tools):
        with patch.object(phase, "_prepare_user_input_for_iteration", return_value="test input"):
            with patch.object(phase, "_copy_previous_version"):
                with patch.object(phase, "_check_if_already_completed", return_value=None):
                    with patch.object(phase, "_check_max_iterations", return_value=None):
                        with patch("cafe.utils.checklist_generator.generate_plan_checklist"):
                            phase.execute()

    # 驗證 allowed_tools 包含 questions.xml 的 edit 權限
    assert len(captured_tools) > 0
    tools = captured_tools[0]
    assert any("questions.xml" in tool for tool in tools if isinstance(tool, str))
