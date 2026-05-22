"""Tests for develop no_changes_needed runtime hook."""

from pathlib import Path
from unittest.mock import patch

from cafe.core.hooks.native import NoChangesNeededHandler, UserInputCollector
from cafe.core.status_codes import PhaseStatusCode


class _FakeDevelopStep:
    def __init__(self, issue_dir: Path, iteration: int = 2, interactive: bool = True) -> None:
        self.issue_dir = issue_dir
        self.issue_name = issue_dir.name
        self.phase_name = "develop"
        self.phase_dir = issue_dir / "develop"
        self.iteration = iteration
        self.interactive = interactive
        self.user_input = ""
        self.step_user_inputs: dict[str, str] = {}

    def _get_iteration_dir(self, iteration: int) -> Path:
        return self.phase_dir / f"iteration_{iteration:03d}"


def _record_no_changes_event(issue_dir: Path) -> None:
    from cafe.core.blackboard import BlackboardStore

    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    store.record_event(
        state,
        "step_completed",
        {"step": "develop", "status_code": "no_changes_needed"},
    )


def test_no_changes_handler_retries_when_output_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    iteration_dir.mkdir(parents=True, exist_ok=True)
    output_file = iteration_dir / "output.md"

    handler = NoChangesNeededHandler()
    result = handler.run(
        stage="after_execute",
        step_name="develop",
        response="no_changes_needed",
        context={"output_file": str(output_file)},
    )

    assert result.retry_requested is True
    assert "continuation_prompt" in result.context_updates
    assert result.events[0]["type"] == "no_changes_reasoning_required"


def test_no_changes_handler_pauses_when_reasoning_present(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    iteration_dir.mkdir(parents=True, exist_ok=True)
    output_file = iteration_dir / "output.md"
    output_file.write_text(
        "Reviewer feedback is unnecessary because tests already cover it.",
        encoding="utf-8",
    )

    handler = NoChangesNeededHandler()
    result = handler.run(
        stage="after_execute",
        step_name="develop",
        response="no_changes_needed",
        context={"output_file": str(output_file)},
    )

    assert result.retry_requested is False
    assert result.continue_pipeline is False
    assert result.override_status_code == PhaseStatusCode.NO_CHANGES_NEEDED


@patch("cafe.ui.inquirer_prompts.prompt_list", return_value="c")
def test_user_input_collector_confirm_routes_manual_handoff(
    mock_prompt_list,
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    phase_dir = issue_dir / "develop"
    prev_iter = phase_dir / "iteration_001"
    prev_iter.mkdir(parents=True, exist_ok=True)
    (prev_iter / "output.md").write_text("reasoning", encoding="utf-8")

    _record_no_changes_event(issue_dir)

    phase = _FakeDevelopStep(issue_dir, iteration=2)
    collector = UserInputCollector()
    result = collector.run(
        stage="prepare_input",
        phase=phase,
        step_name="develop",
        step_def={"role": "developer", "name": "develop"},
        agent_name="David",
    )

    assert result.override_status_code == PhaseStatusCode.MANUAL_HANDOFF
    assert result.events[0]["type"] == "no_changes_user_confirmed"


def test_user_input_collector_non_interactive_confirm_reads_user_input_file(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    phase_dir = issue_dir / "develop"
    prev_iter = phase_dir / "iteration_001"
    prev_iter.mkdir(parents=True, exist_ok=True)
    (prev_iter / "output.md").write_text("reasoning", encoding="utf-8")

    current_iter = phase_dir / "iteration_002"
    current_iter.mkdir(parents=True, exist_ok=True)
    (current_iter / "user_input.md").write_text("confirm", encoding="utf-8")

    _record_no_changes_event(issue_dir)

    phase = _FakeDevelopStep(issue_dir, iteration=2, interactive=False)
    collector = UserInputCollector()
    result = collector.run(
        stage="prepare_input",
        phase=phase,
        step_name="develop",
        step_def={"role": "developer", "name": "develop"},
        agent_name="David",
    )

    assert result.override_status_code == PhaseStatusCode.MANUAL_HANDOFF
    assert result.events[0]["type"] == "no_changes_user_confirmed"


@patch("cafe.ui.inquirer_prompts.prompt_multiline", return_value="Please fix the naming")
@patch("cafe.ui.inquirer_prompts.prompt_list", return_value="m")
def test_user_input_collector_disagree_returns_feedback(
    mock_prompt_list, mock_multiline, tmp_path: Path
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    phase_dir = issue_dir / "develop"
    prev_iter = phase_dir / "iteration_001"
    prev_iter.mkdir(parents=True, exist_ok=True)
    (prev_iter / "output.md").write_text("reasoning", encoding="utf-8")

    _record_no_changes_event(issue_dir)

    phase = _FakeDevelopStep(issue_dir, iteration=2)
    collector = UserInputCollector()
    result = collector.run(
        stage="prepare_input",
        phase=phase,
        step_name="develop",
        step_def={"role": "developer", "name": "develop"},
        agent_name="David",
    )

    assert result.context_updates["user_input"] == "Please fix the naming"
    assert result.events[0]["type"] == "no_changes_user_feedback"


@patch("cafe.ui.chat.launch_chat_session")
@patch("cafe.ui.inquirer_prompts.prompt_list", side_effect=["chat", "c"])
def test_user_input_collector_chat_then_confirm(
    mock_prompt_list,
    mock_chat,
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    phase_dir = issue_dir / "develop"
    prev_iter = phase_dir / "iteration_001"
    prev_iter.mkdir(parents=True, exist_ok=True)
    (prev_iter / "output.md").write_text("reasoning", encoding="utf-8")

    _record_no_changes_event(issue_dir)

    phase = _FakeDevelopStep(issue_dir, iteration=2)
    collector = UserInputCollector()
    result = collector.run(
        stage="prepare_input",
        phase=phase,
        step_name="develop",
        step_def={"role": "developer", "name": "develop"},
        agent_name="David",
    )

    mock_chat.assert_called_once_with("developer", "demo")
    assert result.override_status_code == PhaseStatusCode.MANUAL_HANDOFF
