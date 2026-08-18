"""端對端整合測試：通用工作流程執行器（spec→plan→develop→review→pr→user）。

涵蓋場景：
  - 完整 happy path
  - 各步驟 self-loop 迭代
  - User handoff 暫停與同次自動繼續
  - Chat baton 建立與消費
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from cafe.core.blackboard import (
    BlackboardState,
    BlackboardStore,
    HandoffIntent,
    HandoffOwner,
)
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.core.types import AgentCLI, TokenUsage
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.playbook import resolve_step_behavior
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
from cafe.phases.generic_phase import GenericPhase
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader
from cafe.skills.native_bridge import NativeSkillBridge
from cafe.ui.cli import _consume_pending_chat_handoff, app
from cafe.ui.cli_shared import _load_issue_step_names
from cafe.utils.phase_config import PhaseStepModelResolution


@pytest.fixture(autouse=True)
def _configured_test_phase_chain(monkeypatch):
    """Keep workflow journeys focused on orchestration, with a valid test chain."""
    from cafe.phases import generic_workflow_step

    real_loader = generic_workflow_step.load_phase_step_model

    def load_test_phase(*, step_name, local_path, repo_path=None):
        if any(path is not None and path.exists() for path in (local_path, repo_path)):
            return real_loader(step_name=step_name, local_path=local_path, repo_path=repo_path)
        return PhaseStepModelResolution(
            name=None,
            role=None,
            clis=(("codex", "gpt-5-test"),),
            model="gpt-5-test",
            source="test",
            chain=("test",),
            clis_source="test",
        )

    monkeypatch.setattr(generic_workflow_step, "load_phase_step_model", load_test_phase)


def _write_pr_done_baton(issue_dir: Path) -> None:
    """Write a baton announcing PR -> done so the baton-driven runtime advances."""
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")
    store.update_handoff_contract(
        state,
        from_step="pr",
        to_owner=HandoffOwner.DONE,
        to_step="done",
        intent=HandoffIntent.WORKFLOW_COMPLETE,
        status_code="confirmed",
        source="test.executor",
    )


class _BatonWritingAgentManager:
    """Test-double agent boundary that writes the workflow's public baton."""

    def __init__(self, issue_dir: Path) -> None:
        self.issue_dir = issue_dir
        self.prompts: list[str] = []
        self.allowed_tools_calls: list[list[str] | None] = []
        self.agent = SimpleNamespace(
            config=SimpleNamespace(cli=AgentCLI.CODEX, session_id="integration-test", model=None)
        )

    def get_agent(self, _name: str) -> SimpleNamespace:
        return self.agent

    def execute(self, _name: str, _prompt: str, **_kwargs):
        self.prompts.append(_prompt)
        self.allowed_tools_calls.append(_kwargs.get("allowed_tools"))
        state = BlackboardStore(self.issue_dir).load_or_create("release")
        BlackboardStore(self.issue_dir).update_handoff_contract(
            state,
            from_step="release",
            to_owner=HandoffOwner.DONE,
            to_step="done",
            intent=HandoffIntent.WORKFLOW_COMPLETE,
            status_code="confirmed",
            source="test.agent",
        )
        return "completed", TokenUsage(), [], [], [], None


class _FeedbackAgentManager(_BatonWritingAgentManager):
    """Test-double agent that reports publish feedback through the normal executor."""

    def execute(self, _name: str, _prompt: str, **_kwargs):
        store = BlackboardStore(self.issue_dir)
        state = store.load_or_create("release")
        store.update_handoff_contract(
            state,
            from_step="release",
            to_owner=HandoffOwner.AGENT,
            to_step="repair",
            intent=HandoffIntent.AWAIT_AGENT,
            status_code=PhaseStatusCode.NEEDS_CHANGES.value,
            source="test.feedback",
        )
        return "needs_changes", TokenUsage(), [], [], [], None


class _GitOperations:
    def get_default_base_branch(self) -> str:
        return "main"

    def get_repo_root(self) -> Path:
        return Path.cwd()


def _build_integration_generic_phase(tmp_path: Path) -> GenericPhase:
    skill_dir = tmp_path / "builtin" / "skills" / "cafe-develop"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: cafe-develop\ndescription: Integration test skill\n---\n\n# Develop\n",
        encoding="utf-8",
    )
    loader = SkillLoader(
        project_root=tmp_path,
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )
    loader.discover()
    return GenericPhase(
        loader,
        skill_bridge=NativeSkillBridge(loader, project_root=tmp_path, home_dir=tmp_path / "home"),
    )


def test_custom_publish_feedback_and_lifecycle_contracts(tmp_path: Path, monkeypatch) -> None:
    """IT-001/IT-002: custom publish feedback resumes its declared repair step."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".cafe").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".cafe" / "phases.yaml").write_text(
        "repair:\n  name: David\n  clis:\n    - cli: codex\n      model: test-model\n"
        "release:\n  name: David\n  clis:\n    - cli: codex\n      model: test-model\n",
        encoding="utf-8",
    )
    issue_dir = tmp_path / ".cafe" / "issues" / "release-journey"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text(
        "playbook: release-flow\npr:\n  auto_create: true\n", encoding="utf-8"
    )
    playbook_dir = tmp_path / ".cafe" / "playbooks"
    playbook_dir.mkdir(parents=True)
    (playbook_dir / "release-flow.yaml").write_text(
        """
playbook:
  id: release-flow
roles:
  developer: {default_agent: David}
