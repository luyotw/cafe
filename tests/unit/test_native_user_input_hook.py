"""Tests for workflow user-input hooks."""

import hashlib
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cafe.core.hooks.native import (
    GitHubIssueFetcher,
    GitHubPRCreator,
    InitialInputProviderResolver,
    PRCommentPoster,
    PRLinkOpener,
    UserInputCollector,
)
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.workflow_models import StepExecutionResult
from cafe.phases.generic_phase import GenericPhaseExecution
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor

PUBLISH_STEP = {
    "capability_requests": ["cafe.pr.publish"],
    "behavior": {"publish_confirmation": True},
}


def _browser_phase(*, open_pr: bool) -> SimpleNamespace:
    return SimpleNamespace(open_pr=open_pr)


def _enable_remote_pr(issue_dir: Path) -> None:
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "issue.yaml").write_text("pr:\n  auto_create: true\n", encoding="utf-8")


class _FakePhase:
    def __init__(self, phase_dir: Path, iteration: int, issue_name: str = "demo") -> None:
        self.phase_dir = phase_dir
        self.issue_dir = phase_dir.parent
        self.phase_name = phase_dir.name
        self.iteration = iteration
        self.issue_name = issue_name
        self.interactive = True
        self.step_user_inputs: dict[str, str] = {}

    def _get_iteration_dir(self, iteration: int) -> Path:
        return self.phase_dir / f"iteration_{iteration:03d}"

    def _get_versioned_file_path(self, step_name: str, iteration: int, phase_dir: Path) -> Path:
        return phase_dir / f"iteration_{iteration:03d}" / "output.md"

    def _load_previous_iteration_data(self) -> dict:
        context_file = self._get_iteration_dir(self.iteration - 1) / "iteration.json"
        if not context_file.exists():
            return {}
        return json.loads(context_file.read_text(encoding="utf-8"))

    def _get_issue_config_value(self, config_file: Path, key: str):
        import yaml

        if not config_file.exists():
            return None
        data = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
        value = data
        for part in key.split("."):
            if not isinstance(value, dict) or part not in value:
                return None
            value = value[part]
        return value


def _record_previous_step_status(issue_dir: Path, step_name: str, status_code: str) -> None:
    """Seed the blackboard with a `step_completed` event for the prior iteration."""
    from cafe.core.blackboard import BlackboardStore

    issue_dir.mkdir(parents=True, exist_ok=True)
    store = BlackboardStore(issue_dir)
    state = store.load_or_create(step_name)
    store.record_event(
        state,
        "step_completed",
        {"step": step_name, "status_code": status_code},
    )


def test_user_input_collector_confirms_ready_for_review_without_running_agent(
    tmp_path: Path,
) -> None:
    phase_dir = tmp_path / "spec"
    prev_iter_dir = phase_dir / "iteration_002"
    prev_iter_dir.mkdir(parents=True, exist_ok=True)
    (prev_iter_dir / "output.md").write_text("# Spec\n", encoding="utf-8")
    _record_previous_step_status(tmp_path, "spec", "ready_for_review")

    phase = _FakePhase(phase_dir=phase_dir, iteration=3)
    phase._ask_user_for_review_decision = MagicMock(return_value="confirm")
    phase._process_review_decision = MagicMock()

    hook = UserInputCollector()
    with (
        patch.object(hook, "_display_previous_output") as mock_display_output,
        patch.object(hook, "_display_previous_iteration_delta") as mock_display_delta,
    ):
        result = hook.run(
            stage="prepare_input",
            phase=phase,
            step_name="spec",
            step_def={"role": "pm", "on": {"confirm_output": "spec"}},
            agent_name="Roger",
        )

    assert result.continue_pipeline is False
    assert result.override_status_code == PhaseStatusCode.CONFIRMED
    assert result.events == [
        {"type": "review_confirmed", "step": "spec"},
        {"type": "review_confirmed_advance", "step": "spec"},
    ]
    mock_display_output.assert_not_called()
    mock_display_delta.assert_called_once()
    phase._ask_user_for_review_decision.assert_called_once()
    phase._process_review_decision.assert_called_once()


def test_user_input_collector_brief_ready_for_review_uses_delta_when_confirm_output_declared(
    tmp_path: Path,
) -> None:
    phase_dir = tmp_path / "brief"
    prev_prev_iter_dir = phase_dir / "iteration_001"
    prev_prev_iter_dir.mkdir(parents=True, exist_ok=True)
    (prev_prev_iter_dir / "output.md").write_text("# Brief v1\n", encoding="utf-8")

    prev_iter_dir = phase_dir / "iteration_002"
    prev_iter_dir.mkdir(parents=True, exist_ok=True)
    (prev_iter_dir / "output.md").write_text("# Brief v2\n", encoding="utf-8")
    _record_previous_step_status(tmp_path, "brief", "ready_for_review")

    phase = _FakePhase(phase_dir=phase_dir, iteration=3)
    phase._ask_user_for_review_decision = MagicMock(return_value="confirm")
    phase._process_review_decision = MagicMock()

    hook = UserInputCollector()
    with (
        patch.object(hook, "_display_previous_output") as mock_display_output,
        patch.object(hook, "_display_previous_iteration_delta") as mock_display_delta,
    ):
        hook.run(
            stage="prepare_input",
            phase=phase,
            step_name="brief",
            step_def={"role": "editor", "on": {"confirm_output": "brief"}},
            agent_name="Roger",
        )

    mock_display_output.assert_not_called()
    mock_display_delta.assert_called_once()


def test_user_input_collector_plan_ready_for_review_skips_full_output_display_when_delta_available(
    tmp_path: Path,
) -> None:
    phase_dir = tmp_path / "plan"
    prev_prev_iter_dir = phase_dir / "iteration_001"
    prev_prev_iter_dir.mkdir(parents=True, exist_ok=True)
    (prev_prev_iter_dir / "output.md").write_text("# Plan v1\n", encoding="utf-8")

    prev_iter_dir = phase_dir / "iteration_002"
    prev_iter_dir.mkdir(parents=True, exist_ok=True)
    (prev_iter_dir / "output.md").write_text("# Plan v2\n", encoding="utf-8")
    _record_previous_step_status(tmp_path, "plan", "ready_for_review")

    phase = _FakePhase(phase_dir=phase_dir, iteration=3)
    phase._ask_user_for_review_decision = MagicMock(return_value="confirm")
    phase._process_review_decision = MagicMock()

    hook = UserInputCollector()
    with (
        patch.object(hook, "_display_previous_output") as mock_display_output,
        patch.object(hook, "_display_previous_iteration_delta") as mock_display_delta,
    ):
        result = hook.run(
            stage="prepare_input",
            phase=phase,
            step_name="plan",
            step_def={"role": "developer", "on": {"confirm_output": "plan"}},
            agent_name="David",
        )

    assert result.continue_pipeline is False
    assert result.override_status_code == PhaseStatusCode.CONFIRMED
    mock_display_output.assert_not_called()
    mock_display_delta.assert_called_once()


def test_user_input_collector_plan_ready_for_review_falls_back_to_full_output_without_delta(
    tmp_path: Path,
) -> None:
    phase_dir = tmp_path / "plan"
    prev_iter_dir = phase_dir / "iteration_001"
    prev_iter_dir.mkdir(parents=True, exist_ok=True)
    (prev_iter_dir / "output.md").write_text("# Plan\n", encoding="utf-8")
    _record_previous_step_status(tmp_path, "plan", "ready_for_review")

    phase = _FakePhase(phase_dir=phase_dir, iteration=2)
    phase._ask_user_for_review_decision = MagicMock(return_value="confirm")
    phase._process_review_decision = MagicMock()

    hook = UserInputCollector()
    with (
        patch.object(hook, "_display_previous_output") as mock_display_output,
        patch.object(hook, "_display_previous_iteration_delta") as mock_display_delta,
    ):
        mock_display_delta.return_value = False
        result = hook.run(
            stage="prepare_input",
            phase=phase,
            step_name="plan",
            step_def={"role": "developer", "on": {"confirm_output": "plan"}},
            agent_name="David",
        )

    assert result.continue_pipeline is False
    assert result.override_status_code == PhaseStatusCode.CONFIRMED
    mock_display_delta.assert_called_once()
    mock_display_output.assert_called_once()


