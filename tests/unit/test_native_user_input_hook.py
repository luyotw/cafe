"""Tests for workflow user-input hooks."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from cafe.core.hooks.native import PRLinkOpener, UserInputCollector
from cafe.core.playbook_runner import StepExecutionResult
from cafe.core.status_codes import PhaseStatusCode
from cafe.phases.generic_phase import GenericPhaseExecution
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor


class _FakePhase:
    def __init__(self, phase_dir: Path, iteration: int, issue_name: str = "demo") -> None:
        self.phase_dir = phase_dir
        self.iteration = iteration
        self.issue_name = issue_name
        self.step_user_inputs: dict[str, str] = {}

    def _get_iteration_dir(self, iteration: int) -> Path:
        return self.phase_dir / f"iteration_{iteration:03d}"

    def _get_versioned_file_path(self, step_name: str, iteration: int, phase_dir: Path) -> Path:
        return phase_dir / f"iteration_{iteration:03d}" / "output.md"

    def _load_previous_iteration_data(self) -> dict:
        context_file = self._get_iteration_dir(self.iteration - 1) / "context.json"
        return json.loads(context_file.read_text(encoding="utf-8"))


def test_user_input_collector_confirms_ready_for_review_without_running_agent(tmp_path: Path) -> None:
    phase_dir = tmp_path / "spec"
    prev_iter_dir = phase_dir / "iteration_002"
    prev_iter_dir.mkdir(parents=True, exist_ok=True)
    (prev_iter_dir / "context.json").write_text(
        json.dumps({"status_code": "CAFE_READY_FOR_REVIEW"}),
        encoding="utf-8",
    )
    (prev_iter_dir / "output.md").write_text("# Spec\n", encoding="utf-8")

    phase = _FakePhase(phase_dir=phase_dir, iteration=3)
    phase._ask_user_for_review_decision = MagicMock(return_value="confirm")
    phase._process_review_decision = MagicMock()

    hook = UserInputCollector()
    with patch.object(hook, "_display_previous_output") as mock_display_output, \
         patch.object(hook, "_display_previous_iteration_delta") as mock_display_delta:
        result = hook.run(
            stage="prepare_input",
            phase=phase,
            step_name="spec",
            step_def={"role": "pm"},
            agent_name="Roger",
        )

    assert result.continue_pipeline is False
    assert result.override_status_code == PhaseStatusCode.CONFIRMED
    assert result.events == [{"type": "review_confirmed", "step": "spec"}]
    mock_display_output.assert_called_once()
    mock_display_delta.assert_called_once()
    phase._ask_user_for_review_decision.assert_called_once()
    phase._process_review_decision.assert_called_once()


def test_user_input_collector_loads_interactive_qa_for_need_clarification(tmp_path: Path) -> None:
    phase_dir = tmp_path / "spec"
    prev_iter_dir = phase_dir / "iteration_001"
    prev_iter_dir.mkdir(parents=True, exist_ok=True)
    (prev_iter_dir / "context.json").write_text(
        json.dumps({"status_code": "CAFE_NEED_CLARIFICATION"}),
        encoding="utf-8",
    )
    (prev_iter_dir / "output.md").write_text("# Spec\n", encoding="utf-8")
    (prev_iter_dir / "questions.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<questions>
  <question id="1">
    <title>Question?</title>
    <options><option>Answer</option></options>
  </question>
</questions>
""",
        encoding="utf-8",
    )

    phase = _FakePhase(phase_dir=phase_dir, iteration=2)
    hook = UserInputCollector()

    with patch.object(hook, "_display_previous_output") as mock_display_output, \
         patch("cafe.core.hooks.native.interactive_qa_flow", return_value="Q1: Question?\nA1: Answer") as mock_qa:
        result = hook.run(
            stage="prepare_input",
            phase=phase,
            step_name="spec",
            step_def={"role": "pm"},
            agent_name="Roger",
        )

    assert result.context_updates["user_input"] == "Q1: Question?\nA1: Answer"
    assert result.events == [{"type": "user_input_collected", "step": "spec", "source": "questions_xml"}]
    assert phase.step_user_inputs["spec"] == "Q1: Question?\nA1: Answer"
    mock_display_output.assert_called_once()
    mock_qa.assert_called_once()


def test_execute_step_skips_checklist_validation_when_confirmed_without_agent_run(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    issue_dir.mkdir(parents=True, exist_ok=True)

    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="demo",
        playbook={"playbook": {"id": "default"}, "steps": {}},
        generic_phase=MagicMock(),
        agent_manager=MagicMock(),
        git_ops=MagicMock(),
        role_agent_map={"pm": "Roger"},
    )

    executor.generic_phase.execute = MagicMock(
        return_value=GenericPhaseExecution(
            response="",
            status_code=PhaseStatusCode.CONFIRMED,
            goto_target=None,
            events=[{"type": "review_confirmed", "step": "spec"}],
        )
    )
    executor._resolve_skill_name = MagicMock(return_value="spec_revise")
    executor._resolve_valid_status_codes = MagicMock(return_value=[PhaseStatusCode.CONFIRMED])
    executor._resolve_agent_name = MagicMock(return_value="Roger")
    executor._build_context = MagicMock(return_value={})
    executor._generate_checklist = MagicMock()
    executor._persist_final_status = MagicMock()
    executor._validate_and_retry_checklist_completion = MagicMock()

    result = executor.execute_step(
        "spec",
        {"role": "pm", "output_artifact": "spec", "valid_status_codes": ["CAFE_CONFIRMED"]},
        MagicMock(artifacts={}),
    )

    assert isinstance(result, StepExecutionResult)
    assert result.status_code == "CAFE_CONFIRMED"
    executor._validate_and_retry_checklist_completion.assert_not_called()


def test_pr_link_opener_opens_current_pr_url_when_confirmed() -> None:
    hook = PRLinkOpener()

    with patch("cafe.core.hooks.native.GitHubOps") as mock_github_ops, \
         patch("cafe.core.hooks.native.webbrowser.open") as mock_open:
        mock_github_ops.return_value.get_current_pr_url.return_value = "https://github.com/test/repo/pull/123"

        result = hook.run(stage="publish_output", status_code=PhaseStatusCode.CONFIRMED)

    mock_open.assert_called_once_with("https://github.com/test/repo/pull/123")
    assert result.events == [{"type": "pr_link_opened", "url": "https://github.com/test/repo/pull/123"}]


def test_pr_link_opener_noops_when_pr_url_unavailable() -> None:
    hook = PRLinkOpener()

    with patch("cafe.core.hooks.native.GitHubOps") as mock_github_ops, \
         patch("cafe.core.hooks.native.webbrowser.open") as mock_open:
        mock_github_ops.return_value.get_current_pr_url.side_effect = Exception("no pr")

        result = hook.run(stage="publish_output", status_code=PhaseStatusCode.CONFIRMED)

    mock_open.assert_not_called()
    assert result.events == []
