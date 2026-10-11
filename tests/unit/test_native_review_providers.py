"""Real provider schemas, parent transport and independent terminal review gates."""

import io
import json
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_execution_checkpoints import execution_context as _execution_context
from test_file_scope import repository as _repository

from cafe.agents.cli.native_review import invocation, reviewer_type
from cafe.agents.executor import AgentExecutor, validate_native_review_projection
from cafe.agents.manager import AgentManager
from cafe.core.execution_checkpoints import (
    checkpoint,
    require_current_review,
    require_verified_review,
)
from cafe.core.types import AgentCLI, AgentConfig

execution_context = _execution_context
repository = _repository

PROVIDERS = ["codex", "gemini", "copilot", "cursor-agent"]
PARENT = "b348e882-c4e8-4ab8-93b4-088a67b1fe4b"
CHILD = "70b7a3e6-1f9a-45dc-b656-6cd12bd64cf6"


def configuration(cli, behavior="inherits_parent", model="test"):
    return {
        "cli": cli,
        "model": model,
        "provider_version": "fixture",
        "read_only": True,
        "model_behavior": behavior,
        "checkpoint_interface": "parent_command",
    }


def provider_records(cli, conf, receipt, *, journal_home=None):
    """Captured-schema fixtures: conclusions come from the child, never parent text."""
    role = reviewer_type(conf)
    prompt = "Review. CAFE_REVIEW_CHECKPOINT:" + receipt["receipt_id"]
    conclusion = {"findings": [], "targeted_tests": ["current targeted checks passed"]}
    result = json.dumps(conclusion)
    if cli == "gemini":
        records = [
            {"type": "init", "session_id": PARENT},
            {
                "type": "tool_use",
                "tool_name": "invoke_agent",
                "tool_id": CHILD,
                "parameters": {"agent_name": role, "prompt": prompt},
            },
            {
                "type": "tool_result",
                "tool_id": CHILD,
                "status": "success",
                "output": json.dumps(
                    {
                        "isSubagentProgress": True,
                        "agentName": role,
                        "state": "completed",
                        "terminateReason": "GOAL",
                        "result": result,
                    }
                ),
            },
        ]
    elif cli == "copilot":
        args = {"agent_type": role, "prompt": prompt}
        if conf["model_behavior"] == "independent_override":
            args["model"] = conf["model"]
        records = [
            {
                "type": "tool.execution_start",
                "data": {"toolCallId": CHILD, "toolName": "task", "arguments": args},
            },
            {
                "type": "subagent.started",
                "data": {
                    "toolCallId": CHILD,
                    "agentName": role,
                    "model": conf["model"],
                    "executionMode": "sync",
                },
            },
            {
                "type": "subagent.completed",
                "data": {
                    "toolCallId": CHILD,
                    "agentName": role,
                    "model": conf["model"],
                    "firstDispatchedModel": conf["model"],
                    "cancelled": False,
                },
            },
            {
                "type": "tool.execution_complete",
                "data": {"toolCallId": CHILD, "success": True, "result": {"content": result}},
            },
        ]
    elif cli == "cursor-agent":
        # Actual JSON.stringify(ToolCall) uses protoJSON, not internal case/value oneofs.
        args = {"subagentType": {"custom": {"name": role}}, "prompt": prompt}
        records = [
            {"type": "system", "subtype": "init", "session_id": PARENT},
            {
                "type": "tool_call",
                "subtype": "started",
                "call_id": CHILD,
                "tool_call": {"taskToolCall": {"args": args}},
            },
            {
                "type": "tool_call",
                "subtype": "completed",
                "call_id": CHILD,
                "tool_call": {
                    "taskToolCall": {
                        "args": args,
                        "result": {
                            "success": {
                                "agentId": "independent-child",
                                "isBackground": False,
                                "conversationSteps": [{"assistantMessage": {"text": result}}],
                            }
                        },
                    }
                },
            },
        ]
    else:
        records = [
            {"type": "thread.started", "thread_id": PARENT},
            {
                "type": "item.completed",
                "item": {
                    "type": "collab_tool_call",
                    "tool": "spawn_agent",
                    "status": "completed",
                    "sender_thread_id": PARENT,
                    "receiver_thread_ids": [CHILD],
                    "prompt": prompt,
                    "agents_states": {CHILD: {"status": "running", "message": None}},
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "type": "collab_tool_call",
                    "tool": "wait",
                    "status": "completed",
                    "sender_thread_id": PARENT,
                    "receiver_thread_ids": [CHILD],
                    "agents_states": {CHILD: {"status": "completed", "message": result}},
                },
            },
        ]
        if journal_home is not None:
            journal_home.mkdir(parents=True, exist_ok=True)
            if not (journal_home / "config.toml").exists():
                (journal_home / "config.toml").write_text('sandbox_mode = "read-only"\n')
            directory = journal_home / "sessions/2026/10/10"
            directory.mkdir(parents=True, exist_ok=True)
            journal = directory / ("rollout-fixture-" + PARENT + ".jsonl")
            journal.write_text(
                "\n".join(
                    json.dumps(r)
                    for r in [
                        {"type": "session_meta", "payload": {"id": PARENT}},
                        {
                            "type": "response_item",
                            "payload": {
                                "type": "function_call",
                                "name": "spawn_agent",
                                "call_id": "spawn-call",
                                "arguments": json.dumps({"agent_type": role, "message": prompt}),
                            },
                        },
                        {
                            "type": "response_item",
                            "payload": {
                                "type": "function_call_output",
                                "call_id": "spawn-call",
                                "output": json.dumps({"agent_id": CHILD}),
                            },
                        },
                    ]
                )
                + "\n"
            )
            child = directory / ("rollout-fixture-" + CHILD + ".jsonl")
            child.write_text(
                "\n".join(
                    json.dumps(r)
                    for r in [
                        {"type": "session_meta", "payload": {"id": CHILD}},
                        {
                            "type": "turn_context",
                            "payload": {
                                "model": conf["model"],
                                "sandbox_policy": {"type": "read-only"},
                            },
                        },
                    ]
                )
                + "\n"
            )
    records += (
        [
            {"type": "assistant.message", "data": {"content": "Parent completed"}},
            {"type": "result", "status": "success", "sessionId": PARENT},
        ]
        if cli == "copilot"
        else (
            [{"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}]
            if cli == "codex"
            else [{"type": "result", "status": "success"}]
        )
    )
    return records, conclusion


def set_child_conclusion(cli, records, payload):
    if cli == "gemini":
        progress = json.loads(records[2]["output"])
        progress["result"] = payload
        records[2]["output"] = json.dumps(progress)
    elif cli == "copilot":
        records[3]["data"]["result"]["content"] = payload
    elif cli == "cursor-agent":
        records[2]["tool_call"]["taskToolCall"]["result"]["success"]["conversationSteps"][-1][
            "assistantMessage"
        ]["text"] = payload
    else:
        records[2]["item"]["agents_states"][CHILD]["message"] = payload


def observe(cli, conf, records, when):
    adapter = AgentExecutor(
        AgentConfig(
            name="parent", cli=AgentCLI(cli), model="test", native_review_configuration=conf
        )
    )._get_cli_strategy()
    lines = [json.dumps(record) for record in records]
    return adapter.native_review_observations(lines, observed_at={id(line): when for line in lines})


@pytest.mark.parametrize("cli", PROVIDERS[1:])
def test_native_parent_transport_passes_current_review_and_delivery_validation(
    cli, execution_context, monkeypatch, tmp_path
):
    from cafe.agents.cli.codex import CodexCLI
    from cafe.agents.cli.gemini import GeminiCLI

    monkeypatch.chdir(execution_context["root"])
    monkeypatch.setattr(CodexCLI, "create_stream_activity", lambda *args: None)
    monkeypatch.setattr(GeminiCLI, "ensure_geminiignore", lambda *args: None)
    home = tmp_path / ".cafe/native"
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("CODEX_API_KEY", "fixture-not-a-real-key")
    monkeypatch.setenv("GEMINI_CLI_HOME", str(home))
    conf = configuration(cli)
    execution_context["review_configuration"] = conf
    receipt = checkpoint(execution_context, "before_review", round_id="review", parent_id=PARENT)
    records, conclusion = provider_records(cli, conf, receipt, journal_home=home)
    commands = []
    resources = []
    original_popen = subprocess.Popen

    def parent_process(command, **kwargs):
        if command[0] == "git":
            return original_popen(command, **kwargs)
        commands.append(command)
        assert "__CAFE_NATIVE_REVIEW_RESOURCE__" not in " ".join(command)
        if cli in {"copilot", "cursor-agent"}:
            directory = Path(command[command.index("--plugin-dir") + 1])
            resources.append(directory)
            assert (directory / "plugin.json").is_file()
        elif cli == "gemini":
            directory = Path(kwargs["env"]["GEMINI_CLI_HOME"])
            resources.append(directory)
            assert (directory / ".gemini/agents" / (reviewer_type(conf) + ".md")).is_file()
        else:
            path = Path(
                json.loads(next(arg.split("=", 1)[1] for arg in command if ".config_file=" in arg))
            )
            resources.append(path.parent)
            assert 'sandbox_mode = "read-only"' in path.read_text()
        process = MagicMock()
        process.stdout = io.StringIO("".join(json.dumps(r) + "\n" for r in records))
        process.stderr = io.StringIO("")
        process.wait.return_value = 0
        process.poll.return_value = 0
        process.returncode = 0
        return process

    monkeypatch.setattr("subprocess.Popen", parent_process)
    manager = AgentManager(issue_name="native", stream_agent_output=False)
    manager.register_agent(AgentConfig(name="parent", cli=AgentCLI(cli), model="test"))
    response = manager.execute(
        "parent", "implement and natively review", native_review_configuration=conf
    )
    observations = manager.get_last_native_review_observations()
    assert len(commands) == 1  # One parent process; the native child is not a separate CLI.
    assert len(observations) == 1 and observations[0]["terminal"] == "result"
    assert observations[0]["findings"] == conclusion["findings"]
    assert all(not path.exists() for path in resources)
    if cli == "copilot":
        assert response[0] == "Parent completed"
    evidence = {
        "version": 1,
        "round_id": "review",
        "checkpoint": receipt,
        "invocations": [
            {
                "parent_id": PARENT,
                "reviewer_id": CHILD,
                "configuration": conf,
                "terminal": "result",
                "exit_status": 0,
                "result_reference": "native-child-result",
                **conclusion,
            }
        ],
    }
    host = {"version": 1, "parent_id": PARENT, "observations": observations}
    require_current_review(execution_context, evidence, native_observations=host)
    evidence["native_observations"] = host
    require_verified_review(execution_context, evidence)
    evidence["invocations"][0]["targeted_tests"] = ["rewritten parent claim"]
    with pytest.raises(ValueError):
        require_verified_review(execution_context, evidence)


@pytest.mark.parametrize("cli", PROVIDERS)
@pytest.mark.parametrize(
    "defect",
    [
        "incomplete",
        "wrong_checkpoint",
        "stale",
        "wrong_reviewer",
        "blocking",
        "ambiguous_conclusion",
    ],
)
def test_independent_native_review_rejects_ineligible_evidence(
    cli, defect, execution_context, monkeypatch, tmp_path
):
    conf = configuration(cli)
    execution_context["review_configuration"] = conf
    home = tmp_path / ".cafe/native"
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("CODEX_API_KEY", "fixture-not-a-real-key")
    receipt = checkpoint(execution_context, "before_review", round_id="review", parent_id=PARENT)
    records, conclusion = provider_records(cli, conf, receipt, journal_home=home)
    when = receipt["observed_at"]
    if defect == "incomplete":
        records = records[:2] if cli != "copilot" else records[:1]
    elif defect == "wrong_checkpoint":
        records = json.loads(json.dumps(records).replace(receipt["receipt_id"], "wrong-checkpoint"))
    elif defect == "stale":
        when = (datetime.fromisoformat(when) - timedelta(seconds=1)).isoformat()
    elif defect == "wrong_reviewer":
        records = json.loads(json.dumps(records).replace(reviewer_type(conf), "unapproved"))
        if cli == "codex":
            for journal in home.rglob("*.jsonl"):
                journal.write_text(journal.read_text().replace(reviewer_type(conf), "unapproved"))
    elif defect in {"blocking", "ambiguous_conclusion"}:
        replacement = {
            "findings": [{"severity": "blocking", "detail": "independent defect"}],
            "targeted_tests": conclusion["targeted_tests"],
        }
        old = json.dumps(conclusion)
        new = json.dumps(replacement) if defect == "blocking" else old + "\n" + old

        def replace_value(value):
            if isinstance(value, dict):
                return {k: replace_value(v) for k, v in value.items()}
            if isinstance(value, list):
                return [replace_value(v) for v in value]
            if isinstance(value, str):
                return value.replace(old, new).replace(json.dumps(old)[1:-1], json.dumps(new)[1:-1])
            return value

        records = replace_value(records)
    observed = observe(cli, conf, records, when)
    evidence = {
        "version": 1,
        "round_id": "review",
        "checkpoint": receipt,
        "invocations": [
            {
                "parent_id": PARENT,
                "reviewer_id": CHILD,
                "configuration": conf,
                "terminal": "result",
                "exit_status": 0,
                "result_reference": "result",
                **conclusion,
            }
        ],
    }
    with pytest.raises(ValueError):
        require_current_review(
            execution_context,
            evidence,
            native_observations={"version": 1, "parent_id": PARENT, "observations": observed},
        )


@pytest.mark.parametrize(
    "reason", ["TIMEOUT", "MAX_TURNS", "ABORTED", "ERROR", "ERROR_NO_COMPLETE_TASK_CALL"]
)
def test_gemini_success_wrapper_cannot_hide_incomplete_child(reason, execution_context):
    conf = configuration("gemini")
    receipt = checkpoint(execution_context, "before_review", round_id="review", parent_id=PARENT)
    records, _ = provider_records("gemini", conf, receipt)
    progress = json.loads(records[2]["output"])
    progress["terminateReason"] = reason
    records[2]["output"] = json.dumps(progress)
    assert observe("gemini", conf, records, receipt["observed_at"])[0]["terminal"] is None


@pytest.mark.parametrize("defect", ["cancelled", "model", "background", "missing_start", "failure"])
def test_copilot_requires_completed_native_child_with_confirmed_actual_model(
    defect, execution_context
):
    conf = configuration("copilot")
    receipt = checkpoint(execution_context, "before_review", round_id="review", parent_id=PARENT)
    records, _ = provider_records("copilot", conf, receipt)
    if defect == "cancelled":
        records[2]["data"]["cancelled"] = True
    elif defect == "model":
        records[2]["data"]["firstDispatchedModel"] = "other"
    elif defect == "background":
        records[1]["data"]["executionMode"] = "background"
    elif defect == "missing_start":
        del records[1]
    else:
        records.append({"type": "subagent.failed", "data": {"toolCallId": CHILD}})
    assert observe("copilot", conf, records, receipt["observed_at"])[0]["terminal"] is None


@pytest.mark.parametrize("cli", PROVIDERS)
def test_native_projection_checks_every_fallback_and_keeps_explicit_model_authority(
    cli, monkeypatch, tmp_path
):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CODEX_API_KEY", "fixture-not-a-real-key")
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "codex"
    home.mkdir()
    (home / "config.toml").write_text('sandbox_mode = "read-only"\n')
    conf = configuration(cli)
    validate_native_review_projection(
        {"build": [{"cli": cli, "model": "test"}, {"cli": cli, "model": "test"}]}, ["build"], conf
    )
    with pytest.raises(ValueError):
        validate_native_review_projection(
            {"build": [{"cli": cli, "model": "test"}, {"cli": "claude", "model": "test"}]},
            ["build"],
            conf,
        )
    with pytest.raises(ValueError):
        validate_native_review_projection(
            {"build": [{"cli": cli, "model": "other"}]}, ["build"], conf
        )
    override = configuration(cli, "independent_override", "review-model")
    if cli == "cursor-agent":
        with pytest.raises(ValueError, match="inherit"):
            validate_native_review_projection(
                {"build": [{"cli": cli, "model": "test"}]}, ["build"], override
            )
    else:
        validate_native_review_projection(
            {"build": [{"cli": cli, "model": "test"}]}, ["build"], override
        )


def test_gemini_overlay_preserves_sessions_settings_and_existing_agents(tmp_path):
    original = tmp_path / "native/.gemini"
    original.mkdir(parents=True)
    (original / "settings.json").write_text(
        '{"security": {"auth": {"selectedType": "oauth-personal"}}}'
    )
    (original / "agents").mkdir()
    (original / "agents/existing.md").write_text("original user agent")
    (original / "tmp").mkdir()
    (original / "tmp/session.json").write_text("resume data")
    environment = {"GEMINI_CLI_HOME": str(original.parent)}
    config = AgentConfig(
        name="parent",
        cli=AgentCLI.GEMINI,
        model="test",
        native_review_configuration=configuration("gemini"),
    )
    with invocation(config, ["gemini", "--resume", PARENT], environment) as (command, env):
        overlay = Path(env["GEMINI_CLI_HOME"]) / ".gemini"
        assert (overlay / "tmp/session.json").read_text() == "resume data"
        (overlay / "tmp/new.json").write_text("new session turn")
        assert (overlay / "agents/existing.md").read_text() == "original user agent"
        settings = json.loads((overlay / "settings.json").read_text())
        assert settings["experimental"]["enableAgents"] is True
        assert settings["security"]["auth"]["selectedType"] == "oauth-personal"
        assert command == ["gemini", "--resume", PARENT]
    assert not overlay.exists()
    assert (original / "tmp/new.json").read_text() == "new session turn"
    assert json.loads((original / "settings.json").read_text()) == {
        "security": {"auth": {"selectedType": "oauth-personal"}}
    }
    assert list((original / "agents").iterdir()) == [original / "agents/existing.md"]


@pytest.mark.parametrize("cli", PROVIDERS)
@pytest.mark.parametrize(
    "payload",
    [
        {"findings": [], "targeted_tests": "parent claim"},
        {"findings": [], "targeted_tests": []},
        {"findings": [], "targeted_tests": [""]},
        {"findings": [{"severity": "nonblocking", "detail": 123}], "targeted_tests": ["passed"]},
        {"findings": [{"severity": [], "detail": "invalid"}], "targeted_tests": ["passed"]},
        {"findings": {}, "targeted_tests": ["passed"]},
    ],
)
def test_provider_cannot_accept_malformed_independent_conclusion(
    cli, payload, execution_context, monkeypatch, tmp_path
):
    conf = configuration(cli)
    home = tmp_path / ".cafe/native"
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("CODEX_API_KEY", "fixture-not-a-real-key")
    receipt = checkpoint(execution_context, "before_review", round_id="review", parent_id=PARENT)
    records, _ = provider_records(cli, conf, receipt, journal_home=home)
    set_child_conclusion(cli, records, json.dumps(payload))
    observed = observe(cli, conf, records, receipt["observed_at"])
    assert len(observed) == 1 and observed[0]["terminal"] is None


@pytest.mark.parametrize(
    "sandbox,model",
    [("danger-full-access", "test"), ("workspace-write", "test"), ("read-only", "other")],
)
def test_codex_ignores_role_claim_when_child_effective_configuration_differs(
    sandbox, model, execution_context, monkeypatch, tmp_path
):
    conf = configuration("codex")
    home = tmp_path / ".cafe/native"
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("CODEX_API_KEY", "fixture-not-a-real-key")
    receipt = checkpoint(execution_context, "before_review", round_id="review", parent_id=PARENT)
    records, _ = provider_records("codex", conf, receipt, journal_home=home)
    journal = next(home.rglob("*-" + CHILD + ".jsonl"))
    journal.write_text(
        journal.read_text()
        .replace('"read-only"', json.dumps(sandbox))
        .replace('"test"', json.dumps(model))
    )
    assert observe("codex", conf, records, receipt["observed_at"])[0]["terminal"] is None


def test_codex_writable_parent_projects_native_app_server(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "codex"
    home.mkdir()
    (home / "config.toml").write_text('sandbox_mode = "danger-full-access"\n')
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("CODEX_API_KEY", "fixture-not-a-real-key")
    validate_native_review_projection(
        {"build": [{"cli": "codex", "model": "test"}]}, ["build"], configuration("codex")
    )


@pytest.mark.parametrize("cli", ["gemini", "cursor-agent"])
def test_existing_native_workspace_role_is_never_shadowed(tmp_path, monkeypatch, cli):
    import yaml

    monkeypatch.chdir(tmp_path)
    conf = configuration(cli)
    directory = tmp_path / (".gemini" if cli == "gemini" else ".cursor") / "agents"
    directory.mkdir(parents=True)
    original = directory / "existing.md"
    content = "---\n" + yaml.safe_dump({"name": reviewer_type(conf)}) + "---\nUser agent\n"
    original.write_text(content)
    config = AgentConfig(
        name="parent", cli=AgentCLI(cli), model="test", native_review_configuration=conf
    )
    with pytest.raises(ValueError, match="collides"):
        with invocation(config, [cli], {"GEMINI_CLI_HOME": str(tmp_path / "native")}):
            pytest.fail("conflicting native configuration must never launch")
    assert original.read_text() == content


def test_gemini_saved_role_override_cannot_replace_confirmed_tools(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    conf = configuration("gemini")
    directory = tmp_path / "native/.gemini"
    directory.mkdir(parents=True)
    (directory / "settings.json").write_text(
        json.dumps(
            {"agents": {"overrides": {reviewer_type(conf): {"modelConfig": {"model": "other"}}}}}
        )
    )
    config = AgentConfig(
        name="parent", cli=AgentCLI.GEMINI, model="test", native_review_configuration=conf
    )
    with pytest.raises(ValueError, match="overridden"):
        with invocation(config, ["gemini"], {"GEMINI_CLI_HOME": str(directory.parent)}):
            pytest.fail("saved overrides cannot rewrite confirmed authority")


def test_gemini_same_name_native_tool_uses_its_real_query_schema(execution_context):
    conf = configuration("gemini")
    receipt = checkpoint(execution_context, "before_review", round_id="review", parent_id=PARENT)
    records, _ = provider_records("gemini", conf, receipt)
    invoke = records[1]
    prompt = invoke["parameters"]["prompt"]
    invoke.update(tool_name=reviewer_type(conf), parameters={"query": prompt})
    observed = observe("gemini", conf, records, receipt["observed_at"])
    assert observed[0]["receipt_id"] == receipt["receipt_id"]
    assert observed[0]["terminal"] == "result"


@pytest.mark.parametrize("cli", PROVIDERS)
@pytest.mark.parametrize(
    "sequence", ["blocking_then_clean", "clean_then_failure", "cancelled_then_clean", "same_replay"]
)
def test_native_terminal_evidence_cannot_be_rewritten(
    cli, sequence, execution_context, monkeypatch, tmp_path
):
    conf = configuration(cli)
    execution_context["review_configuration"] = conf
    home = tmp_path / ".cafe/native"
    monkeypatch.setenv("CODEX_HOME", str(home))
    receipt = checkpoint(execution_context, "before_review", round_id="review", parent_id=PARENT)
    records, conclusion = provider_records(cli, conf, receipt, journal_home=home)
    terminal_index = 3 if cli == "copilot" else 2
    clean = deepcopy(records[terminal_index])
    if sequence == "blocking_then_clean":
        set_child_conclusion(
            cli,
            records,
            json.dumps({**conclusion, "findings": [{"severity": "blocking", "detail": "defect"}]}),
        )
        records.append(clean)
    elif sequence == "same_replay":
        records.append(clean)
    else:
        failure = deepcopy(records[2])  # Copilot cancellation is subagent.completed.
        if cli == "gemini":
            failure["status"] = "error"
        elif cli == "copilot":
            failure["data"]["cancelled"] = True
        elif cli == "cursor-agent":
            failure["tool_call"]["taskToolCall"]["result"] = {"error": {"message": "cancelled"}}
        else:
            failure["item"]["agents_states"][CHILD] = {"status": "errored", "message": "cancelled"}
        if sequence == "clean_then_failure":
            records.append(failure)
        else:
            records.insert(terminal_index, failure)
    observed = observe(cli, conf, records, receipt["observed_at"])
    evidence = {
        "version": 1,
        "round_id": "review",
        "checkpoint": receipt,
        "invocations": [
            {
                "parent_id": PARENT,
                "reviewer_id": CHILD,
                "configuration": conf,
                "terminal": "result",
                "exit_status": 0,
                "result_reference": "native-result",
                **conclusion,
            }
        ],
    }
    host = {"version": 1, "parent_id": PARENT, "observations": observed}
    if sequence == "same_replay":
        require_current_review(execution_context, evidence, native_observations=host)
    else:
        assert len(observed) == 1 and observed[0]["terminal"] is None
        with pytest.raises(ValueError):
            require_current_review(execution_context, evidence, native_observations=host)


@pytest.mark.parametrize("cli", PROVIDERS)
def test_duplicate_start_cannot_replace_checkpoint(cli, execution_context, monkeypatch, tmp_path):
    conf = configuration(cli)
    home = tmp_path / ".cafe/native"
    monkeypatch.setenv("CODEX_HOME", str(home))
    receipt = checkpoint(execution_context, "before_review", round_id="review", parent_id=PARENT)
    records, _ = provider_records(cli, conf, receipt, journal_home=home)
    start_index = 0 if cli == "copilot" else 1
    altered = json.loads(json.dumps(records[start_index]).replace(receipt["receipt_id"], "other"))
    records.insert(start_index + 1, altered)
    observed = observe(cli, conf, records, receipt["observed_at"])
    assert len(observed) == 1
    assert observed[0]["receipt_id"] == receipt["receipt_id"]
    assert observed[0]["terminal"] is None


@pytest.mark.parametrize("defect", ["default", "cloud_auth", "keyring", "permissions"])
def test_codex_auth_and_config_are_verified_by_native_rpc(defect, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "codex"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("CODEX_API_KEY", "fixture-not-a-real-key")
    if defect != "default":
        (home / "config.toml").write_text('sandbox_mode = "read-only"\n')
    if defect == "cloud_auth":
        (home / "auth.json").write_text("{}")
    elif defect == "keyring":
        (home / "config.toml").write_text(
            'sandbox_mode = "read-only"\ncli_auth_credentials_store = "keyring"\n'
        )
    elif defect == "permissions":
        (home / "config.toml").write_text(
            'sandbox_mode = "read-only"\ndefault_permissions = "custom"\n'
        )
    validate_native_review_projection(
        {"build": [{"cli": "codex", "model": "test"}]}, ["build"], configuration("codex")
    )


@pytest.mark.parametrize("cli", PROVIDERS)
def test_incompatible_child_identity_or_model_cannot_be_repaired_by_later_success(
    cli, execution_context, monkeypatch, tmp_path
):
    conf = configuration(cli)
    home = tmp_path / ".cafe/native"
    monkeypatch.setenv("CODEX_HOME", str(home))
    receipt = checkpoint(execution_context, "before_review", round_id="review", parent_id=PARENT)
    records, _ = provider_records(cli, conf, receipt, journal_home=home)
    incorrect = deepcopy(records[2])
    if cli == "gemini":
        progress = json.loads(incorrect["output"])
        progress["agentName"] = "unapproved"
        incorrect["output"] = json.dumps(progress)
    elif cli == "copilot":
        incorrect["data"]["model"] = "other"
    elif cli == "cursor-agent":
        incorrect["tool_call"]["taskToolCall"]["args"]["model"] = "other"
    else:
        journal = next(home.rglob("*-" + CHILD + ".jsonl"))
        journal.write_text(
            journal.read_text().replace('"test"', '"other"')
            + json.dumps(
                {
                    "type": "turn_context",
                    "payload": {"model": "test", "sandbox_policy": {"type": "read-only"}},
                }
            )
            + "\n"
        )
    records.insert(2, incorrect)
    observed = observe(cli, conf, records, receipt["observed_at"])
    assert len(observed) == 1 and observed[0]["terminal"] is None
