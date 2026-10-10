"""Confirmed compact authority through native Codex execution and delivery readiness."""

import json

from cafe.agents.executor import AgentExecutionControl
from cafe.agents.manager import AgentManager
from cafe.core.execution_checkpoints import checkpoint, require_verified_review
from cafe.core.types import AgentCLI, AgentConfig
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from tests.integration.test_compact_workflow import native_context
from tests.unit.test_codex_native_review import rpc as _rpc
from tests.unit.test_compact_contract import compact_request as _request
from tests.unit.test_native_review_providers import PARENT

rpc = _rpc
compact_request = _request


def test_codex_chatgpt_native_flow_reaches_confirmed_delivery_readiness(
    rpc, compact_request, tmp_path, monkeypatch
):
    root, issue, playbook, context = native_context(
        compact_request, tmp_path, monkeypatch, cli_name="codex"
    )
    # Retain a generic graph: the provider adapter never knows these step names.
    develop = playbook["steps"]["build"]
    develop["on"]["await_agent"] = "publish_ready"
    playbook["steps"]["publish_ready"] = {
        "role": develop["role"],
        "skill": "cafe-deliver",
        "behavior": {"completion": "baton"},
        "execution": {
            "checkpoints": ["before_delivery"],
            "delivery_evidence_artifact": "delivery_readiness.json",
        },
        "on": {"await_agent": "_done", "workflow_complete": "_done"},
    }
    _, selected, _, commands, _, home = rpc
    # A saved ChatGPT login and writable config are present; no API key is needed.
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    manager = AgentManager(issue_name="sample", stream_agent_output=False)
    manager.register_agent(AgentConfig(name="parent", cli=AgentCLI.CODEX, model="test"))
    calls = []

    def provider(step, definition, board, **kwargs):
        calls.append(step)
        iteration = issue / step / "iteration_001"
        iteration.mkdir(parents=True)
        if step == "build":
            (root / "app.py").write_text("value = 1\n")
            receipt = checkpoint(context, "before_review", round_id="native", parent_id=PARENT)
            selected["receipt"] = receipt["receipt_id"]
            manager.execute(
                "parent",
                "implement and review",
                native_review_configuration=context["review_configuration"],
                execution_control=AgentExecutionControl(working_directory=root),
            )
            observations = manager.get_last_native_review_observations()
            conclusion = observations[0]
            evidence = {
                "version": 1,
                "round_id": "native",
                "checkpoint": receipt,
                "invocations": [
                    {
                        "parent_id": PARENT,
                        "reviewer_id": conclusion["reviewer_id"],
                        "configuration": context["review_configuration"],
                        "terminal": "result",
                        "exit_status": 0,
                        "result_reference": "native-child",
                        "findings": conclusion["findings"],
                        "targeted_tests": conclusion["targeted_tests"],
                    }
                ],
            }
            (iteration / "native-review.json").write_text(json.dumps(evidence))
            (iteration / "native_invocations.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "parent_id": manager.get_last_session_id(),
                        "observations": observations,
                    }
                )
            )
            (issue / "next_step.txt").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "to_owner": "agent",
                        "to_step": "publish_ready",
                        "intent": "await_agent",
                    }
                )
            )
        else:
            retained = json.loads((issue / "execution_review.json").read_text())
            require_verified_review(context, retained)
            ready = {
                "authority_digest": context["authority_digest"],
                "endpoint": context["delivery_endpoint"],
                "checkpoint": checkpoint(
                    context,
                    "before_delivery",
                    round_id="deliver",
                    parent_id=manager.get_last_session_id(),
                ),
            }
            (iteration / "delivery_readiness.json").write_text(json.dumps(ready))
            (issue / "next_step.txt").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "to_owner": "done",
                        "to_step": "done",
                        "intent": "workflow_complete",
                    }
                )
            )
        (iteration / "output.md").write_text("Current native review and delivery evidence\n")
        return StepExecutionResult(response="Ready", artifacts={})

    result = BlackboardWorkflowRuntime(
        issue_dir=issue, playbook=playbook, executor=provider, execution_context=context
    ).run()
    assert result.completed and calls == ["build", "publish_ready"]
    assert len(commands) == 1
    delivery = json.loads((issue / "execution_delivery.json").read_text())
    assert delivery["endpoint"] == context["delivery_endpoint"]
    retained = json.loads((issue / "execution_review.json").read_text())
    require_verified_review(context, retained)
    assert retained["native_observations"]["parent_id"] == PARENT
