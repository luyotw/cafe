"""Tests for playbook runner."""

from pathlib import Path

import pytest

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.playbook_runner import PlaybookRunner, StepExecutionResult
from cafe.phases.generic_phase import GenericPhase
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader


def _build_loader(tmp_path: Path) -> GenericPhase:
    skill_dir = tmp_path / "builtin" / "skills" / "spec_first"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: spec_first\ndescription: d\n---\n\ntext\n",
        encoding="utf-8",
    )
    loader = SkillLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )
    loader.discover()
    return GenericPhase(loader)


def _write_agent_baton(
    issue_dir: Path,
    state: object,
    *,
    from_step: str,
    to_step: str,
    status_code: str = "",
    intent: HandoffIntent = HandoffIntent.AWAIT_AGENT,
) -> None:
    store = BlackboardStore(issue_dir)
    owner = HandoffOwner.AGENT if to_step not in {"user", "done"} else HandoffOwner(to_step)
    store.update_handoff_contract(
        state,
        from_step=from_step,
        to_owner=owner,
        to_step=to_step,
        intent=intent,
        status_code=status_code,
        source="test.executor",
    )


def _write_user_pause_baton(
    issue_dir: Path,
    state: object,
    *,
    from_step: str,
    status_code: str,
    intent: HandoffIntent,
) -> None:
    store = BlackboardStore(issue_dir)
    store.update_handoff_contract(
        state,
        from_step=from_step,
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=intent,
        status_code=status_code,
        source="test.executor",
    )


def test_runner_can_advance_and_loop_back_via_baton(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "develop": {
                "skill": "spec_first",
                "role": "developer",
                "on": {"CAFE_CONFIRMED": "review"},
            },
            "review": {
                "skill": "spec_first",
                "role": "reviewer",
                "on": {"CAFE_NEEDS_CHANGES": "develop", "CAFE_CONFIRMED": "_done"},
            },
        },
    }
    calls: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        calls.append(step_name)
        if calls == ["develop"]:
            _write_agent_baton(issue_dir, state, from_step="develop", to_step="review", status_code="CAFE_CONFIRMED")
            return StepExecutionResult(response="done", artifacts={"code": "d1"})
        if calls == ["develop", "review"]:
            _write_agent_baton(
                issue_dir,
                state,
                from_step="review",
                to_step="develop",
                status_code="CAFE_NEEDS_CHANGES",
            )
            return StepExecutionResult(response="needs changes", artifacts={"review": "r1"})
        if calls == ["develop", "review", "develop"]:
            _write_agent_baton(issue_dir, state, from_step="develop", to_step="review", status_code="CAFE_CONFIRMED")
            return StepExecutionResult(response="done", artifacts={"code": "d2"})
        _write_agent_baton(issue_dir, state, from_step="review", to_step="done", status_code="CAFE_CONFIRMED")
        return StepExecutionResult(response="confirmed", artifacts={"review": "r2"})

    runner = PlaybookRunner(
        issue_dir=issue_dir,
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )
    result = runner.run(max_transitions=10)

    assert result.completed is True
    assert result.final_step == "review"
    assert result.final_status_code == "CAFE_CONFIRMED"


def test_runner_pauses_when_baton_targets_user_for_clarification(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {"spec": {"skill": "spec_first", "role": "pm", "on": {"CAFE_CONFIRMED": "plan"}}},
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        _write_user_pause_baton(
            issue_dir,
            state,
            from_step="spec",
            status_code="CAFE_NEED_CLARIFICATION",
            intent=HandoffIntent.NEED_CLARIFICATION,
        )
        return StepExecutionResult(response="need user", artifacts={})

    runner = PlaybookRunner(
        issue_dir=issue_dir,
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )
    result = runner.run(max_transitions=3)

    assert result.completed is False
    assert result.final_step == "spec"
    assert result.final_status_code == "CAFE_NEED_CLARIFICATION"
    blackboard = BlackboardStore(issue_dir).load_or_create("spec")
    assert blackboard.current_step == "user"
    assert blackboard.handoff_contract is not None
    assert blackboard.handoff_contract.intent == HandoffIntent.NEED_CLARIFICATION


def test_runner_routes_developer_dispute_to_review_via_baton(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "develop": {"skill": "spec_first", "role": "developer", "on": {"CAFE_CONFIRMED": "review"}},
            "review": {"skill": "spec_first", "role": "reviewer", "on": {"CAFE_CONFIRMED": "_done"}},
        },
    }
    calls: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == "develop":
            _write_agent_baton(
                issue_dir,
                state,
                from_step="develop",
                to_step="review",
                status_code="CAFE_NO_CHANGES_NEEDED",
            )
            return StepExecutionResult(response="dispute raised", artifacts={})
        _write_agent_baton(issue_dir, state, from_step="review", to_step="done", status_code="CAFE_CONFIRMED")
        return StepExecutionResult(response="resolved", artifacts={})

    runner = PlaybookRunner(
        issue_dir=issue_dir,
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )
    result = runner.run(max_transitions=4)

    assert result.completed is True
    assert calls == ["develop", "review"]


