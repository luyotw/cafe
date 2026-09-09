"""Runtime journeys supporting the Driver's graph-only completion contract."""

import subprocess
from pathlib import Path

import pytest

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime


@pytest.mark.parametrize("steps", [("brief", "draft"), ("diagnose", "repair", "verify")])
def test_non_pr_graph_reaches_done_without_synthetic_steps_or_external_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, steps: tuple[str, ...]
) -> None:
    issue_dir = tmp_path / ".cafe/issues/minimal"
    observed_steps = []
    external_calls = []

    def unexpected_call(*args, **kwargs):
        external_calls.append(args)
        raise AssertionError("no external command belongs to this workflow")

    monkeypatch.setattr(subprocess, "run", unexpected_call)
    graph = {
        "playbook": {"id": "minimal"},
        "entry_point": steps[0],
        "steps": {
            name: {
                "skill": "cafe-draft",
                "role": "writer",
                "on": {"await_agent": steps[i + 1] if i + 1 < len(steps) else "_done"},
            }
            for i, name in enumerate(steps)
        },
    }

    def execute(name, _step, _state):
        observed_steps.append(name)
        index = steps.index(name)
        last = index == len(steps) - 1
        store = BlackboardStore(issue_dir)
        state = store.load_or_create(steps[0], playbook_id="minimal")
        output = issue_dir / name / "output.md"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(f"Completed {name}\n", encoding="utf-8")
        store.update_handoff_contract(
            state,
            from_step=name,
            to_owner=HandoffOwner.DONE if last else HandoffOwner.AGENT,
            to_step="done" if last else steps[index + 1],
            intent=HandoffIntent.WORKFLOW_COMPLETE if last else HandoffIntent.AWAIT_AGENT,
            status_code="confirmed",
            source="test.phase",
        )
        return StepExecutionResult(response="", artifacts={f"{name}_result": str(output)})

    runtime = BlackboardWorkflowRuntime(issue_dir=issue_dir, playbook=graph, executor=execute)
    result = runtime.run(start_step=steps[0])
    assert result.completed is True
    assert observed_steps == list(steps)
    assert set(runtime.blackboard.artifacts) == {f"{step}_result" for step in steps}
    assert runtime.blackboard.current_step == "done"
    assert runtime.blackboard.capability_receipts == []
    assert external_calls == []
