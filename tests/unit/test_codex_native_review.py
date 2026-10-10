"""One real stdio process, authenticated parent continuation and native fork gates."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cafe.agents.executor import AgentExecutionControl, AgentExecutionError, AgentExecutor
from cafe.core.execution_checkpoints import checkpoint, require_verified_review
from cafe.core.types import AgentCLI, AgentConfig
from tests.unit.test_execution_checkpoints import execution_context as _context
from tests.unit.test_file_scope import repository as _repository
from tests.unit.test_native_review_providers import CHILD, PARENT, configuration

execution_context = _context
repository = _repository

# This fixture speaks native RPC schemas over actual pipes. Popen stays real so
# buffering, lifecycle, deadlines and output limits exercise the public executor.
_SERVER = r"""
import json, sys, time
from pathlib import Path

case, transcript, receipt, root = sys.argv[1:]
parent = "b348e882-c4e8-4ab8-93b4-088a67b1fe4b"
child = "70b7a3e6-1f9a-45dc-b656-6cd12bd64cf6"
turns = 0
resumed = False
parent_model = "test"
child_model = "test"
conclusion = {"findings": [], "targeted_tests": ["checked current content"]}
if case == "blocking":
    conclusion["findings"] = [{"severity": "blocking", "detail": "real defect"}]


def emit(value):
    print(json.dumps(value), flush=True)


def notify(method, params):
    emit({"method": method, "params": params})


