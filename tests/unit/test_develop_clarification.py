"""Tests for develop phase CAFE_NEED_CLARIFICATION handling with questions.xml."""
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cafe.phases.develop_phase import DevelopPhase
from cafe.core.types import PhaseStatus, PhaseResult, TokenUsage
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


@pytest.fixture
def interactive_phase(tmp_path, mock_deps):
    """Create an interactive DevelopPhase instance for permission UI tests."""
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
        interactive=True,
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

    def test_need_permission_interactive_points_back_to_make(
        self, interactive_phase, tmp_path, monkeypatch, capsys
    ):
        """Interactive permission prompts should send users back through workflow."""
        monkeypatch.chdir(tmp_path)
        with patch.object(interactive_phase, "_check_if_already_completed_with_review", return_value=None):
            with patch.object(interactive_phase, "_prepare_user_input_for_iteration", return_value=""):
                with patch.object(interactive_phase, "_handle_previous_permission_denials", return_value=([], "")):
                    with patch.object(interactive_phase, "_merge_allowed_tools", return_value=["read", "edit"]):
                        with patch.object(
                            interactive_phase,
                            "_execute_and_handle_agent_response",
                            return_value=(None, "CAFE_NEED_PERMISSION"),
                        ):
                            with patch("cafe.utils.checklist_generator.generate_develop_checklist"):
                                result = interactive_phase.execute()

        captured = capsys.readouterr()
        assert result.status == PhaseStatus.IN_PROGRESS
        assert "Resume with 'cafe make'" in captured.out

    def test_need_clarification_interactive_points_back_to_make(
        self, interactive_phase, tmp_path, monkeypatch, capsys
    ):
        """Interactive clarification prompts should send users back through workflow."""
        monkeypatch.chdir(tmp_path)
        iter_dir = interactive_phase.issue_dir / "develop" / "iteration_001"
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

        with patch.object(interactive_phase, "_check_if_already_completed_with_review", return_value=None):
            with patch.object(interactive_phase, "_prepare_user_input_for_iteration", return_value=""):
                with patch.object(interactive_phase, "_handle_previous_permission_denials", return_value=([], "")):
                    with patch.object(interactive_phase, "_merge_allowed_tools", return_value=["read", "edit"]):
                        with patch.object(
                            interactive_phase,
                            "_execute_and_handle_agent_response",
                            return_value=(None, "CAFE_NEED_CLARIFICATION"),
                        ):
                            with patch.object(
                                interactive_phase, "_validate_and_retry_questions_xml", return_value=True
                            ):
                                with patch("cafe.utils.checklist_generator.generate_develop_checklist"):
                                    result = interactive_phase.execute()

        captured = capsys.readouterr()
        assert result.status == PhaseStatus.IN_PROGRESS
        assert "Resume with 'cafe make'" in captured.out


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

        mock_flow.assert_called_once_with(mock_questions, role="developer", issue_name="test-issue", agent_name="David")
        assert result == "Option A"

    def test_falls_back_to_prompt_when_no_questions_xml(self, phase, monkeypatch, tmp_path):
        """Should fall back to prompt when no questions.xml exists."""
        monkeypatch.chdir(tmp_path)
        phase.iteration = 2
        # No questions.xml created

        with patch("cafe.core.phase.prompt_list", return_value="answer") as mock_list:
            with patch("cafe.core.phase.prompt_multiline", return_value="user answer") as mock_prompt:
                result = phase._ask_user_for_clarification()

        mock_list.assert_called_once()
        mock_prompt.assert_called_once()
        assert result == "user answer"

    def test_falls_back_to_prompt_on_iteration_1(self, phase, monkeypatch, tmp_path):
        """Should use prompt on iteration 1 (no previous iteration)."""
        monkeypatch.chdir(tmp_path)
        phase.iteration = 1

        with patch("cafe.core.phase.prompt_list", return_value="answer") as mock_list:
            with patch("cafe.core.phase.prompt_multiline", return_value="user answer") as mock_prompt:
                result = phase._ask_user_for_clarification()

        mock_list.assert_called_once()
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


