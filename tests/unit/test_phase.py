"""Tests for Phase base class."""

import json
import pytest
from abc import ABC
from pathlib import Path
from unittest.mock import MagicMock, patch

from cafe.core.phase import Phase
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import PhaseResult, PhaseStatus, TokenUsage


class ConcretePhase(Phase):
    """Concrete implementation for testing."""

    def execute(self) -> PhaseResult:
        """測試用的簡單實作"""
        return PhaseResult(status=PhaseStatus.COMPLETED, message="Test phase completed")


class TestPhase:
    """Test Phase base class."""

    def test_phase_is_abstract(self) -> None:
        """測試 Phase 是抽象基礎類別，無法直接實例化"""
        with pytest.raises(TypeError):
            Phase()  # type: ignore

    def test_concrete_phase_can_be_instantiated(self) -> None:
        """測試具體實作的 Phase 可以被實例化"""
        phase = ConcretePhase()
        assert isinstance(phase, Phase)

    def test_execute_returns_phase_result(self) -> None:
        """測試 execute 方法回傳 PhaseResult"""
        phase = ConcretePhase()
        result = phase.execute()

        assert isinstance(result, PhaseResult)
        assert result.status == PhaseStatus.COMPLETED
        assert result.message == "Test phase completed"

    def test_phase_must_implement_execute(self) -> None:
        """測試子類別必須實作 execute 方法"""

        class IncompletePhase(Phase):
            pass

        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            IncompletePhase()  # type: ignore


class TestPhaseWithDependencies:
    """Test Phase with dependencies injection."""

    def test_phase_accepts_dependencies(self) -> None:
        """測試 Phase 可以接受依賴注入（如 git、session manager）"""

        class PhaseWithDeps(Phase):
            def __init__(self, git_ops: MagicMock, session_mgr: MagicMock) -> None:
                self.git = git_ops
                self.session = session_mgr

            def execute(self) -> PhaseResult:
                # Use dependencies
                branch = self.git.get_current_branch()
                return PhaseResult(
                    status=PhaseStatus.COMPLETED, data={"branch": branch}
                )

        mock_git = MagicMock()
        mock_git.get_current_branch.return_value = "feature-branch"
        mock_session = MagicMock()

        phase = PhaseWithDeps(mock_git, mock_session)
        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data["branch"] == "feature-branch"
        mock_git.get_current_branch.assert_called_once()


class TestPhaseErrorHandling:
    """Test Phase error handling."""

    def test_phase_can_return_failed_status(self) -> None:
        """測試 Phase 可以回傳失敗狀態"""

        class FailingPhase(Phase):
            def execute(self) -> PhaseResult:
                return PhaseResult(
                    status=PhaseStatus.FAILED, message="Something went wrong"
                )

        phase = FailingPhase()
        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert result.message == "Something went wrong"

    def test_phase_can_return_data_with_result(self) -> None:
        """測試 Phase 可以在結果中回傳額外資料"""

        class DataPhase(Phase):
            def execute(self) -> PhaseResult:
                return PhaseResult(
                    status=PhaseStatus.COMPLETED,
                    message="Phase completed with data",
                    data={"commits": 3, "files_changed": 5},
                )

        phase = DataPhase()
        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data["commits"] == 3
        assert result.data["files_changed"] == 5

    def test_phase_exception_propagates_to_caller(self) -> None:
        """測試 Phase 執行時的例外會傳播給呼叫者處理"""

        class ExceptionPhase(Phase):
            def execute(self) -> PhaseResult:
                raise ValueError("Test error")

        phase = ExceptionPhase()

        # Phase 不處理例外，由外層的 workflow 處理
        with pytest.raises(ValueError, match="Test error"):
            phase.execute()


