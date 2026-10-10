"""Plan I1/I2/I4/I5/I6/I7/I8: public callers retain native physical work."""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from cafe.agents.executor import AgentExecutionError
from cafe.agents.transport_types import AccountingScope
from cafe.core.native_accounting import native_projection
from cafe.core.types import AgentCLI
from cafe.core.usage import iteration_usage_sink
from cafe.manager.costs import (
    CostStore,
    accounted_call,
    inclusive_report,
    manager_usage_sink,
    preserve_worker_cost,
    worker_cost,
)
from cafe.services.cost_summary import collect_cost_sources, summarize_sources
from tests.unit.test_codex_subagent_usage import CHILD, NESTED, ROOT, counters
from tests.unit.test_conversation_transport import provider_process as replay_process_fixture
from tests.unit.test_conversation_transport import transport
from tests.unit.test_manager_costs import cost_journey


@pytest.fixture
def provider_process(monkeypatch):
    # Reuse only the existing process/polling I/O fixture.
    return replay_process_fixture.__wrapped__(monkeypatch)


def native_journal(
    home,
    identity,
    parent=None,
    *,
    model="actual-child-model",
    n=100,
    complete=True,
    version="0.159.3",
):
    now = datetime.now(timezone.utc).isoformat()
    path = home / "sessions" / f"rollout-{identity}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    source = (
        dict(subagent=dict(thread_spawn=dict(parent_thread_id=parent, agent_path="/root/child")))
        if parent
        else "exec"
    )
    rows = [
        dict(
            type="session_meta",
            payload=dict(id=identity, cli_version=version, source=source, timestamp=now),
        ),
        dict(type="event_msg", payload=dict(type="task_started", turn_id="owned-turn")),
        dict(type="turn_context", payload=dict(turn_id="owned-turn", model=model)),
        dict(
            type="event_msg",
            payload=dict(type="token_count", info=dict(total_token_usage=counters(n))),
        ),
    ]
    if complete:
        rows.append(
            dict(type="event_msg", payload=dict(type="task_complete", turn_id="owned-turn"))
        )
    path.write_text("".join(json.dumps(dict(timestamp=now, **r)) + "\n" for r in rows))
    return path


def supply_native(
    provider_process, monkeypatch, home, *, failure=False, unsupported=False, publication=None
):
    rows = [
        dict(type="thread.started", thread_id=ROOT),
        dict(
            type="turn.completed",
            usage=dict(input_tokens=10, cached_input_tokens=2, output_tokens=2),
        ),
    ]
    launch = provider_process(rows if not failure else rows[:1], returncode=1 if failure else 0)

    def launch_native(*args, **kwargs):
        if publication is not None:
            assert publication(), "entry checkpoint must exist before provider submission"
        native_journal(home, ROOT, n=10)
        native_journal(
            home, CHILD, ROOT, complete=not failure, version="9" if unsupported else "0.159.3"
        )
        native_journal(home, NESTED, CHILD, n=50)
        return launch.return_value

    launch.side_effect = launch_native
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("CAFE_PRICING_AUTO_UPDATE", "0")
    return launch


