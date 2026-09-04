"""Skill-owned event driver binding and session persistence tests."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from cafe.core.types import AgentCLI


def _callback_module():
    path = (
        Path(__file__).parents[2]
        / "src/cafe/data/skills/use-cafe-workflow/scripts/workflow_event_callback.py"
    )
    spec = importlib.util.spec_from_file_location("workflow_event_callback_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _clear_host_session_binding(monkeypatch) -> None:
    """Keep the test process's Codex App thread out of ordinary callback tests."""
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)


def test_event_driver_config_is_per_issue_and_cannot_replace_session(tmp_path: Path) -> None:
    callback = _callback_module()
    issue_dir = tmp_path / ".cafe" / "issues" / "issue456"
    callback.write_config(issue_dir, cli="codex", model="gpt-5.6-sol")

    config = (issue_dir / "driver" / "config.yaml").read_text(encoding="utf-8")
    assert "mode: event-driven" in config
    assert "model: gpt-5.6-sol" in config

    store = callback.EventDriverSessionStore(
        issue_dir / "driver", workflow_id="workflow", cli=AgentCLI.CODEX, model="gpt-5.6-sol"
    )
    store.save_session(callback.DRIVER_AGENT_NAME, AgentCLI.CODEX, "session")
    store.commit()
    with pytest.raises(ValueError, match="cannot change"):
        callback.write_config(issue_dir, cli="codex", model="another-model")


def test_version_three_config_preserves_order_and_exact_shape(tmp_path: Path) -> None:
    callback = _callback_module()
    issue_dir = tmp_path / ".cafe" / "issues" / "issue457"

    callback.write_config(
        issue_dir,
        clis=[("codex", "gpt-exact"), ("claude", "opus-exact"), ("gemini", "pro-exact")],
    )

    config = callback._load_config(issue_dir / "driver")
    assert config == {
        "schema_version": 3,
        "mode": "event-driven",
        "clis": [
            {"cli": "codex", "model": "gpt-exact"},
            {"cli": "claude", "model": "opus-exact"},
            {"cli": "gemini", "model": "pro-exact"},
        ],
    }


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        (
            "schema_version: 1\nmode: event-driven\ncli: claude\nmodel: exact\n",
            {
                "schema_version": 1,
                "mode": "event-driven",
                "cli": "claude",
                "model": "exact",
            },
        ),
        (
            "schema_version: 2\nmode: event-driven\ncli: codex\nmodel: exact\nhost_session: null\n",
            {
                "schema_version": 2,
                "mode": "event-driven",
                "cli": "codex",
                "model": "exact",
            },
        ),
    ],
)
def test_legacy_config_shapes_remain_single_transport_compatible(
    tmp_path: Path, document: str, expected: dict[str, object]
) -> None:
    callback = _callback_module()
    driver_dir = tmp_path / "driver"
    driver_dir.mkdir()
    path = driver_dir / "config.yaml"
    path.write_text(document, encoding="utf-8")

    assert callback._load_config(driver_dir) == expected
    assert path.read_text(encoding="utf-8") == document


@pytest.mark.parametrize(
    "document",
    [
        "schema_version: 3\nmode: event-driven\nclis: []\n",
        "schema_version: 3\nmode: event-driven\nclis: null\n",
        "schema_version: 3\nmode: event-driven\nclis: codex\n",
        "schema_version: 3\nmode: event-driven\nclis:\n  - cli: codex\n",
        "schema_version: 3\nmode: event-driven\nclis:\n  - cli: codex\n    model: x\n    extra: y\n",
        "schema_version: 3\nmode: event-driven\nclis:\n  - cli: codex\n    model: x\n  - cli: codex\n    model: y\n",
        "schema_version: 3\nmode: event-driven\ncli: codex\nmodel: x\nclis:\n  - cli: codex\n    model: x\n",
        "schema_version: 2\nmode: event-driven\ncli: codex\nmodel: x\nclis: []\n",
        "schema_version: 3\nmode: attached\nclis:\n  - cli: codex\n    model: x\n",
        "schema_version: 3\nmode: event-driven\nclis:\n  - cli: codex\n    cli: claude\n    model: x\n",
        "schema_version: true\nmode: event-driven\ncli: codex\nmodel: x\n",
    ],
)
def test_version_three_config_rejects_non_exact_forms(tmp_path: Path, document: str) -> None:
    callback = _callback_module()
    driver_dir = tmp_path / "driver"
    driver_dir.mkdir()
    (driver_dir / "config.yaml").write_text(document, encoding="utf-8")

    with pytest.raises(ValueError):
        callback._load_config(driver_dir)


