"""U7/I4/I5/I8: one terminal independent review of the current snapshot."""

from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_execution_checkpoints import execution_context
from test_file_scope import repository


@pytest.fixture
def review(execution_context):
    from cafe.core.execution_checkpoints import checkpoint

    execution_context["review_configuration"] = {
        "cli": "codex",
        "model": "test",
        "provider_version": "v1",
        "read_only": True,
        "model_behavior": "inherits_parent",
        "checkpoint_interface": "parent_command",
    }
    receipt = checkpoint(execution_context, "before_review", round_id="round-1", parent_id="parent")
    return {
        "version": 1,
        "round_id": "round-1",
        "checkpoint": receipt,
        "invocations": [
            {
                "parent_id": "parent",
                "reviewer_id": "child",
                "configuration": execution_context["review_configuration"],
                "terminal": "turn.completed",
                "exit_status": 0,
                "findings": [],
                "targeted_tests": ["tests passed"],
                "result_reference": "native/tool-result-1",
            }
        ],
    }


def test_nonblocking_findings_allow_current_review(execution_context, review):
    from cafe.core.execution_checkpoints import require_current_review

    review["invocations"][0]["findings"] = [{"severity": "nonblocking", "detail": "Suggestion"}]
    require_current_review(execution_context, review)
    (Path(execution_context["root"]) / "allowed").write_text("fix")
    with pytest.raises(ValueError):
        require_current_review(execution_context, review)


@pytest.mark.parametrize(
    "defect", ["self", "two", "progress", "blocker", "configuration", "missing_tests"]
)
def test_review_cannot_substitute_ineligible_evidence(execution_context, review, defect):
    from cafe.core.execution_checkpoints import require_current_review

    invocation = review["invocations"][0]
    if defect == "self":
        invocation["reviewer_id"] = "parent"
    if defect == "two":
        review["invocations"].append(deepcopy(invocation))
    if defect == "progress":
        invocation["terminal"] = "progress"
    if defect == "blocker":
        invocation["findings"] = [{"severity": "blocking", "detail": "Bug"}]
    if defect == "configuration":
        invocation["configuration"] = {**invocation["configuration"], "model": "other"}
    if defect == "missing_tests":
        invocation["targeted_tests"] = []
    with pytest.raises(ValueError):
        require_current_review(execution_context, review)


def test_streamlined_declaration_is_separate_from_existing_dual_review():
    from cafe.playbooks.loader import PlaybookLoader

    loader = PlaybookLoader()
    model = loader.load_model("streamlined", strict=True).model
    assert model.contract.mode == "compact"
    assert list(model.steps) == ["develop", "deliver"]
    assert model.steps["develop"].execution.review_policy == "single_native"
    assert model.steps["develop"].max_attempts_per_cycle is not None
    dual = loader.load_model("direct-subagent-review", strict=True).model
    assert dual.contract.mode == "full"
    assert dual.steps["develop"].execution.review_policy is None


def test_public_agent_manager_projects_read_only_native_reviewer(monkeypatch, tmp_path):
    from cafe.agents.manager import AgentManager
    from cafe.agents.executor import AgentExecutor
    from cafe.agents.cli.claude import ClaudeCLI
    from cafe.core.types import AgentConfig, AgentCLI, AgentResponse, TokenUsage
    import json

    monkeypatch.chdir(tmp_path)
    configuration = {
        "cli": "claude",
        "model": "test",
        "provider_version": "fixture",
        "read_only": True,
        "model_behavior": "inherits_parent",
        "checkpoint_interface": "parent_command",
    }
    seen = []

    def transport(executor, *args, **kwargs):
        command = executor.preview_cli_command_args("fixture", allowed_tools=["Agent"])
        agent = json.loads(command[command.index("--agents") + 1])["cafe_reviewer"]
        assert set(agent["tools"]) == {"Read", "Glob", "Grep"}
        assert agent["model"] == "inherit"
        seen.append(command)
        return AgentResponse(response="complete", token_usage=TokenUsage(), cli=AgentCLI.CLAUDE)

    monkeypatch.setattr(AgentExecutor, "execute", transport)
    manager = AgentManager(issue_name="sample", stream_agent_output=False)
    manager.register_agent(AgentConfig(name="operator", cli=AgentCLI.CLAUDE, model="test"))
    manager.execute("operator", "fixture", native_review_configuration=configuration)
    assert len(seen) == 1