def test_user_input_collector_uses_resolved_publish_contract_from_context(tmp_path: Path) -> None:
    """UT-004: playbook-level publish behavior bypasses duplicate confirmation."""
    phase_dir = tmp_path / "release"
    previous_dir = phase_dir / "iteration_001"
    previous_dir.mkdir(parents=True, exist_ok=True)
    (previous_dir / "output.md").write_text("# Release\n", encoding="utf-8")
    _record_previous_step_status(tmp_path, "release", "ready_for_review")

    phase = _FakePhase(phase_dir=phase_dir, iteration=2)
    phase._ask_user_for_review_decision = MagicMock(return_value="confirm")
    hook = UserInputCollector()

    result = hook.run(
        stage="prepare_input",
        phase=phase,
        step_name="release",
        step_def={"role": "developer"},
        context={"publish_confirmation": True},
        agent_name="David",
    )

    assert result.continue_pipeline is True
    phase._ask_user_for_review_decision.assert_not_called()


def test_user_input_collector_loads_interactive_qa_for_need_clarification(tmp_path: Path) -> None:
    phase_dir = tmp_path / "spec"
    prev_iter_dir = phase_dir / "iteration_001"
    prev_iter_dir.mkdir(parents=True, exist_ok=True)
    (prev_iter_dir / "output.md").write_text("# Spec\n", encoding="utf-8")
    _record_previous_step_status(tmp_path, "spec", "need_clarification")
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

    with (
        patch.object(hook, "_display_previous_output") as mock_display_output,
        patch(
            "cafe.core.hooks.native.interactive_qa_flow", return_value="Q1: Question?\nA1: Answer"
        ) as mock_qa,
    ):
        result = hook.run(
            stage="prepare_input",
            phase=phase,
            step_name="spec",
            step_def={"role": "pm"},
            agent_name="Roger",
        )

    assert result.context_updates["user_input"] == "Q1: Question?\nA1: Answer"
    assert result.events == [
        {"type": "user_input_collected", "step": "spec", "source": "questions_xml"}
    ]
    assert phase.step_user_inputs["spec"] == "Q1: Question?\nA1: Answer"
    mock_display_output.assert_called_once()
    mock_qa.assert_called_once()


def test_user_input_collector_falls_back_to_prompt_when_no_questions_xml(tmp_path: Path) -> None:
    phase_dir = tmp_path / "spec"
    prev_iter_dir = phase_dir / "iteration_001"
    prev_iter_dir.mkdir(parents=True, exist_ok=True)
    (prev_iter_dir / "output.md").write_text("# Spec\n", encoding="utf-8")
    _record_previous_step_status(tmp_path, "spec", "need_clarification")

    phase = _FakePhase(phase_dir=phase_dir, iteration=2)
    hook = UserInputCollector()

    with (
        patch.object(hook, "_display_previous_output"),
        patch("cafe.core.hooks.native.interactive_qa_flow") as mock_qa,
        patch(
            "cafe.core.hooks.native.prompt_multiline", return_value="manual clarification"
        ) as mock_prompt,
    ):
        result = hook.run(
            stage="prepare_input",
            phase=phase,
            step_name="spec",
            step_def={"role": "pm"},
            agent_name="Roger",
        )

    assert result.context_updates["user_input"] == "manual clarification"
    assert result.events == [{"type": "user_input_collected", "step": "spec", "source": "prompt"}]
    mock_qa.assert_not_called()
    mock_prompt.assert_called_once()


def test_user_input_collector_falls_back_to_prompt_when_questions_xml_invalid(
    tmp_path: Path,
) -> None:
    phase_dir = tmp_path / "spec"
    prev_iter_dir = phase_dir / "iteration_001"
    prev_iter_dir.mkdir(parents=True, exist_ok=True)
    (prev_iter_dir / "output.md").write_text("# Spec\n", encoding="utf-8")
    (prev_iter_dir / "questions.xml").write_text(
        "<questions><question id='1'><title>No options</title></question></questions>",
        encoding="utf-8",
    )
    _record_previous_step_status(tmp_path, "spec", "need_clarification")

    phase = _FakePhase(phase_dir=phase_dir, iteration=2)
    hook = UserInputCollector()

    with (
        patch.object(hook, "_display_previous_output"),
        patch("cafe.core.hooks.native.interactive_qa_flow") as mock_qa,
        patch(
            "cafe.core.hooks.native.prompt_multiline", return_value="fallback answer"
        ) as mock_prompt,
    ):
        result = hook.run(
            stage="prepare_input",
            phase=phase,
            step_name="spec",
            step_def={"role": "pm"},
            agent_name="Roger",
        )

    assert result.context_updates["user_input"] == "fallback answer"
    assert result.events[0]["source"] == "questions_xml"
    mock_qa.assert_not_called()
    mock_prompt.assert_called_once()


def test_user_input_collector_noninteractive_reads_existing_user_input_file(tmp_path: Path) -> None:
    phase_dir = tmp_path / "spec"
    prev_iter_dir = phase_dir / "iteration_001"
    current_iter_dir = phase_dir / "iteration_002"
    prev_iter_dir.mkdir(parents=True, exist_ok=True)
    current_iter_dir.mkdir(parents=True, exist_ok=True)
    (prev_iter_dir / "output.md").write_text("# Spec\n", encoding="utf-8")
    _record_previous_step_status(tmp_path, "spec", "need_clarification")
    (current_iter_dir / "user_input.md").write_text(
        "Resume answer from workflow",
        encoding="utf-8",
    )

    phase = _FakePhase(phase_dir=phase_dir, iteration=2)
    phase.interactive = False
    hook = UserInputCollector()

    result = hook.run(
        stage="prepare_input",
        phase=phase,
        step_name="spec",
        step_def={"role": "pm"},
        agent_name="Roger",
    )

    assert result.context_updates["user_input"] == "Resume answer from workflow"
    assert result.events[0]["source"] == "user_input_file"


def test_user_input_collector_reuses_existing_user_input_file_without_reasking(
    tmp_path: Path,
) -> None:
    phase_dir = tmp_path / "spec"
    prev_iter_dir = phase_dir / "iteration_001"
    current_iter_dir = phase_dir / "iteration_002"
    prev_iter_dir.mkdir(parents=True, exist_ok=True)
    current_iter_dir.mkdir(parents=True, exist_ok=True)
    (prev_iter_dir / "output.md").write_text("# Spec\n", encoding="utf-8")
    _record_previous_step_status(tmp_path, "spec", "need_clarification")
    (current_iter_dir / "user_input.md").write_text(
        "Q1: Question?\nA1: Confirmed answer",
        encoding="utf-8",
    )

    phase = _FakePhase(phase_dir=phase_dir, iteration=2)
    hook = UserInputCollector()

    with (
        patch.object(hook, "_display_previous_output") as mock_display_output,
        patch("cafe.core.hooks.native.interactive_qa_flow") as mock_qa,
    ):
        result = hook.run(
            stage="prepare_input",
            phase=phase,
            step_name="spec",
            step_def={"role": "pm"},
            agent_name="Roger",
        )

    assert result.context_updates["user_input"] == "Q1: Question?\nA1: Confirmed answer"
    assert result.events == [
        {"type": "user_input_collected", "step": "spec", "source": "user_input_file"}
    ]
    assert phase.step_user_inputs["spec"] == "Q1: Question?\nA1: Confirmed answer"
    mock_display_output.assert_not_called()
    mock_qa.assert_not_called()


def test_user_input_collector_prompts_initial_plan_user_input_on_first_iteration(
    tmp_path: Path,
) -> None:
    phase_dir = tmp_path / "plan"
    phase_dir.mkdir(parents=True, exist_ok=True)
    phase = _FakePhase(phase_dir=phase_dir, iteration=1)
    hook = UserInputCollector()

    with patch(
        "cafe.ui.inquirer_prompts.prompt_multiline", return_value="Follow strict TDD first"
    ) as mock_prompt:
        result = hook.run(
            stage="prepare_input",
            phase=phase,
            step_name="plan",
            step_def={
                "role": "developer",
                "skill": "cafe-plan",
                "human_tasks": [{"trigger": "initial", "task_id": "development-guide"}],
            },
            agent_name="David",
        )

    assert result.context_updates["user_input"] == "Follow strict TDD first"
    assert result.events[0]["type"] == "human_task_completed"
    assert phase.step_user_inputs["plan"] == "Follow strict TDD first"
    prompt_text = mock_prompt.call_args.args[0]
    assert prompt_text == "Please enter development guide (can be left empty)"


