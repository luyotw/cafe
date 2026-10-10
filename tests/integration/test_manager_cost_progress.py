"""I1/I4: real accounting writers, callback transport and progress projection."""

import json
from dataclasses import replace
from datetime import datetime, timezone

from cafe.agents.executor import AgentExecutor
from cafe.agents.transport_types import TransportResult
from cafe.core.types import TokenUsage
from cafe.core.usage import chat_usage_sink, iteration_usage_sink
from cafe.manager.costs import inclusive_report, preserve_worker_cost
from tests.fixtures.manager_chat import adapter, repository
from tests.unit.test_event_driver_callback import _contract_event_context, _executor_result
from tests.unit.test_workflow_cost_summary import record
from tests.unit.test_workflow_progress_renderer import _custom_playbook


def test_worker_retry_chat_and_manager_callback_update_progress_once(tmp_path, monkeypatch):
    root = repository(tmp_path / "repo")
    callback = adapter("workflow_event_callback")
    manager_dir, state, event = _contract_event_context(callback, root, [("codex", "test")])
    issue = manager_dir.parent
    phase = issue / "custom/iteration_001"
    phase.mkdir(parents=True)
    metadata = phase / "iteration.json"
    metadata.write_text(
        json.dumps({"iteration": 1, "timestamp": datetime.now(timezone.utc).isoformat()})
    )
    sink = iteration_usage_sink(root, metadata)
    for identity in ("primary", "retry", "fallback"):
        sink(TokenUsage(cost_records=[record(identity)], total_cost_usd=1))
    chat_sink = chat_usage_sink(
        root, metadata, cli="codex", requested_model="test", mode="one_shot", phase="custom"
    )
    chat_sink(
        [
            TransportResult(
                usage=TokenUsage(cost_records=[record("chat")], total_cost_usd=1),
                reported_model="test",
                completed=True,
                accepted=True,
            )
        ]
    )
    calls = []

    def provider(self, prompt, **kwargs):
        identity = f"manager-{len(calls)}"
        calls.append(identity)
        result = _executor_result(session_id="session", accepted=self.config.session_id is not None)
        return replace(
            result,
            transport_result=replace(
                result.transport_result,
                usage=TokenUsage(cost_records=[record(identity, "2")], total_cost_usd=2),
            ),
        )

    monkeypatch.setattr(AgentExecutor, "execute_event_driver", provider)
    callback.run_callback(event, repository_root=root)
    assert len(calls) == 2
    renderer = adapter("render_workflow_progress")
    first = renderer.render_progress(
        playbook=_custom_playbook(),
        issue_dir=issue,
        manager_state={"deliver": "pending", "cleanup": "pending"},
    )
    assert "$4.0000 reported" in first.splitlines()[-1]
    sink(TokenUsage(cost_records=[record("next")], total_cost_usd=1))
    second = renderer.render_progress(
        playbook=_custom_playbook(),
        issue_dir=issue,
        manager_state={"deliver": "pending", "cleanup": "pending"},
    )
    assert "$5.0000 reported" in second.splitlines()[-1]
    preserve_worker_cost(root, issue, issue.name, event["workflow_id"])
    report = inclusive_report(root, issue.name, event["workflow_id"])
    assert report["manager"]["known"] == 4
    assert report["worker"]["known"] == 5
    assert report["combined"]["known"] == 9