class TestExecuteAgentIteration:
    """測試 Phase._execute_agent_iteration() 通用方法"""

    def test_execute_agent_iteration_success(self, tmp_path: Path) -> None:
        """測試成功執行 agent iteration 的完整流程"""
        # Setup
        history_dir = tmp_path / "history"
        history_dir.mkdir(parents=True)

        class TestPhase(Phase):
            def __init__(self, agent_manager: MagicMock, history_dir: Path):
                self.agent_manager = agent_manager
                self.history_dir = history_dir
                self.iteration = 1

            def execute(self) -> PhaseResult:
                return PhaseResult(status=PhaseStatus.COMPLETED)

            def _save_progress(self, status_code: PhaseStatusCode) -> None:
                """Mock save progress"""
                pass

        # Mock agent manager
        agent_manager = MagicMock()
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent
        agent_manager.execute.return_value = ("CAFE_CONFIRMED\n需求已清楚", TokenUsage(), [])

        phase = TestPhase(agent_manager, history_dir)

        # Execute
        response, status_code = phase._execute_agent_iteration(
            agent_name="test_agent",
            prompt="Test prompt",
            user_input="Test input",
            valid_status_codes=[PhaseStatusCode.CONFIRMED, PhaseStatusCode.NEED_CLARIFICATION],
            allowed_tools=["write", "read"],
        )

        # Verify
        assert response == "CAFE_CONFIRMED\n需求已清楚"
        assert status_code == PhaseStatusCode.CONFIRMED

        # Check history file was created
        iteration_file = history_dir / "iteration_001.json"
        assert iteration_file.exists()

        with open(iteration_file) as f:
            history_data = json.load(f)

        assert history_data["iteration"] == 1
        assert history_data["user_input"] == "Test input"
        assert history_data["prompt"] == "Test prompt"
        assert history_data["response"] == "CAFE_CONFIRMED\n需求已清楚"
        assert history_data["status_code"] == "CAFE_CONFIRMED"
        assert history_data["cli"] == "claude"
        assert history_data["session_id"] == "test_session"
        assert history_data["allowed_tools"] == ["write", "read"]

    def test_execute_agent_iteration_empty_response(self, tmp_path: Path) -> None:
        """測試 agent 返回空回應時的處理"""
        history_dir = tmp_path / "history"
        history_dir.mkdir(parents=True)

        class TestPhase(Phase):
            def __init__(self, agent_manager: MagicMock, history_dir: Path):
                self.agent_manager = agent_manager
                self.history_dir = history_dir
                self.iteration = 1

            def execute(self) -> PhaseResult:
                return PhaseResult(status=PhaseStatus.COMPLETED)

        agent_manager = MagicMock()
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "copilot"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent
        agent_manager.execute.return_value = ("", TokenUsage(), [])  # Empty response

        phase = TestPhase(agent_manager, history_dir)

        # Execute
        response, status_code = phase._execute_agent_iteration(
            agent_name="test_agent",
            prompt="Test prompt",
            user_input="Test input",
            valid_status_codes=[PhaseStatusCode.CONFIRMED],
            allowed_tools=["write"],
        )

        # Verify
        assert response == ""
        assert status_code == PhaseStatusCode.NO_RESPONSE

        # Check history was saved with NO_RESPONSE status
        iteration_file = history_dir / "iteration_001.json"
        assert iteration_file.exists()

        with open(iteration_file) as f:
            history_data = json.load(f)

        assert history_data["status_code"] == "CAFE_NO_RESPONSE"
        assert history_data["response"] == ""

    def test_execute_agent_iteration_no_status_code(self, tmp_path: Path) -> None:
        """測試 agent 回應中沒有 status code 的情況"""
        history_dir = tmp_path / "history"
        history_dir.mkdir(parents=True)

        class TestPhase(Phase):
            def __init__(self, agent_manager: MagicMock, history_dir: Path):
                self.agent_manager = agent_manager
                self.history_dir = history_dir
                self.iteration = 1

            def execute(self) -> PhaseResult:
                return PhaseResult(status=PhaseStatus.COMPLETED)

        agent_manager = MagicMock()
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent
        # Response without status code
        agent_manager.execute.return_value = ("Some response without status code", TokenUsage(), [])

        phase = TestPhase(agent_manager, history_dir)

        # Execute
        response, status_code = phase._execute_agent_iteration(
            agent_name="test_agent",
            prompt="Test prompt",
            user_input="Test input",
            valid_status_codes=[PhaseStatusCode.CONFIRMED],
        )

        # Verify
        assert response == "Some response without status code"
        assert status_code is None

        # Check history was saved without status_code
        iteration_file = history_dir / "iteration_001.json"
        assert iteration_file.exists()

        with open(iteration_file) as f:
            history_data = json.load(f)

        assert history_data["status_code"] is None
        assert history_data["response"] == "Some response without status code"

    def test_execute_agent_iteration_missing_attributes(self, tmp_path: Path) -> None:
        """測試缺少必要屬性時拋出錯誤"""
        class IncompletePhase(Phase):
            def execute(self) -> PhaseResult:
                return PhaseResult(status=PhaseStatus.COMPLETED)

        phase = IncompletePhase()

        # Should raise AttributeError for missing history_dir
        with pytest.raises(AttributeError, match="history_dir"):
            phase._execute_agent_iteration(
                agent_name="test_agent",
                prompt="Test prompt",
                user_input="Test input",
                valid_status_codes=[PhaseStatusCode.CONFIRMED],
            )

    def test_execute_agent_iteration_only_passes_allowed_tools(self, tmp_path: Path) -> None:
        """測試 _execute_agent_iteration 只傳遞 allowed_tools 給 agent_manager.execute()"""
        history_dir = tmp_path / "history"
        history_dir.mkdir(parents=True)

        class TestPhase(Phase):
            def __init__(self, agent_manager: MagicMock, history_dir: Path):
                self.agent_manager = agent_manager
                self.history_dir = history_dir
                self.iteration = 1

            def execute(self) -> PhaseResult:
                return PhaseResult(status=PhaseStatus.COMPLETED)

            def _save_progress(self, status_code: PhaseStatusCode) -> None:
                """Mock save progress"""
                pass

        # Mock agent manager
        agent_manager = MagicMock()
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent
        agent_manager.execute.return_value = ("CAFE_CONFIRMED\n測試完成", TokenUsage(), [])

        phase = TestPhase(agent_manager, history_dir)

        # Execute with allowed_tools
        phase._execute_agent_iteration(
            agent_name="test_agent",
            prompt="Test prompt",
            user_input="Test input",
            valid_status_codes=[PhaseStatusCode.CONFIRMED],
            allowed_tools=["write", "read", "bash"],
        )

        # Verify agent_manager.execute was called with correct parameters
        # Should only have: agent_name, prompt, allowed_tools (NO denied_tools)
        agent_manager.execute.assert_called_once_with(
            "test_agent",
            "Test prompt",
            allowed_tools=["write", "read", "bash"],
        )


