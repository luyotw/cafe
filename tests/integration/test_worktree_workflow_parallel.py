"""Integration tests for CAFE workflows inside git worktrees and parallel issue isolation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.git import GitOperations
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.playbooks.loader import PlaybookLoader
from tests.conftest import create_minimal_config


def _init_repo_with_cafe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GitOperations:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.chdir(repo)
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(var, raising=False)
    git = GitOperations(str(repo))
    git.run_git("init", "-b", "main")
    git.run_git("config", "user.email", "test@example.com")
    git.run_git("config", "user.name", "Test User")
    create_minimal_config(repo)
    (repo / "README.md").write_text("root\n", encoding="utf-8")
    git.run_git("add", ".")
    git.commit("Initial commit")
    return git


def _run_until_settled(
    *,
    issue_dir: Path,
    playbook: dict,
    executor,
    start_step: str = "spec",
    max_transitions: int = 20,
):
    last_result = None
    pending_start: str | None = start_step
    for _ in range(8):
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
        pending_start = latest.current_step
    return last_result


def _write_agent_baton(
    issue_dir: Path,
    *,
    from_step: str,
    to_owner: str,
    to_step: str,
    intent: str,
) -> None:
    payload = {
        "version": 1,
        "to_owner": to_owner,
        "to_step": to_step,
        "intent": intent,
    }
    (issue_dir / "next_step.txt").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def test_worktree_workflow_spec_pause_resume_reaches_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Workflow in a worktree pauses at user, resumes on spec, and reaches plan."""
    git = _init_repo_with_cafe(tmp_path, monkeypatch)
    main_repo = Path.cwd()
    worktree_path = tmp_path / "repo" / "worktrees" / "feature-wt"
    git.create_worktree(str(worktree_path), "feature-wt", "main")

    playbook = PlaybookLoader().load("standard")
    issue_name = "issue-wt-flow"
    spec_calls = 0

    def executor(step_name: str, step_def: dict, state) -> StepExecutionResult:
        nonlocal spec_calls
        artifact_key = str(step_def.get("output_artifact", step_name))
        rel_path = f"{step_name}/iteration_001/output.md"
        artifact_path = issue_dir / rel_path
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        if step_name == "spec":
            spec_calls += 1
            if spec_calls == 1:
                artifact_path.write_text("# Spec draft\n", encoding="utf-8")
                _write_agent_baton(
                    issue_dir,
                    from_step="spec",
                    to_owner="user",
                    to_step="user",
                    intent="confirm_output",
                )
                return StepExecutionResult(
                    response="confirm_output",
                    artifacts={artifact_key: str(artifact_path)},
                    status_code="confirm_output",
                    auto_continue=False,
                )
            artifact_path.write_text("# Spec confirmed\n", encoding="utf-8")
            _write_agent_baton(
                issue_dir,
                from_step="spec",
                to_owner="agent",
                to_step="plan",
                intent="await_agent",
            )
        elif step_name == "plan":
            artifact_path.write_text("# Plan\n", encoding="utf-8")
        else:
            artifact_path.write_text(f"# {step_name}\n", encoding="utf-8")
        return StepExecutionResult(
            response="confirmed",
            artifacts={artifact_key: str(artifact_path)},
            status_code="confirmed",
        )

    monkeypatch.chdir(worktree_path)
    issue_dir = worktree_path / ".cafe" / "issues" / issue_name
    issue_dir.mkdir(parents=True)

    runner = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    paused = runner.run(start_step="spec", max_transitions=10)

    assert paused.completed is False
    blackboard = BlackboardStore(issue_dir).load_or_create("spec")
    assert blackboard.current_step == "user"
    assert spec_calls == 1
    pause_events = [e for e in blackboard.events if e.event_type == "workflow_paused"]
    assert pause_events

    with pytest.raises(RuntimeError, match="max transition limit"):
        BlackboardWorkflowRuntime(
            issue_dir=issue_dir,
            playbook=playbook,
            executor=executor,
        ).run(start_step="spec", max_transitions=2)

    assert spec_calls == 2
    blackboard = BlackboardStore(issue_dir).load_or_create("spec")
    plan_started = [
        e
        for e in blackboard.events
        if e.event_type == "step_started" and e.data.get("step") == "plan"
    ]
    plan_completed = [
        e
        for e in blackboard.events
        if e.event_type == "step_completed" and e.data.get("step") == "plan"
    ]
    assert plan_started
    assert plan_completed
    plan_artifact = issue_dir / "plan" / "iteration_001" / "output.md"
    assert plan_artifact.exists()
    assert plan_artifact.read_text(encoding="utf-8") == "# Plan\n"
    assert str(issue_dir).startswith(str(worktree_path.resolve()))
    assert not (main_repo / ".cafe" / "issues" / issue_name).exists()


def test_parallel_worktrees_keep_issue_state_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two worktrees running different issues do not share blackboard or baton files."""
    git = _init_repo_with_cafe(tmp_path, monkeypatch)
    wt_a = tmp_path / "repo" / "worktrees" / "wt-a"
    wt_b = tmp_path / "repo" / "worktrees" / "wt-b"
    git.create_worktree(str(wt_a), "wt-a", "main")
    git.create_worktree(str(wt_b), "wt-b", "main")

    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "spec": {
                "skill": "spec_first",
                "role": "pm",
                "output_artifact": "spec",
                "valid_intents": ["await_agent", "confirm_output"],
                "on": {"await_agent": "plan", "confirm_output": "spec"},
            },
        },
    }

    def make_executor(issue_dir: Path, marker: str):
        def executor(step_name: str, step_def: dict, state) -> StepExecutionResult:
            _write_agent_baton(
                issue_dir,
                from_step="spec",
                to_owner="user",
                to_step="user",
                intent="confirm_output",
            )
            artifact = issue_dir / "spec" / "iteration_001" / "output.md"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(f"# {marker}\n", encoding="utf-8")
            return StepExecutionResult(
                response="confirmed",
                artifacts={"spec": str(artifact)},
                status_code="confirmed",
            )

        return executor

    issue_a = wt_a / ".cafe" / "issues" / "issue-a"
    issue_b = wt_b / ".cafe" / "issues" / "issue-b"
    issue_a.mkdir(parents=True)
    issue_b.mkdir(parents=True)

    runtime_a = BlackboardWorkflowRuntime(
        issue_dir=issue_a,
        playbook=playbook,
        executor=make_executor(issue_a, "alpha"),
    )
    runtime_b = BlackboardWorkflowRuntime(
        issue_dir=issue_b,
        playbook=playbook,
        executor=make_executor(issue_b, "beta"),
    )

    runtime_a.run(start_step="spec", max_transitions=3)
    runtime_b.run(start_step="spec", max_transitions=3)

    baton_a = json.loads((issue_a / "next_step.txt").read_text(encoding="utf-8"))
    baton_b = json.loads((issue_b / "next_step.txt").read_text(encoding="utf-8"))
    assert baton_a["to_step"] == "user"
    assert baton_b["to_step"] == "user"
    assert (issue_a / "spec" / "iteration_001" / "output.md").read_text(encoding="utf-8") == "# alpha\n"
    assert (issue_b / "spec" / "iteration_001" / "output.md").read_text(encoding="utf-8") == "# beta\n"
    assert (issue_a / "blackboard.json").resolve() != (issue_b / "blackboard.json").resolve()
    bb_a = json.loads((issue_a / "blackboard.json").read_text(encoding="utf-8"))
    bb_b = json.loads((issue_b / "blackboard.json").read_text(encoding="utf-8"))
    assert bb_a != bb_b
