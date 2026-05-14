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
from typing import List
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
from cafe.playbooks.loader import PlaybookLoader
from cafe.ui.cli import _consume_pending_chat_handoff, app


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
                artifacts={str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"},
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
            e.data.get("step")
            for e in blackboard.events
            if e.event_type == "step_completed"
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
                    artifacts={str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"},
                    status_code=final_status,
                )
            subsequent_calls[step_name] = subsequent_calls.get(step_name, 0) + 1
            events = []
            if step_name == "pr":
                _write_pr_done_baton(issue_dir)
                events.append({"type": "pr_synced", "url": "https://example.com/pr/1"})
            return StepExecutionResult(
                response="confirmed",
                artifacts={str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"},
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
                artifacts={str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"},
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
                artifacts={str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"},
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
            e for e in blackboard.events
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
                artifacts={str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"},
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

        issue_dir = tmp_path / ".cafe" / "issues" / "issue-cli-resume"
        issue_dir.mkdir(parents=True, exist_ok=True)

        call_log: List[str] = []
        spec_calls = 0

        class FakeExecutor:
            def execute_step(
                self, step_name: str, step_def: dict, blackboard_state: object
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
                    artifacts={str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"},
                    status_code="confirmed",
                )

        cli_runner = CliRunner()
        # _handle_user_phase 第一次返回 "spec"（繼續執行），第二次返回 None（結束）
        handle_user_side_effects = ["spec", None]
        with (
            patch("cafe.ui.cli.GitOperations") as mock_git_cls,
            patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
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
    def test_workflow_initializes_next_step_txt_when_missing(self, tmp_path: Path, monkeypatch) -> None:
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
                self, step_name: str, step_def: dict, blackboard_state: object
            ) -> tuple[str, dict[str, str]]:
                return (
                    "confirmed",
                    {str(step_def.get("output_artifact", step_name)): f"{step_name}/output.md"},
                )

        cli_runner = CliRunner()
        with (
            patch("cafe.ui.cli.GitOperations") as mock_git_cls,
            patch("cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()),
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
        """next_step.txt 存在且內容有效時，應被消費並更新 blackboard。"""
        issue_dir = tmp_path / ".cafe" / "issues" / "issue-baton-1"
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

        assert result == "develop"
        assert next_step_path.exists(), "next_step.txt should remain as persistent baton"

        blackboard = BlackboardStore(issue_dir).load_or_create("spec")
        assert blackboard.current_step == "develop"

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

    def test_chat_baton_empty_raises_error(self, tmp_path: Path) -> None:
        """空的 next_step.txt 應拋出 ValueError。"""
        issue_dir = tmp_path / ".cafe" / "issues" / "issue-baton-2"
        issue_dir.mkdir(parents=True, exist_ok=True)
        (issue_dir / "next_step.txt").write_text("", encoding="utf-8")

        playbook_data = self._make_playbook_data()

        with pytest.raises(ValueError, match="empty"):
            _consume_pending_chat_handoff(
                issue_dir=issue_dir,
                playbook_data=playbook_data,
                requested_start_step=None,
            )

    def test_chat_baton_nonexistent_step_raises_error(self, tmp_path: Path) -> None:
        """next_step.txt 含不存在步驟名稱時應拋出 ValueError。"""
        issue_dir = tmp_path / ".cafe" / "issues" / "issue-baton-3"
        issue_dir.mkdir(parents=True, exist_ok=True)
        (issue_dir / "next_step.txt").write_text("no_such_step", encoding="utf-8")

        playbook_data = self._make_playbook_data()

        with pytest.raises(ValueError, match="no_such_step"):
            _consume_pending_chat_handoff(
                issue_dir=issue_dir,
                playbook_data=playbook_data,
                requested_start_step=None,
            )

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
        assert next_step_path.exists(), "next_step.txt should NOT be deleted when uncommitted changes exist"

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