class TestHandleStandardStatusCodes:
    """測試 Phase._handle_standard_status_codes() 通用方法"""

    def test_handle_no_response_returns_failed(self) -> None:
        """測試 NO_RESPONSE 返回 FAILED 狀態"""
        class TestPhase(Phase):
            def __init__(self):
                self.iteration = 1
                self.interactive = True

            def execute(self) -> PhaseResult:
                return PhaseResult(status=PhaseStatus.COMPLETED)

        phase = TestPhase()
        result = phase._handle_standard_status_codes(
            status_code=PhaseStatusCode.NO_RESPONSE,
            response="",
        )

        assert result is not None
        assert result.status == PhaseStatus.FAILED
        assert "no response" in result.message.lower()
        assert result.data["status_code"] == "CAFE_NO_RESPONSE"

    def test_handle_rejected_returns_failed(self) -> None:
        """測試 REJECTED 返回 FAILED 狀態"""
        class TestPhase(Phase):
            def __init__(self):
                self.iteration = 2
                self.interactive = True

            def execute(self) -> PhaseResult:
                return PhaseResult(status=PhaseStatus.COMPLETED)

        phase = TestPhase()
        result = phase._handle_standard_status_codes(
            status_code=PhaseStatusCode.REJECTED,
            response="User rejected the plan",
        )

        assert result is not None
        assert result.status == PhaseStatus.FAILED
        assert "rejected" in result.message.lower()
        assert result.data["status_code"] == "CAFE_REJECTED"
        assert result.data["final_response"] == "User rejected the plan"

    def test_handle_complete_codes_returns_none(self) -> None:
        """測試 complete_codes（如 READY_FOR_REVIEW）返回 None（繼續循環）"""
        class TestPhase(Phase):
            def __init__(self):
                self.iteration = 1
                self.interactive = True

            def execute(self) -> PhaseResult:
                return PhaseResult(status=PhaseStatus.COMPLETED)

        phase = TestPhase()
        result = phase._handle_standard_status_codes(
            status_code=PhaseStatusCode.READY_FOR_REVIEW,
            response="Plan is ready",
            complete_codes=[PhaseStatusCode.READY_FOR_REVIEW, PhaseStatusCode.CONFIRMED],
        )

        assert result is None  # Continue to next iteration

    def test_handle_continue_codes_returns_none(self) -> None:
        """測試 continue_codes（如 NEED_CLARIFICATION）返回 None（繼續循環）"""
        class TestPhase(Phase):
            def __init__(self):
                self.iteration = 1
                self.interactive = True

            def execute(self) -> PhaseResult:
                return PhaseResult(status=PhaseStatus.COMPLETED)

        phase = TestPhase()
        result = phase._handle_standard_status_codes(
            status_code=PhaseStatusCode.NEED_CLARIFICATION,
            response="Need more info",
            continue_codes=[PhaseStatusCode.NEED_CLARIFICATION],
        )

        assert result is None  # Continue to next iteration

    def test_handle_no_status_code_interactive_returns_none(self) -> None:
        """測試沒有 status code 且 interactive 模式返回 None（繼續循環）"""
        class TestPhase(Phase):
            def __init__(self):
                self.iteration = 1
                self.interactive = True

            def execute(self) -> PhaseResult:
                return PhaseResult(status=PhaseStatus.COMPLETED)

        phase = TestPhase()
        result = phase._handle_standard_status_codes(
            status_code=None,
            response="Some response without status code",
        )

        assert result is None  # Continue in interactive mode

    def test_handle_no_status_code_non_interactive_returns_in_progress(self) -> None:
        """測試沒有 status code 且 non-interactive 模式返回 IN_PROGRESS"""
        class TestPhase(Phase):
            def __init__(self):
                self.iteration = 3
                self.interactive = False

            def execute(self) -> PhaseResult:
                return PhaseResult(status=PhaseStatus.COMPLETED)

        phase = TestPhase()
        result = phase._handle_standard_status_codes(
            status_code=None,
            response="Some response without status code",
        )

        assert result is not None
        assert result.status == PhaseStatus.IN_PROGRESS
        assert "No status code found" in result.message
        assert result.data["status_code"] is None
        assert result.data["iterations"] == 3

    def test_handle_missing_iteration_raises_error(self) -> None:
        """測試缺少 iteration 屬性時拋出錯誤"""
        class IncompletePhase(Phase):
            def __init__(self):
                self.interactive = True

            def execute(self) -> PhaseResult:
                return PhaseResult(status=PhaseStatus.COMPLETED)

        phase = IncompletePhase()

        with pytest.raises(AttributeError, match="iteration"):
            phase._handle_standard_status_codes(
                status_code=PhaseStatusCode.CONFIRMED,
                response="Test",
            )

    def test_handle_missing_interactive_raises_error(self) -> None:
        """測試缺少 interactive 屬性時拋出錯誤"""
        class IncompletePhase(Phase):
            def __init__(self):
                self.iteration = 1

            def execute(self) -> PhaseResult:
                return PhaseResult(status=PhaseStatus.COMPLETED)

        phase = IncompletePhase()

        with pytest.raises(AttributeError, match="interactive"):
            phase._handle_standard_status_codes(
                status_code=PhaseStatusCode.CONFIRMED,
                response="Test",
            )