@pytest.mark.parametrize("operation", ["run_one_shot", "acquire_session"])
def test_custom_worker_public_caller_keeps_child_identity_and_owns_cutoff(
    tmp_path, monkeypatch, provider_process, operation
):
    home = tmp_path / "codex"
    issue = tmp_path / ".cafe/issues/topic"
    metadata = issue / "custom-build/iteration_001/iteration.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps(dict(iteration=1, workflow_id="flow", preserved=True)))
    sink = iteration_usage_sink(tmp_path, metadata)
    scope = AccountingScope("flow", "custom-build", sink)
    launch = supply_native(
        provider_process,
        monkeypatch,
        home,
        publication=lambda: bool(json.loads(metadata.read_text()).get("stats")),
    )
    result = getattr(transport(AgentCLI.CODEX), operation)(
        "work", accounting_scope=scope, on_usage=sink
    )
    assert result.returncode == 0 and result.completed is True
    assert launch.call_count == 1
    stats = json.loads(metadata.read_text())["stats"]
    assert stats["input_tokens"] == 160
    view = native_projection(stats["cost_records"])
    assert {r["session_id"] for r in view["children"]} == {CHILD, NESTED}
    assert {r["model"] for r in view["children"]} == {"actual-child-model"}
    assert view["child_tokens"]["total_tokens"] == 165
    assert view["tokens"]["input_tokens"] == 160
    before = metadata.read_bytes()
    sink(result.usage)
    assert metadata.read_bytes() == before
    assert (
        summarize_sources(collect_cost_sources(issue))["native_usage"]["child_tokens"][
            "input_tokens"
        ]
        == 150
    )
    # Reports are usable without the native source and never reprice.
    import shutil

    shutil.rmtree(home)
    assert (
        summarize_sources(collect_cost_sources(issue))["native_usage"]["child_tokens"][
            "input_tokens"
        ]
        == 150
    )


@pytest.mark.parametrize("failure", [True, False])
def test_partial_or_unsupported_native_accounting_preserves_provider_result(
    tmp_path, monkeypatch, provider_process, failure
):
    published = []
    supply_native(
        provider_process, monkeypatch, tmp_path / "native", failure=failure, unsupported=not failure
    )
    caller = transport(AgentCLI.CODEX)
    if failure:
        with pytest.raises(AgentExecutionError) as caught:
            caller.acquire_session(
                "work", accounting_scope=AccountingScope("f", "custom", published.append)
            )
        result = caught.value.transport_result
        assert result.returncode == 1 and result.completed is None
    else:
        result = caller.acquire_session(
            "work", accounting_scope=AccountingScope("f", "custom", published.append)
        )
        assert result.returncode == 0 and result.completed is True
    assert len(published) >= 2
    view = native_projection(published[-1].cost_records)
    assert not view["complete"]
    if not failure:
        assert (
            "input_tokens"
            not in next(r for r in view["children"] if r["session_id"] == CHILD)["usage"]
        )


def test_accounting_publication_failure_does_not_change_terminal_or_exit(
    tmp_path, monkeypatch, provider_process
):
    supply_native(provider_process, monkeypatch, tmp_path / "native")

    def fail(_usage):
        raise OSError("unavailable sink")

    result = transport(AgentCLI.CODEX).acquire_session(
        "work", accounting_scope=AccountingScope("f", "custom", fail)
    )
    assert result.returncode == 0 and result.completed is True
    assert len(native_projection(result.usage.cost_records)["children"]) == 2


def test_manager_public_call_stays_out_of_worker_report_and_retains_children(
    tmp_path, monkeypatch, provider_process
):
    root, issue = cost_journey(tmp_path)
    supply_native(provider_process, monkeypatch, tmp_path / "native")
    sink = manager_usage_sink(root, "topic", "wf", "callback")
    result = accounted_call(
        sink, "event-attempt", transport(AgentCLI.CODEX).acquire_session, "work", on_usage=sink
    )
    assert result.completed is True
    assert "native_usage" not in worker_cost(root, issue, "topic", "wf")
    report = inclusive_report(root, "topic", "wf", issue_dir=issue)
    assert report["manager"]["native_usage"]["child_tokens"]["input_tokens"] == 150
    preserve_worker_cost(root, issue, "topic", "wf")
    import shutil

    shutil.rmtree(issue)
    retained = inclusive_report(root, "topic", "wf")
    assert retained["manager"]["native_usage"]["child_tokens"]["input_tokens"] == 150
    before = CostStore(root, "topic", "wf").path.read_bytes()
    inclusive_report(root, "topic", "wf")
    assert CostStore(root, "topic", "wf").path.read_bytes() == before