def test_version_three_host_binding_applies_only_to_first_codex_entry(
    tmp_path: Path, monkeypatch
) -> None:
    callback = _callback_module()
    monkeypatch.setenv("CODEX_THREAD_ID", "runtime-thread")
    issue_dir = tmp_path / ".cafe" / "issues" / "bound"

    callback.write_config(
        issue_dir,
        clis=[("codex", "exact"), ("claude", "fallback")],
    )

    assert callback._load_config(issue_dir / "driver")["host_session"] == {
        "kind": "codex",
        "thread_id": "runtime-thread",
    }
    loaded = yaml.safe_load((issue_dir / "driver" / "config.yaml").read_text())
    loaded["clis"] = [loaded["clis"][1], loaded["clis"][0]]
    (issue_dir / "driver" / "config.yaml").write_text(yaml.safe_dump(loaded))
    with pytest.raises(ValueError, match="host session"):
        callback._load_config(issue_dir / "driver")


def test_version_three_state_binds_immutable_policy_without_legacy_session_files(
    tmp_path: Path,
) -> None:
    callback = _callback_module()
    issue_dir = tmp_path / ".cafe" / "issues" / "state"
    callback.write_config(issue_dir, clis=[("codex", "one"), ("claude", "two")])
    driver_dir = issue_dir / "driver"
    config = callback._load_config(driver_dir)

    state = callback._load_or_initialize_dispatch_state(
        driver_dir, workflow_id="workflow", config=config
    )
    assert state["policy"] == config
    assert state["active_index"] == 0
    assert not (driver_dir / "session.json").exists()
    assert not (driver_dir / "sessions").exists()

    changed = {**config, "clis": [*config["clis"]]}
    changed["clis"][0] = {"cli": "codex", "model": "changed"}
    with pytest.raises(ValueError, match="policy"):
        callback._load_or_initialize_dispatch_state(
            driver_dir, workflow_id="workflow", config=changed
        )
    with pytest.raises(ValueError, match="workflow"):
        callback._load_or_initialize_dispatch_state(
            driver_dir, workflow_id="foreign", config=config
        )


def _v3_event_context(callback, tmp_path: Path, clis: list[tuple[str, str]]):
    from cafe.core.blackboard import BlackboardStore

    issue_dir = tmp_path / ".cafe" / "issues" / "issue457"
    callback.write_config(issue_dir, clis=clis)
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("spec")
    event = store.prepare_workflow_callback_event(
        blackboard,
        {
            "workflow_id": blackboard.workflow_id,
            "issue": issue_dir.name,
            "event_type": "phase_terminal",
            "step": "develop",
            "status_code": "ok",
        },
    )
    driver_dir = issue_dir / "driver"
    state = callback._load_or_initialize_dispatch_state(
        driver_dir,
        workflow_id=blackboard.workflow_id,
        config=callback._load_config(driver_dir),
    )
    state = callback._ensure_dispatch_event(driver_dir, state, event)
    return driver_dir, state, event