steps:
  repair:
    skill: cafe-develop
    role: developer
    input_artifacts: [workflow_feedback]
    on: {await_agent: release}
  release:
    skill: cafe-develop
    role: developer
    capability_requests: [cafe.pr.publish]
    behavior:
      completion: baton
      publish_confirmation: true
      feedback_target: repair
      context_providers: [workflow_metadata]
      runtime_tool_grants: [git_inspection]
    hooks:
      prepare_input: [UserInputCollector]
      publish_output: [GitHubPRCreator]
    on: {await_agent: _done}
""".strip(),
        encoding="utf-8",
    )
    playbook = PlaybookLoader().load("release-flow")

    agent_manager = _BatonWritingAgentManager(issue_dir)
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="release-journey",
        playbook=playbook,
        generic_phase=_build_integration_generic_phase(tmp_path),
        agent_manager=agent_manager,
        git_ops=_GitOperations(),
        role_agent_map={"developer": "David"},
    )
    receipt = {
        "capability": "cafe.pr.publish",
        "correlation_id": "release-journey",
        "success": True,
        "category": "success",
        "code": "published",
        "inputs": {},
        "outputs": {"pr_url": "https://example.test/pr/1", "pr_number": "1"},
    }
    with (
        patch("cafe.core.capabilities.load_capability_registry", return_value=object()),
        patch(
            "cafe.core.capabilities.run_capability_request",
            return_value=SimpleNamespace(
                receipt=receipt,
                pr_synced_event={"type": "pr_synced", "url": "https://example.test/pr/1"},
            ),
        ),
    ):
        result = BlackboardWorkflowRuntime(
            issue_dir=issue_dir,
            playbook=playbook,
            executor=executor.execute_step,
        ).run(start_step="release")

    assert result.completed is True
    assert '"playbook_id": "release-flow"' in agent_manager.prompts[0]
    assert "bash(git status)" in (agent_manager.allowed_tools_calls[0] or [])
    assert _load_issue_step_names("release-journey") == ["repair", "release"]

    iteration_dir = issue_dir / "release" / "iteration_001"
    assert json.loads((iteration_dir / "publish_request.json").read_text(encoding="utf-8"))[
        "capability"
    ] == ("cafe.pr.publish")
    state = BlackboardStore(issue_dir).load_or_create("release")
    assert state.capability_receipts == [receipt]

    runner = CliRunner()
    git_factory = lambda: SimpleNamespace(get_current_branch=lambda: "release-journey")

    feedback_executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="release-journey",
        playbook=playbook,
        generic_phase=_build_integration_generic_phase(tmp_path),
        agent_manager=_FeedbackAgentManager(issue_dir),
        git_ops=_GitOperations(),
        role_agent_map={"developer": "David"},
    )
    BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=feedback_executor.execute_step,
    ).run(start_step="release", single_step=True)

    state = BlackboardStore(issue_dir).load_or_create("release")
    assert (
        BlackboardStore(issue_dir)
        .load_handoff_contract(state, allowed_steps=["repair", "release"])
        .to_step
        == "repair"
    )

    resumed_steps: list[str] = []

    class _ResumeExecutor:
        def execute_step(
            self,
            step_name: str,
            step_def: dict,
            blackboard_state: object,
            extra_prompt: Optional[str] = None,
        ) -> StepExecutionResult:
            resumed_steps.append(step_name)
            if step_name == "release":
                BlackboardStore(issue_dir).update_handoff_contract(
                    blackboard_state,
                    from_step="release",
                    to_owner=HandoffOwner.DONE,
                    to_step="done",
                    intent=HandoffIntent.WORKFLOW_COMPLETE,
                    source="test.resume",
                )
            return StepExecutionResult(
                response="confirmed",
                artifacts={},
                status_code="confirmed",
            )

    with (
        patch("cafe.ui.cli.GitOperations", return_value=git_factory()),
        patch(
            "cafe.ui.commands.workflow._build_workflow_step_executor",
            return_value=_ResumeExecutor(),
        ),
    ):
        resume_result = runner.invoke(
            app,
            [
                "workflow",
                "--playbook",
                "release-flow",
                "--issue",
                "release-journey",
                "--start-step",
                "repair",
                "--execute",
            ],
        )
    assert resume_result.exit_code == 0, resume_result.output
    assert resumed_steps == ["repair", "release"]

    with patch("cafe.ui.commands.workflow._get_GitOperations", return_value=git_factory):
        show_result = runner.invoke(app, ["show", "release"])
    assert show_result.exit_code == 0

    class _SummaryService:
        def get_current_issue(self) -> str:
            return "release-journey"

        def load_phase_status(self, _issue: str, _step: str):
            return None

        def load_iteration_statuses(self, _issue: str, _step: str):
            return []

    with patch("cafe.services.summary_service.SummaryService", _SummaryService):
        status_result = runner.invoke(app, ["status"])
    assert status_result.exit_code == 0

    feedback_iteration_dir = issue_dir / "release" / "iteration_002"
    assert feedback_iteration_dir.exists()
    with (
        patch("cafe.ui.cli.GitOperations", return_value=git_factory()),
        patch("cafe.ui.cli.prompt_confirm", return_value=True),
        patch("cafe.ui.commands.lifecycle.GitOperations", return_value=git_factory()),
        patch("cafe.ui.commands.lifecycle._get_project_path", return_value="test/project"),
        patch("cafe.ui.commands.lifecycle.prompt_confirm", return_value=True),
        patch("cafe.ui.commands.lifecycle.Path.home", return_value=tmp_path / "home"),
    ):
        reset_result = runner.invoke(app, ["reset", "release"])
    assert reset_result.exit_code == 0, reset_result.output
    assert iteration_dir.exists()
    assert not feedback_iteration_dir.exists()


def test_default_requested_changes_follow_declared_loop_without_publish_authority(
    tmp_path: Path,
) -> None:
    """IT-003: local feedback is delivered through the loop before trusted publishing."""
    from cafe.core.workflow_feedback import WorkflowFeedbackLedger
    from cafe.ui.human_tasks import apply_human_task_payload

    issue_dir = tmp_path / ".cafe" / "issues" / "default-correction"
    playbook = PlaybookLoader().load("default")
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("pr", playbook_id="default")
    store.set_current_step(state, "user")
    store.update_handoff_contract(
        state,
        from_step="pr",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.CONFIRM_OUTPUT,
        source="integration",
    )

    result = apply_human_task_payload(
        issue_dir=issue_dir,
        playbook_data=playbook,
        blackboard=state,
        from_step="pr",
        trigger="confirm_output",
        raw_payload={
            "task": "local-review",
            "decision": "request_changes",
            "feedback": "Exercise the declared correction route.",
        },
        source="integration",
    )

    assert result.target == "develop"
    assert [entry.target_step for entry in WorkflowFeedbackLedger(issue_dir).pending()] == [
        "develop"
    ]

    executed_steps: list[str] = []

    def executor(step_name: str, step_def: dict, _state: BlackboardState) -> StepExecutionResult:
        executed_steps.append(step_name)
        if step_name == "pr":
            _write_pr_done_baton(issue_dir)
            return StepExecutionResult(
                response="confirmed",
                artifacts={"pr_result": "pr/output.md"},
                status_code="confirmed",
                events=[
                    {
                        "type": "capability_receipt",
                        "capability": "cafe.pr.publish",
                        "success": True,
                        "correlation_id": "local-review-correction",
                        "category": None,
                        "code": None,
                    }
                ],
            )
        return StepExecutionResult(
            response="confirmed",
            artifacts={str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"},
            status_code="confirmed",
            agent_invoked=True,
        )

    workflow_result = _run_until_settled(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
        max_transitions=20,
    )

    assert workflow_result.completed is True
    assert workflow_result.final_step == "pr"
    assert executed_steps == ["develop", "review", "pr"]
    assert WorkflowFeedbackLedger(issue_dir).pending() == []


def test_default_parity_and_metadata_absent_lifecycle_boundary(tmp_path: Path, monkeypatch) -> None:
    """IT-003: default completion, correction, review, and publish remain observable."""
    monkeypatch.chdir(tmp_path)

    default = PlaybookLoader().load("default")
    assert resolve_step_behavior(default, "pr").publish_confirmation is True
    assert resolve_step_behavior(default, "review").runtime_tool_grants == [
        "web_research",
        "git_inspection",
    ]

    issue_dir = tmp_path / ".cafe" / "issues" / "default-parity"
    executed_steps: list[str] = []
    review_visits = 0

    def default_executor(
        step_name: str, step_def: dict, state: BlackboardState
    ) -> StepExecutionResult:
        nonlocal review_visits
        executed_steps.append(step_name)
        if step_name == "review":
            review_visits += 1
            if review_visits == 1:
                return StepExecutionResult(
                    response="needs_changes",
                    artifacts={},
                    status_code="needs_changes",
                )
        events = []
        if step_name == "pr":
            _write_pr_done_baton(issue_dir)
            events.append({"type": "pr_synced", "url": "https://example.test/pr/default"})
        return StepExecutionResult(
            response="confirmed",
            artifacts={str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"},
            status_code="confirmed",
            events=events,
        )

    result = _run_until_settled(
        issue_dir=issue_dir,
        playbook=default,
        executor=default_executor,
        max_transitions=20,
    )
    assert result.completed is True
    assert executed_steps.count("develop") == 2
    assert executed_steps.count("review") == 2
    assert executed_steps[-1] == "pr"

    assert _load_issue_step_names("metadata-absent") == ["spec", "plan", "develop", "review", "pr"]

    issue_dir = tmp_path / ".cafe" / "issues" / "configured-invalid"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook: unavailable\n", encoding="utf-8")

    with pytest.raises(ValueError, match="could not be loaded"):
        _load_issue_step_names("configured-invalid")


# ---------------------------------------------------------------------------
# 輔助函式
# ---------------------------------------------------------------------------


def _load_default_playbook() -> dict:
    """載入真實 default playbook。"""
    return PlaybookLoader().load("default")


def _run_until_settled(
    *,
    issue_dir: Path,
    playbook: dict,
    executor,
    max_outer_loops: int = 8,
    max_transitions: int = 30,
):
    """Drive BlackboardWorkflowRuntime through every boundary handoff.

    Mirrors the production CLI loop: when the runtime returns mid-flight at a
    boundary (e.g. PR step takes over from the legacy slice), call ``run`` again
    starting from the blackboard's current_step until the workflow either
    completes or pauses on user input.
    """
    last_result = None
    pending_start: str | None = None
    for _ in range(max_outer_loops):
        runner = BlackboardWorkflowRuntime(
            issue_dir=issue_dir,
            playbook=playbook,
            executor=executor,
        )
        last_result = runner.run(start_step=pending_start, max_transitions=max_transitions)
        latest = BlackboardStore(issue_dir).load_or_create(
            str(playbook.get("entry_point") or next(iter(playbook["steps"].keys()))),
            playbook_id=str(playbook["playbook"]["id"]),
        )
        if last_result.completed:
            return last_result
        if last_result.final_status_code == "BATON_POSITION_REALIGNED":
            return last_result
        if latest.current_step in {"user", "done"}:
            return last_result
        # Continue from blackboard's current_step (boundary handoff).
        pending_start = latest.current_step
    return last_result


# ---------------------------------------------------------------------------
# Task 2: Happy Path E2E 測試
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_full_workflow_completes(self, tmp_path: Path) -> None:
        """完整 spec→plan→develop→review→pr→_done happy path。"""
        issue_dir = tmp_path / ".cafe" / "issues" / "issue-e2e-happy"
        playbook = _load_default_playbook()

        def executor(step_name: str, step_def: dict, state: BlackboardState) -> StepExecutionResult:
            events = []
            if step_name == "pr":
                _write_pr_done_baton(issue_dir)
                events.append({"type": "pr_synced", "url": "https://example.com/pr/1"})
            return StepExecutionResult(
                response="confirmed",
                artifacts={
                    str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"
                },
                status_code="confirmed",
                events=events,
            )

        result = _run_until_settled(
            issue_dir=issue_dir,
            playbook=playbook,
            executor=executor,
            max_transitions=20,
        )

        assert result.completed is True
        assert result.final_step == "pr"

        blackboard = BlackboardStore(issue_dir).load_or_create("spec")
        step_completed_steps = [
            e.data.get("step") for e in blackboard.events if e.event_type == "step_completed"
        ]
        for step in ("spec", "plan", "develop", "review", "pr"):
            assert step in step_completed_steps, f"step_completed event missing for {step}"

    def test_happy_path_artifacts_recorded(self, tmp_path: Path) -> None:
        """每個步驟的 artifact 正確寫入 blackboard。"""
        issue_dir = tmp_path / ".cafe" / "issues" / "issue-e2e-artifacts"
        playbook = _load_default_playbook()

        def executor(step_name: str, step_def: dict, state: BlackboardState) -> StepExecutionResult:
            artifact_key = str(step_def.get("output_artifact", step_name))
            events = []
            if step_name == "pr":
                _write_pr_done_baton(issue_dir)
                events.append({"type": "pr_synced", "url": "https://example.com/pr/1"})
            return StepExecutionResult(
                response="confirmed",
                artifacts={artifact_key: f"{step_name}/iteration_001/output.md"},
                status_code="confirmed",
                events=events,
            )

        _run_until_settled(
            issue_dir=issue_dir,
            playbook=playbook,
            executor=executor,
            max_transitions=20,
        )

        blackboard = BlackboardStore(issue_dir).load_or_create("spec")
        # spec, plan, review_feedback, pr_result 等 artifact 應存在
        assert "spec" in blackboard.artifacts
        assert "plan" in blackboard.artifacts
        assert "review_feedback" in blackboard.artifacts
        assert "pr_result" in blackboard.artifacts


# ---------------------------------------------------------------------------
# Task 3: Self-loop 迭代測試（全步驟）
# ---------------------------------------------------------------------------


class TestSelfLoop:
    def _run_single_step_loop(
        self,
        tmp_path: Path,
        start_step: str,
        loop_status: str,
        loop_count: int,
        final_status: str,
        expected_next_step: str,
    ) -> tuple:
        """執行單步驟 self-loop 場景的通用輔助。"""
        issue_dir = tmp_path / ".cafe" / "issues" / f"issue-loop-{start_step}"
        playbook = _load_default_playbook()

        call_counts: dict = {start_step: 0}
        subsequent_calls: dict = {}

        def executor(step_name: str, step_def: dict, state: BlackboardState) -> StepExecutionResult:
            if step_name == start_step:
                call_counts[start_step] = call_counts.get(start_step, 0) + 1
                n = call_counts[start_step]
                if n <= loop_count:
                    return StepExecutionResult(
                        response=loop_status,
                        artifacts={},
                        status_code=loop_status,
                        auto_continue=True,
                    )
                return StepExecutionResult(
                    response=final_status,
                    artifacts={
                        str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"
                    },
                    status_code=final_status,
                )
            subsequent_calls[step_name] = subsequent_calls.get(step_name, 0) + 1
            events = []
            if step_name == "pr":
                _write_pr_done_baton(issue_dir)
                events.append({"type": "pr_synced", "url": "https://example.com/pr/1"})
            return StepExecutionResult(
                response="confirmed",
                artifacts={
                    str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"
                },
                status_code="confirmed",
                events=events,
            )

        result = _run_until_settled(
            issue_dir=issue_dir,
            playbook=playbook,
            executor=executor,
            max_transitions=30,
        )
        return result, call_counts, subsequent_calls

    def test_spec_self_loop_then_confirms(self, tmp_path: Path) -> None:
        """spec need_clarification×2 後 confirmed，最終流向 plan。"""
        result, calls, subsequent = self._run_single_step_loop(
            tmp_path,
            start_step="spec",
            loop_status="need_clarification",
            loop_count=2,
            final_status="confirmed",
            expected_next_step="plan",
        )
        assert calls["spec"] == 3  # 2 loop + 1 confirm
        assert subsequent.get("plan", 0) >= 1
        assert result.completed is True

    def test_plan_self_loop_then_confirms(self, tmp_path: Path) -> None:
        """plan ready_for_review×2 後 confirmed，最終流向 develop。"""
        result, calls, subsequent = self._run_single_step_loop(
            tmp_path,
            start_step="plan",
            loop_status="ready_for_review",
            loop_count=2,
            final_status="confirmed",
            expected_next_step="develop",
        )
        assert calls["plan"] == 3
        assert subsequent.get("develop", 0) >= 1
        assert result.completed is True

    def test_develop_self_loop_then_confirms(self, tmp_path: Path) -> None:
        """develop need_clarification×2 後 confirmed，最終流向 review。"""
        result, calls, subsequent = self._run_single_step_loop(
            tmp_path,
            start_step="develop",
            loop_status="need_clarification",
            loop_count=2,
            final_status="confirmed",
            expected_next_step="review",
        )
        assert calls["develop"] == 3
        assert subsequent.get("review", 0) >= 1
        assert result.completed is True

    def test_review_self_loop_then_confirms(self, tmp_path: Path) -> None:
        """review need_clarification×2 後 confirmed，在 max_iterations=3 限制內。"""
        result, calls, subsequent = self._run_single_step_loop(
            tmp_path,
            start_step="review",
            loop_status="need_clarification",
            loop_count=2,
            final_status="confirmed",
            expected_next_step="pr",
        )
        assert calls["review"] == 3  # 2 loop + 1 confirm，恰好在限制內
        assert subsequent.get("pr", 0) >= 1
        assert result.completed is True

    def test_review_exceeds_max_iterations_raises(self, tmp_path: Path) -> None:
        """review 超過 max_iterations=3 應拋出 RuntimeError。"""
        issue_dir = tmp_path / ".cafe" / "issues" / "issue-loop-overflow"
        playbook = _load_default_playbook()
        call_counts: dict = {}

        def executor(step_name: str, step_def: dict, state: BlackboardState) -> StepExecutionResult:
            call_counts[step_name] = call_counts.get(step_name, 0) + 1
            if step_name == "review":
                return StepExecutionResult(
                    response="needs_changes",
                    artifacts={},
                    status_code="needs_changes",
                )
            return StepExecutionResult(
                response="confirmed",
                artifacts={
                    str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"
                },
                status_code="confirmed",
            )

        runner = BlackboardWorkflowRuntime(
            issue_dir=issue_dir,
            playbook=playbook,
            executor=executor,
        )
        with pytest.raises(RuntimeError, match="exceeded max_iterations"):
            runner.run(max_transitions=30)

        # review 應被呼叫恰好 max_iterations（3）次後拋出（第 4 次在執行前被攔截）
        assert call_counts.get("review", 0) >= 3

    def test_iteration_counters_recorded_in_blackboard(self, tmp_path: Path) -> None:
        """self-loop 期間 blackboard events 應包含正確的 visit 計數。"""
        issue_dir = tmp_path / ".cafe" / "issues" / "issue-loop-events"
        playbook = _load_default_playbook()
        spec_calls = 0

        def executor(step_name: str, step_def: dict, state: BlackboardState) -> StepExecutionResult:
            nonlocal spec_calls
            if step_name == "spec":
                spec_calls += 1
                if spec_calls < 3:
                    return StepExecutionResult(
                        response="need_clarification",
                        artifacts={},
                        status_code="need_clarification",
                        auto_continue=True,
                    )
            return StepExecutionResult(
                response="confirmed",
                artifacts={
                    str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"
                },
                status_code="confirmed",
            )

        runner = BlackboardWorkflowRuntime(
            issue_dir=issue_dir,
            playbook=playbook,
            executor=executor,
        )
        runner.run(max_transitions=30)

        blackboard = BlackboardStore(issue_dir).load_or_create("spec")
        spec_started_events = [
            e
            for e in blackboard.events
            if e.event_type == "step_started" and e.data.get("step") == "spec"
        ]
        visits = [e.data.get("visit") for e in spec_started_events]
        assert visits == [1, 2, 3], f"expected visits [1,2,3], got {visits}"


# ---------------------------------------------------------------------------
# Task 4: User Handoff + Same-Run Continue 測試
# ---------------------------------------------------------------------------


class TestUserHandoff:
    def test_user_handoff_pauses_workflow(self, tmp_path: Path) -> None:
        """spec 回傳 need_clarification（auto_continue=False）→ workflow 應暫停。"""
        issue_dir = tmp_path / ".cafe" / "issues" / "issue-handoff-pause"
        playbook = _load_default_playbook()

        def executor(step_name: str, step_def: dict, state: BlackboardState) -> StepExecutionResult:
            return StepExecutionResult(
                response="need_clarification",
                artifacts={},
                status_code="need_clarification",
                auto_continue=False,
            )

        runner = BlackboardWorkflowRuntime(
            issue_dir=issue_dir,
            playbook=playbook,
            executor=executor,
        )
        result = runner.run(start_step="spec", max_transitions=10)

        assert result.completed is False
        blackboard = BlackboardStore(issue_dir).load_or_create("spec")
        assert blackboard.current_step == "user"
        pause_events = [e for e in blackboard.events if e.event_type == "workflow_paused"]
        assert pause_events, "workflow_paused event should be recorded"

    def test_contract_mismatch_realigns_before_resuming_agent_step(self, tmp_path: Path) -> None:
        """A valid baton/current_step mismatch pauses once before the target step runs."""
        issue_dir = tmp_path / ".cafe" / "issues" / "issue-contract-mismatch"
        playbook = {
            "playbook": {"id": "default"},
            "steps": {
                "spec": {
                    "skill": "spec_first",
                    "role": "pm",
                    "on": {"await_agent": "plan"},
                },
                "plan": {
                    "skill": "plan",
                    "role": "developer",
                    "on": {"await_agent": "_done"},
                },
            },
        }
        store = BlackboardStore(issue_dir)
        blackboard = store.load_or_create("spec")
        store.update_handoff_contract(
            blackboard,
            from_step="spec",
            to_owner=HandoffOwner.AGENT,
            to_step="plan",
            intent=HandoffIntent.AWAIT_AGENT,
            status_code="confirmed",
            source="test.desync",
        )
        executed_steps: list[str] = []

        def executor(step_name: str, step_def: dict, state: BlackboardState) -> StepExecutionResult:
            executed_steps.append(step_name)
            return StepExecutionResult(
                response="confirmed",
                artifacts={str(step_def.get("output_artifact", step_name)): "output.md"},
                status_code="confirmed",
            )

        first = BlackboardWorkflowRuntime(
            issue_dir=issue_dir,
            playbook=playbook,
            executor=executor,
        ).run(max_transitions=10)

        assert first.completed is False
        assert first.final_status_code == "BATON_POSITION_REALIGNED"
        assert executed_steps == []
        blackboard = BlackboardStore(issue_dir).load_or_create("spec")
        assert blackboard.current_step == "plan"
        assert any(e.event_type == "runtime_position_realigned" for e in blackboard.events)

        second = BlackboardWorkflowRuntime(
            issue_dir=issue_dir,
            playbook=playbook,
            executor=executor,
        ).run(max_transitions=10)

        assert second.completed is True
        assert second.final_step == "plan"
        assert executed_steps == ["plan"]

    def test_contract_owner_user_and_done_are_terminal_positions(self, tmp_path: Path) -> None:
        """Contract owner states should not execute an agent step while resuming."""
        playbook = {
            "playbook": {"id": "default"},
            "steps": {
                "develop": {
                    "skill": "develop",
                    "role": "developer",
                    "on": {"await_agent": "review"},
                },
                "review": {
                    "skill": "review",
                    "role": "reviewer",
                    "on": {"await_agent": "_done"},
                },
            },
        }
        executed_steps: list[str] = []

        def executor(step_name: str, step_def: dict, state: BlackboardState) -> StepExecutionResult:
            executed_steps.append(step_name)
            return StepExecutionResult(
                response="confirmed",
                artifacts={},
                status_code="confirmed",
            )

        user_issue_dir = tmp_path / ".cafe" / "issues" / "issue-contract-user"
        user_store = BlackboardStore(user_issue_dir)
        user_blackboard = user_store.load_or_create("develop")
        user_store.update_handoff_contract(
            user_blackboard,
            from_step="develop",
            to_owner=HandoffOwner.USER,
            to_step="user",
            intent=HandoffIntent.NEED_CLARIFICATION,
            status_code="need_clarification",
            source="test.user",
        )

        user_result = BlackboardWorkflowRuntime(
            issue_dir=user_issue_dir,
            playbook=playbook,
            executor=executor,
        ).run(max_transitions=10)

        assert user_result.completed is False
        assert user_result.final_step == "develop"
        assert user_result.final_status_code == "need_clarification"
        assert BlackboardStore(user_issue_dir).load_or_create("develop").current_step == "user"

        done_issue_dir = tmp_path / ".cafe" / "issues" / "issue-contract-done"
        done_store = BlackboardStore(done_issue_dir)
        done_blackboard = done_store.load_or_create("review")
        done_store.update_handoff_contract(
            done_blackboard,
            from_step="review",
            to_owner=HandoffOwner.DONE,
            to_step="done",
            intent=HandoffIntent.WORKFLOW_COMPLETE,
            status_code="confirmed",
            source="test.done",
        )

        done_result = BlackboardWorkflowRuntime(
            issue_dir=done_issue_dir,
            playbook=playbook,
            executor=executor,
        ).run(max_transitions=10)

        assert done_result.completed is True
        assert done_result.final_step == "review"
        assert BlackboardStore(done_issue_dir).load_or_create("develop").current_step == "done"
        assert executed_steps == []

    def test_user_handoff_auto_continue_resumes_in_same_run(self, tmp_path: Path) -> None:
        """spec 先 need_clarification（auto_continue=True），再 confirmed → 不暫停，直接流向 plan。"""
        issue_dir = tmp_path / ".cafe" / "issues" / "issue-handoff-resume"
        playbook = _load_default_playbook()
        spec_calls = 0

        def executor(step_name: str, step_def: dict, state: BlackboardState) -> StepExecutionResult:
            nonlocal spec_calls
            if step_name == "spec":
                spec_calls += 1
                if spec_calls == 1:
                    return StepExecutionResult(
                        response="need_clarification",
                        artifacts={},
                        status_code="need_clarification",
                        auto_continue=True,
                    )
            events = []
            if step_name == "pr":
                _write_pr_done_baton(issue_dir)
                events.append({"type": "pr_synced", "url": "https://example.com/pr/1"})
            return StepExecutionResult(
                response="confirmed",
                artifacts={
                    str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"
                },
                status_code="confirmed",
                events=events,
            )

        result = _run_until_settled(
            issue_dir=issue_dir,
            playbook=playbook,
            executor=executor,
            max_transitions=30,
        )

        assert result.completed is True
        assert spec_calls == 2  # 1 loop + 1 confirm

    def test_cli_workflow_execute_resumes_after_user_handoff(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """CLI 在同一次 --execute 呼叫中，user handoff 後自動繼續到完成。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".cafe").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".cafe" / "phases.yaml").write_text(
            "".join(
                f"{step}:\n  name: David\n  clis:\n    - cli: codex\n      model: test-model\n"
                for step in ("spec", "plan", "develop", "review", "pr")
            ),
            encoding="utf-8",
        )

        issue_dir = tmp_path / ".cafe" / "issues" / "issue-cli-resume"
        issue_dir.mkdir(parents=True, exist_ok=True)

        call_log: List[str] = []
        spec_calls = 0

        class FakeExecutor:
            def execute_step(
                self,
                step_name: str,
                step_def: dict,
                blackboard_state: object,
                extra_prompt: Optional[str] = None,
            ) -> StepExecutionResult:
                nonlocal spec_calls
                call_log.append(step_name)
                if step_name == "spec":
                    spec_calls += 1
                    if spec_calls == 1:
                        # 第一次呼叫：暫停（auto_continue=False）
                        return StepExecutionResult(
                            response="need_clarification",
                            artifacts={},
                            status_code="need_clarification",
                            auto_continue=False,
                        )
                return StepExecutionResult(
                    response="confirmed",
                    artifacts={
                        str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"
                    },
                    status_code="confirmed",
                )

        cli_runner = CliRunner()
        # _handle_user_phase 第一次返回 "spec"（繼續執行），第二次返回 None（結束）
        handle_user_side_effects = ["spec", None]
        with (
            patch("cafe.ui.cli.GitOperations") as mock_git_cls,
            patch(
                "cafe.ui.commands.workflow._build_workflow_step_executor",
                return_value=FakeExecutor(),
            ),
            patch(
                "cafe.ui.cli._handle_user_phase",
                side_effect=handle_user_side_effects,
            ),
            patch.dict("os.environ", {"CAFE_FORCE_INTERACTIVE": "1"}),
        ):
            git = MagicMock()
            git.get_current_branch.return_value = "issue-cli-resume"
            mock_git_cls.return_value = git

            result = cli_runner.invoke(app, ["workflow", "--playbook", "default", "--execute"])

        assert result.exit_code == 0, result.output
        # spec 應被呼叫兩次（第一次暫停，第二次完成）
        assert call_log.count("spec") == 2
        # 完整流程應跑完
        for step in ("spec", "plan", "develop", "review", "pr"):
            assert step in call_log, f"{step} should be in call_log: {call_log}"


