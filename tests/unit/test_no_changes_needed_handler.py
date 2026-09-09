"""Tests for develop no_changes_needed runtime hook."""

from pathlib import Path
from unittest.mock import patch

import pytest

from cafe.core.hooks.native import (
    InitialInputProviderResolver,
    NoChangesNeededHandler,
    UserInputCollector,
)
from cafe.core.status_codes import PhaseStatusCode
from cafe.playbooks.loader import PlaybookLoader

pytestmark = pytest.mark.usefixtures("cached_builtin_playbook_models")


def _no_change_step_def() -> dict:
    return {
        "role": "developer",
        "skill": "cafe-develop",
        "human_tasks": [{"trigger": "no_changes_needed", "task_id": "no-change-decision"}],
    }


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
        step_def=_no_change_step_def(),
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
        step_def=_no_change_step_def(),
        response="no_changes_needed",
        context={"output_file": str(output_file)},
    )

    assert result.retry_requested is False
    assert result.continue_pipeline is False
    assert result.override_status_code == PhaseStatusCode.NO_CHANGES_NEEDED


def test_direct_initial_input_does_not_count_as_no_change_reasoning(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    phase = _FakeDevelopStep(issue_dir, iteration=1, interactive=False)
    output_file = phase._get_iteration_dir(1) / "output.md"
    step_def = PlaybookLoader().load("direct", strict=True)["steps"]["develop"]

    initial = InitialInputProviderResolver().run(
        stage="prepare_input",
        phase=phase,
        step_name="develop",
        step_def=step_def,
        output_file=output_file,
        context={"user_input": "Implement the reviewed direct workflow."},
    )
    no_change = NoChangesNeededHandler().run(
        stage="after_execute",
        step_name="develop",
        step_def=step_def,
        response="no_changes_needed",
        context={"output_file": str(output_file)},
    )

    assert initial.context_updates == {
        "user_input": "Implement the reviewed direct workflow."
    }
    assert not output_file.exists()
    assert no_change.retry_requested is True


@patch("cafe.ui.inquirer_prompts.prompt_list", return_value="agree")
def test_user_input_collector_confirm_routes_declared_successor(
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
        step_def=_no_change_step_def(),
        agent_name="David",
    )

    assert result.override_status_code == PhaseStatusCode.CONFIRMED
    assert result.events[0]["type"] == "human_task_completed"


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
    (current_iter / "user_input.md").write_text(
        '{"task":"no-change-decision","decision":"agree"}', encoding="utf-8"
    )

    _record_no_changes_event(issue_dir)

    phase = _FakeDevelopStep(issue_dir, iteration=2, interactive=False)
    collector = UserInputCollector()
    result = collector.run(
        stage="prepare_input",
        phase=phase,
        step_name="develop",
        step_def=_no_change_step_def(),
        agent_name="David",
    )

    assert result.override_status_code == PhaseStatusCode.CONFIRMED
    assert result.events[0]["type"] == "human_task_completed"


@patch("cafe.ui.inquirer_prompts.prompt_multiline", return_value="Please fix the naming")
@patch("cafe.ui.inquirer_prompts.prompt_list", return_value="disagree")
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
        step_def=_no_change_step_def(),
        agent_name="David",
    )

    assert result.context_updates["user_input"] == "Please fix the naming"
    assert result.events[0]["type"] == "human_task_completed"


@patch("cafe.ui.inquirer_prompts.prompt_list", return_value="agree")
def test_user_input_collector_decision_labels_are_policy_owned(
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
        step_def=_no_change_step_def(),
        agent_name="David",
    )

    assert result.override_status_code == PhaseStatusCode.CONFIRMED
    assert (
        mock_prompt_list.call_args.args[0]
        == "Review the implementation reasoning and choose how to continue."
    )


def test_user_input_collector_non_interactive_pauses_when_no_user_input_file_present(
    tmp_path: Path,
) -> None:
    """非互動模式且 playbook 路由到 user（或缺少映射）時，hook 作為第二道防線繼續暫停。

    此測試記錄了 UserInputCollector 的「second-line guard」角色：
    當 _write_status_transition_handoff 已決定暫停（to_owner=USER），
    workflow 重回 develop 時，hook 在 prepare_input 仍確保 user_input.md
    為空時不會繼續執行，避免無輸入的空跑。
    """
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    phase_dir = issue_dir / "develop"
    prev_iter = phase_dir / "iteration_001"
    prev_iter.mkdir(parents=True, exist_ok=True)
    (prev_iter / "output.md").write_text("reasoning", encoding="utf-8")

    # 目前 iteration 沒有 user_input.md（模擬 playbook 指向 user 後重回 develop 的場景）
    current_iter = phase_dir / "iteration_002"
    current_iter.mkdir(parents=True, exist_ok=True)

    _record_no_changes_event(issue_dir)

    phase = _FakeDevelopStep(issue_dir, iteration=2, interactive=False)
    collector = UserInputCollector()
    result = collector.run(
        stage="prepare_input",
        phase=phase,
        step_name="develop",
        step_def=_no_change_step_def(),
        agent_name="David",
    )

    assert result.continue_pipeline is False
    assert any(e.get("type") == "human_task_rejected" for e in result.events)