@pytest.mark.parametrize("chain_kind", ["single", "retry", "fallback"])
def test_worker_attempt_chain_forwarding_retains_native_usage(
    tmp_path, monkeypatch, provider_process, chain_kind
):
    import uuid

    from cafe.agents.manager import AgentManager
    from cafe.core.session import SessionManager
    from cafe.core.types import AgentConfig, CliEntry

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CAFE_MOCK_AGENTS", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "native"))
    monkeypatch.setenv("CAFE_PRICING_AUTO_UPDATE", "0")
    monkeypatch.setattr("time.sleep", lambda _seconds: None)  # External delay boundary.
    metadata = tmp_path / ".cafe/issues/topic/custom-worker/iteration_001/iteration.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps(dict(iteration=1)))
    sink = iteration_usage_sink(tmp_path, metadata)
    launch = provider_process([])
    attempts = []

    def invoke(command, **_kwargs):
        number = len(attempts) + 1
        attempts.append(command)
        process = MagicMock()
        fail = number == 1 and chain_kind != "single"
        cli = "codex" if command[0] == "codex" else "claude"
        if cli == "codex":
            parent = next(
                (v for v in command if len(v) == 36 and v.count("-") == 4),
                str(uuid.UUID(int=number * 100)),
            )
            child = str(uuid.UUID(int=number * 100 + 1))
            root_path = tmp_path / "native/sessions" / f"rollout-{parent}.jsonl"
            if root_path.exists():
                now = datetime.now(timezone.utc).isoformat()
                with root_path.open("a") as handle:
                    for row in [
                        dict(
                            type="event_msg", payload=dict(type="task_started", turn_id="resumed")
                        ),
                        dict(
                            type="turn_context",
                            payload=dict(turn_id="resumed", model="actual-child-model"),
                        ),
                        dict(
                            type="event_msg",
                            payload=dict(
                                type="token_count", info=dict(total_token_usage=counters(20))
                            ),
                        ),
                        dict(
                            type="event_msg", payload=dict(type="task_complete", turn_id="resumed")
                        ),
                    ]:
                        handle.write(json.dumps(dict(timestamp=now, **row)) + "\n")
            else:
                native_journal(tmp_path / "native", parent, n=10)
            native_journal(tmp_path / "native", child, parent)
            rows = [dict(type="thread.started", thread_id=parent)]
            if fail:
                rows.append(
                    dict(
                        type="error",
                        message="Rate limit exceeded" if chain_kind == "retry" else "unknown model",
                    )
                )
            else:
                rows.append(
                    dict(
                        type="turn.completed",
                        usage=dict(input_tokens=20 if number > 1 else 10, output_tokens=2),
                    )
                )
        else:
            rows = [
                dict(type="system", subtype="init", session_id="claude-session"),
                dict(type="result", content="done", usage=dict(input_tokens=3, output_tokens=1)),
            ]
        process.stdout.readline.side_effect = [json.dumps(row) + "\n" for row in rows] + [""]
        process.stderr.read.return_value = (
            ("rate limit exceeded" if chain_kind == "retry" else "unknown model") if fail else ""
        )
        process.poll.return_value = None

        def wait(**_kwargs):
            process.poll.return_value = 1 if fail else 0
            return process.poll.return_value

        process.wait.side_effect = wait
        return process

    launch.side_effect = invoke
    manager = AgentManager(
        session_manager=SessionManager(str(tmp_path / "sessions")),
        issue_name="topic",
        stream_agent_output=False,
    )
    chain = [CliEntry(cli=AgentCLI.CODEX)]
    if chain_kind == "fallback":
        chain.append(CliEntry(cli=AgentCLI.CLAUDE))
    manager.register_agent(AgentConfig(name="custom-worker", cli=AgentCLI.CODEX, clis=chain))
    _reply, usage, *_ = manager.execute(
        "custom-worker",
        "work",
        phase_name="custom-worker",
        accounting_scope=AccountingScope("flow", "custom-worker", sink),
    )
    sink(usage)
    stats = json.loads(metadata.read_text())["stats"]
    children = native_projection(stats["cost_records"])["children"]
    assert len(attempts) == (1 if chain_kind == "single" else 2)
    assert len(children) == (2 if chain_kind == "retry" else 1)
    expected = {"single": 110, "retry": 210, "fallback": 103}[chain_kind]
    assert stats["input_tokens"] == expected
    sink(usage)
    assert json.loads(metadata.read_text())["stats"]["input_tokens"] == expected
    if chain_kind == "fallback":
        assert attempts[1][0] == "claude"