for line in sys.stdin:
    request = json.loads(line)
    with open(transcript, "a") as f:
        f.write(line)
    method = request["method"]
    if case == "orphan_exit":
        import subprocess
        child_process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        Path(transcript + ".child").write_text(str(child_process.pid))
        raise SystemExit(0)
    if "id" not in request:
        continue
    params = request.get("params", {})
    result = {}
    if method == "config/read":
        result = {
            "config": {
                "mcp_servers": {
                    "write.api": {
                        "enabled": True,
                        "tool_timeout_sec": None,
                        "url": "https://fixture.invalid/mcp",
                        "env": {"API_KEY": "do-not-log"},
                    }
                }
            }
        }
        if case == "late_failure":
            notify(
                "turn/completed",
                {
                    "threadId": parent,
                    "turn": {"id": "turn-1", "status": "failed", "error": {"message": "late"}},
                },
            )
        if case == "late_message_conflict":
            notify(
                "item/completed",
                {
                    "threadId": parent,
                    "turnId": "turn-1",
                    "item": {
                        "id": "message-1",
                        "type": "agentMessage",
                        "phase": "final_answer",
                        "text": "replaced",
                    },
                },
            )
    elif method == "configRequirements/read":
        feature = case.split("managed:", 1)[-1] if case.startswith("managed:") else None
        result = {"requirements": {"featureRequirements": {feature: True}} if feature else None}
    elif method in {"thread/start", "thread/resume", "thread/fork"}:
        if method == "thread/resume":
            resumed = True
            if case in {"missing_session", "auto_recover", "resume_auth_error"}:
                message = "no rollout found" if case != "resume_auth_error" else "not authenticated"
                emit({"id": request["id"], "error": {"code": -32600, "message": message}})
                continue
        fork = method == "thread/fork"
        if fork:
            child_model = params["model"]
        else:
            parent_model = params["model"]
        result = {
            "thread": {"id": child if fork else parent},
            "model": params["model"],
            "cwd": root,
            "approvalPolicy": "never",
            "sandbox": (
                {"type": "readOnly", "networkAccess": False} if fork else {"type": "workspaceWrite"}
            ),
        }
        if fork:
            result["thread"]["forkedFromId"] = parent
            if case == "wrong_child_model":
                result["model"] = "other"
            if case == "wrong_parent":
                result["thread"]["forkedFromId"] = "other"
            if case == "same_child":
                result["thread"]["id"] = parent
            if case == "writable_child":
                result["sandbox"]["type"] = "workspaceWrite"
            if case == "network_child":
                result["sandbox"]["networkAccess"] = True
            if case == "approval_child":
                result["approvalPolicy"] = "on-request"
            if case == "wrong_cwd":
                result["cwd"] = str(Path(root).parent)
            if case == "fork_missing_source":
                emit(
                    {"id": request["id"], "error": {"code": -32600, "message": "no rollout found"}}
                )
                continue
        elif method == "thread/resume" and case == "wrong_resume":
            result["thread"]["id"] = "other"
    elif method == "turn/start":
        turns += 1
        thread = params["threadId"]
        turn = "turn-" + str(turns)
        result = {"turn": {"id": turn, "status": "inProgress"}}
        emit({"id": request["id"], "result": result})
        if case == "hang":
            time.sleep(30)
            continue
        if case == "extra_actor":
            notify(
                "item/completed",
                {
                    "threadId": thread,
                    "turnId": turn,
                    "item": {"type": "collabAgentToolCall", "tool": "spawnAgent"},
                },
            )
        if case == "malformed_rpc":
            print("[bad json", flush=True)
            continue
        if case == "oversized_line":
            print("x" * (2 * 1024 * 1024), flush=True)
            continue
        if case == "interactive":
            emit(
                {
                    "id": 999,
                    "method": "item/commandExecution/requestApproval",
                    "params": {"threadId": thread},
                }
            )
            continue
        if case == "auto_recover":
            resumed = False
        offset = 200 if resumed else 0
        inp, out = (100, 10) if turns == 1 else (115, 12) if thread == child else (135, 18)
        if case != "no_telemetry":
            counters = {
                "inputTokens": inp + offset,
                "outputTokens": out + (20 if resumed else 0),
                "cachedInputTokens": 0,
                "reasoningOutputTokens": 0,
                "totalTokens": inp + out,
            }
            if case == "late_optional":
                if turns == 1:
                    counters.pop("cachedInputTokens")
                else:
                    counters["cachedInputTokens"] = 40
            notify(
                "thread/tokenUsage/updated",
                {
                    "threadId": thread,
                    "turnId": turn,
                    "tokenUsage": {"total": counters, "last": counters},
                },
            )
        if thread == child and case == "rerouted":
            notify(
                "model/rerouted",
                {"threadId": thread, "turnId": turn, "fromModel": child_model, "toModel": "other"},
            )
        if thread == child and case == "settings_changed":
            notify(
                "thread/settings/updated",
                {
                    "threadId": thread,
                    "threadSettings": {
                        "model": child_model,
                        "approvalPolicy": "never",
                        "sandboxPolicy": {"type": "workspaceWrite"},
                    },
                },
            )
        text = (
            json.dumps(
                {"cafe_native_review": {"prompt": "Review. CAFE_REVIEW_CHECKPOINT:" + receipt}}
            )
            if turns == 1
            else json.dumps(conclusion) if thread == child else "Parent completed"
        )
        if case == "human_handoff":
            text = "User permission needed"
        if case == "bad_request":
            text = json.dumps({"cafe_native_review": {"prompt": "no checkpoint"}})
        if turns == 3 and case == "second_review":
            text = json.dumps(
                {"cafe_native_review": {"prompt": "Review. CAFE_REVIEW_CHECKPOINT:" + receipt}}
            )
        if thread == child and case == "ambiguous":
            text += "\n" + text
        if thread == child and case == "missing_result":
            text = "no conclusion"
        phase = "commentary" if thread == child and case == "commentary_only" else "final_answer"
        item = {"id": "message-" + str(turns), "type": "agentMessage", "text": text, "phase": phase}
        notify(
            "item/completed",
            {
                "threadId": (
                    "other" if thread == child and case == "wrong_message_thread" else thread
                ),
                "turnId": turn,
                "item": item,
            },
        )
        if case == "message_replay":
            notify("item/completed", {"threadId": thread, "turnId": turn, "item": item})
        error = (
            {"codexErrorInfo": "rateLimitExceeded", "message": "rate limited"}
            if case == "rate_limit"
            else None
        )
        status = (
            "interrupted"
            if thread == child and case == "cancelled"
            else "failed" if error else "completed"
        )
        terminal = {"threadId": thread, "turn": {"id": turn, "status": status, "error": error}}
        notify("turn/completed", terminal)
        if case == "terminal_replay":
            notify("turn/completed", terminal)
        continue
    emit({"id": request["id"], "result": result})