@pytest.mark.parametrize("cli", list(AgentCLI))
def test_every_unbound_entry_bootstraps_without_event_authority(
    tmp_path: Path, cli: AgentCLI
) -> None:
    callback = _callback_module()
    driver_dir, state, event = _v3_event_context(callback, tmp_path, [(cli.value, "exact")])
    calls = []

    class FakeExecutor:
        def __init__(self, config, **_kwargs):
            self.config = config

        def execute_event_driver(self, prompt, **kwargs):
            calls.append((self.config, prompt, kwargs))
            return SimpleNamespace(
                session_id=f"{self.config.cli.value}-provider-session",
                records=(),
            )

    updated, outcome = callback._acquire_v3_session(
        driver_dir,
        state,
        event_id=event["event_id"],
        index=0,
        repository_root=tmp_path,
        executor_factory=FakeExecutor,
    )

    assert outcome == "acquired"
    assert calls[0][1] == 'say "HI"'
    assert calls[0][2]["allowed_tools"] == []
    assert calls[0][2]["allowed_directories"] == []
    assert event["event_id"] not in calls[0][1]
    persisted = json.loads((driver_dir / "dispatch_state.json").read_text())
    assert persisted["entries"][0]["session"]["id"] == updated["entries"][0]["session"]["id"]
    assert persisted["events"][event["event_id"]]["attempts"][-1]["stage"] == "bootstrap"
    assert not (driver_dir / "session.json").exists()


def test_bound_or_acquired_session_skips_bootstrap_and_binding_never_spreads(
    tmp_path: Path, monkeypatch
) -> None:
    callback = _callback_module()
    monkeypatch.setenv("CODEX_THREAD_ID", "bound-session")
    driver_dir, state, event = _v3_event_context(
        callback,
        tmp_path,
        [("codex", "exact"), ("claude", "fallback")],
    )

    class ForbiddenExecutor:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("an acquired session must not bootstrap")

    updated, outcome = callback._acquire_v3_session(
        driver_dir,
        state,
        event_id=event["event_id"],
        index=0,
        repository_root=tmp_path,
        executor_factory=ForbiddenExecutor,
    )

    assert outcome == "acquired"
    assert updated["entries"][0]["session"]["id"] == "bound-session"
    assert updated["entries"][1]["session"] is None

    raw = json.loads((driver_dir / "dispatch_state.json").read_text())
    raw["entries"][0]["session"]["id"] = "conflict"
    (driver_dir / "dispatch_state.json").write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="host session"):
        callback._load_or_initialize_dispatch_state(
            driver_dir,
            workflow_id=state["workflow_id"],
            config=state["policy"],
        )


@pytest.mark.parametrize(
    ("error_type", "expected"),
    [
        ("cli_not_found", "conclusive_nonacceptance"),
        ("cli_unavailable", "conclusive_nonacceptance"),
        ("model_not_found", "conclusive_nonacceptance"),
        ("rate_limit", "conclusive_nonacceptance"),
        ("session_not_found", "conclusive_nonacceptance"),
        ("incomplete_stream", "ambiguous"),
        ("timeout", "ambiguous"),
        (None, "ambiguous"),
    ],
)
def test_provider_failure_classification_is_fail_closed(error_type, expected) -> None:
    callback = _callback_module()
    error = callback.AgentExecutionError("provider failed", error_type=error_type)

    assert callback._classify_provider_failure(error) == expected


@pytest.mark.parametrize(
    ("result_or_error", "expected", "recovery_pending"),
    [
        (
            lambda callback: callback.AgentExecutionError(
                "model unavailable", error_type="model_not_found"
            ),
            "conclusive_nonacceptance",
            False,
        ),
        (
            lambda callback: callback.AgentExecutionError(
                "truncated", error_type="incomplete_stream"
            ),
            "ambiguous",
            True,
        ),
        (
            lambda _callback: SimpleNamespace(
                session_id=None,
                records=({"type": "result", "status": "success"},),
            ),
            "conclusive_nonacceptance",
            False,
        ),
        (
            lambda _callback: SimpleNamespace(
                session_id=None,
                records=(
                    {"type": "init", "session_id": "one"},
                    {"type": "init", "session_id": "two"},
                ),
            ),
            "ambiguous",
            True,
        ),
    ],
)
def test_bootstrap_outcomes_never_create_event_delivery(
    tmp_path: Path, result_or_error, expected: str, recovery_pending: bool
) -> None:
    callback = _callback_module()
    driver_dir, state, event = _v3_event_context(callback, tmp_path, [("gemini", "exact")])
    outcome_value = result_or_error(callback)

    class FakeExecutor:
        def __init__(self, *_args, **_kwargs):
            pass

        def execute_event_driver(self, *_args, **_kwargs):
            if isinstance(outcome_value, BaseException):
                raise outcome_value
            return outcome_value

    updated, outcome = callback._acquire_v3_session(
        driver_dir,
        state,
        event_id=event["event_id"],
        index=0,
        repository_root=tmp_path,
        executor_factory=FakeExecutor,
    )

    event_state = updated["events"][event["event_id"]]
    assert outcome == expected
    assert updated["entries"][0]["session"] is None
    assert event_state["accepted_index"] is None
    assert event_state["attempts"][-1]["stage"] == "bootstrap"
    assert event_state["recovery_pending"] is recovery_pending