def test_custom_workflow_phase_admits_and_persists_native_accounting_before_launch(
    tmp_path, monkeypatch, provider_process
):
    """I1/I7/U10: removal of phase→Manager→executor forwarding loses entry proof."""
    import yaml

    from cafe.agents.manager import AgentManager
    from cafe.core.blackboard import BlackboardStore
    from cafe.core.session import SessionManager
    from cafe.core.types import AgentConfig
    from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
    from tests.unit.test_generic_workflow_step import FakeGitOperations, _build_loader

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cafe.phases.generic_workflow_step.get_repo_root", lambda: tmp_path)
    monkeypatch.setattr("cafe.phases.generic_workflow_step.get_git_toplevel", lambda: tmp_path)
    monkeypatch.delenv("CAFE_MOCK_AGENTS", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "native"))
    monkeypatch.setenv("CAFE_PRICING_AUTO_UPDATE", "0")
    directory = tmp_path / ".cafe/issues/topic"
    phase = directory / "compose/iteration_001"
    cafe = tmp_path / ".cafe"
    cafe.mkdir()
    (cafe / "strategic_context.yaml").write_text("version: 1\n")
    (cafe / "phases.yaml").write_text(
        yaml.safe_dump(
            dict(
                compose=dict(
                    name="custom-worker",
                    role="author",
                    clis=[dict(cli="codex", model="actual-child-model")],
                )
            )
        )
    )
    agent = cafe / "agents/author/custom-worker.md"
    agent.parent.mkdir(parents=True)
    agent.write_text("---\nname: custom-worker\ndescription: test author\n---\nRead the request.\n")
    step = dict(
        skill="cafe-spec",
        role="author",
        output_artifact="brief_note",
        allowed_tools=["Read"],
        valid_intents=["confirmed"],
        on=dict(await_agent="_done"),
    )
    book = dict(
        playbook=dict(id="custom"),
        roles=dict(author=dict(default_agent="custom-worker")),
        steps=dict(compose=step),
    )
    board = BlackboardStore(directory).load_or_create("compose")
    manager = AgentManager(
        session_manager=SessionManager(str(tmp_path / "sessions")),
        issue_name="topic",
        stream_agent_output=False,
    )
    manager.register_agent(AgentConfig(name="custom-worker", cli=AgentCLI.CODEX))
    launch = provider_process(
        [
            dict(type="thread.started", thread_id=ROOT),
            dict(type="item.completed", item=dict(type="agent_message", text="confirmed")),
            dict(type="turn.completed", usage=dict(input_tokens=10, output_tokens=2)),
        ]
    )

    def submit(*_args, **_kwargs):
        data = json.loads((phase / "iteration.json").read_text())
        assert data["stats"]["cost_records"][0]["native_usage"]["status"] == "open"
        native_journal(tmp_path / "native", ROOT, n=10)
        native_journal(tmp_path / "native", CHILD, ROOT)
        (phase / "output.md").write_text("# Report\nConfirmed observations.\n")
        checklist = phase / "checklist.md"
        checklist.write_text(checklist.read_text().replace("[ ]", "[x]"))
        (directory / "next_step.txt").write_text(json.dumps(dict(version=1, intent="await_agent")))
        return launch.return_value

    launch.side_effect = submit
    engine = GenericWorkflowStepExecutor(
        issue_dir=directory,
        issue_name="topic",
        playbook=book,
        generic_phase=_build_loader(tmp_path),
        agent_manager=manager,
        git_ops=FakeGitOperations(),
        role_agent_map=dict(author="custom-worker"),
    )
    result = engine.execute_step("compose", step, board)
    assert result.response == "confirmed"
    stats = json.loads((phase / "iteration.json").read_text())["stats"]
    assert stats["input_tokens"] == 110
    assert native_projection(stats["cost_records"])["children"][0]["session_id"] == CHILD