"""


@pytest.fixture
def rpc(tmp_path, monkeypatch):
    fixture_root = tmp_path / ".cafe/rpc"
    fixture_root.mkdir(parents=True)
    server = fixture_root / "native-server.py"
    server.write_text(_SERVER)
    transcript = fixture_root / "rpc.jsonl"
    home = fixture_root / "codex-home"
    home.mkdir()
    (home / "auth.json").write_text('{"auth_mode":"chatgpt"}')
    (home / "config.toml").write_text('sandbox_mode="workspace-write"\n')
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.setenv("CAFE_PRICING_AUTO_UPDATE", "0")
    original = subprocess.Popen
    commands = []
    processes = []
    selected = {"case": "ok", "receipt": "checkpoint"}

    def launch(command, **kwargs):
        if command[0] != "codex" or "app-server" not in command:
            return original(command, **kwargs)
        commands.append(command)
        assert "app-server" in command and "exec" not in command
        process = original(
            [
                sys.executable,
                "-u",
                str(server),
                selected["case"],
                str(transcript),
                selected["receipt"],
                str(kwargs["cwd"]),
            ],
            **kwargs,
        )
        processes.append(process)
        return process

    monkeypatch.setattr("subprocess.Popen", launch)

    def run(case="ok", *, model="test", session=None, **kwargs):
        selected["case"] = case
        executor = AgentExecutor(
            AgentConfig(
                name="parent",
                cli=AgentCLI.CODEX,
                model="test",
                session_id=session,
                native_review_configuration=configuration("codex", "independent_override", model),
            ),
            stream_output=False,
        )
        control = kwargs.pop("execution_control", AgentExecutionControl(working_directory=tmp_path))
        return executor, lambda: executor.execute(
            "implement and review", execution_control=control, **kwargs
        )

    yield run, selected, transcript, commands, processes, home
    assert all(process.poll() is not None for process in processes)


def _records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.parametrize("case", ["ok", "message_replay", "terminal_replay"])
def test_chatgpt_writable_development_native_readonly_review_and_continuation(rpc, case):
    run, _, transcript, commands, _, home = rpc
    executor, execute = run(case)
    response = execute()
    assert response.response == "Parent completed"
    assert response.session_id == executor.config.session_id == PARENT
    assert len(commands) == 1
    requests = _records(transcript)
    start = next(r for r in requests if r["method"] == "thread/start")
    fork = next(r for r in requests if r["method"] == "thread/fork")
    assert start["params"]["sandbox"] == "workspace-write"
    assert fork["params"]["sandbox"] == "read-only"
    assert fork["params"]["threadId"] == PARENT
    config = fork["params"]["config"]
    assert config["mcp_servers"]["write.api"]["enabled"] is False
    assert "tool_timeout_sec" not in config["mcp_servers"]["write.api"]
    assert config["mcp_servers"]["write.api"]["url"] == "https://fixture.invalid/mcp"
    assert config["notify"] == []
    assert all(
        config["features." + flag] is False
        for flag in ("apps", "plugins", "hooks", "multi_agent", "multi_agent_v2", "computer_use")
    )
    turns = [r["params"] for r in requests if r["method"] == "turn/start"]
    assert [r["threadId"] for r in turns] == [PARENT, CHILD, PARENT]
    assert turns[1]["sandboxPolicy"] == {"type": "readOnly", "networkAccess": False}
    observed = response.native_review_observations
    assert len(observed) == 1 and observed[0]["reviewer_id"] == CHILD
    assert observed[0]["terminal"] == "result"
    assert "do-not-log" not in "\n".join(response.streaming_log)
    assert response.token_usage.input_tokens == 150
    assert response.token_usage.output_tokens == 20
    assert len(response.token_usage.cost_records) == 3
    assert "cache_write_input_tokens" not in response.token_usage.cost_records[0]["usage"]
    assert (home / "auth.json").read_text() == '{"auth_mode":"chatgpt"}'


@pytest.mark.parametrize(
    "case",
    [
        "wrong_child_model",
        "wrong_parent",
        "same_child",
        "writable_child",
        "network_child",
        "approval_child",
        "wrong_cwd",
        "fork_missing_source",
        "cancelled",
        "ambiguous",
        "missing_result",
        "commentary_only",
        "wrong_message_thread",
        "rerouted",
        "settings_changed",
        "late_failure",
        "late_message_conflict",
        "malformed_rpc",
        "interactive",
        "second_review",
        "bad_request",
        "extra_actor",
        "managed:apps",
        "managed:hooks",
        "managed:plugins",
        "managed:multi_agent_v2",
    ],
)
def test_native_review_fails_closed_and_reaps_process(rpc, case):
    run, _, _, commands, _, _ = rpc
    executor, execute = run(case)
    with pytest.raises(AgentExecutionError):
        execute()
    assert len(commands) == 1
    assert executor.config.session_id == (None if case.startswith("managed:") else PARENT)


def test_blocking_child_is_retained_without_parent_downgrade(rpc):
    run, _, _, _, _, _ = rpc
    _, execute = run("blocking")
    response = execute()
    assert response.native_review_observations[0]["findings"][0]["severity"] == "blocking"


def test_override_model_is_native_and_costed_separately(rpc):
    run, _, transcript, _, _, _ = rpc
    _, execute = run(model="review-model")
    response = execute()
    assert [record["model"] for record in response.token_usage.cost_records] == [
        "test",
        "review-model",
        "test",
    ]
    turns = [r["params"] for r in _records(transcript) if r["method"] == "turn/start"]
    assert turns[1]["model"] == "review-model"


def test_human_handoff_does_not_launch_reviewer(rpc):
    run, _, transcript, _, _, _ = rpc
    _, execute = run("human_handoff")
    response = execute()
    assert response.native_review_observations == []
    assert "thread/fork" not in [r["method"] for r in _records(transcript)]


def test_absent_telemetry_stays_unavailable(rpc):
    run, _, _, _, _, _ = rpc
    _, execute = run("no_telemetry")
    response = execute()
    assert not response.usage_available
    assert all(
        record["provenance"] == "unavailable" for record in response.token_usage.cost_records
    )
    assert all(
        "input_tokens" not in record["usage"] for record in response.token_usage.cost_records
    )


def _resume_journal(home):
    sessions = home / "sessions"
    sessions.mkdir()
    (sessions / ("rollout-" + PARENT + ".jsonl")).write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {"type": "session_meta", "payload": {"id": PARENT}},
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 200,
                                "output_tokens": 20,
                                "cached_input_tokens": 0,
                                "reasoning_output_tokens": 0,
                            }
                        },
                    },
                },
            ]
        )
        + "\n"
    )


def test_resumes_existing_exec_session_without_creating_replacement(rpc):
    run, _, transcript, _, _, home = rpc
    _resume_journal(home)
    _, execute = run(session=PARENT, exact_session=True)
    response = execute()
    methods = [r["method"] for r in _records(transcript)]
    assert "thread/resume" in methods and "thread/start" not in methods
    assert response.session_id == PARENT
    assert response.token_usage.input_tokens == 150
    assert response.token_usage.output_tokens == 20


@pytest.mark.parametrize("case", ["missing_session", "wrong_resume", "resume_auth_error"])
def test_exact_resume_does_not_replace_session(rpc, case):
    run, _, transcript, commands, _, _ = rpc
    _, execute = run(case, session=PARENT, exact_session=True)
    with pytest.raises(AgentExecutionError):
        execute()
    assert len(commands) == 1
    assert "thread/start" not in [r["method"] for r in _records(transcript)]


def test_auto_missing_session_uses_existing_recovery_contract(rpc):
    run, _, transcript, commands, _, _ = rpc
    _, execute = run("auto_recover", session=PARENT)
    response = execute()
    assert response.response == "Parent completed"
    assert len(commands) == 2
    assert "thread/start" in [r["method"] for r in _records(transcript)]


def test_auth_error_does_not_trigger_cold_recovery(rpc):
    run, _, _, commands, _, _ = rpc
    _, execute = run("resume_auth_error", session=PARENT)
    with pytest.raises(AgentExecutionError):
        execute()
    assert len(commands) == 1


@pytest.mark.parametrize("mode", ["read_only", "empty", "event_driver"])
def test_incompatible_public_execution_scope_rejected_before_launch(rpc, mode, tmp_path):
    run, _, _, commands, _, _ = rpc
    kwargs = (
        {"read_only": True}
        if mode == "read_only"
        else ({"allowed_tools": [], "allowed_directories": []} if mode == "empty" else {})
    )
    executor, execute = run(**kwargs)
    with pytest.raises(AgentExecutionError):
        if mode == "event_driver":
            executor.execute_event_driver("callback")
        else:
            execute()
    assert commands == []


@pytest.mark.parametrize("case", ["hang", "oversized_line"])
def test_process_and_partial_line_limits_are_bounded(rpc, case, tmp_path):
    run, _, _, commands, _, _ = rpc
    _, execute = run(
        case,
        execution_control=AgentExecutionControl(
            working_directory=tmp_path, max_duration_seconds=0.3, max_output_bytes=4096
        ),
    )
    with pytest.raises(AgentExecutionError):
        execute()
    assert len(commands) == 1


def test_rate_limit_preserves_session_and_partial_accounting(rpc):
    run, _, _, _, _, _ = rpc
    executor, execute = run("rate_limit")
    with pytest.raises(AgentExecutionError) as caught:
        execute()
    assert caught.value.error_type == "rate_limit"
    assert executor.config.session_id == PARENT
    assert caught.value.accounting_usage.input_tokens == 100
    assert caught.value.accounting_usage.cost_records[0]["complete"] is False


def test_public_manager_native_result_is_required_by_delivery_gate(rpc, execution_context):
    from cafe.agents.manager import AgentManager

    run, selected, _, commands, _, _ = rpc
    conf = configuration("codex")
    execution_context["review_configuration"] = conf
    receipt = checkpoint(execution_context, "before_review", round_id="round", parent_id=PARENT)
    selected["receipt"] = receipt["receipt_id"]
    manager = AgentManager(issue_name="native", stream_agent_output=False)
    manager.register_agent(AgentConfig(name="parent", cli=AgentCLI.CODEX, model="test"))
    manager.execute(
        "parent",
        "implement and review",
        native_review_configuration=conf,
        execution_control=AgentExecutionControl(working_directory=Path(execution_context["root"])),
    )
    observed = manager.get_last_native_review_observations()
    assert len(commands) == 1 and len(observed) == 1
    evidence = {
        "version": 1,
        "round_id": "round",
        "checkpoint": receipt,
        "invocations": [
            {
                "parent_id": PARENT,
                "reviewer_id": CHILD,
                "configuration": conf,
                "terminal": "result",
                "exit_status": 0,
                "result_reference": "native-result",
                "findings": observed[0]["findings"],
                "targeted_tests": observed[0]["targeted_tests"],
            }
        ],
        "native_observations": {"version": 1, "parent_id": PARENT, "observations": observed},
    }
    require_verified_review(execution_context, evidence)
    evidence["invocations"][0]["findings"] = [{"severity": "nonblocking", "detail": "parent guess"}]
    with pytest.raises(ValueError):
        require_verified_review(execution_context, evidence)
    evidence["invocations"][0]["findings"] = observed[0]["findings"]
    (Path(execution_context["root"]) / "allowed").write_text("changed after review")
    with pytest.raises(ValueError):
        require_verified_review(execution_context, evidence)


def test_late_optional_cumulative_counter_is_not_attributed_from_zero(rpc):
    run, _, _, _, _, _ = rpc
    _, execute = run("late_optional")
    response = execute()
    assert "cache_read_input_tokens" not in response.token_usage.turn_usages[-1]
    assert "cache_read_input_tokens" not in response.token_usage.cost_records[-1]["usage"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX process group contract")
def test_cleanup_kills_descendant_after_server_leader_exits(rpc):
    run, _, transcript, _, _, _ = rpc
    _, execute = run("orphan_exit")
    started = time.monotonic()
    with pytest.raises(AgentExecutionError):
        execute()
    assert time.monotonic() - started < 3
    pid = int(Path(str(transcript) + ".child").read_text())
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            # The container init can retain a killed orphan as a zombie.
            state = Path(f"/proc/{pid}/stat")
            if state.exists() and state.read_text().split()[2] == "Z":
                return
        except ProcessLookupError:
            return
        time.sleep(0.02)
    pytest.fail("Native command descendant survived app-server cleanup")