class TestDevelopNeedPermissionFallback:
    """Tests for NEED_PERMISSION without structured permission denials."""

    def test_prepare_user_input_uses_freeform_permission_input(self, phase, monkeypatch, tmp_path):
        """Should use user_input when NEED_PERMISSION has no permission_denials."""
        monkeypatch.chdir(tmp_path)
        phase.iteration = 2
        phase.user_input = "You may run git commit for this task."

        with patch.object(
            phase,
            "_load_previous_iteration_data",
            return_value={"iteration": 1, "status_code": "CAFE_NEED_PERMISSION", "response": "CAFE_NEED_PERMISSION"},
        ):
            result = phase._prepare_user_input_for_iteration()

        assert result == "You may run git commit for this task."
        assert phase.user_input == ""

    def test_prepare_user_input_recovers_need_permission_from_response_only(self, phase, monkeypatch, tmp_path):
        """Should recover NEED_PERMISSION when previous data omitted explicit status_code."""
        monkeypatch.chdir(tmp_path)
        phase.iteration = 2
        phase.user_input = "You may run git commit for this task."

        with patch.object(
            phase,
            "_load_previous_iteration_data",
            return_value={"iteration": 1, "response": "CAFE_NEED_PERMISSION"},
        ):
            result = phase._prepare_user_input_for_iteration()

        assert result == "You may run git commit for this task."
        assert phase.user_input == ""

    def test_prepare_user_input_fails_without_noninteractive_permission_input(self, phase, monkeypatch, tmp_path):
        """Should fail in non-interactive mode when no freeform permission input is provided."""
        monkeypatch.chdir(tmp_path)
        phase.iteration = 2
        phase.user_input = ""

        with patch.object(
            phase,
            "_load_previous_iteration_data",
            return_value={"iteration": 1, "status_code": "CAFE_NEED_PERMISSION", "response": "CAFE_NEED_PERMISSION"},
        ):
            result = phase._prepare_user_input_for_iteration()

        assert isinstance(result, PhaseResult)
        assert result.status == PhaseStatus.FAILED
        assert result.data.get("status_code") == "CAFE_NEED_PERMISSION"

    def test_prepare_user_input_uses_host_execution_failure_followup(self, phase, monkeypatch, tmp_path):
        """Should reuse failed host execution context instead of asking for permission again."""
        monkeypatch.chdir(tmp_path)
        phase.iteration = 2
        prev_iter_dir = phase._get_iteration_dir(1)
        prev_iter_dir.mkdir(parents=True, exist_ok=True)
        (prev_iter_dir / "host_execution.json").write_text(
            json.dumps(
                [
                    {
                        "tool_name": "Bash",
                        "command": 'git commit -m "feat: test"',
                        "stdout": "",
                        "stderr": "ModuleNotFoundError: No module named 'pydantic'\nMore detail",
                        "returncode": 1,
                        "ok": False,
                    }
                ]
            ),
            encoding="utf-8",
        )

        with patch.object(
            phase,
            "_load_previous_iteration_data",
            return_value={"iteration": 1, "status_code": "CAFE_NEED_PERMISSION", "response": "CAFE_NEED_PERMISSION"},
        ):
            result = phase._prepare_user_input_for_iteration()

        assert "The host environment already attempted the previously blocked command" in result
        assert "host_execution.json" in result
        assert 'git commit -m "feat: test"' in result
        assert "ModuleNotFoundError" in result

    def test_recovers_permission_denials_from_streaming_file(self, phase, monkeypatch, tmp_path):
        """Should recover Codex denied commands from previous streaming.jsonl."""
        monkeypatch.chdir(tmp_path)
        phase.iteration = 2
        prev_iter_dir = phase._get_iteration_dir(1)
        prev_iter_dir.mkdir(parents=True, exist_ok=True)
        (prev_iter_dir / "streaming.jsonl").write_text(
            '{"type":"stderr","content":"error=exec_command failed for `/bin/zsh -lc \'git add src/cafe/ui/cli.py && git commit -m \\"msg\\"\'`: Codex(Sandbox(Denied {}))"}\n',
            encoding="utf-8",
        )

        with patch.object(
            phase,
            "_load_previous_iteration_data",
            return_value={"iteration": 1, "status_code": "CAFE_NEED_PERMISSION", "response": "CAFE_NEED_PERMISSION"},
        ):
            approved_tools, user_input = phase._handle_previous_permission_denials()

        assert approved_tools == []
        assert user_input == ""

        recovered = phase._extract_codex_permission_denials_from_streaming_file(1)
        assert len(recovered) == 1
        assert recovered[0].tool_name == "Bash"
        assert recovered[0].tool_input["command"] == 'git add src/cafe/ui/cli.py && git commit -m "msg"'

    def test_handle_previous_permission_denials_recovers_status_from_response_only(
        self, phase, monkeypatch, tmp_path
    ):
        """Should treat response-only NEED_PERMISSION as the previous round status."""
        monkeypatch.chdir(tmp_path)
        phase.iteration = 2

        with patch.object(
            phase,
            "_load_previous_iteration_data",
            return_value={"iteration": 1, "response": "CAFE_NEED_PERMISSION"},
        ):
            approved_tools, user_input = phase._handle_previous_permission_denials()

        assert approved_tools == []
        assert user_input == ""