class TestHandlePreviousPermissionDenials:
    """測試 Phase._handle_previous_permission_denials() 處理上一輪的權限請求"""

    def test_no_previous_iteration_returns_empty(self, tmp_path: Path) -> None:
        """測試沒有上一輪 iteration 時返回空的 approved tools 和 user_input"""
        history_dir = tmp_path / "history"
        history_dir.mkdir(parents=True)

        class TestPhase(Phase):
            def __init__(self, history_dir: Path):
                self.history_dir = history_dir
                self.iteration = 1  # First iteration, no previous
                self.interactive = False
                self.approved_denial_indices = []
                self.user_input = ""

            def execute(self) -> PhaseResult:
                return PhaseResult(status=PhaseStatus.COMPLETED)

        phase = TestPhase(history_dir)

        approved_tools, user_input = phase._handle_previous_permission_denials()

        assert approved_tools == []
        assert user_input == ""

    def test_previous_iteration_no_permission_denials_returns_empty(self, tmp_path: Path) -> None:
        """測試上一輪沒有 permission_denials 時返回空"""
        history_dir = tmp_path / "history"
        history_dir.mkdir(parents=True)

        # Create iteration_001.json without permission_denials
        iteration_001 = {
            "iteration": 1,
            "timestamp": "2025-11-14T10:00:00",
            "user_input": "",
            "prompt": "Test prompt",
            "response": "CAFE_CONFIRMED\n完成",
            "status_code": "CAFE_CONFIRMED"
        }

        with open(history_dir / "iteration_001.json", "w") as f:
            json.dump(iteration_001, f)

        class TestPhase(Phase):
            def __init__(self, history_dir: Path):
                self.history_dir = history_dir
                self.iteration = 2  # Second iteration, check iteration_001.json
                self.interactive = False
                self.approved_denial_indices = []
                self.user_input = ""

            def execute(self) -> PhaseResult:
                return PhaseResult(status=PhaseStatus.COMPLETED)

        phase = TestPhase(history_dir)

        approved_tools, user_input = phase._handle_previous_permission_denials()

        assert approved_tools == []
        assert user_input == ""

    def test_non_interactive_with_approved_indices_returns_tools(self, tmp_path: Path) -> None:
        """測試 non-interactive 模式，使用 approved_denial_indices 批准權限"""
        history_dir = tmp_path / "history"
        history_dir.mkdir(parents=True)

        # Create iteration_001.json with permission_denials
        iteration_001 = {
            "iteration": 1,
            "timestamp": "2025-11-14T10:00:00",
            "user_input": "",
            "prompt": "Test prompt",
            "response": "CAFE_NEED_PERMISSION\n需要權限",
            "status_code": "CAFE_NEED_PERMISSION",
            "permission_denials": [
                {"tool_name": "Read", "tool_input": {"file_path": "/home/user/test.txt"}},
                {"tool_name": "Bash", "tool_input": {"command": "git status"}},
                {"tool_name": "Edit", "tool_input": {"file_path": "/home/user/config.php"}},
            ]
        }

        with open(history_dir / "iteration_001.json", "w") as f:
            json.dump(iteration_001, f)

        class TestPhase(Phase):
            def __init__(self, history_dir: Path):
                self.history_dir = history_dir
                self.iteration = 2  # Second iteration
                self.interactive = False
                self.approved_denial_indices = [0, 2]  # Approve indices 0 and 2
                self.user_input = "請小心修改"

            def execute(self) -> PhaseResult:
                return PhaseResult(status=PhaseStatus.COMPLETED)

        phase = TestPhase(history_dir)

        approved_tools, user_input = phase._handle_previous_permission_denials()

        # Should return approved tools based on indices
        assert len(approved_tools) == 2
        assert "Read(/home/user/test.txt)" in approved_tools
        assert "Edit(/home/user/config.php)" in approved_tools
        assert "Bash(git status)" not in approved_tools

        # Should return user_input
        assert user_input == "請小心修改"

    def test_interactive_mode_asks_user_for_each_denial(self, tmp_path: Path) -> None:
        """測試 interactive 模式逐一詢問用戶是否批准權限"""
        history_dir = tmp_path / "history"
        history_dir.mkdir(parents=True)

        # Create iteration_001.json with permission_denials
        iteration_001 = {
            "iteration": 1,
            "timestamp": "2025-11-14T10:00:00",
            "user_input": "",
            "prompt": "Test prompt",
            "response": "CAFE_NEED_PERMISSION\n需要權限",
            "status_code": "CAFE_NEED_PERMISSION",
            "permission_denials": [
                {"tool_name": "Read", "tool_input": {"file_path": "/home/user/test.txt"}},
                {"tool_name": "Bash", "tool_input": {"command": "git --no-pager log --oneline -n 10"}},
            ]
        }

        with open(history_dir / "iteration_001.json", "w") as f:
            json.dump(iteration_001, f)

        class TestPhase(Phase):
            def __init__(self, history_dir: Path, display: MagicMock):
                self.history_dir = history_dir
                self.iteration = 2  # Second iteration
                self.interactive = True
                self.display = display

            def execute(self) -> PhaseResult:
                return PhaseResult(status=PhaseStatus.COMPLETED)

        mock_display = MagicMock()
        mock_display.get_multiline_input.return_value = "請注意安全"

        phase = TestPhase(history_dir, mock_display)

        # Mock user input: approve first (y), reject second (n)
        with patch('builtins.input', side_effect=['y', 'n']):
            approved_tools, user_input = phase._handle_previous_permission_denials()

        # Should only approve first one
        assert len(approved_tools) == 1
        assert "Read(/home/user/test.txt)" in approved_tools

        # Should have user_input from get_multiline_input
        assert user_input == "請注意安全"
        mock_display.get_multiline_input.assert_called_once()

    def test_bash_command_pattern_uses_first_two_words(self, tmp_path: Path) -> None:
        """測試 Bash 命令的 pattern 使用前兩個詞"""
        history_dir = tmp_path / "history"
        history_dir.mkdir(parents=True)

        iteration_001 = {
            "iteration": 1,
            "permission_denials": [
                {"tool_name": "Bash", "tool_input": {"command": "git --no-pager log --oneline -n 10"}},
            ]
        }

        with open(history_dir / "iteration_001.json", "w") as f:
            json.dump(iteration_001, f)

        class TestPhase(Phase):
            def __init__(self, history_dir: Path):
                self.history_dir = history_dir
                self.iteration = 2  # Second iteration
                self.interactive = False
                self.approved_denial_indices = [0]
                self.user_input = ""

            def execute(self) -> PhaseResult:
                return PhaseResult(status=PhaseStatus.COMPLETED)

        phase = TestPhase(history_dir)

        approved_tools, _ = phase._handle_previous_permission_denials()

        # Should use "git --no-pager" as pattern (first two words)
        assert approved_tools == ["Bash(git --no-pager)"]