def test_bootstrap_intent_write_failure_prevents_provider_launch(tmp_path: Path) -> None:
    callback = _callback_module()
    driver_dir, state, event = _v3_event_context(callback, tmp_path, [("cursor-agent", "exact")])
    launched = False

    class FakeExecutor:
        def __init__(self, *_args, **_kwargs):
            nonlocal launched
            launched = True

    with patch.object(callback, "_atomic_write", side_effect=OSError("replace failed")):
        with pytest.raises(OSError):
            callback._acquire_v3_session(
                driver_dir,
                state,
                event_id=event["event_id"],
                index=0,
                repository_root=tmp_path,
                executor_factory=FakeExecutor,
            )

    assert launched is False


def test_session_persistence_failure_never_launches_actual_callback(
    tmp_path: Path,
) -> None:
    callback = _callback_module()
    driver_dir, state, event = _v3_event_context(callback, tmp_path, [("claude", "exact")])
    calls = []

    class FakeExecutor:
        def __init__(self, config, **_kwargs):
            self.config = config

        def execute_event_driver(self, prompt, **_kwargs):
            calls.append(prompt)
            return SimpleNamespace(session_id="provider-session", records=())

    original_atomic_write = callback._atomic_write
    writes = 0

    def fail_second_write(path, payload):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("replace failed")
        original_atomic_write(path, payload)

    with patch.object(callback, "_atomic_write", side_effect=fail_second_write):
        with pytest.raises(OSError):
            callback._acquire_v3_session(
                driver_dir,
                state,
                event_id=event["event_id"],
                index=0,
                repository_root=tmp_path,
                executor_factory=FakeExecutor,
            )

    persisted = callback._load_or_initialize_dispatch_state(
        driver_dir,
        workflow_id=state["workflow_id"],
        config=state["policy"],
    )
    assert persisted["entries"][0]["session"] is None
    assert persisted["events"][event["event_id"]]["attempts"][-1]["status"] == "pending"

    reloaded, outcome = callback._acquire_v3_session(
        driver_dir,
        persisted,
        event_id=event["event_id"],
        index=0,
        repository_root=tmp_path,
        executor_factory=FakeExecutor,
    )
    assert outcome == "ambiguous"
    assert reloaded["entries"][0]["session"] is None
    assert calls == ['say "HI"']


def test_event_driver_session_rejects_a_different_workflow(tmp_path: Path) -> None:
    callback = _callback_module()
    driver_dir = tmp_path / "driver"
    writer = callback.EventDriverSessionStore(
        driver_dir, workflow_id="one", cli=AgentCLI.CODEX, model="exact"
    )
    writer.save_session(callback.DRIVER_AGENT_NAME, AgentCLI.CODEX, "session")
    writer.commit()
    reader = callback.EventDriverSessionStore(
        driver_dir, workflow_id="two", cli=AgentCLI.CODEX, model="exact"
    )
    with pytest.raises(ValueError, match="another workflow"):
        reader.load_session(callback.DRIVER_AGENT_NAME, AgentCLI.CODEX)