def test_user_input_collector_skips_initial_plan_prompt_in_noninteractive_mode(
    tmp_path: Path,
) -> None:
    phase_dir = tmp_path / "plan"
    phase_dir.mkdir(parents=True, exist_ok=True)
    phase = _FakePhase(phase_dir=phase_dir, iteration=1)
    phase.interactive = False
    hook = UserInputCollector()

    with patch("cafe.ui.inquirer_prompts.prompt_multiline") as mock_prompt:
        result = hook.run(
            stage="prepare_input",
            phase=phase,
            step_name="plan",
            step_def={
                "role": "developer",
                "skill": "cafe-plan",
                "human_tasks": [{"trigger": "initial", "task_id": "development-guide"}],
            },
            agent_name="David",
        )

    assert result.context_updates["user_input"] == ""
    assert result.events[0]["type"] == "human_task_completed"
    assert phase.step_user_inputs["plan"] == ""
    mock_prompt.assert_not_called()


def test_github_issue_fetcher_uses_context_user_input_without_prompting(tmp_path: Path) -> None:
    phase_dir = tmp_path / "spec"
    phase = _FakePhase(phase_dir=phase_dir, iteration=1)
    output_file = phase._get_iteration_dir(1) / "output.md"
    hook = GitHubIssueFetcher()

    with (
        patch.object(hook, "_prompt_input_method") as mock_prompt_method,
        patch.object(hook, "_prompt_manual_input") as mock_prompt_manual,
        patch.object(hook, "_fetch_github_issue") as mock_fetch_issue,
    ):
        result = hook.run(
            stage="prepare_input",
            phase=phase,
            step_name="spec",
            output_file=output_file,
            context={"user_input": "Build a standalone myip command."},
        )

    assert (
        output_file.read_text(encoding="utf-8")
        == "# Initial Requirements\n\nBuild a standalone myip command.\n"
    )
    assert result.context_updates["user_input"] == "Build a standalone myip command."
    assert result.events == [
        {
            "type": "user_input_collected",
            "step": "spec",
            "source": "workflow_user_input",
        }
    ]
    mock_prompt_method.assert_not_called()
    mock_prompt_manual.assert_not_called()
    mock_fetch_issue.assert_not_called()


def test_github_issue_fetcher_fetches_configured_issue_noninteractively(tmp_path: Path) -> None:
    """U8 — legacy GitHub config remains usable without an interactive prompt."""
    phase_dir = tmp_path / "spec"
    phase = _FakePhase(phase_dir=phase_dir, iteration=1)
    phase.interactive = False
    phase.issue_dir.mkdir(parents=True, exist_ok=True)
    (phase.issue_dir / "issue.yaml").write_text(
        "spec:\n  input_method: github\n  issue_id: 346\n", encoding="utf-8"
    )
    output_file = phase._get_iteration_dir(1) / "output.md"
    hook = GitHubIssueFetcher()

    with (
        patch.object(hook, "_prompt_input_method") as mock_prompt_method,
        patch.object(hook, "_prompt_manual_input") as mock_prompt_manual,
        patch.object(
            hook,
            "_fetch_github_issue",
            return_value="**Issue Title:** Restore legacy input",
        ) as mock_fetch_issue,
    ):
        result = hook.run(
            stage="prepare_input",
            phase=phase,
            step_name="spec",
            output_file=output_file,
        )

    assert "Restore legacy input" in output_file.read_text(encoding="utf-8")
    assert result.context_updates == {"user_input": "**Issue Title:** Restore legacy input"}
    assert result.events == [{"type": "user_input_collected", "step": "spec", "source": "github"}]
    mock_prompt_method.assert_not_called()
    mock_prompt_manual.assert_not_called()
    mock_fetch_issue.assert_called_once_with(346)


def test_github_only_provider_prompts_for_issue_id_interactively(tmp_path: Path) -> None:
    """U5 — a GitHub-only declaration obtains its issue ID at the trusted UI boundary."""
    phase = _FakePhase(phase_dir=tmp_path / "intake", iteration=1)
    output_file = phase._get_iteration_dir(1) / "output.md"
    hook = InitialInputProviderResolver()

    prompt = MagicMock(return_value=("github", 346))
    fetch = MagicMock(return_value="**Issue Title:** Gather requirements")
    result = hook.run(
        stage="prepare_input",
        phase=phase,
        step_name="intake",
        step_def={
            "output_artifact": "intake_brief",
            "initial_input": {
                "providers": ["github_issue"],
                "bind": {"artifact": "intake_brief", "prompt_context": "user_input"},
            },
        },
        output_file=output_file,
        initial_input_prompt_input_method=prompt,
        initial_input_fetch_github_issue=fetch,
    )

    assert output_file.read_text(encoding="utf-8") == ("**Issue Title:** Gather requirements\n")
    assert result.context_updates == {"user_input": "**Issue Title:** Gather requirements"}
    prompt.assert_called_once_with()
    fetch.assert_called_once_with(346)


def test_github_only_provider_rejects_undeclared_prompt_selection_without_persisting(
    tmp_path: Path,
) -> None:
    """U5 — an unavailable interactive choice cannot poison a later retry."""
    phase = _FakePhase(phase_dir=tmp_path / "intake", iteration=1)
    output_file = phase._get_iteration_dir(1) / "output.md"
    hook = InitialInputProviderResolver()

    with pytest.raises(ValueError, match="not declared"):
        hook.run(
            stage="prepare_input",
            phase=phase,
            step_name="intake",
            step_def={
                "output_artifact": "intake_brief",
                "initial_input": {
                    "providers": ["github_issue"],
                    "bind": {"artifact": "intake_brief"},
                },
            },
            output_file=output_file,
            initial_input_prompt_input_method=MagicMock(return_value=("manual", None)),
        )

    assert not (phase.issue_dir / "issue.yaml").exists()
    assert not output_file.exists()


def test_initial_input_provider_delivers_prefilled_manual_text_to_custom_entry_step(
    tmp_path: Path,
) -> None:
    """U4/U6 — declared custom entry bindings receive invocation input once."""
    phase_dir = tmp_path / "intake"
    phase = _FakePhase(phase_dir=phase_dir, iteration=1)
    output_file = phase._get_iteration_dir(1) / "output.md"
    hook = InitialInputProviderResolver()

    result = hook.run(
        stage="prepare_input",
        phase=phase,
        step_name="intake",
        step_def={
            "output_artifact": "intake_brief",
            "initial_input": {
                "providers": ["manual_text", "github_issue"],
                "bind": {"artifact": "intake_brief", "prompt_context": "user_input"},
            },
        },
        output_file=output_file,
        context={"user_input": "Summarize the incoming customer report."},
    )

    assert output_file.read_text(encoding="utf-8") == ("Summarize the incoming customer report.\n")
    assert result.context_updates == {"user_input": "Summarize the incoming customer report."}
    assert result.events == [
        {"type": "initial_input_resolved", "step": "intake", "provider": "manual_text"}
    ]


def test_initial_input_provider_skips_resume_and_existing_artifact(tmp_path: Path) -> None:
    """U9 — providers cannot overwrite resumed or already seeded entry output."""
    hook = InitialInputProviderResolver()
    step_def = {
        "output_artifact": "intake_brief",
        "initial_input": {
            "providers": ["manual_text"],
            "bind": {"artifact": "intake_brief", "prompt_context": "user_input"},
        },
    }
    resumed_phase = _FakePhase(phase_dir=tmp_path / "intake", iteration=2)
    resumed_output = resumed_phase._get_iteration_dir(2) / "output.md"

    resumed = hook.run(
        stage="prepare_input",
        phase=resumed_phase,
        step_name="intake",
        step_def=step_def,
        output_file=resumed_output,
        context={"user_input": "new content"},
    )

    phase = _FakePhase(phase_dir=tmp_path / "existing", iteration=1)
    output = phase._get_iteration_dir(1) / "output.md"
    output.parent.mkdir(parents=True)
    output.write_text("existing artifact", encoding="utf-8")
    existing = hook.run(
        stage="prepare_input",
        phase=phase,
        step_name="intake",
        step_def=step_def,
        output_file=output,
        context={"user_input": "new content"},
    )

    assert resumed.events == []
    assert existing.events == []
    assert output.read_text(encoding="utf-8") == "existing artifact"