class TestCodexPermissionRules:
    """Tests for Codex blocked-command handling."""

    def test_auto_executes_safe_git_denials_without_prompting(
        self, interactive_phase, monkeypatch, tmp_path
    ):
        repo_root = tmp_path / "repo"
        (repo_root / ".git" / "info").mkdir(parents=True)
        monkeypatch.chdir(repo_root)

        interactive_phase.iteration = 2
        with patch.object(
            interactive_phase,
            "_load_previous_iteration_data",
            return_value={
                "iteration": 1,
                "status_code": "CAFE_NEED_PERMISSION",
                "response": "CAFE_NEED_PERMISSION",
                "cli": "codex",
                "permission_denials": [
                    {
                        "tool_name": "Bash",
                        "tool_input": {
                            "command": 'git add src/cafe/ui/cli.py && git commit -m "msg"',
                        },
                    }
                ],
            },
        ):
            with patch("cafe.ui.inquirer_prompts.prompt_list") as mock_prompt_list:
                with patch("cafe.core.phase.subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
                    approved_tools, user_input = interactive_phase._handle_previous_permission_denials()

        assert approved_tools == []
        assert "attempted by the host environment" in user_input
        mock_prompt_list.assert_not_called()
        mock_run.assert_called_once()

    def test_approved_codex_execution_runs_on_host_and_does_not_return_allowed_tools(
        self, interactive_phase, monkeypatch, tmp_path
    ):
        repo_root = tmp_path / "repo"
        git_dir = repo_root / ".git"
        (git_dir / "info").mkdir(parents=True)
        monkeypatch.chdir(repo_root)

        interactive_phase.iteration = 2
        with patch.object(
            interactive_phase,
            "_load_previous_iteration_data",
            return_value={
                "iteration": 1,
                "status_code": "CAFE_NEED_PERMISSION",
                "response": "CAFE_NEED_PERMISSION",
                "cli": "codex",
                "permission_denials": [
                    {
                        "tool_name": "Bash",
                        "tool_input": {
                            "command": 'git add src/cafe/ui/cli.py tests/unit/test_cli_setup.py && git commit -m "feat: support selective role updates in cafe setup"'
                        },
                    }
                ],
            },
        ):
            with patch("cafe.ui.inquirer_prompts.prompt_list", side_effect=["approve", "confirm"]):
                with patch("cafe.core.phase.subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
                    approved_tools, user_input = interactive_phase._handle_previous_permission_denials()

        assert approved_tools == []
        assert "attempted by the host environment" in user_input
        assert "host_execution.json" in user_input
        mock_run.assert_called_once_with(
            [
                "/bin/zsh",
                "-lc",
                'git add src/cafe/ui/cli.py tests/unit/test_cli_setup.py && git commit -m "feat: support selective role updates in cafe setup"',
            ],
            check=False,
            text=True,
            capture_output=True,
        )

    def test_cleanup_codex_rules_removes_only_managed_rules_file(self, phase, monkeypatch, tmp_path):
        repo_root = tmp_path / "repo"
        git_dir = repo_root / ".git"
        (git_dir / "info").mkdir(parents=True)
        rules_dir = repo_root / "codex" / "rules"
        rules_dir.mkdir(parents=True)
        rules_file = rules_dir / "cafe-approved.rules"
        rules_file.write_text("prefix_rule(...)\n", encoding="utf-8")
        other_file = rules_dir / "team.rules"
        other_file.write_text("prefix_rule(...)\n", encoding="utf-8")
        monkeypatch.chdir(repo_root)

        with patch("cafe.utils.git_utils.get_git_toplevel", return_value=repo_root):
            phase._cleanup_codex_approved_rules()

        assert not rules_file.exists()
        assert other_file.exists()
        assert rules_dir.exists()

    def test_confirmed_status_cleans_up_codex_rules(self, phase, monkeypatch, tmp_path):
        repo_root = tmp_path / "repo"
        git_dir = repo_root / ".git"
        (git_dir / "info").mkdir(parents=True)
        rules_dir = repo_root / "codex" / "rules"
        rules_dir.mkdir(parents=True)
        rules_file = rules_dir / "cafe-approved.rules"
        rules_file.write_text("prefix_rule(...)\n", encoding="utf-8")
        monkeypatch.chdir(repo_root)

        phase.iteration = 2
        phase.agent_manager.get_total_token_usage.return_value = TokenUsage()
        with patch.object(phase, "_print_token_usage_summary"):
            with patch("cafe.utils.git_utils.get_git_toplevel", return_value=repo_root):
                result = phase._handle_standard_status_codes(
                    status_code=PhaseStatusCode.CONFIRMED,
                    response="CAFE_CONFIRMED",
                    continue_codes=[],
                    complete_codes=[],
                )

        assert result is not None
        assert result.status == PhaseStatus.COMPLETED
        assert not rules_file.exists()

    def test_split_codex_rules_ignores_leading_cd_segment(self, phase):
        command = (
            "cd /Users/YO_1/side_projects/cafe/.cafe/worktrees/issue187 && "
            "git add src/cafe/ui/cli.py tests/unit/test_cli_setup.py "
            ".cafe/issues/issue187/review/iteration_001/output.md "
            ".cafe/issues/issue187/develop/iteration_006/checklist.md && "
            'git commit -m "fix: preserve role-level setup fields in selective edit flow"'
        )

        segments = phase._split_command_for_codex_rules(command)

        assert segments == [
            [
                "git",
                "add",
                "src/cafe/ui/cli.py",
                "tests/unit/test_cli_setup.py",
                ".cafe/issues/issue187/review/iteration_001/output.md",
                ".cafe/issues/issue187/develop/iteration_006/checklist.md",
            ],
            [
                "git",
                "commit",
                "-m",
                "fix: preserve role-level setup fields in selective edit flow",
            ],
        ]

    def test_persist_codex_rules_uses_stable_git_prefixes(self, phase, monkeypatch, tmp_path):
        repo_root = tmp_path / "repo"
        git_dir = repo_root / ".git"
        (git_dir / "info").mkdir(parents=True)
        monkeypatch.chdir(repo_root)

        from cafe.core.types import PermissionDenial

        denial = PermissionDenial(
            tool_name="Bash",
            tool_input={
                "command": (
                    'git add src/cafe/ui/cli.py tests/unit/test_cli_setup.py && '
                    'git commit -m "fix: preserve role-level settings in selective setup flow"'
                )
            },
        )

        with patch("cafe.utils.git_utils.get_git_toplevel", return_value=repo_root):
            phase._persist_codex_approved_rules([denial], [0])

        rules_text = (repo_root / "codex" / "rules" / "cafe-approved.rules").read_text(encoding="utf-8")
        assert 'pattern = ["git", "add"]' in rules_text
        assert 'pattern = ["git", "commit", "-m"]' in rules_text

    def test_execute_approved_codex_commands_returns_agent_note(self, phase):
        from cafe.core.types import PermissionDenial

        denial = PermissionDenial(
            tool_name="Bash",
            tool_input={"command": 'git commit -m "msg"'},
        )

        with patch("cafe.core.phase.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
            note = phase._execute_approved_codex_commands([denial], [0])

        host_execution_file = phase._get_iteration_dir(phase.iteration) / "host_execution.json"
        assert "attempted by the host environment" in note
        assert "host_execution.json" in note
        assert host_execution_file.exists()
        content = json.loads(host_execution_file.read_text(encoding="utf-8"))
        assert content[0]["command"] == 'git commit -m "msg"'
        assert content[0]["ok"] is True
        mock_run.assert_called_once()

    def test_execute_approved_codex_commands_records_failures_without_raising(self, phase):
        from cafe.core.types import PermissionDenial

        denial = PermissionDenial(
            tool_name="Bash",
            tool_input={"command": 'git commit -m "msg"'},
        )

        with patch("cafe.core.phase.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="",
                stderr="nothing to commit, working tree clean\n",
                returncode=1,
            )
            note = phase._execute_approved_codex_commands([denial], [0])

        host_execution_file = phase._get_iteration_dir(phase.iteration) / "host_execution.json"
        content = json.loads(host_execution_file.read_text(encoding="utf-8"))
        assert "failed" in note
        assert content[0]["ok"] is False
        assert content[0]["returncode"] == 1

    def test_auto_host_execution_only_allows_safe_git_commands(self, phase):
        from cafe.core.types import PermissionDenial

        safe_denial = PermissionDenial(
            tool_name="Bash",
            tool_input={"command": 'git add a.py && git commit -m "msg"'},
        )
        unsafe_denial = PermissionDenial(
            tool_name="Bash",
            tool_input={"command": "git reset --hard HEAD"},
        )
        safe_with_cwd_denial = PermissionDenial(
            tool_name="Bash",
            tool_input={"command": 'git -C /tmp/worktree commit -m "msg"'},
        )

        assert phase._is_auto_host_executable_codex_denial(safe_denial) is True
        assert phase._is_auto_host_executable_codex_denial(safe_with_cwd_denial) is True
        assert phase._is_auto_host_executable_codex_denial(unsafe_denial) is False