def test_event_driver_stages_a_new_session_until_identity_is_verified(tmp_path: Path) -> None:
    callback = _callback_module()
    store = callback.EventDriverSessionStore(
        tmp_path / "driver", workflow_id="one", cli=AgentCLI.CODEX, model="exact"
    )

    store.save_session(callback.DRIVER_AGENT_NAME, AgentCLI.CODEX, "session")

    assert not store.path.exists()
    store.commit()
    assert store.load_session(callback.DRIVER_AGENT_NAME, AgentCLI.CODEX).session_id == "session"


def test_event_driver_refuses_callbacks_without_a_process_lock(tmp_path: Path) -> None:
    callback = _callback_module()
    callback.fcntl = None
    callback.msvcrt = None

    with pytest.raises(RuntimeError, match="cross-process file locking"):
        with callback._session_lock(tmp_path / "driver"):
            pass


def test_callback_acquires_then_exactly_resumes_its_session(tmp_path: Path, monkeypatch) -> None:
    callback = _callback_module()
    issue_dir = tmp_path / ".cafe" / "issues" / "issue456"
    callback.write_config(issue_dir, cli="codex", model="exact")
    from cafe.core.blackboard import BlackboardStore

    workflow_id = BlackboardStore(issue_dir).load_or_create("spec").workflow_id
    continuations = []

    class FakeManager:
        def __init__(self, *, session_manager, **_kwargs):
            self.session_manager = session_manager
            self._last_cli = AgentCLI.CODEX
            self._last_reported_model = None  # Codex does not report this in standard JSONL.
            self._last_session_id = "session-1"

        def register_agent(self, _config):
            pass

        def execute(self, _name, _prompt, *, continuation, **_kwargs):
            continuations.append(continuation)
            self.session_manager.save_session(
                callback.DRIVER_AGENT_NAME, AgentCLI.CODEX, "session-1"
            )

    monkeypatch.setattr(callback, "AgentManager", FakeManager)
    event = {"issue": "issue456", "workflow_id": workflow_id, "event_type": "phase_terminal"}
    callback.run_callback(event, repository_root=tmp_path)
    callback.run_callback(event, repository_root=tmp_path)

    assert continuations[0].is_exact is False
    assert continuations[1].is_exact is True
    assert continuations[1].session_id == "session-1"


def test_callback_queues_the_bound_codex_host_thread(tmp_path: Path, monkeypatch) -> None:
    callback = _callback_module()
    monkeypatch.setenv("CODEX_THREAD_ID", "visible-thread")
    issue_dir = tmp_path / ".cafe" / "issues" / "issue456"
    callback.write_config(issue_dir, cli="codex", model="exact")
    assert callback._load_config(issue_dir / "driver")["host_session"] == {
        "kind": "codex",
        "thread_id": "visible-thread",
    }

    from cafe.core.blackboard import BlackboardStore

    workflow_id = BlackboardStore(issue_dir).load_or_create("spec").workflow_id
    event = {"issue": "issue456", "workflow_id": workflow_id, "event_type": "human_task"}
    with patch.object(callback.subprocess, "run") as run:
        callback.run_callback(event, repository_root=tmp_path)

    command = run.call_args.args[0]
    assert command[:4] == ["codex", "queue", "--thread", "visible-thread"]
    assert command[command.index("--model") + 1] == "exact"
    assert command[command.index("--cd") + 1] == str(tmp_path)
    prompt = command[command.index("--message") + 1]
    assert "event-driven CAFE workflow driver" in prompt
    assert '"event_type": "human_task"' in prompt
    assert run.call_args.kwargs == {
        "check": True,
        "capture_output": True,
        "text": True,
        "timeout": 30,
    }
    stored = callback.EventDriverSessionStore(
        issue_dir / "driver", workflow_id=workflow_id, cli=AgentCLI.CODEX, model="exact"
    ).load_session(callback.DRIVER_AGENT_NAME, AgentCLI.CODEX)
    assert stored is not None
    assert stored.session_id == "visible-thread"