def test_runner_falls_back_to_explicit_status_when_baton_is_unchanged(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {"skill": "spec_first", "role": "pm", "on": {"CAFE_CONFIRMED": "plan"}},
            "plan": {"skill": "spec_first", "role": "developer", "on": {"CAFE_CONFIRMED": "_done"}},
        },
    }
    calls: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        calls.append(step_name)
        return StepExecutionResult(response="legacy path", artifacts={}, status_code="CAFE_CONFIRMED")

    runner = PlaybookRunner(
        issue_dir=issue_dir,
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )
    result = runner.run(max_transitions=5)

    assert result.completed is True
    assert result.final_step == "plan"
    assert calls == ["spec", "plan"]


def test_runner_fails_when_neither_baton_nor_explicit_status_advances(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {"develop": {"skill": "spec_first", "role": "developer", "on": {"CAFE_CONFIRMED": "_done"}}},
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        return StepExecutionResult(response="no transition data", artifacts={}, status_code=None)

    runner = PlaybookRunner(
        issue_dir=issue_dir,
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )
    result = runner.run(max_transitions=2)

    assert result.completed is False
    assert result.final_status_code == "NO_BATON_TRANSITION"


def test_runner_rejects_baton_owner_mismatch_before_step_execution(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-owner-mismatch"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "next_step.txt").write_text(
        '{"version":1,"from_step":"spec","to_owner":"user","to_step":"user","intent":"manual_handoff","status_code":"","source":"test"}',
        encoding="utf-8",
    )
    playbook = {
        "playbook": {"id": "default"},
        "steps": {"spec": {"skill": "spec_first", "role": "pm", "on": {"CAFE_CONFIRMED": "_done"}}},
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        raise AssertionError("executor should not run")

    runner = PlaybookRunner(
        issue_dir=issue_dir,
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )
    with pytest.raises(RuntimeError, match=r"expected agent, got user"):
        runner.run(max_transitions=2)


def test_runner_rejects_baton_target_mismatch_before_step_execution(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-target-mismatch"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "next_step.txt").write_text(
        '{"version":1,"from_step":"spec","to_owner":"agent","to_step":"plan","intent":"await_agent","status_code":"","source":"test"}',
        encoding="utf-8",
    )
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {"skill": "spec_first", "role": "pm", "on": {"CAFE_CONFIRMED": "plan"}},
            "plan": {"skill": "spec_first", "role": "developer", "on": {"CAFE_CONFIRMED": "_done"}},
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        raise AssertionError("executor should not run")

    runner = PlaybookRunner(
        issue_dir=issue_dir,
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )
    with pytest.raises(RuntimeError, match=r"baton points to 'plan'"):
        runner.run(max_transitions=2)


def test_runner_blocks_pr_done_without_publish_receipt(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-pr"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "pr": {"skill": "spec_first", "role": "developer", "on": {"CAFE_CONFIRMED": "_done"}},
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        _write_agent_baton(issue_dir, state, from_step="pr", to_step="done", status_code="CAFE_CONFIRMED")
        return StepExecutionResult(response="local only", artifacts={"pr_result": "p1"})

    runner = PlaybookRunner(
        issue_dir=issue_dir,
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )
    result = runner.run(max_transitions=2)

    assert result.completed is False
    assert result.final_step == "pr"
    assert result.final_status_code == "MISSING_CAPABILITY_RECEIPT"
    blackboard = BlackboardStore(issue_dir).load_or_create("pr")
    assert blackboard.current_step == "pr"
    assert blackboard.handoff_contract is not None
    assert blackboard.handoff_contract.to_owner == HandoffOwner.AGENT
    assert blackboard.handoff_contract.to_step == "pr"
    assert any(
        event.event_type == "workflow_blocked" and event.data.get("required_event") == "pr_synced"
        for event in blackboard.events
    )


def test_runner_allows_local_pr_done_without_publish_receipt(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-local-pr"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "issue.yaml").write_text("pr:\n  auto_create: false\n", encoding="utf-8")
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "pr": {"skill": "spec_first", "role": "developer", "on": {"CAFE_CONFIRMED": "_done"}},
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        _write_agent_baton(issue_dir, state, from_step="pr", to_step="done", status_code="CAFE_CONFIRMED")
        return StepExecutionResult(response="local review done", artifacts={"pr_result": "p1"})

    runner = PlaybookRunner(
        issue_dir=issue_dir,
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )
    result = runner.run(max_transitions=2)

    assert result.completed is True
    assert result.final_step == "pr"
    assert result.final_status_code == "CAFE_CONFIRMED"


def test_runner_allows_remote_pr_done_with_publish_receipt(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-remote-pr"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "pr": {"skill": "spec_first", "role": "developer", "on": {"CAFE_CONFIRMED": "_done"}},
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        _write_agent_baton(issue_dir, state, from_step="pr", to_step="done", status_code="CAFE_CONFIRMED")
        return StepExecutionResult(
            response="published",
            artifacts={"pr_result": "p1"},
            events=[{"type": "pr_synced", "url": "https://github.com/example/repo/pull/1"}],
        )

    runner = PlaybookRunner(
        issue_dir=issue_dir,
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )
    result = runner.run(max_transitions=2)

    assert result.completed is True
    assert result.final_step == "pr"
    assert result.final_status_code == "CAFE_CONFIRMED"
def test_runner_rejects_reserved_assignee_type_at_runtime(tmp_path: Path) -> None:
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "develop": {
                "skill": "spec_first",
                "role": "developer",
                "assignee_type": "human",
                "valid_status_codes": ["CAFE_CONFIRMED"],
                "on": {"CAFE_CONFIRMED": "_done"},
            }
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> tuple[str, dict[str, str]]:
        return ("CAFE_CONFIRMED", {})

    runner = PlaybookRunner(
        issue_dir=tmp_path / ".cafe" / "issues" / "demo",
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )
    with pytest.raises(RuntimeError, match="assignee_type=human"):
        runner.run()


def test_runner_stops_when_step_exceeds_max_iterations(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "review": {
                "skill": "spec_first",
                "role": "reviewer",
                "max_iterations": 2,
                "valid_status_codes": ["CAFE_NEEDS_CHANGES"],
                "on": {"CAFE_NEEDS_CHANGES": "review"},
            }
        },
    }

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        return StepExecutionResult(
            response="repeat review",
            artifacts={},
            status_code="CAFE_NEEDS_CHANGES",
        )

    runner = PlaybookRunner(
        issue_dir=issue_dir,
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )

    with pytest.raises(RuntimeError, match="exceeded max_iterations=2"):
        runner.run(max_transitions=5)

    blackboard = BlackboardStore(issue_dir).load_or_create("review")
    loop_events = [event for event in blackboard.events if event.event_type == "loop_detected"]
    assert loop_events
    assert loop_events[-1].data["max_iterations"] == 2


def test_default_review_loop_budget_is_three_rounds() -> None:
    from cafe.playbooks.loader import PlaybookLoader

    playbook = PlaybookLoader().load_model("default").model

    assert playbook.steps["review"].max_iterations == 3


def test_runner_records_hop_limit_event(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-hop"
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "develop": {"skill": "spec_first", "role": "developer", "on": {"CAFE_CONFIRMED": "review"}},
            "review": {"skill": "spec_first", "role": "reviewer", "on": {"CAFE_NEEDS_CHANGES": "develop"}},
        },
    }
    calls: list[str] = []

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        calls.append(step_name)
        if step_name == "develop":
            _write_agent_baton(issue_dir, state, from_step="develop", to_step="review", status_code="CAFE_CONFIRMED")
            return StepExecutionResult(response="go review", artifacts={})
        _write_agent_baton(
            issue_dir,
            state,
            from_step="review",
            to_step="develop",
            status_code="CAFE_NEEDS_CHANGES",
        )
        return StepExecutionResult(response="back to develop", artifacts={})

    runner = PlaybookRunner(
        issue_dir=issue_dir,
        playbook=playbook,
        generic_phase=_build_loader(tmp_path),
        executor=executor,
    )
    with pytest.raises(RuntimeError, match=r"max transition limit \(2\)"):
        runner.run(max_transitions=2)

    blackboard = BlackboardStore(issue_dir).load_or_create("develop")
    hop_events = [event for event in blackboard.events if event.event_type == "hop_limit_reached"]
    assert hop_events
    assert hop_events[-1].data["max_transitions"] == 2