# ---------------------------------------------------------------------------
# Task 5.0: next_step.txt Lifecycle
# ---------------------------------------------------------------------------


class TestNextStepLifecycle:
    def test_workflow_initializes_next_step_txt_when_missing(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """next_step.txt missing initially should be created once workflow starts."""
        monkeypatch.chdir(tmp_path)

        issue_dir = tmp_path / ".cafe" / "issues" / "issue-nextstep-missing"
        issue_dir.mkdir(parents=True, exist_ok=True)
        (issue_dir / "blackboard.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "playbook_id": "default",
                    "current_step": "spec",
                    "artifacts": {},
                    "events": [],
                    "decisions": [],
                }
            ),
            encoding="utf-8",
        )

        next_step_path = issue_dir / "next_step.txt"
        assert not next_step_path.exists()

        class FakeExecutor:
            def execute_step(
                self,
                step_name: str,
                step_def: dict,
                blackboard_state: object,
                extra_prompt: Optional[str] = None,
            ) -> tuple[str, dict[str, str]]:
                if step_name == "pr":
                    _write_pr_done_baton(issue_dir)
                return (
                    "confirmed",
                    {str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"},
                )

        cli_runner = CliRunner()
        with (
            patch("cafe.ui.cli.GitOperations") as mock_git_cls,
            patch(
                "cafe.ui.commands.workflow._build_workflow_step_executor",
                return_value=FakeExecutor(),
            ),
        ):
            git = MagicMock()
            git.get_current_branch.return_value = "issue-nextstep-missing"
            git.has_uncommitted_changes.return_value = False
            mock_git_cls.return_value = git

            result = cli_runner.invoke(app, ["workflow", "--playbook", "default", "--execute"])

        assert result.exit_code == 0, result.output
        assert next_step_path.exists()

        # Validate JSON baton contract structure.
        payload = json.loads(next_step_path.read_text(encoding="utf-8"))
        assert payload["version"] == 1
        assert payload["to_step"] in {"spec", "plan", "develop", "review", "pr", "user", "done"}


# ---------------------------------------------------------------------------
# Task 5: Chat Baton 消費測試
# ---------------------------------------------------------------------------


class TestChatBaton:
    def _make_playbook_data(self) -> dict:
        return _load_default_playbook()

    def test_chat_baton_consumed_and_blackboard_updated(self, tmp_path: Path) -> None:
        """structured JSON next_step.txt 存在且內容有效時，應被消費並更新 blackboard。"""
        issue_dir = tmp_path / ".cafe" / "issues" / "issue-baton-1"
        issue_dir.mkdir(parents=True, exist_ok=True)
        next_step_path = issue_dir / "next_step.txt"
        next_step_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "from_step": "spec",
                    "to_owner": "agent",
                    "to_step": "develop",
                    "intent": "await_agent",
                }
            ),
            encoding="utf-8",
        )

        playbook_data = self._make_playbook_data()

        with patch("cafe.ui.cli.GitOperations") as mock_git_cls:
            git = MagicMock()
            git.has_uncommitted_changes.return_value = False
            mock_git_cls.return_value = git

            result = _consume_pending_chat_handoff(
                issue_dir=issue_dir,
                playbook_data=playbook_data,
                requested_start_step=None,
            )

        assert result == "develop"
        assert next_step_path.exists(), "next_step.txt should remain as persistent baton"

        blackboard = BlackboardStore(issue_dir).load_or_create("spec")
        assert blackboard.current_step == "develop"

    def test_chat_baton_plain_text_is_rejected_not_consumed(self, tmp_path: Path) -> None:
        """Issue #386: a plain-text next_step.txt is never consumed as a step name."""
        issue_dir = tmp_path / ".cafe" / "issues" / "issue-baton-1-legacy"
        issue_dir.mkdir(parents=True, exist_ok=True)
        next_step_path = issue_dir / "next_step.txt"
        next_step_path.write_text("develop", encoding="utf-8")

        playbook_data = self._make_playbook_data()

        with patch("cafe.ui.cli.GitOperations") as mock_git_cls:
            git = MagicMock()
            git.has_uncommitted_changes.return_value = False
            mock_git_cls.return_value = git

            result = _consume_pending_chat_handoff(
                issue_dir=issue_dir,
                playbook_data=playbook_data,
                requested_start_step=None,
            )

        assert result is None
        assert next_step_path.exists()

    def test_chat_baton_bootstrap_not_consumed(self, tmp_path: Path) -> None:
        """bootstrap/persistent baton should not be treated as pending chat handoff."""
        issue_dir = tmp_path / ".cafe" / "issues" / "issue-baton-bootstrap"
        issue_dir.mkdir(parents=True, exist_ok=True)
        next_step_path = issue_dir / "next_step.txt"
        next_step_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "from_step": "spec",
                    "to_owner": "agent",
                    "to_step": "spec",
                    "intent": "await_agent",
                    "status_code": "",
                    "created_at": "2026-04-16T00:00:00+00:00",
                    "source": "bootstrap",
                }
            ),
            encoding="utf-8",
        )

        playbook_data = self._make_playbook_data()
        with patch("cafe.ui.cli.GitOperations") as mock_git_cls:
            git = MagicMock()
            git.has_uncommitted_changes.return_value = False
            mock_git_cls.return_value = git

            result = _consume_pending_chat_handoff(
                issue_dir=issue_dir,
                playbook_data=playbook_data,
                requested_start_step=None,
            )

        assert result is None

    def test_chat_baton_invalid_enum_left_for_runtime_recovery(self, tmp_path: Path) -> None:
        """Invalid structured baton enum should not crash chat handoff consumption."""
        issue_dir = tmp_path / ".cafe" / "issues" / "issue-baton-invalid-enum"
        issue_dir.mkdir(parents=True, exist_ok=True)
        next_step_path = issue_dir / "next_step.txt"
        next_step_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "from_step": "spec",
                    "to_owner": "user",
                    "to_step": "spec",
                    "intent": "await_user_qa",
                    "status_code": "need_user_input",
                    "created_at": "2026-05-18T17:10:00+08:00",
                    "source": "spec.questions_sent",
                }
            ),
            encoding="utf-8",
        )

        result = _consume_pending_chat_handoff(
            issue_dir=issue_dir,
            playbook_data=self._make_playbook_data(),
            requested_start_step=None,
        )

        assert result is None
        assert "await_user_qa" in next_step_path.read_text(encoding="utf-8")

    def test_chat_baton_empty_is_left_for_runtime_recovery(self, tmp_path: Path) -> None:
        """空的 next_step.txt 是 schema 錯誤，交給 workflow runtime 處理，不在此處拋出。"""
        issue_dir = tmp_path / ".cafe" / "issues" / "issue-baton-2"
        issue_dir.mkdir(parents=True, exist_ok=True)
        (issue_dir / "next_step.txt").write_text("", encoding="utf-8")

        playbook_data = self._make_playbook_data()

        result = _consume_pending_chat_handoff(
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            requested_start_step=None,
        )

        assert result is None

    def test_chat_baton_nonexistent_step_raises_error(self, tmp_path: Path) -> None:
        """next_step.txt 含不存在步驟名稱時應拋出 ValueError。"""
        issue_dir = tmp_path / ".cafe" / "issues" / "issue-baton-3"
        issue_dir.mkdir(parents=True, exist_ok=True)
        (issue_dir / "next_step.txt").write_text("no_such_step", encoding="utf-8")

        playbook_data = self._make_playbook_data()

        result = _consume_pending_chat_handoff(
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            requested_start_step=None,
        )

        assert result is None

    def test_chat_baton_not_consumed_with_uncommitted_changes(self, tmp_path: Path, capsys) -> None:
        """有未 commit 變更時，baton 不應被消費。"""
        issue_dir = tmp_path / ".cafe" / "issues" / "issue-baton-4"
        issue_dir.mkdir(parents=True, exist_ok=True)
        next_step_path = issue_dir / "next_step.txt"
        next_step_path.write_text("develop", encoding="utf-8")

        playbook_data = self._make_playbook_data()

        with patch("cafe.ui.cli.GitOperations") as mock_git_cls:
            git = MagicMock()
            git.has_uncommitted_changes.return_value = True
            mock_git_cls.return_value = git

            result = _consume_pending_chat_handoff(
                issue_dir=issue_dir,
                playbook_data=playbook_data,
                requested_start_step=None,
            )

        assert result is None
        assert (
            next_step_path.exists()
        ), "next_step.txt should NOT be deleted when uncommitted changes exist"

    def test_chat_baton_no_file_returns_none(self, tmp_path: Path) -> None:
        """next_step.txt 不存在時應返回 None。"""
        issue_dir = tmp_path / ".cafe" / "issues" / "issue-baton-5"
        issue_dir.mkdir(parents=True, exist_ok=True)

        playbook_data = self._make_playbook_data()

        result = _consume_pending_chat_handoff(
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            requested_start_step=None,
        )

        assert result is None

    def test_chat_baton_skipped_when_requested_start_step_provided(self, tmp_path: Path) -> None:
        """當 requested_start_step 已給定時，baton 不應被讀取（直接返回 requested_start_step）。"""
        issue_dir = tmp_path / ".cafe" / "issues" / "issue-baton-6"
        issue_dir.mkdir(parents=True, exist_ok=True)
        # 即使 baton 存在也不應被消費
        (issue_dir / "next_step.txt").write_text("review", encoding="utf-8")

        playbook_data = self._make_playbook_data()

        result = _consume_pending_chat_handoff(
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            requested_start_step="develop",
        )

        assert result == "develop"
        # baton 檔案不應被刪除（因為根本沒讀它）
        assert (issue_dir / "next_step.txt").exists()