def test_builtin_initial_input_preserves_legacy_requirements_seed(
    tmp_path: Path,
) -> None:
    """I3 — the shared built-in resolver retains the legacy initial-input experience."""
    import yaml

    playbook_name = "standard"
    playbook_file = (
        Path(__file__).parents[2] / "src" / "cafe" / "data" / "playbooks" / f"{playbook_name}.yaml"
    )
    playbook = yaml.safe_load(playbook_file.read_text(encoding="utf-8"))
    step_def = playbook["steps"]["spec"]
    assert step_def["hooks"]["prepare_input"][0] == "InitialInputProviderResolver"

    phase = _FakePhase(phase_dir=tmp_path / "spec", iteration=1)
    output_file = phase._get_iteration_dir(1) / "output.md"

    result = InitialInputProviderResolver().run(
        stage="prepare_input",
        phase=phase,
        step_name="spec",
        step_def=step_def,
        output_file=output_file,
        context={"user_input": "Preserve the established workflow kickoff."},
    )

    assert output_file.read_text(encoding="utf-8") == (
        "# Initial Requirements\n\nPreserve the established workflow kickoff.\n"
    )
    assert result.context_updates == {"user_input": "Preserve the established workflow kickoff."}


def test_builtin_initial_input_seeds_empty_legacy_requirements_non_interactively(
    tmp_path: Path,
) -> None:
    """I3 — the shared built-in resolver retains the empty legacy requirements seed."""
    import yaml

    playbook_name = "standard"
    playbook_file = (
        Path(__file__).parents[2] / "src" / "cafe" / "data" / "playbooks" / f"{playbook_name}.yaml"
    )
    step_def = yaml.safe_load(playbook_file.read_text(encoding="utf-8"))["steps"]["spec"]
    phase = _FakePhase(phase_dir=tmp_path / "spec", iteration=1)
    phase.interactive = False
    phase.issue_dir.mkdir(parents=True, exist_ok=True)
    (phase.issue_dir / "issue.yaml").write_text(
        "initial_input:\n  provider: manual_text\n", encoding="utf-8"
    )
    output_file = phase._get_iteration_dir(1) / "output.md"

    result = InitialInputProviderResolver().run(
        stage="prepare_input",
        phase=phase,
        step_name="spec",
        step_def=step_def,
        output_file=output_file,
    )

    assert output_file.read_text(encoding="utf-8") == "# Initial Requirements\n\n\n"
    assert result.context_updates == {"user_input": ""}
    assert result.events == [
        {"type": "initial_input_resolved", "step": "spec", "provider": "manual_text"}
    ]


def test_builtin_initial_input_reuses_legacy_github_ui_and_formatter(tmp_path: Path) -> None:
    """I3 — generic built-in resolution keeps the established GitHub interaction."""
    import yaml

    playbook_file = Path(__file__).parents[2] / "src/cafe/data/playbooks/standard.yaml"
    step_def = yaml.safe_load(playbook_file.read_text(encoding="utf-8"))["steps"]["spec"]
    phase = _FakePhase(phase_dir=tmp_path / "spec", iteration=1)
    output_file = phase._get_iteration_dir(1) / "output.md"
    prompt = MagicMock(return_value=("github_issue", 346))

    with (
        patch.object(
            GitHubIssueFetcher,
            "_prompt_and_save_input_method",
            return_value=prompt,
        ) as select_provider,
        patch.object(
            GitHubIssueFetcher,
            "_fetch_github_issue",
            return_value="**Issue Title:** Restore compatibility",
        ) as fetch_issue,
    ):
        result = InitialInputProviderResolver().run(
            stage="prepare_input",
            phase=phase,
            step_name="spec",
            step_def=step_def,
            output_file=output_file,
        )

    select_provider.assert_called_once_with(phase)
    prompt.assert_called_once_with()
    fetch_issue.assert_called_once_with(346)
    assert output_file.read_text(encoding="utf-8") == (
        "# Initial Requirements\n\n**Issue Title:** Restore compatibility\n"
    )
    assert result.context_updates == {"user_input": "**Issue Title:** Restore compatibility"}