def test_bound_host_thread_queue_failure_never_creates_a_new_session(
    tmp_path: Path, monkeypatch
) -> None:
    callback = _callback_module()
    monkeypatch.setenv("CODEX_THREAD_ID", "visible-thread")
    issue_dir = tmp_path / ".cafe" / "issues" / "issue456"
    callback.write_config(issue_dir, cli="codex", model="exact")
    from cafe.core.blackboard import BlackboardStore

    workflow_id = BlackboardStore(issue_dir).load_or_create("spec").workflow_id
    failure = subprocess.CalledProcessError(1, ["codex", "queue"], stderr="not found")
    with patch.object(callback.subprocess, "run", side_effect=failure) as run:
        with pytest.raises(subprocess.CalledProcessError):
            callback.run_callback(
                {
                    "issue": "issue456",
                    "workflow_id": workflow_id,
                    "event_type": "human_task",
                },
                repository_root=tmp_path,
            )

    assert run.call_count == 1
    assert not (issue_dir / "driver" / "session.json").exists()


def test_callback_identity_mismatch_keeps_existing_session(tmp_path: Path, monkeypatch) -> None:
    callback = _callback_module()
    issue_dir = tmp_path / ".cafe" / "issues" / "issue456"
    callback.write_config(issue_dir, cli="codex", model="exact")
    from cafe.core.blackboard import BlackboardStore

    workflow_id = BlackboardStore(issue_dir).load_or_create("spec").workflow_id
    store = callback.EventDriverSessionStore(
        issue_dir / "driver", workflow_id=workflow_id, cli=AgentCLI.CODEX, model="exact"
    )
    store.save_session(callback.DRIVER_AGENT_NAME, AgentCLI.CODEX, "existing")
    store.commit()
    original = store.path.read_bytes()

    class FakeManager:
        def __init__(self, *, session_manager, **_kwargs):
            self.session_manager = session_manager
            self._last_cli = AgentCLI.CODEX
            self._last_reported_model = "wrong"
            self._last_session_id = "existing"

        def register_agent(self, _config):
            pass

        def execute(self, *_args, **_kwargs):
            self.session_manager.save_session(
                callback.DRIVER_AGENT_NAME, AgentCLI.CODEX, "existing"
            )

    monkeypatch.setattr(callback, "AgentManager", FakeManager)
    with pytest.raises(ValueError, match="identity mismatch"):
        callback.run_callback(
            {"issue": "issue456", "workflow_id": workflow_id, "event_type": "phase_terminal"},
            repository_root=tmp_path,
        )
    assert store.path.read_bytes() == original


def test_callback_session_conflict_keeps_existing_session(tmp_path: Path, monkeypatch) -> None:
    callback = _callback_module()
    issue_dir = tmp_path / ".cafe" / "issues" / "issue456"
    callback.write_config(issue_dir, cli="codex", model="exact")
    from cafe.agents.executor import AgentExecutionError
    from cafe.core.blackboard import BlackboardStore

    workflow_id = BlackboardStore(issue_dir).load_or_create("spec").workflow_id
    store = callback.EventDriverSessionStore(
        issue_dir / "driver", workflow_id=workflow_id, cli=AgentCLI.CODEX, model="exact"
    )
    store.save_session(callback.DRIVER_AGENT_NAME, AgentCLI.CODEX, "existing")
    store.commit()
    original = store.path.read_bytes()

    class FakeManager:
        def __init__(self, **_kwargs):
            pass

        def register_agent(self, _config):
            pass

        def execute(self, *_args, **_kwargs):
            raise AgentExecutionError("conflict", error_type="SESSION_CONFLICT")

    monkeypatch.setattr(callback, "AgentManager", FakeManager)
    with pytest.raises(AgentExecutionError, match="conflict"):
        callback.run_callback(
            {"issue": "issue456", "workflow_id": workflow_id, "event_type": "phase_terminal"},
            repository_root=tmp_path,
        )
    assert store.path.read_bytes() == original
