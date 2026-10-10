"""I1/I4: real accounting writers, callback transport and progress projection."""

import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from cafe.agents.executor import AgentExecutor
from cafe.agents.transport_types import TransportResult
from cafe.core.types import TokenUsage
from cafe.core.usage import chat_usage_sink, iteration_usage_sink
from cafe.manager.costs import inclusive_report, preserve_worker_cost
from tests.fixtures.manager_chat import adapter, repository
from tests.unit import test_conversation_transport as transport_tests
from tests.unit.test_event_driver_callback import _contract_event_context, _executor_result
from tests.unit.test_workflow_cost_summary import record
from tests.unit.test_workflow_progress_renderer import _custom_playbook

provider_process = transport_tests.provider_process

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


@pytest.mark.parametrize("outcome", ["available", "unavailable", "failed"])
def test_manager_chat_persists_usage_or_gap_and_continues_same_session(
    tmp_path, monkeypatch, provider_process, outcome
):
    from decimal import Decimal

    from cafe.manager.costs import CostStore
    from tests.fixtures.manager_chat import issue, mutate, snapshot
    from tests.integration.test_chat_cost_accounting import append_codex_totals
    from tests.unit.test_conversation_transport import codex_reply
    from tests.unit.test_manager_chat import terminal

    root = repository(tmp_path / "chat-repo")
    directory = issue(root)
    session = "01a10a7d-c447-7032-a913-d78a3919d35e"
    callback = adapter("workflow_event_callback")
    mutate(
        directory / "manager" / callback.DISPATCH_STATE_FILENAME,
        lambda state: state["entries"][0]["session"].update(id=session),
    )
    workflow_id = json.loads((directory / "blackboard.json").read_text())["workflow_id"]
    native = tmp_path / "native"
    monkeypatch.setenv("CODEX_HOME", str(native))
    journal = native / "sessions" / f"rollout-test-{session}.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text(json.dumps(dict(type="session_meta", payload=dict(id=session))) + "\n")
    totals = dict(input_tokens=0, output_tokens=0, cached_input_tokens=0)
    append_codex_totals(journal, totals)
    before = snapshot(root)
    module = adapter("manager_chat")

    def turn(outcome):
        terminal(monkeypatch, ["account this turn", "/quit"])
        records = codex_reply(session, model="gpt-5.3-codex", complete=outcome != "failed")
        if outcome == "available":
            for key, delta in dict(
                input_tokens=100, output_tokens=20, cached_input_tokens=30
            ).items():
                totals[key] += delta
            records[-1]["usage"] = totals.copy()
        elif outcome == "unavailable":
            records[-1]["usage"] = {}
        launch = provider_process(records, returncode=1 if outcome == "failed" else 0)
        process = launch.return_value

        def run(command, **kwargs):
            if outcome == "available":
                append_codex_totals(journal, totals)
            return process

        launch.side_effect = run
        return launch, module.run_chat(root, "topic")

    launch, status = turn(outcome)
    assert status == (1 if outcome == "failed" else 0)
    stored = CostStore(root, "topic", workflow_id).read()
    if outcome == "available":
        assert inclusive_report(root, "topic", workflow_id)["manager"]["known"] == Decimal(
            "0.00040775"
        )
    else:
        assert any(
            s["gap"] or any(r["amount_usd"] is None or not r["complete"] for r in s["records"])
            for s in stored["manager_sources"]
        ) or any(key.startswith("chat-") for key in stored["manager_gaps"])
    # A later explicit turn reuses the same authority and retained destination.
    _, status = turn("available")
    assert status == 0
    assert launch.call_count == 2
    for call in launch.call_args_list:
        command = call.args[0]
        assert command[command.index("resume") + 1] == session
    assert inclusive_report(root, "topic", workflow_id)["manager"]["known"] == (
        Decimal("0.00081550") if outcome == "available" else Decimal("0.00040775")
    )
    assert snapshot(root) == before