def test_initial_input_provider_preserves_github_fetch_failure_guidance(tmp_path: Path) -> None:
    """U5 — operator-facing provider errors retain the host boundary's remedy."""
    phase = _FakePhase(phase_dir=tmp_path / "intake", iteration=1)
    phase.interactive = False
    phase.issue_dir.mkdir(parents=True, exist_ok=True)
    (phase.issue_dir / "issue.yaml").write_text(
        "initial_input:\n  provider: github_issue\n  issue_id: 346\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="gh auth login"):
        InitialInputProviderResolver().run(
            stage="prepare_input",
            phase=phase,
            step_name="intake",
            step_def={
                "output_artifact": "intake_brief",
                "initial_input": {
                    "providers": ["github_issue"],
                    "bind": {"artifact": "intake_brief"},
                },
            },
            output_file=phase._get_iteration_dir(1) / "output.md",
            initial_input_fetch_github_issue=MagicMock(
                side_effect=RuntimeError("GitHub CLI unavailable; run gh auth login")
            ),
        )


def test_github_issue_fetcher_uses_phase_step_user_input_without_prompting(tmp_path: Path) -> None:
    phase_dir = tmp_path / "spec"
    phase = _FakePhase(phase_dir=phase_dir, iteration=1)
    phase.step_user_inputs["spec"] = "Build a standalone myip command."
    output_file = phase._get_iteration_dir(1) / "output.md"
    hook = GitHubIssueFetcher()

    with (
        patch.object(hook, "_prompt_input_method") as mock_prompt_method,
        patch.object(hook, "_prompt_manual_input") as mock_prompt_manual,
        patch.object(hook, "_fetch_github_issue") as mock_fetch_issue,
    ):
        result = hook.run(
            stage="prepare_input",
            phase=phase,
            step_name="spec",
            output_file=output_file,
        )

    assert (
        output_file.read_text(encoding="utf-8")
        == "# Initial Requirements\n\nBuild a standalone myip command.\n"
    )
    assert result.context_updates["user_input"] == "Build a standalone myip command."
    assert result.events == [
        {
            "type": "user_input_collected",
            "step": "spec",
            "source": "workflow_user_input",
        }
    ]
    mock_prompt_method.assert_not_called()
    mock_prompt_manual.assert_not_called()
    mock_fetch_issue.assert_not_called()


def test_execute_step_skips_checklist_validation_when_confirmed_without_agent_run(
    tmp_path: Path,
) -> None:
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
    executor._resolve_agent_name = MagicMock(return_value="Roger")
    executor._apply_step_agent_model = MagicMock()
    executor._build_context = MagicMock(return_value={})
    executor._generate_checklist = MagicMock()
    executor._persist_final_status = MagicMock()
    executor._validate_and_retry_checklist_completion = MagicMock()

    result = executor.execute_step(
        "spec",
        {"role": "pm", "output_artifact": "spec", "valid_intents": ["confirmed"]},
        MagicMock(artifacts={}),
    )

    assert isinstance(result, StepExecutionResult)
    assert result.status_code == "confirmed"
    executor._validate_and_retry_checklist_completion.assert_not_called()


def test_pr_link_opener_opens_current_pr_url_when_confirmed() -> None:
    hook = PRLinkOpener()

    with (
        patch("cafe.core.capabilities.GitHubOps") as mock_github_ops,
        patch("cafe.core.capabilities.webbrowser.open") as mock_open,
        patch("cafe.core.capabilities.sys.stdin.isatty", return_value=True),
        patch("cafe.core.capabilities._current_repo_slug", return_value="test/repo"),
    ):
        mock_github_ops.return_value.get_current_pr_url.return_value = (
            "https://github.com/test/repo/pull/123"
        )

        result = hook.run(
            stage="publish_output",
            phase=_browser_phase(open_pr=True),
            status_code=PhaseStatusCode.CONFIRMED,
            step_def=PUBLISH_STEP,
        )

    mock_open.assert_called_once_with("https://github.com/test/repo/pull/123")
    assert result.events == [
        {"type": "pr_link_opened", "url": "https://github.com/test/repo/pull/123"},
    ]


def test_pr_link_opener_requires_explicit_opt_in_even_with_a_tty() -> None:
    hook = PRLinkOpener()

    with (
        patch("cafe.core.capabilities.GitHubOps") as mock_github_ops,
        patch("cafe.core.capabilities.webbrowser.open") as mock_open,
        patch("cafe.core.capabilities.sys.stdin.isatty", return_value=True),
        patch("cafe.core.capabilities._current_repo_slug", return_value="test/repo"),
    ):
        mock_github_ops.return_value.get_current_pr_url.return_value = (
            "https://github.com/test/repo/pull/123"
        )

        result = hook.run(
            stage="publish_output",
            phase=_browser_phase(open_pr=False),
            status_code=PhaseStatusCode.CONFIRMED,
            step_def=PUBLISH_STEP,
        )

    mock_open.assert_not_called()
    assert result.events == []


def test_pr_link_opener_noops_when_pr_url_unavailable() -> None:
    hook = PRLinkOpener()

    with (
        patch("cafe.core.capabilities.GitHubOps") as mock_github_ops,
        patch("cafe.core.capabilities.webbrowser.open") as mock_open,
        patch("cafe.core.capabilities._current_repo_slug", return_value="test/repo"),
    ):
        mock_github_ops.return_value.get_current_pr_url.side_effect = Exception("no pr")

        result = hook.run(
            stage="publish_output",
            phase=_browser_phase(open_pr=True),
            status_code=PhaseStatusCode.CONFIRMED,
            step_def=PUBLISH_STEP,
        )

    mock_open.assert_not_called()
    assert result.events == []


def test_pr_link_opener_noops_when_browser_open_fails() -> None:
    hook = PRLinkOpener()

    with (
        patch("cafe.core.capabilities.GitHubOps") as mock_github_ops,
        patch(
            "cafe.core.capabilities.webbrowser.open", side_effect=Exception("blocked")
        ) as mock_open,
        patch("cafe.core.capabilities.sys.stdin.isatty", return_value=True),
        patch("cafe.core.capabilities._current_repo_slug", return_value="test/repo"),
    ):
        mock_github_ops.return_value.get_current_pr_url.return_value = (
            "https://github.com/test/repo/pull/123"
        )

        result = hook.run(
            stage="publish_output",
            phase=_browser_phase(open_pr=True),
            status_code=PhaseStatusCode.CONFIRMED,
            step_def=PUBLISH_STEP,
        )

    mock_open.assert_called_once_with("https://github.com/test/repo/pull/123")
    assert result.events == []


def test_github_pr_creator_publish_output_runs_sync_pr_script(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    _enable_remote_pr(issue_dir)
    phase_dir = issue_dir / "pr"
    output_file = phase_dir / "iteration_001" / "output.md"
    publish_request_file = phase_dir / "iteration_001" / "publish_request.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("# Test PR\n\nBody\n", encoding="utf-8")
    publish_request_file.write_text(
        json.dumps(
            {
                "capability": "cafe.pr.publish",
                "args": {
                    "output": ".cafe/issues/demo/pr/iteration_001/output.md",
                    "base": "develop",
                },
                "permissions": {
                    "network": ["github.com", "api.github.com"],
                    "writes": [".git", ".cafe/issues/demo"],
                },
            }
        ),
        encoding="utf-8",
    )

    phase = _FakePhase(phase_dir=phase_dir, iteration=1)
    phase.git_ops = MagicMock()
    phase.git_ops.get_repo_root.return_value = tmp_path

    completed = MagicMock()
    completed.returncode = 0
    completed.stdout = (
        '{"action":"created","pr_number":"42",' '"pr_url":"https://github.com/test/repo/pull/42"}\n'
    )
    completed.stderr = ""

    hook = GitHubPRCreator()
    with patch("cafe.core.capabilities.subprocess.run", return_value=completed) as mock_run:
        result = hook.run(
            stage="publish_output",
            phase=phase,
            step_name="pr",
            step_def=PUBLISH_STEP,
            output_file=output_file,
            publish_request_file=publish_request_file,
            status_code=PhaseStatusCode.CONFIRMED,
        )

    cmd = mock_run.call_args.args[0]
    assert cmd[:3] == ["/bin/bash", str(hook._resolve_sync_script(tmp_path)), "--output"]
    assert str(output_file) in cmd
    assert cmd[-2:] == ["--base", "develop"]
    assert result.context_updates["pr_url"] == "https://github.com/test/repo/pull/42"
    assert result.events[0] == {
        "type": "pr_synced",
        "url": "https://github.com/test/repo/pull/42",
        "pr_number": "42",
        "action": "created",
        "source": "capability",
        "display": {
            "style": "green",
            "lines": ["PR synced", "  URL: https://github.com/test/repo/pull/42"],
        },
    }
    assert result.events[1]["type"] == "capability_receipt"
    assert result.events[1]["capability"] == "cafe.pr.publish"
    assert result.events[1]["success"] is True


def test_github_pr_creator_prepares_history_from_fetched_remote_base(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    _enable_remote_pr(issue_dir)
    (issue_dir / "issue.yaml").write_text(
        "base_branch: develop\npr:\n  auto_create: true\n",
        encoding="utf-8",
    )
    phase = _FakePhase(phase_dir=issue_dir / "pr", iteration=1)
    phase.git_ops = MagicMock()
    phase.git_ops.get_current_branch.return_value = "feature/demo"
    phase.git_ops.ensure_remote_base_ancestor.return_value = "origin/develop"
    phase.git_ops.get_commits_between.return_value = "abc123 local base commit"

    with patch("cafe.core.hooks.native.GitHubOps") as mock_github_ops:
        mock_github_ops.return_value.get_pr_for_branch.return_value = None
        result = GitHubPRCreator().run(stage="prepare_input", phase=phase)

    phase.git_ops.ensure_remote_base_ancestor.assert_called_once_with("develop", "HEAD")
    phase.git_ops.get_commits_between.assert_called_once_with("origin/develop", "HEAD")
    assert result.context_updates["commits"] == "abc123 local base commit"


def test_github_pr_creator_local_mode_keeps_existing_pr_metadata_without_fetch(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text(
        "base_branch: develop\npr:\n  auto_create: false\n",
        encoding="utf-8",
    )
    phase = _FakePhase(phase_dir=issue_dir / "pr", iteration=1)
    phase.git_ops = MagicMock()
    phase.git_ops.get_current_branch.return_value = "feature/demo"

    with patch("cafe.core.hooks.native.GitHubOps") as mock_github_ops:
        mock_github_ops.return_value.get_pr_for_branch.return_value = {
            "number": 42,
            "url": "https://github.com/example/repo/pull/42",
        }
        result = GitHubPRCreator().run(stage="prepare_input", phase=phase)

    phase.git_ops.ensure_remote_base_ancestor.assert_not_called()
    phase.git_ops.get_commits_between.assert_not_called()
    assert result.context_updates == {
        "pr_number": "42",
        "pr_url": "https://github.com/example/repo/pull/42",
    }


def test_github_pr_creator_publish_output_rejects_unknown_generic_capability_without_script(
    tmp_path: Path,
) -> None:
    from cafe.core.blackboard import BlackboardStore

    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    phase_dir = issue_dir / "publish"
    output_file = phase_dir / "iteration_001" / "output.md"
    capability_request_file = phase_dir / "iteration_001" / "capability_request.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("# Publish\n", encoding="utf-8")
    capability_request_file.write_text(
        json.dumps({"capability": "demo.unknown", "args": {}, "permissions": {}}),
        encoding="utf-8",
    )
    phase = _FakePhase(phase_dir=phase_dir, iteration=1)
    phase.git_ops = MagicMock()
    phase.git_ops.get_repo_root.return_value = tmp_path
    store = BlackboardStore(issue_dir)
    blackboard_state = store.load_or_create("publish")

    hook = GitHubPRCreator()
    with patch("cafe.core.capabilities.subprocess.run") as mock_run:
        result = hook.run(
            stage="publish_output",
            phase=phase,
            step_name="publish",
            step_def={"capability_requests": ["demo.unknown"]},
            output_file=output_file,
            capability_request_file=capability_request_file,
            blackboard_state=blackboard_state,
            status_code=PhaseStatusCode.CONFIRMED,
        )

    mock_run.assert_not_called()
    assert result.events == [
        {
            "type": "capability_receipt",
            "capability": "demo.unknown",
            "success": False,
            "correlation_id": result.events[0]["correlation_id"],
            "category": "validation_error",
            "code": "unknown_capability",
        }
    ]
    loaded = store.load_or_create("publish")
    assert loaded.capability_receipts[-1]["capability"] == "demo.unknown"
    assert loaded.capability_receipts[-1]["success"] is False


@pytest.mark.parametrize(
    "failure", ["request_json", "request_encoding", "request_read", "registry"]
)
def test_github_pr_creator_load_failures_persist_correlated_rejection_receipts(
    tmp_path: Path, failure: str
) -> None:
    from cafe.core.blackboard import BlackboardStore
    from cafe.core.capabilities import CapabilityRegistryError, default_capability_definition_dirs

    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    phase_dir = issue_dir / "publish"
    output_file = phase_dir / "iteration_001" / "output.md"
    capability_request_file = phase_dir / "iteration_001" / "capability_request.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("# Publish\n", encoding="utf-8")
    valid_request = {"capability": "demo.echo", "args": {"target_ref": "current_pr"}}
    if failure == "request_encoding":
        capability_request_file.write_bytes(b"{\xff}")
    else:
        capability_request_file.write_text(
            "not-json" if failure == "request_json" else json.dumps(valid_request),
            encoding="utf-8",
        )
    phase = _FakePhase(phase_dir=phase_dir, iteration=1)
    phase.git_ops = MagicMock()
    phase.git_ops.get_repo_root.return_value = tmp_path
    store = BlackboardStore(issue_dir)
    blackboard_state = store.load_or_create("publish")

    hook = GitHubPRCreator()
    registry_patch = patch(
        "cafe.core.capabilities.load_capability_registry",
        side_effect=CapabilityRegistryError("invalid registry"),
    )
    read_patch = (
        patch("pathlib.Path.read_text", side_effect=PermissionError("request unreadable"))
        if failure == "request_read"
        else nullcontext()
    )
    bytes_patch = (
        patch("pathlib.Path.read_bytes", side_effect=PermissionError("request unreadable"))
        if failure == "request_read"
        else nullcontext()
    )
    with registry_patch, read_patch, bytes_patch:
        result = hook.run(
            stage="publish_output",
            phase=phase,
            step_name="publish",
            step_def={"capability_requests": ["demo.echo"]},
            output_file=output_file,
            capability_request_file=capability_request_file,
            blackboard_state=blackboard_state,
            status_code=PhaseStatusCode.CONFIRMED,
        )

    receipt = store.load_or_create("publish").capability_receipts[-1]
    assert result.events[0]["type"] == "capability_receipt"
    assert receipt["request_fingerprint"]
    assert receipt["manifest"] is None
    assert "requested_effects" in receipt
    assert receipt["allowed_effects"] == {}
    assert receipt["decision"]["outcome"] == "deny"
    assert receipt["outcome"] == "validation_rejection"
    assert receipt["rejection"]["error_detail"]
    if failure.startswith("request_"):
        expected_source = {
            "kind": "request_artifact",
            "path": str(capability_request_file.resolve()),
        }
        if failure != "request_read":
            expected_source["content_sha256"] = hashlib.sha256(
                capability_request_file.read_bytes()
            ).hexdigest()
        assert receipt["rejection"]["source"] == expected_source
        if failure == "request_json":
            assert receipt["rejection"]["rejected_value"] == "not-json"
        elif failure == "request_encoding":
            assert receipt["rejection"]["rejected_value"] == "{\ufffd}"
        else:
            assert receipt["rejection"]["rejected_value"] is None
    else:
        assert receipt["rejection"]["source"] == {
            "kind": "capability_registry",
            "paths": [str(path) for path in default_capability_definition_dirs(tmp_path)],
        }
        assert receipt["rejection"]["rejected_value"] == {
            "capability": "demo.echo",
            "args": {"target_ref": "current_pr"},
        }
        assert "invalid registry" in receipt["rejection"]["error_detail"]


def test_github_pr_creator_malformed_request_fingerprint_tracks_rejected_artifact(
    tmp_path: Path,
) -> None:
    from cafe.core.blackboard import BlackboardStore

    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    phase_dir = issue_dir / "publish"
    output_file = phase_dir / "iteration_001" / "output.md"
    capability_request_file = phase_dir / "iteration_001" / "capability_request.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("# Publish\n", encoding="utf-8")
    phase = _FakePhase(phase_dir=phase_dir, iteration=1)
    phase.git_ops = MagicMock()
    phase.git_ops.get_repo_root.return_value = tmp_path
    store = BlackboardStore(issue_dir)
    blackboard_state = store.load_or_create("publish")
    hook = GitHubPRCreator()
    fingerprints: list[str] = []

    for malformed in ("not-json", "also-not-json"):
        capability_request_file.write_text(malformed, encoding="utf-8")
        hook.run(
            stage="publish_output",
            phase=phase,
            step_name="publish",
            step_def={"capability_requests": ["demo.echo"]},
            output_file=output_file,
            capability_request_file=capability_request_file,
            blackboard_state=blackboard_state,
            status_code=PhaseStatusCode.CONFIRMED,
        )
        fingerprints.append(
            store.load_or_create("publish").capability_receipts[-1]["request_fingerprint"]
        )

    assert fingerprints[0] != fingerprints[1]


def test_github_pr_creator_unreadable_request_fingerprint_tracks_source_path(
    tmp_path: Path,
) -> None:
    from cafe.core.blackboard import BlackboardStore

    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    phase_dir = issue_dir / "publish"
    output_file = phase_dir / "iteration_001" / "output.md"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("# Publish\n", encoding="utf-8")
    phase = _FakePhase(phase_dir=phase_dir, iteration=1)
    phase.git_ops = MagicMock()
    phase.git_ops.get_repo_root.return_value = tmp_path
    store = BlackboardStore(issue_dir)
    blackboard_state = store.load_or_create("publish")
    hook = GitHubPRCreator()
    fingerprints: list[str] = []

    with (
        patch("pathlib.Path.read_text", side_effect=PermissionError("request unreadable")),
        patch("pathlib.Path.read_bytes", side_effect=PermissionError("request unreadable")),
    ):
        for filename in ("first.json", "second.json"):
            request_file = output_file.parent / filename
            request_file.write_text("same request", encoding="utf-8")
            hook.run(
                stage="publish_output",
                phase=phase,
                step_name="publish",
                step_def={"capability_requests": ["demo.echo"]},
                output_file=output_file,
                capability_request_file=request_file,
                blackboard_state=blackboard_state,
                status_code=PhaseStatusCode.CONFIRMED,
            )
            fingerprints.append(
                blackboard_state.capability_receipts[-1]["request_fingerprint"]
            )

    assert fingerprints[0] != fingerprints[1]


def test_github_pr_creator_invalid_utf8_fingerprint_tracks_original_bytes(
    tmp_path: Path,
) -> None:
    from cafe.core.blackboard import BlackboardStore

    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    phase_dir = issue_dir / "publish"
    output_file = phase_dir / "iteration_001" / "output.md"
    capability_request_file = output_file.parent / "capability_request.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("# Publish\n", encoding="utf-8")
    phase = _FakePhase(phase_dir=phase_dir, iteration=1)
    phase.git_ops = MagicMock()
    phase.git_ops.get_repo_root.return_value = tmp_path
    store = BlackboardStore(issue_dir)
    blackboard_state = store.load_or_create("publish")
    hook = GitHubPRCreator()
    fingerprints: list[str] = []

    for malformed in (b"{\xff}", b"{\xfe}"):
        capability_request_file.write_bytes(malformed)
        hook.run(
            stage="publish_output",
            phase=phase,
            step_name="publish",
            step_def={"capability_requests": ["demo.echo"]},
            output_file=output_file,
            capability_request_file=capability_request_file,
            blackboard_state=blackboard_state,
            status_code=PhaseStatusCode.CONFIRMED,
        )
        fingerprints.append(
            store.load_or_create("publish").capability_receipts[-1][
                "request_fingerprint"
            ]
        )

    assert fingerprints[0] != fingerprints[1]


def test_github_pr_creator_publish_output_records_all_multi_capability_receipts(
    tmp_path: Path,
) -> None:
    from cafe.core.blackboard import BlackboardStore

    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    phase_dir = issue_dir / "publish"
    output_file = phase_dir / "iteration_001" / "output.md"
    capability_request_file = phase_dir / "iteration_001" / "capability_request.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("# Publish\n", encoding="utf-8")
    capability_request_file.write_text(
        json.dumps(
            {
                "requests": [
                    {"capability": "demo.first", "args": {}, "permissions": {}},
                    {"capability": "demo.second", "args": {}, "permissions": {}},
                ]
            }
        ),
        encoding="utf-8",
    )
    phase = _FakePhase(phase_dir=phase_dir, iteration=1)
    phase.git_ops = MagicMock()
    phase.git_ops.get_repo_root.return_value = tmp_path
    store = BlackboardStore(issue_dir)
    blackboard_state = store.load_or_create("publish")

    hook = GitHubPRCreator()
    with patch("cafe.core.capabilities.subprocess.run") as mock_run:
        result = hook.run(
            stage="publish_output",
            phase=phase,
            step_name="publish",
            step_def={"capability_requests": ["demo.first", "demo.second"]},
            output_file=output_file,
            capability_request_file=capability_request_file,
            blackboard_state=blackboard_state,
            status_code=PhaseStatusCode.CONFIRMED,
        )

    mock_run.assert_not_called()
    assert [event["capability"] for event in result.events] == ["demo.first", "demo.second"]
    assert [event["code"] for event in result.events] == [
        "unknown_capability",
        "unknown_capability",
    ]
    loaded = store.load_or_create("publish")
    assert [receipt["capability"] for receipt in loaded.capability_receipts[-2:]] == [
        "demo.first",
        "demo.second",
    ]


def test_github_pr_creator_publish_output_runs_from_workflow_complete_baton_without_status_code(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    _enable_remote_pr(issue_dir)
    phase_dir = issue_dir / "pr"
    output_file = phase_dir / "iteration_001" / "output.md"
    publish_request_file = phase_dir / "iteration_001" / "publish_request.json"
    next_step_file = issue_dir / "next_step.txt"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("# Test PR\n\nBody\n", encoding="utf-8")
    publish_request_file.write_text(
        json.dumps(
            {
                "capability": "cafe.pr.publish",
                "args": {
                    "output": ".cafe/issues/demo/pr/iteration_001/output.md",
                    "base": "develop",
                },
            }
        ),
        encoding="utf-8",
    )
    next_step_file.write_text(
        json.dumps(
            {
                "version": 1,
                "from_step": "pr",
                "to_owner": "done",
                "to_step": "done",
                "intent": "workflow_complete",
                "status_code": "BATON_WORKFLOW_COMPLETE",
                "created_at": "2026-04-26T22:49:02.559908+08:00",
                "source": "agent.test",
            }
        ),
        encoding="utf-8",
    )

    phase = _FakePhase(phase_dir=phase_dir, iteration=1)
    phase.git_ops = MagicMock()
    phase.git_ops.get_repo_root.return_value = tmp_path

    completed = MagicMock()
    completed.returncode = 0
    completed.stdout = (
        '{"action":"created","pr_number":"42",' '"pr_url":"https://github.com/test/repo/pull/42"}\n'
    )
    completed.stderr = ""

    hook = GitHubPRCreator()
    with patch("cafe.core.capabilities.subprocess.run", return_value=completed) as mock_run:
        result = hook.run(
            stage="publish_output",
            phase=phase,
            step_name="pr",
            step_def=PUBLISH_STEP,
            output_file=output_file,
            publish_request_file=publish_request_file,
            context={"next_step_path": str(next_step_file)},
            status_code=None,
        )

    assert mock_run.call_count == 2
    assert mock_run.call_args_list[0].args[0][0] == "git"
    assert mock_run.call_args_list[1].args[0][0] == "/bin/bash"
    assert result.events[0]["type"] == "pr_synced"
    assert result.events[0]["source"] == "capability"
    assert result.events[1]["type"] == "capability_receipt"


def test_github_pr_creator_publish_output_rejects_pr_done_await_agent_baton(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    _enable_remote_pr(issue_dir)
    phase_dir = issue_dir / "pr"
    output_file = phase_dir / "iteration_001" / "output.md"
    publish_request_file = phase_dir / "iteration_001" / "publish_request.json"
    next_step_file = issue_dir / "next_step.txt"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("# Test PR\n\nBody\n", encoding="utf-8")
    publish_request_file.write_text(
        json.dumps(
            {
                "capability": "cafe.pr.publish",
                "args": {
                    "output": ".cafe/issues/demo/pr/iteration_001/output.md",
                    "base": "develop",
                },
            }
        ),
        encoding="utf-8",
    )
    next_step_file.write_text(
        json.dumps(
            {
                "version": 1,
                "from_step": "pr",
                "to_owner": "done",
                "to_step": "done",
                "intent": "await_agent",
                "status_code": "confirmed",
                "created_at": "2026-04-26T22:49:02.559908+08:00",
                "source": "agent.test",
            }
        ),
        encoding="utf-8",
    )

    phase = _FakePhase(phase_dir=phase_dir, iteration=1)
    phase.git_ops = MagicMock()
    phase.git_ops.get_repo_root.return_value = tmp_path

    completed = MagicMock()
    completed.returncode = 0
    completed.stdout = (
        '{"action":"updated","pr_number":"42",' '"pr_url":"https://github.com/test/repo/pull/42"}\n'
    )
    completed.stderr = ""

    hook = GitHubPRCreator()
    with patch("cafe.core.capabilities.subprocess.run", return_value=completed) as mock_run:
        result = hook.run(
            stage="publish_output",
            phase=phase,
            step_name="pr",
            step_def=PUBLISH_STEP,
            output_file=output_file,
            publish_request_file=publish_request_file,
            context={"next_step_path": str(next_step_file)},
            status_code=None,
        )

    mock_run.assert_not_called()
    assert result.events == []
    assert result.context_updates == {}


def test_github_pr_creator_rejects_invalid_baton_with_inherited_baton_completion(
    tmp_path: Path,
) -> None:
    """A playbook-level baton contract must gate confirmed PR publication too."""
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    _enable_remote_pr(issue_dir)
    phase_dir = issue_dir / "pr"
    output_file = phase_dir / "iteration_001" / "output.md"
    publish_request_file = phase_dir / "iteration_001" / "publish_request.json"
    next_step_file = issue_dir / "next_step.txt"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("# Test PR\n\nBody\n", encoding="utf-8")
    publish_request_file.write_text(
        json.dumps(
            {
                "capability": "cafe.pr.publish",
                "args": {"output": ".cafe/issues/demo/pr/iteration_001/output.md"},
            }
        ),
        encoding="utf-8",
    )
    next_step_file.write_text(
        json.dumps(
            {
                "version": 1,
                "from_step": "pr",
                "to_owner": "done",
                "to_step": "done",
                "intent": "await_agent",
            }
        ),
        encoding="utf-8",
    )
    step_def = {
        "capability_requests": ["cafe.pr.publish"],
        "on": {"workflow_complete": "_done"},
    }
    phase = _FakePhase(phase_dir=phase_dir, iteration=1)
    phase.git_ops = MagicMock()
    phase.git_ops.get_repo_root.return_value = tmp_path
    phase.playbook = {
        "behavior": {"completion": "baton", "publish_confirmation": True},
        "steps": {"pr": step_def},
    }

    hook = GitHubPRCreator()
    with patch("cafe.core.capabilities.subprocess.run") as mock_run:
        result = hook.run(
            stage="publish_output",
            phase=phase,
            step_name="pr",
            step_def=step_def,
            output_file=output_file,
            publish_request_file=publish_request_file,
            context={
                "next_step_path": str(next_step_file),
                "publish_confirmation": True,
                "behavior_completion": "baton",
            },
            status_code=PhaseStatusCode.CONFIRMED,
        )

    mock_run.assert_not_called()
    assert result.events == []
    assert result.context_updates == {}


def test_github_pr_creator_publish_output_runs_from_legacy_done_baton(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    _enable_remote_pr(issue_dir)
    phase_dir = issue_dir / "pr"
    output_file = phase_dir / "iteration_001" / "output.md"
    publish_request_file = phase_dir / "iteration_001" / "publish_request.json"
    next_step_file = issue_dir / "next_step.txt"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("# Test PR\n\nBody\n", encoding="utf-8")
    publish_request_file.write_text(
        json.dumps(
            {
                "capability": "cafe.pr.publish",
                "args": {
                    "output": ".cafe/issues/demo/pr/iteration_001/output.md",
                    "base": "develop",
                },
            }
        ),
        encoding="utf-8",
    )
    next_step_file.write_text("done\n", encoding="utf-8")

    phase = _FakePhase(phase_dir=phase_dir, iteration=1)
    phase.git_ops = MagicMock()
    phase.git_ops.get_repo_root.return_value = tmp_path

    completed = MagicMock()
    completed.returncode = 0
    completed.stdout = (
        '{"action":"updated","pr_number":"42",' '"pr_url":"https://github.com/test/repo/pull/42"}\n'
    )
    completed.stderr = ""

    hook = GitHubPRCreator()
    with patch("cafe.core.capabilities.subprocess.run", return_value=completed) as mock_run:
        result = hook.run(
            stage="publish_output",
            phase=phase,
            step_name="pr",
            step_def=PUBLISH_STEP,
            output_file=output_file,
            publish_request_file=publish_request_file,
            context={"next_step_path": str(next_step_file)},
            status_code=None,
        )

    assert mock_run.call_count == 2
    assert mock_run.call_args_list[0].args[0][0] == "git"
    assert mock_run.call_args_list[1].args[0][0] == "/bin/bash"
    assert result.events[0]["type"] == "pr_synced"
    assert result.events[0]["action"] == "updated"
    assert result.events[1]["type"] == "capability_receipt"


def test_github_pr_creator_publish_output_skips_local_pr_mode(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    phase_dir = issue_dir / "pr"
    output_file = phase_dir / "iteration_001" / "output.md"
    publish_request_file = phase_dir / "iteration_001" / "publish_request.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("# Test PR\n\nBody\n", encoding="utf-8")
    publish_request_file.write_text("{}", encoding="utf-8")
    (issue_dir / "issue.yaml").write_text("pr:\n  auto_create: false\n", encoding="utf-8")

    phase = _FakePhase(phase_dir=phase_dir, iteration=1)
    phase.git_ops = MagicMock()

    hook = GitHubPRCreator()
    with patch("cafe.core.capabilities.subprocess.run") as mock_run:
        result = hook.run(
            stage="publish_output",
            phase=phase,
            step_name="pr",
            step_def=PUBLISH_STEP,
            output_file=output_file,
            publish_request_file=publish_request_file,
            status_code=PhaseStatusCode.CONFIRMED,
        )

    mock_run.assert_not_called()
    assert result.events == []


@pytest.mark.parametrize(
    "issue_config",
    [None, "{}\n", "pr:\n  auto_create: maybe\n", "[invalid yaml\n"],
)
def test_github_pr_creator_publish_output_fails_safe_without_explicit_true(
    tmp_path: Path, issue_config: str | None
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    phase_dir = issue_dir / "pr"
    output_file = phase_dir / "iteration_001" / "output.md"
    publish_request_file = phase_dir / "iteration_001" / "publish_request.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("# Test PR\n\nBody\n", encoding="utf-8")
    publish_request_file.write_text("{}", encoding="utf-8")
    if issue_config is not None:
        (issue_dir / "issue.yaml").write_text(issue_config, encoding="utf-8")

    phase = _FakePhase(phase_dir=phase_dir, iteration=1)
    phase.git_ops = MagicMock()

    with patch("cafe.core.capabilities.subprocess.run") as mock_run:
        result = GitHubPRCreator().run(
            stage="publish_output",
            phase=phase,
            step_name="pr",
            step_def=PUBLISH_STEP,
            output_file=output_file,
            publish_request_file=publish_request_file,
            status_code=PhaseStatusCode.CONFIRMED,
        )

    mock_run.assert_not_called()
    assert result.events == []


def test_github_pr_creator_publish_rejects_untrusted_script_field_before_dispatch(
    tmp_path: Path,
) -> None:
    """Agent-supplied executable authority invalidates the request."""
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    _enable_remote_pr(issue_dir)
    phase_dir = issue_dir / "pr"
    output_file = phase_dir / "iteration_001" / "output.md"
    publish_request_file = phase_dir / "iteration_001" / "publish_request.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("# Test PR\n\nBody\n", encoding="utf-8")
    publish_request_file.write_text(
        json.dumps(
            {
                "capability": "cafe.pr.publish",
                "script": "scripts/not-trusted.sh",
                "args": {
                    "output": ".cafe/issues/demo/pr/iteration_001/output.md",
                    "base": "develop",
                },
            }
        ),
        encoding="utf-8",
    )

    phase = _FakePhase(phase_dir=phase_dir, iteration=1)
    phase.git_ops = MagicMock()
    phase.git_ops.get_repo_root.return_value = tmp_path

    completed = MagicMock()
    completed.returncode = 0
    completed.stdout = (
        '{"action":"created","pr_number":"42",' '"pr_url":"https://github.com/test/repo/pull/42"}\n'
    )
    completed.stderr = ""

    hook = GitHubPRCreator()
    with patch("cafe.core.capabilities.subprocess.run", return_value=completed) as mock_run:
        result = hook.run(
            stage="publish_output",
            phase=phase,
            step_name="pr",
            step_def=PUBLISH_STEP,
            output_file=output_file,
            publish_request_file=publish_request_file,
            status_code=PhaseStatusCode.CONFIRMED,
        )

    mock_run.assert_not_called()
    assert result.events[-1]["type"] == "capability_receipt"
    assert result.events[-1]["success"] is False
    assert result.events[-1]["code"] == "malformed_request"


def test_pr_comment_poster_posts_todo_comment_only_when_confirmed_and_complete(
    tmp_path: Path,
) -> None:
    output_file = tmp_path / "output.md"
    output_file.write_text("## Todo List\n- [x] Fix comment\n", encoding="utf-8")

    phase = MagicMock()
    phase.git_ops.get_current_branch.return_value = "issue-183"

    hook = PRCommentPoster()

    with patch("cafe.core.hooks.native.GitHubOps") as mock_github_ops:
        mock_github_ops.return_value.get_pr_for_branch.return_value = {
            "number": 42,
            "url": "https://github.com/test/repo/pull/42",
        }
        result = hook.run(
            stage="publish_output",
            phase=phase,
            output_file=output_file,
            status_code=PhaseStatusCode.CONFIRMED,
            step_def=PUBLISH_STEP,
        )

    mock_github_ops.return_value.add_pr_comment.assert_called_once()
    assert result.events == [{"type": "pr_todo_comment_posted", "pr_number": "42"}]


def test_pr_comment_poster_skips_when_unchecked_items_exist(tmp_path: Path) -> None:
    output_file = tmp_path / "output.md"
    output_file.write_text("## Todo List\n- [ ] Fix comment\n", encoding="utf-8")

    phase = MagicMock()
    phase.git_ops.get_current_branch.return_value = "issue-183"

    hook = PRCommentPoster()

    with patch("cafe.core.hooks.native.GitHubOps") as mock_github_ops:
        mock_github_ops.return_value.get_pr_for_branch.return_value = {
            "number": 42,
            "url": "https://github.com/test/repo/pull/42",
        }
        result = hook.run(
            stage="publish_output",
            phase=phase,
            output_file=output_file,
            status_code=PhaseStatusCode.CONFIRMED,
        )

    mock_github_ops.return_value.add_pr_comment.assert_not_called()
    assert result.events == []


def test_pr_comment_poster_skips_for_needs_changes_status(tmp_path: Path) -> None:
    output_file = tmp_path / "output.md"
    output_file.write_text("## Todo List\n- [x] Fix comment\n", encoding="utf-8")

    phase = MagicMock()
    phase.git_ops.get_current_branch.return_value = "issue-183"

    hook = PRCommentPoster()

    with patch("cafe.core.hooks.native.GitHubOps") as mock_github_ops:
        result = hook.run(
            stage="publish_output",
            phase=phase,
            output_file=output_file,
            status_code=PhaseStatusCode.NEEDS_CHANGES,
        )

    mock_github_ops.return_value.add_pr_comment.assert_not_called()
    assert result.events == []
