"""Tests for workflow user-input hooks."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.core.hooks.native import (
    GitHubIssueFetcher,
    GitHubPRCreator,
    LocalPRReviewer,
    PRCommentPoster,
    PRLinkOpener,
    UserInputCollector,
)
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.status_codes import PhaseStatusCode
from cafe.phases.generic_phase import GenericPhaseExecution
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor


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
        context_file = self._get_iteration_dir(self.iteration - 1) / "context.json"
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


def test_user_input_collector_confirms_ready_for_review_without_running_agent(tmp_path: Path) -> None:
    phase_dir = tmp_path / "spec"
    prev_iter_dir = phase_dir / "iteration_002"
    prev_iter_dir.mkdir(parents=True, exist_ok=True)
    (prev_iter_dir / "output.md").write_text("# Spec\n", encoding="utf-8")
    _record_previous_step_status(tmp_path, "spec", "ready_for_review")

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
    assert result.events == [
        {"type": "review_confirmed", "step": "spec"},
        {"type": "review_confirmed_advance", "step": "spec"},
    ]
    mock_display_output.assert_not_called()
    mock_display_delta.assert_called_once()
    phase._ask_user_for_review_decision.assert_called_once()
    phase._process_review_decision.assert_called_once()


def test_user_input_collector_plan_ready_for_review_skips_full_output_display_when_delta_available(tmp_path: Path) -> None:
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
    with patch.object(hook, "_display_previous_output") as mock_display_output, \
         patch.object(hook, "_display_previous_iteration_delta") as mock_display_delta:
        result = hook.run(
            stage="prepare_input",
            phase=phase,
            step_name="plan",
            step_def={"role": "developer"},
            agent_name="David",
        )

    assert result.continue_pipeline is False
    assert result.override_status_code == PhaseStatusCode.CONFIRMED
    mock_display_output.assert_not_called()
    mock_display_delta.assert_called_once()


def test_user_input_collector_plan_ready_for_review_falls_back_to_full_output_without_delta(tmp_path: Path) -> None:
    phase_dir = tmp_path / "plan"
    prev_iter_dir = phase_dir / "iteration_001"
    prev_iter_dir.mkdir(parents=True, exist_ok=True)
    (prev_iter_dir / "output.md").write_text("# Plan\n", encoding="utf-8")
    _record_previous_step_status(tmp_path, "plan", "ready_for_review")

    phase = _FakePhase(phase_dir=phase_dir, iteration=2)
    phase._ask_user_for_review_decision = MagicMock(return_value="confirm")
    phase._process_review_decision = MagicMock()

    hook = UserInputCollector()
    with patch.object(hook, "_display_previous_output") as mock_display_output, \
         patch.object(hook, "_display_previous_iteration_delta") as mock_display_delta:
        mock_display_delta.return_value = False
        result = hook.run(
            stage="prepare_input",
            phase=phase,
            step_name="plan",
            step_def={"role": "developer"},
            agent_name="David",
        )

    assert result.continue_pipeline is False
    assert result.override_status_code == PhaseStatusCode.CONFIRMED
    mock_display_delta.assert_called_once()
    mock_display_output.assert_called_once()


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


def test_user_input_collector_reuses_existing_user_input_file_without_reasking(tmp_path: Path) -> None:
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
    assert result.events == [{"type": "user_input_collected", "step": "spec", "source": "user_input_file"}]
    assert phase.step_user_inputs["spec"] == "Q1: Question?\nA1: Confirmed answer"
    mock_display_output.assert_called_once()
    mock_qa.assert_not_called()


def test_user_input_collector_prompts_initial_plan_user_input_on_first_iteration(tmp_path: Path) -> None:
    phase_dir = tmp_path / "plan"
    phase_dir.mkdir(parents=True, exist_ok=True)
    phase = _FakePhase(phase_dir=phase_dir, iteration=1)
    hook = UserInputCollector()

    with patch("cafe.core.hooks.native.prompt_multiline", return_value="Follow strict TDD first") as mock_prompt:
        result = hook.run(
            stage="prepare_input",
            phase=phase,
            step_name="plan",
            step_def={"role": "developer"},
            agent_name="David",
        )

    assert result.context_updates["user_input"] == "Follow strict TDD first"
    assert result.events == [{"type": "user_input_collected", "step": "plan", "source": "initial_prompt"}]
    assert phase.step_user_inputs["plan"] == "Follow strict TDD first"
    prompt_text = mock_prompt.call_args.args[0]
    assert "Suggested content:" in prompt_text
    assert "Technical solution/direction" in prompt_text


def test_user_input_collector_skips_initial_plan_prompt_in_noninteractive_mode(tmp_path: Path) -> None:
    phase_dir = tmp_path / "plan"
    phase_dir.mkdir(parents=True, exist_ok=True)
    phase = _FakePhase(phase_dir=phase_dir, iteration=1)
    phase.interactive = False
    hook = UserInputCollector()

    with patch("cafe.core.hooks.native.prompt_multiline") as mock_prompt:
        result = hook.run(
            stage="prepare_input",
            phase=phase,
            step_name="plan",
            step_def={"role": "developer"},
            agent_name="David",
        )

    assert result.context_updates["user_input"] == ""
    assert result.events == [{"type": "user_input_collected", "step": "plan", "source": "initial_prompt"}]
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

    assert output_file.read_text(encoding="utf-8") == "# Initial Requirements\n\nBuild a standalone myip command.\n"
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

    assert output_file.read_text(encoding="utf-8") == "# Initial Requirements\n\nBuild a standalone myip command.\n"
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
    executor._resolve_agent_name = MagicMock(return_value="Roger")
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

    with patch("cafe.core.hooks.native.GitHubOps") as mock_github_ops, \
         patch("cafe.core.hooks.native.webbrowser.open") as mock_open:
        mock_github_ops.return_value.get_current_pr_url.return_value = "https://github.com/test/repo/pull/123"

        result = hook.run(stage="publish_output", status_code=PhaseStatusCode.CONFIRMED)

    mock_open.assert_called_once_with("https://github.com/test/repo/pull/123")
    assert result.events == [
        {"type": "pr_synced", "url": "https://github.com/test/repo/pull/123"},
        {"type": "pr_link_opened", "url": "https://github.com/test/repo/pull/123"},
    ]


def test_pr_link_opener_noops_when_pr_url_unavailable() -> None:
    hook = PRLinkOpener()

    with patch("cafe.core.hooks.native.GitHubOps") as mock_github_ops, \
         patch("cafe.core.hooks.native.webbrowser.open") as mock_open:
        mock_github_ops.return_value.get_current_pr_url.side_effect = Exception("no pr")

        result = hook.run(stage="publish_output", status_code=PhaseStatusCode.CONFIRMED)

    mock_open.assert_not_called()
    assert result.events == []


def test_pr_link_opener_returns_pr_synced_even_when_browser_open_fails() -> None:
    hook = PRLinkOpener()

    with patch("cafe.core.hooks.native.GitHubOps") as mock_github_ops, \
         patch("cafe.core.hooks.native.webbrowser.open", side_effect=Exception("blocked")) as mock_open:
        mock_github_ops.return_value.get_current_pr_url.return_value = "https://github.com/test/repo/pull/123"

        result = hook.run(stage="publish_output", status_code=PhaseStatusCode.CONFIRMED)

    mock_open.assert_called_once_with("https://github.com/test/repo/pull/123")
    assert result.events == [{"type": "pr_synced", "url": "https://github.com/test/repo/pull/123"}]


def test_local_pr_reviewer_displays_diff_and_confirms_local_mode(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    phase_dir = issue_dir / "pr"
    phase_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "issue.yaml").write_text(
        "base_branch: develop\npr:\n  auto_create: false\n",
        encoding="utf-8",
    )
    output_file = phase_dir / "iteration_001" / "output.md"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("# PR title\n", encoding="utf-8")
    phase = _FakePhase(phase_dir=phase_dir, iteration=1)
    phase.git_ops = MagicMock()
    phase.git_ops.get_diff.return_value = "diff --git a/app.py b/app.py\n+change\n"
    phase._ask_user_for_review_decision = MagicMock(return_value="confirm")
    phase._process_review_decision = MagicMock(return_value=MagicMock())

    hook = LocalPRReviewer()
    result = hook.run(
        stage="publish_output",
        phase=phase,
        step_name="pr",
        agent_name="Nick",
        output_file=output_file,
        status_code=PhaseStatusCode.CONFIRMED,
    )

    phase.git_ops.get_diff.assert_called_once_with("develop", "HEAD")
    phase._ask_user_for_review_decision.assert_called_once()
    assert result.override_status_code is None
    assert result.events == [{"type": "local_pr_review_confirmed"}]


def test_local_pr_reviewer_writes_feedback_todo_and_requests_changes(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    phase_dir = issue_dir / "pr"
    phase_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "issue.yaml").write_text(
        "base_branch: develop\npr:\n  auto_create: false\n",
        encoding="utf-8",
    )
    output_file = phase_dir / "iteration_001" / "output.md"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("# PR title\n", encoding="utf-8")
    phase = _FakePhase(phase_dir=phase_dir, iteration=1)
    phase.git_ops = MagicMock()
    phase.git_ops.get_diff.return_value = "diff --git a/app.py b/app.py\n+change\n"
    phase._ask_user_for_review_decision = MagicMock(return_value="modify")
    phase._process_review_decision = MagicMock(return_value="Please fix the failing test")

    hook = LocalPRReviewer()
    result = hook.run(
        stage="publish_output",
        phase=phase,
        step_name="pr",
        agent_name="Nick",
        output_file=output_file,
        status_code=PhaseStatusCode.CONFIRMED,
    )

    assert result.override_status_code == PhaseStatusCode.NEEDS_CHANGES
    assert "- [ ] Please fix the failing test" in output_file.read_text(encoding="utf-8")
    assert (output_file.parent / "user_input.md").read_text(encoding="utf-8") == "Please fix the failing test"
    assert result.events[0]["type"] == "local_pr_review_changes_requested"


def test_github_pr_creator_prepare_input_loads_unresolved_comments(tmp_path: Path) -> None:
    phase_dir = tmp_path / "pr"
    phase_dir.mkdir(parents=True, exist_ok=True)
    phase = _FakePhase(phase_dir=phase_dir, iteration=2)
    phase.git_ops = MagicMock()
    phase.git_ops.get_current_branch.return_value = "issue-183"
    phase.git_ops.has_unpushed_commits.return_value = False

    hook = GitHubPRCreator()

    with (
        patch("cafe.core.hooks.native.GitHubOps") as mock_github_ops,
        patch("cafe.core.hooks.native.get_processed_comment_ids_from_history", return_value=set()),
        patch("cafe.core.hooks.native.get_all_pr_comments", return_value=["raw-comments"]),
        patch("cafe.core.hooks.native.filter_unresolved_comments", return_value=["unresolved-comment"]),
        patch("cafe.core.hooks.native.format_comments_for_prompt", return_value="Comment #1\nPlease fix this"),
    ):
        mock_github_ops.return_value.get_pr_for_branch.return_value = {"number": 42, "url": "https://github.com/test/repo/pull/42"}
        result = hook.run(stage="prepare_input", phase=phase, step_name="pr")

    assert result.context_updates["pr_number"] == "42"
    assert result.context_updates["user_input"] == "Comment #1\nPlease fix this"
    assert phase.step_user_inputs["pr"] == "Comment #1\nPlease fix this"
    assert result.events == [{"type": "pr_comments_loaded", "count": 1, "pr_number": "42"}]


def test_github_pr_creator_prepare_input_uses_and_updates_last_seen_comments(tmp_path: Path) -> None:
    phase_dir = tmp_path / "pr"
    artifact_file = phase_dir / "artifacts" / "pr_last_seen_comments.json"
    artifact_file.parent.mkdir(parents=True, exist_ok=True)
    artifact_file.write_text(
        json.dumps({"last_seen_comment_ids": ["OLD"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    phase = _FakePhase(phase_dir=phase_dir, iteration=2)
    phase.git_ops = MagicMock()
    phase.git_ops.get_current_branch.return_value = "issue-250"
    phase.git_ops.has_unpushed_commits.return_value = False

    comment = MagicMock()
    comment.id = "NEW"

    hook = GitHubPRCreator()

    with (
        patch("cafe.core.hooks.native.get_processed_comment_ids_from_history") as mock_processed,
        patch("cafe.core.hooks.native.GitHubOps") as mock_github_ops,
        patch("cafe.core.hooks.native.get_all_pr_comments", return_value=[comment]) as mock_comments,
        patch("cafe.core.hooks.native.filter_unresolved_comments", return_value=[comment]),
        patch("cafe.core.hooks.native.format_comments_for_prompt", return_value="Comment #1\nPlease fix this"),
    ):
        mock_github_ops.return_value.get_pr_for_branch.return_value = {
            "number": 250,
            "url": "https://github.com/test/repo/pull/250",
        }
        result = hook.run(stage="prepare_input", phase=phase, step_name="pr")

    mock_processed.assert_not_called()
    mock_comments.assert_called_once_with(250, exclude_ids={"OLD"})
    assert result.events == [{"type": "pr_comments_loaded", "count": 1, "pr_number": "250"}]
    payload = json.loads(artifact_file.read_text(encoding="utf-8"))
    assert payload["last_seen_comment_ids"] == ["NEW", "OLD"]


def test_github_pr_creator_publish_output_runs_sync_pr_script(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
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
        '{"action":"created","pr_number":"42",'
        '"pr_url":"https://github.com/test/repo/pull/42"}\n'
    )
    completed.stderr = ""

    hook = GitHubPRCreator()
    with patch("cafe.core.capabilities.subprocess.run", return_value=completed) as mock_run:
        result = hook.run(
            stage="publish_output",
            phase=phase,
            step_name="pr",
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
    }
    assert result.events[1]["type"] == "capability_receipt"
    assert result.events[1]["capability"] == "cafe.pr.publish"
    assert result.events[1]["success"] is True


def test_github_pr_creator_publish_output_runs_from_workflow_complete_baton_without_status_code(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
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
        '{"action":"created","pr_number":"42",'
        '"pr_url":"https://github.com/test/repo/pull/42"}\n'
    )
    completed.stderr = ""

    hook = GitHubPRCreator()
    with patch("cafe.core.capabilities.subprocess.run", return_value=completed) as mock_run:
        result = hook.run(
            stage="publish_output",
            phase=phase,
            step_name="pr",
            output_file=output_file,
            publish_request_file=publish_request_file,
            context={"next_step_path": str(next_step_file)},
            status_code=None,
        )

    mock_run.assert_called_once()
    assert result.events[0]["type"] == "pr_synced"
    assert result.events[0]["source"] == "capability"
    assert result.events[1]["type"] == "capability_receipt"


def test_github_pr_creator_publish_output_runs_from_legacy_done_baton(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
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
        '{"action":"updated","pr_number":"42",'
        '"pr_url":"https://github.com/test/repo/pull/42"}\n'
    )
    completed.stderr = ""

    hook = GitHubPRCreator()
    with patch("cafe.core.capabilities.subprocess.run", return_value=completed) as mock_run:
        result = hook.run(
            stage="publish_output",
            phase=phase,
            step_name="pr",
            output_file=output_file,
            publish_request_file=publish_request_file,
            context={"next_step_path": str(next_step_file)},
            status_code=None,
        )

    mock_run.assert_called_once()
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
            output_file=output_file,
            publish_request_file=publish_request_file,
            status_code=PhaseStatusCode.CONFIRMED,
        )

    mock_run.assert_not_called()
    assert result.events == []


def test_github_pr_creator_publish_ignores_untrusted_script_field_in_request(tmp_path: Path) -> None:
    """Registry-resolved script is used; agent-supplied script path must not change dispatch."""
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
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
        '{"action":"created","pr_number":"42",'
        '"pr_url":"https://github.com/test/repo/pull/42"}\n'
    )
    completed.stderr = ""

    hook = GitHubPRCreator()
    with patch("cafe.core.capabilities.subprocess.run", return_value=completed) as mock_run:
        hook.run(
            stage="publish_output",
            phase=phase,
            step_name="pr",
            output_file=output_file,
            publish_request_file=publish_request_file,
            status_code=PhaseStatusCode.CONFIRMED,
        )

    cmd = mock_run.call_args.args[0]
    assert cmd[1] == str(hook._resolve_sync_script(tmp_path))


def test_pr_comment_poster_posts_todo_comment_only_when_confirmed_and_complete(tmp_path: Path) -> None:
    output_file = tmp_path / "output.md"
    output_file.write_text("## Todo List\n- [x] Fix comment\n", encoding="utf-8")

    phase = MagicMock()
    phase.git_ops.get_current_branch.return_value = "issue-183"

    hook = PRCommentPoster()

    with patch("cafe.core.hooks.native.GitHubOps") as mock_github_ops:
        mock_github_ops.return_value.get_pr_for_branch.return_value = {"number": 42, "url": "https://github.com/test/repo/pull/42"}
        result = hook.run(
            stage="publish_output",
            phase=phase,
            output_file=output_file,
            status_code=PhaseStatusCode.CONFIRMED,
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
        mock_github_ops.return_value.get_pr_for_branch.return_value = {"number": 42, "url": "https://github.com/test/repo/pull/42"}
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
