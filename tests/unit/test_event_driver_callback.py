"""Skill-owned event driver binding and session persistence tests."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import struct
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

from cafe.core.packet_io import canonical_json
from cafe.agents.executor import AgentExecutor, EventDriverExecutionResult
from cafe.agents.transport_types import TransportResult
from cafe.core.types import AgentCLI, AgentResponse, TokenUsage
from tests.fixtures.delivery_contract import delivery_contract, legacy_driver_contract


def _executor_result(*, session_id=None, accepted=False, records=()):
    """Compact evidence supplied by the executor boundary in caller-policy tests.

    Real provider parsing is covered by the transport composition journeys.
    Keep conflicting fixture evidence explicit rather than discarding it.
    """
    observed = {record[field] for record in records
                for field in ("session_id", "sessionId", "thread_id")
                if isinstance(record.get(field), str)}
    conflicting = len(observed) > 1 or (session_id and observed and observed != {session_id})
    return EventDriverExecutionResult(
        session_id=session_id, accepted=accepted, event_id=None, records=tuple(records),
        transport_result=TransportResult(
            observed_session_id=session_id, accepted=accepted, completed=True, returncode=0,
            failure_code="conflicting_session_evidence" if conflicting else None,
        ),
    )


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


def _prepare_issue(issue_dir: Path):
    from cafe.core.blackboard import BlackboardStore

    return BlackboardStore(issue_dir).load_or_create("spec")


def _activate_event_contract(
    issue_dir: Path, *, workflow_id: str, clis: list[tuple[str, str]]
) -> None:
    """Create the complete Driver authority required by public callback tests."""
    from cafe.driver import ActivateConfirmedContract, activate_confirmed_contract

    phase_clis = [{"cli": cli, "model": model} for cli, model in clis]
    driver_clis = [
        {"cli": cli} if index == 0 else {"cli": cli, "model": model}
        for index, (cli, model) in enumerate(clis)
    ]
    proposal: dict[str, object] = {
        "delivery_contract": delivery_contract(),
        "locales": {"conversation": {"value": "en", "source": "test"}},
        "confirmation_contract": {
            "user_required": ["spec", "plan"],
            "driver_confirmable": [],
            "mandatory_human_stops": ["spec", "plan"],
        },
        "reactive_user_handoffs": {
            "need_clarification": "user_required",
            "need_permission": "user_required",
            "alignment_checkpoint": "driver_resolvable_when_clear",
        },
        "phases": [
            {
                "name": "develop",
                "chain": phase_clis,
            }
        ],
        "proactive_review": {
            "phase_decisions": [
                {
                    "phase": "develop",
                    "decision": "not_required",
                }
            ]
        },
        "driver": {"mode": "event-driven", "clis": driver_clis},
        "checkout": {"kind": "current_checkout"},
    }
    activate_confirmed_contract(
        ActivateConfirmedContract(
            issue_dir=issue_dir,
            issue_name=issue_dir.name,
            workflow_id=workflow_id,
            confirmed_by="user",
            confirmed_at=datetime(2026, 9, 6, 2, tzinfo=timezone.utc),
            proposal=proposal,
        )
    )


@pytest.fixture(autouse=True)
def _clear_host_session_binding(monkeypatch) -> None:
    """Keep the test process's Codex App thread out of ordinary callback tests."""
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)


def test_callback_prompt_allows_only_bounded_driver_confirmable_clarification(
    tmp_path: Path,
) -> None:
    callback = _callback_module()
    prompt = callback._callback_prompt(
        {"event_id": "event-1", "event_type": "human_task"},
        repository_root=tmp_path,
    )

    assert "confirmed overall need_clarification policy" in prompt
    assert "Explicit task ownership overrides the overall policy" in prompt
    assert "within confirmed scope, constraints and authority" in prompt
    assert "trigger no contract deviation" in prompt
    assert "otherwise leave it for the user" in prompt
    assert "Do not answer mandatory, user-required, permission, or capability tasks" in prompt
    assert "user-required, clarification" not in prompt


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
    blackboard = _prepare_issue(issue_dir)

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
    state = callback._load_or_initialize_dispatch_state(
        issue_dir / "driver",
        workflow_id=blackboard.workflow_id,
        config=config,
    )
    assert state["workflow_id"] == blackboard.workflow_id
    assert state["policy"] == config


def test_version_three_config_cannot_change_before_first_callback(tmp_path: Path) -> None:
    callback = _callback_module()
    issue_dir = tmp_path / ".cafe" / "issues" / "immutable-policy"
    blackboard = _prepare_issue(issue_dir)
    callback.write_config(
        issue_dir,
        clis=[("codex", "primary"), ("claude", "fallback")],
    )

    with pytest.raises(ValueError, match="cannot change"):
        callback.write_config(issue_dir, clis=[("gemini", "replacement")])

    config = callback._load_config(issue_dir / "driver")
    state = callback._load_or_initialize_dispatch_state(
        issue_dir / "driver",
        workflow_id=blackboard.workflow_id,
        config=config,
    )
    assert state["policy"]["clis"] == [
        {"cli": "codex", "model": "primary"},
        {"cli": "claude", "model": "fallback"},
    ]


def test_version_three_config_requires_a_prepared_workflow(tmp_path: Path) -> None:
    callback = _callback_module()
    issue_dir = tmp_path / ".cafe" / "issues" / "not-prepared"

    with pytest.raises(ValueError, match="prepared workflow"):
        callback.write_config(issue_dir, clis=[("codex", "primary")])

    assert not (issue_dir / "driver" / "config.yaml").exists()
    assert not (issue_dir / "driver" / "dispatch_state.json").exists()


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
        (
            "schema_version: 3\nmode: event-driven\nclis:\n"
            "  - cli: codex\n    model: x\n    extra: y\n"
        ),
        (
            "schema_version: 3\nmode: event-driven\nclis:\n"
            "  - cli: codex\n    model: x\n"
            "  - cli: codex\n    model: y\n"
        ),
        (
            "schema_version: 3\nmode: event-driven\ncli: codex\nmodel: x\nclis:\n"
            "  - cli: codex\n    model: x\n"
        ),
        "schema_version: 2\nmode: event-driven\ncli: codex\nmodel: x\nclis: []\n",
        "schema_version: 3\nmode: attached\nclis:\n  - cli: codex\n    model: x\n",
        (
            "schema_version: 3\nmode: event-driven\nclis:\n"
            "  - cli: codex\n    cli: claude\n    model: x\n"
        ),
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


def test_callback_config_reader_rejects_oversized_input_before_parsing(tmp_path: Path) -> None:
    callback = _callback_module()
    driver_dir = tmp_path / "driver"
    driver_dir.mkdir()
    (driver_dir / "config.yaml").write_bytes(b"x" * (callback.MAX_CALLBACK_INPUT_BYTES + 1))

    with pytest.raises(ValueError, match="maximum bounded size"):
        callback._load_config(driver_dir)


def test_version_three_host_binding_applies_only_to_first_codex_entry(
    tmp_path: Path, monkeypatch
) -> None:
    callback = _callback_module()
    monkeypatch.setenv("CODEX_THREAD_ID", "runtime-thread")
    issue_dir = tmp_path / ".cafe" / "issues" / "bound"
    _prepare_issue(issue_dir)

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
    blackboard = _prepare_issue(issue_dir)
    callback.write_config(issue_dir, clis=[("codex", "one"), ("claude", "two")])
    driver_dir = issue_dir / "driver"
    config = callback._load_config(driver_dir)

    state = callback._load_or_initialize_dispatch_state(
        driver_dir, workflow_id=blackboard.workflow_id, config=config
    )
    assert state["policy"] == config
    assert state["active_index"] == 0
    assert not (driver_dir / "session.json").exists()
    assert not (driver_dir / "sessions").exists()

    changed = {**config, "clis": [*config["clis"]]}
    changed["clis"][0] = {"cli": "codex", "model": "changed"}
    with pytest.raises(ValueError, match="policy"):
        callback._load_or_initialize_dispatch_state(
            driver_dir, workflow_id=blackboard.workflow_id, config=changed
        )
    with pytest.raises(ValueError, match="workflow"):
        callback._load_or_initialize_dispatch_state(
            driver_dir, workflow_id="foreign", config=config
        )


def test_version_three_state_rejects_accepted_event_without_accepted_attempt(
    tmp_path: Path,
) -> None:
    callback = _callback_module()
    driver_dir, state, event = _v3_event_context(callback, tmp_path, [("codex", "one")])
    invalid = json.loads(json.dumps(state))
    invalid_event = invalid["events"][event["event_id"]]
    invalid_event["status"] = "accepted"
    invalid_event["accepted_index"] = 0
    (driver_dir / "dispatch_state.json").write_text(json.dumps(invalid), encoding="utf-8")

    with pytest.raises(ValueError, match="event"):
        callback._load_or_initialize_dispatch_state(
            driver_dir,
            workflow_id=state["workflow_id"],
            config=state["policy"],
        )


def test_historical_attempt_session_does_not_pin_current_callback_session() -> None:
    callback = _callback_module()
    attempt = {
        "index": 0,
        "stage": "delivery",
        "status": "accepted",
        "outcome": "durable_acceptance",
        "reason": "provider_acknowledgement",
        "session_id": "historical-session",
        "started_at": "2026-09-09T00:00:00+00:00",
        "finished_at": "2026-09-09T00:00:01+00:00",
    }
    entries = [
        {
            "index": 0,
            "session": {
                "id": "current-session",
                "source": "host_session",
                "acquired_at": "2026-09-10T00:00:00+00:00",
            },
        }
    ]

    assert callback._validate_dispatch_attempt(attempt, route_length=len(entries)) == (
        0,
        "delivery",
        "accepted",
    )


@pytest.mark.parametrize(
    "corruption",
    ["status", "accepted_index", "attempt_history", "takeover", "recovery"],
)
def test_version_three_state_rejects_inconsistent_event_transitions(
    tmp_path: Path,
    corruption: str,
) -> None:
    callback = _callback_module()
    driver_dir, state, event = _v3_event_context(
        callback, tmp_path, [("codex", "one"), ("claude", "two")]
    )
    for index, session_id in enumerate(("codex-session", "claude-session")):
        state["entries"][index]["session"] = {
            "id": session_id,
            "source": "provider",
            "acquired_at": "2026-09-04T00:00:00+00:00",
        }

    class FakeExecutor(AgentExecutor):
        def __init__(self, config, **_kwargs):
            super().__init__(config, stream_output=False)
            self.config = config

        def execute_event_driver(self, _prompt, **_kwargs):
            if self.config.cli is AgentCLI.CODEX:
                raise callback.AgentExecutionError("rejected", error_type="transport_rejected")
            return _executor_result(session_id="claude-session", accepted=True, records=())

    accepted = callback._run_v3_callback(
        driver_dir,
        state,
        event,
        repository_root=tmp_path,
        executor_factory=FakeExecutor,
    )
    invalid = json.loads(json.dumps(accepted))
    event_state = invalid["events"][event["event_id"]]
    if corruption == "status":
        event_state["status"] = "routing"
    elif corruption == "accepted_index":
        event_state["accepted_index"] = 0
    elif corruption == "attempt_history":
        event_state["attempts"] = []
    elif corruption == "takeover":
        event_state["takeover"]["eligible_reason"] = "unrelated"
    else:
        event_state["recovery_pending"] = True
    (driver_dir / "dispatch_state.json").write_text(json.dumps(invalid), encoding="utf-8")

    with pytest.raises(ValueError):
        callback._load_or_initialize_dispatch_state(
            driver_dir,
            workflow_id=state["workflow_id"],
            config=state["policy"],
        )


def _v3_event_context(callback, tmp_path: Path, clis: list[tuple[str, str]]):
    from cafe.core.blackboard import BlackboardStore

    issue_dir = tmp_path / ".cafe" / "issues" / "issue457"
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("spec")
    callback.write_config(issue_dir, clis=clis)
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


def _contract_event_context(
    callback,
    tmp_path: Path,
    clis: list[tuple[str, str]],
    *,
    issue_name: str = "issue457",
    event_type: str = "phase_terminal",
    bind_host: bool = False,
):
    from cafe.core.blackboard import BlackboardStore

    issue_dir = tmp_path / ".cafe" / "issues" / issue_name
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("spec")
    if bind_host:
        callback.activate_confirmed_contract_with_host_session(
            issue_dir=issue_dir,
            issue_name=issue_dir.name,
            workflow_id=blackboard.workflow_id,
            activate_contract=lambda: _activate_event_contract(
                issue_dir, workflow_id=blackboard.workflow_id, clis=clis
            ),
        )
    else:
        _activate_event_contract(issue_dir, workflow_id=blackboard.workflow_id, clis=clis)
    event = store.prepare_workflow_callback_event(
        blackboard,
        {
            "workflow_id": blackboard.workflow_id,
            "issue": issue_dir.name,
            "event_type": event_type,
            "step": "develop",
            "status_code": "ok",
        },
    )
    driver_dir = issue_dir / "driver"
    config = callback._contract_callback_config(
        issue_dir=issue_dir,
        issue_name=issue_dir.name,
        workflow_id=blackboard.workflow_id,
    )
    state = callback._load_or_initialize_dispatch_state(
        driver_dir,
        workflow_id=blackboard.workflow_id,
        config=config,
    )
    state = callback._ensure_dispatch_event(driver_dir, state, event)
    return driver_dir, state, event


def test_contract_chain_update_keeps_sessions_and_historical_routes(tmp_path: Path) -> None:
    callback = _callback_module()
    driver_dir, state, event = _contract_event_context(
        callback,
        tmp_path,
        [("codex", "implicit"), ("claude", "one"), ("gemini", "two")],
    )
    for index, name in enumerate(("codex", "claude", "gemini")):
        state["entries"][index]["session"] = {
            "id": f"{name}-session",
            "source": "provider",
            "acquired_at": "2026-09-07T00:00:00+00:00",
        }
    state["active_index"] = 2
    state["events"][event["event_id"]]["starting_index"] = 2
    state = callback._append_pending_attempt(
        driver_dir, state, event_id=event["event_id"], index=2, stage="delivery"
    )
    state = callback._accept_delivery(
        driver_dir, state, event_id=event["event_id"], index=2, session_id="gemini-session"
    )
    config = callback._contract_callback_config(
        issue_dir=driver_dir.parent,
        issue_name=driver_dir.parent.name,
        workflow_id=state["workflow_id"],
    )
    same = callback._load_or_initialize_dispatch_state(
        driver_dir, workflow_id=state["workflow_id"], config=config
    )
    assert same["active_index"] == 2
    assert [entry["session"]["id"] for entry in same["entries"]] == [
        "codex-session",
        "claude-session",
        "gemini-session",
    ]
    reordered = {
        **config,
        "clis": [
            {"cli": "codex"},
            {"cli": "gemini", "model": "two"},
            {"cli": "claude", "model": "one"},
        ],
    }
    loaded = callback._load_or_initialize_dispatch_state(
        driver_dir, workflow_id=state["workflow_id"], config=reordered
    )
    assert [entry["session"]["id"] for entry in loaded["entries"]] == [
        "codex-session",
        "gemini-session",
        "claude-session",
    ]
    assert loaded["active_index"] == 0
    assert callback._project_v3_events(loaded)[0]["attempts"][0]["cli"] == "gemini"

    removed = {**config, "clis": [{"cli": "codex"}]}
    loaded = callback._load_or_initialize_dispatch_state(
        driver_dir, workflow_id=state["workflow_id"], config=removed
    )
    assert len(loaded["entries"]) == 1
    assert loaded["events"][event["event_id"]]["accepted_index"] == 2
    assert callback._project_v3_events(loaded)[0]["attempts"][0]["cli"] == "gemini"
    with patch.object(callback, "_contract_callback_config", return_value=removed):
        status = callback.read_status(driver_dir.parent)
    assert len(status["entries"]) == 1
    assert status["events"][0]["attempts"][0]["cli"] == "gemini"


def test_contract_dispatch_loads_legacy_transport_snapshot(tmp_path: Path) -> None:
    callback = _callback_module()
    driver_dir, state, event = _contract_event_context(
        callback, tmp_path, [("codex", "implicit")]
    )
    state["transport_clis"] = [{"cli": "codex"}]
    callback._write_dispatch_state(driver_dir, state)
    config = callback._contract_callback_config(
        issue_dir=driver_dir.parent,
        issue_name=driver_dir.parent.name,
        workflow_id=state["workflow_id"],
    )

    loaded = callback._load_or_initialize_dispatch_state(
        driver_dir, workflow_id=state["workflow_id"], config=config
    )

    assert "transport_clis" not in loaded
    assert event["event_id"] in loaded["events"]
    assert loaded["entries"][0]["session"] == state["entries"][0]["session"]
    callback._write_dispatch_state(driver_dir, loaded)
    assert "transport_clis" not in json.loads(
        (driver_dir / "dispatch_state.json").read_text()
    )


def test_unknown_legacy_sessions_are_rebuilt_without_misrouting_host(tmp_path: Path) -> None:
    callback = _callback_module()
    driver_dir, state, event = _contract_event_context(
        callback, tmp_path, [("codex", "implicit"), ("claude", "one")]
    )
    state["entries"][0]["session"] = {
        "id": "old-codex-host",
        "source": "host_session",
        "acquired_at": "2026-09-07T00:00:00+00:00",
    }
    state["entries"][1]["session"] = {
        "id": "unknown-provider",
        "source": "provider",
        "acquired_at": "2026-09-07T00:00:00+00:00",
    }
    state["entries"] = [
        {"index": entry["index"], "session": entry["session"]} for entry in state["entries"]
    ]
    del state["events"][event["event_id"]]["routing_chain"]
    (driver_dir / "dispatch_state.json").write_text(json.dumps(state), encoding="utf-8")
    config = callback._contract_callback_config(
        issue_dir=driver_dir.parent,
        issue_name=driver_dir.parent.name,
        workflow_id=state["workflow_id"],
    )
    changed = {**config, "clis": [{"cli": "claude"}, {"cli": "codex", "model": "one"}]}
    loaded = callback._load_or_initialize_dispatch_state(
        driver_dir, workflow_id=state["workflow_id"], config=changed
    )
    assert [entry["session"] for entry in loaded["entries"]] == [None, None]
    assert event["event_id"] in loaded["events"]

    from cafe.core.blackboard import BlackboardStore

    store = BlackboardStore(driver_dir.parent)
    later_event = store.prepare_workflow_callback_event(
        store.load_or_create("spec"),
        {
            "workflow_id": state["workflow_id"],
            "issue": driver_dir.parent.name,
            "event_type": "workflow_completed",
            "step": "review",
            "status_code": "ok",
        },
    )
    loaded = callback._ensure_dispatch_event(driver_dir, loaded, later_event)

    class FakeExecutor(AgentExecutor):
        def __init__(self, config, **_kwargs):
            super().__init__(config, stream_output=False)
            assert config.cli == AgentCLI.CLAUDE

        def execute_event_driver(self, _prompt, **kwargs):
            session_id = kwargs.get("expected_session_id")
            return _executor_result(
                session_id=session_id or "new-claude-session",
                accepted=session_id is not None,
                records=(),
            )

    with patch.object(callback, "_queue_host_callback", side_effect=AssertionError("wrong CLI")):
        delivered = callback._run_v3_callback(
            driver_dir,
            loaded,
            later_event,
            repository_root=tmp_path,
            executor_factory=FakeExecutor,
        )
    assert delivered["events"][later_event["event_id"]]["status"] == "accepted"
    assert delivered["entries"][0]["session"]["id"] == "new-claude-session"


def test_changed_chain_does_not_continue_an_attempt_at_an_old_index(tmp_path: Path) -> None:
    callback = _callback_module()
    driver_dir, state, event = _contract_event_context(callback, tmp_path, [("codex", "implicit")])
    state = callback._append_pending_attempt(
        driver_dir, state, event_id=event["event_id"], index=0, stage="bootstrap"
    )
    state = callback._finish_pending_attempt(
        driver_dir,
        state,
        event_id=event["event_id"],
        status="failed",
        outcome="conclusive_nonacceptance",
        reason="cli_unavailable",
    )
    config = callback._contract_callback_config(
        issue_dir=driver_dir.parent,
        issue_name=driver_dir.parent.name,
        workflow_id=state["workflow_id"],
    )
    expanded = {
        **config,
        "clis": [{"cli": "codex"}, {"cli": "claude", "model": "one"}],
    }
    loaded = callback._load_or_initialize_dispatch_state(
        driver_dir, workflow_id=state["workflow_id"], config=expanded
    )

    def unexpected_executor(*_args, **_kwargs):
        raise AssertionError("old index was reused")

    assert (
        callback._run_v3_callback(
            driver_dir,
            loaded,
            event,
            repository_root=tmp_path,
            executor_factory=unexpected_executor,
        )
        == loaded
    )
    assert (
        loaded["events"][event["event_id"]]["attempts"]
        == state["events"][event["event_id"]]["attempts"]
    )

    from cafe.core.blackboard import BlackboardStore

    store = BlackboardStore(driver_dir.parent)
    later_event = store.prepare_workflow_callback_event(
        store.load_or_create("spec"),
        {
            "workflow_id": state["workflow_id"],
            "issue": driver_dir.parent.name,
            "event_type": "workflow_completed",
            "step": "review",
            "status_code": "ok",
        },
    )
    later = callback._ensure_dispatch_event(driver_dir, loaded, later_event)
    assert later["events"][later_event["event_id"]]["routing_chain"] == [
        {"cli": "codex", "model": None},
        {"cli": "claude", "model": "one"},
    ]


def test_returning_to_an_old_chain_does_not_repeat_acquired_bootstrap(tmp_path: Path) -> None:
    callback = _callback_module()
    driver_dir, state, event = _contract_event_context(callback, tmp_path, [("codex", "implicit")])
    state = callback._append_pending_attempt(
        driver_dir, state, event_id=event["event_id"], index=0, stage="bootstrap"
    )
    state["entries"][0]["session"] = {
        "id": "old-codex-session",
        "source": "provider",
        "acquired_at": "2026-09-07T00:00:00+00:00",
    }
    state["events"][event["event_id"]]["attempts"][-1].update(
        status="acquired",
        outcome="session_acquired",
        reason="provider_evidence",
        session_id="old-codex-session",
        finished_at="2026-09-07T00:00:01+00:00",
    )
    callback._write_dispatch_state(driver_dir, state)
    config = callback._contract_callback_config(
        issue_dir=driver_dir.parent,
        issue_name=driver_dir.parent.name,
        workflow_id=state["workflow_id"],
    )
    other = {**config, "clis": [{"cli": "claude"}]}
    changed = callback._load_or_initialize_dispatch_state(
        driver_dir, workflow_id=state["workflow_id"], config=other
    )
    callback._write_dispatch_state(driver_dir, changed)
    restored = callback._load_or_initialize_dispatch_state(
        driver_dir, workflow_id=state["workflow_id"], config=config
    )
    assert restored["entries"][0]["session"] is None
    before = (driver_dir / "dispatch_state.json").read_bytes()
    assert (
        callback._run_v3_callback(
            driver_dir,
            restored,
            event,
            repository_root=tmp_path,
            executor_factory=lambda *_args, **_kwargs: pytest.fail("repeated bootstrap"),
        )
        == restored
    )
    assert (driver_dir / "dispatch_state.json").read_bytes() == before
    callback._load_or_initialize_dispatch_state(
        driver_dir, workflow_id=state["workflow_id"], config=config
    )


def test_status_does_not_attribute_old_model_bootstrap_to_current_model(
    tmp_path: Path, monkeypatch
) -> None:
    callback = _callback_module()
    driver_dir, state, event = _contract_event_context(
        callback, tmp_path, [("codex", "implicit"), ("claude", "old")]
    )
    state["active_index"] = 1
    state["events"][event["event_id"]]["starting_index"] = 1
    state = callback._append_pending_attempt(
        driver_dir, state, event_id=event["event_id"], index=1, stage="bootstrap"
    )
    callback._finish_pending_attempt(
        driver_dir,
        state,
        event_id=event["event_id"],
        status="failed",
        outcome="conclusive_nonacceptance",
        reason="model_not_found",
    )
    config = callback._contract_callback_config(
        issue_dir=driver_dir.parent,
        issue_name=driver_dir.parent.name,
        workflow_id=state["workflow_id"],
    )
    changed = {
        **config,
        "clis": [{"cli": "codex"}, {"cli": "claude", "model": "new"}],
    }
    monkeypatch.setattr(callback, "_contract_callback_config", lambda **_kwargs: changed)
    status = callback.read_status(driver_dir.parent)
    assert status["entries"][1]["acquisition"] == {"status": "unacquired", "session": None}
    assert status["events"][0]["attempts"][0]["model"] == "old"


def _write_legacy_contract(contract_path: Path, *, schema_version: int) -> bytes:
    """Install an explicit historical fixture using the confirmed transport identity."""
    current = json.loads(contract_path.read_text(encoding="utf-8"))
    document = legacy_driver_contract(
        schema_version=schema_version,
        identity=current["identity"],
        driver=current["driver"],
    )
    predecessor = canonical_json(document)
    contract_path.write_bytes(predecessor)
    return predecessor


@pytest.mark.parametrize("schema_version", [3, 4])
@pytest.mark.parametrize("damage", [None, "digest", "identity", "missing_rationale"])
def test_contract_callback_validates_legacy_transport_without_upgrading(
    tmp_path: Path, schema_version: int, damage: str | None
) -> None:
    """A delivery-schema upgrade must not break an already-confirmed callback."""
    callback = _callback_module()
    issue_dir = tmp_path / ".cafe" / "issues" / "v3-event-contract"
    blackboard = _prepare_issue(issue_dir)
    _activate_event_contract(
        issue_dir,
        workflow_id=blackboard.workflow_id,
        clis=[("codex", "primary"), ("claude", "fallback")],
    )
    contract_path = issue_dir / "driver" / "contract.json"
    predecessor = _write_legacy_contract(contract_path, schema_version=schema_version)
    if damage:
        document = json.loads(predecessor)
        if damage == "digest":
            document["provenance"]["proposal_digest"] = "0" * 64
        elif damage == "identity":
            document["identity"]["workflow_id"] = "different-workflow"
        else:
            del document["phases"][0]["rationale"]
        predecessor = canonical_json(document)
        contract_path.write_bytes(predecessor)
        with pytest.raises(ValueError):
            callback._contract_callback_config(
                issue_dir=issue_dir,
                issue_name=issue_dir.name,
                workflow_id=blackboard.workflow_id,
            )
        assert contract_path.read_bytes() == predecessor
        return

    config = callback._contract_callback_config(
        issue_dir=issue_dir,
        issue_name=issue_dir.name,
        workflow_id=blackboard.workflow_id,
    )

    assert config == {
        "schema_version": callback._CONTRACT_CALLBACK_CONFIG_SCHEMA,
        "mode": "event-driven",
        "contract_sha256": hashlib.sha256(predecessor).hexdigest(),
        "clis": [{"cli": "codex"}, {"cli": "claude", "model": "fallback"}],
    }
    assert contract_path.read_bytes() == predecessor


@pytest.mark.parametrize("cli", list(AgentCLI))
def test_every_unbound_entry_bootstraps_without_event_authority(
    tmp_path: Path, cli: AgentCLI
) -> None:
    callback = _callback_module()
    driver_dir, state, event = _v3_event_context(callback, tmp_path, [(cli.value, "exact")])
    calls = []

    class FakeExecutor(AgentExecutor):
        def __init__(self, config, **_kwargs):
            super().__init__(config, stream_output=False)
            self.config = config

        def execute_event_driver(self, prompt, **kwargs):
            calls.append((self.config, prompt, kwargs))
            return _executor_result(
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
            lambda _callback: _executor_result(
                session_id=None,
                records=({"type": "result", "status": "success"},),
            ),
            "conclusive_nonacceptance",
            False,
        ),
        (
            lambda _callback: _executor_result(
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

    class FakeExecutor(AgentExecutor):
        def __init__(self, *_args, **_kwargs):
            super().__init__(_args[0], stream_output=False)
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

    class FakeExecutor(AgentExecutor):
        def __init__(self, *_args, **_kwargs):
            super().__init__(_args[0], stream_output=False)
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

    class FakeExecutor(AgentExecutor):
        def __init__(self, config, **_kwargs):
            super().__init__(config, stream_output=False)
            self.config = config

        def execute_event_driver(self, prompt, **_kwargs):
            calls.append(prompt)
            return _executor_result(session_id="provider-session", records=())

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


def test_actual_callback_starts_only_after_session_is_durable(tmp_path: Path) -> None:
    callback = _callback_module()
    driver_dir, state, event = _v3_event_context(callback, tmp_path, [("claude", "exact")])
    calls = []

    class FakeExecutor(AgentExecutor):
        def __init__(self, config, **_kwargs):
            super().__init__(config, stream_output=False)
            self.config = config

        def execute_event_driver(self, prompt, **kwargs):
            calls.append((prompt, kwargs))
            if kwargs.get("expected_session_id") is None:
                return _executor_result(session_id="provider-session", accepted=False, records=())
            persisted = json.loads((driver_dir / "dispatch_state.json").read_text())
            assert persisted["entries"][0]["session"]["id"] == "provider-session"
            assert kwargs["expected_session_id"] == "provider-session"
            assert event["event_id"] in prompt
            return _executor_result(
                session_id="provider-session",
                accepted=True,
                records=({"type": "system", "subtype": "init", "session_id": "provider-session"},),
            )

    updated = callback._run_v3_callback(
        driver_dir,
        state,
        event,
        repository_root=tmp_path,
        executor_factory=FakeExecutor,
    )

    assert [call[0] for call in calls][0] == 'say "HI"'
    assert calls[0][1].get("event_id") is None
    assert calls[1][1]["event_id"] == event["event_id"]
    event_state = updated["events"][event["event_id"]]
    assert [attempt["stage"] for attempt in event_state["attempts"]] == [
        "bootstrap",
        "delivery",
    ]
    assert event_state["status"] == "accepted"
    assert event_state["accepted_index"] == 0


def test_actual_acceptance_is_durable_before_downstream_output_finishes(
    tmp_path: Path,
) -> None:
    callback = _callback_module()
    driver_dir, state, event = _v3_event_context(callback, tmp_path, [("claude", "exact")])
    state["entries"][0]["session"] = {
        "id": "provider-session",
        "source": "provider",
        "acquired_at": "2026-09-04T00:00:00+00:00",
    }
    (driver_dir / "dispatch_state.json").write_text(json.dumps(state), encoding="utf-8")

    class FakeExecutor(AgentExecutor):
        def __init__(self, _config, **_kwargs):
            super().__init__(_config, stream_output=False)
            pass

        def execute_event_driver(self, _prompt, **kwargs):
            kwargs["on_acceptance"]()
            persisted = json.loads((driver_dir / "dispatch_state.json").read_text())
            assert persisted["events"][event["event_id"]]["status"] == "accepted"
            raise callback.AgentExecutionError(
                "downstream output ended late",
                error_type="incomplete_stream",
            )

    updated, outcome = callback._deliver_v3_callback(
        driver_dir,
        state,
        event,
        index=0,
        repository_root=tmp_path,
        executor_factory=FakeExecutor,
    )

    assert outcome == "accepted"
    assert updated["events"][event["event_id"]]["status"] == "accepted"


def test_copilot_captured_acceptance_stops_before_fallback(tmp_path: Path) -> None:
    callback = _callback_module()
    driver_dir, state, event = _v3_event_context(
        callback, tmp_path, [("copilot", "exact"), ("claude", "fallback")]
    )
    state["entries"][0]["session"] = {
        "id": "provider-session",
        "source": "provider",
        "acquired_at": "2026-09-04T00:00:00+00:00",
    }
    (driver_dir / "dispatch_state.json").write_text(json.dumps(state), encoding="utf-8")
    calls = []

    def execute_stream(**kwargs):
        calls.append(kwargs["cmd"])
        for record in (
            {
                "type": "user.message",
                "data": {"content": f"callback {event['event_id']}"},
            },
            {"type": "result", "sessionId": "provider-session"},
        ):
            kwargs["structured_records"].append(record)
            kwargs["structured_record_observer"](record)
        return AgentResponse(response="", token_usage=TokenUsage())

    with patch.object(
        callback.AgentExecutor, "_execute_with_streaming", side_effect=execute_stream
    ):
        updated = callback._run_v3_callback(
            driver_dir,
            state,
            event,
            repository_root=tmp_path,
        )

    assert len(calls) == 1
    assert calls[0][calls[0].index("--resume") + 1] == "provider-session"
    assert updated["events"][event["event_id"]]["status"] == "accepted"
    assert updated["events"][event["event_id"]]["accepted_index"] == 0
    assert updated["entries"][1]["session"] is None


def test_copilot_acceptance_at_record_65_stops_before_fallback(tmp_path: Path) -> None:
    callback = _callback_module()
    driver_dir, state, event = _v3_event_context(
        callback, tmp_path, [("copilot", "exact"), ("claude", "fallback")]
    )
    state["entries"][0]["session"] = {
        "id": "provider-session",
        "source": "provider",
        "acquired_at": "2026-09-04T00:00:00+00:00",
    }
    (driver_dir / "dispatch_state.json").write_text(json.dumps(state), encoding="utf-8")
    records = [
        {
            "type": "user.message",
            "data": {"content": f"callback {event['event_id']}"},
        },
        *({"type": "assistant.message", "index": index} for index in range(63)),
        {"type": "result", "sessionId": "provider-session"},
    ]
    process = MagicMock()
    process.stdout.readline.side_effect = [
        *(f"{json.dumps(record)}\n" for record in records),
        "",
    ]
    process.stderr.read.return_value = ""
    process.wait.return_value = 0

    with (
        patch("subprocess.Popen", return_value=process) as popen,
        patch("sys.platform", "win32"),
    ):
        updated = callback._run_v3_callback(
            driver_dir,
            state,
            event,
            repository_root=tmp_path,
        )

    assert popen.call_count == 1
    assert updated["events"][event["event_id"]]["status"] == "accepted"
    assert updated["events"][event["event_id"]]["accepted_index"] == 0
    assert updated["entries"][1]["session"] is None


def test_multi_hop_delivery_is_serial_forward_only_and_sticky(tmp_path: Path) -> None:
    callback = _callback_module()
    chain = [("codex", "one"), ("claude", "two"), ("gemini", "three")]
    driver_dir, state, event = _v3_event_context(callback, tmp_path, chain)
    calls = []

    class FakeExecutor(AgentExecutor):
        def __init__(self, config, **_kwargs):
            super().__init__(config, stream_output=False)
            self.config = config

        def execute_event_driver(self, prompt, **kwargs):
            stage = "delivery" if kwargs.get("expected_session_id") else "bootstrap"
            calls.append((self.config.cli.value, stage, kwargs.get("event_id")))
            session_id = f"{self.config.cli.value}-session"
            return _executor_result(
                session_id=session_id,
                accepted=stage == "delivery" and self.config.cli is AgentCLI.GEMINI,
                records=(),
            )

    updated = callback._run_v3_callback(
        driver_dir,
        state,
        event,
        repository_root=tmp_path,
        executor_factory=FakeExecutor,
    )

    assert [(cli, stage) for cli, stage, _event_id in calls] == [
        ("codex", "bootstrap"),
        ("codex", "delivery"),
        ("claude", "bootstrap"),
        ("claude", "delivery"),
        ("gemini", "bootstrap"),
        ("gemini", "delivery"),
    ]
    assert {event_id for _cli, stage, event_id in calls if stage == "delivery"} == {
        event["event_id"]
    }
    assert updated["active_index"] == 2
    takeover = updated["events"][event["event_id"]]["takeover"]
    assert takeover["from_index"] == 0
    assert takeover["to_index"] == 2

    replayed = callback._run_v3_callback(
        driver_dir,
        updated,
        event,
        repository_root=tmp_path,
        executor_factory=FakeExecutor,
    )
    assert replayed == updated
    assert len(calls) == 6

    from cafe.core.blackboard import BlackboardStore

    issue_dir = driver_dir.parent
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("spec")
    later_event = store.prepare_workflow_callback_event(
        blackboard,
        {
            "workflow_id": state["workflow_id"],
            "issue": issue_dir.name,
            "event_type": "workflow_completed",
            "step": "review",
            "status_code": "ok",
        },
    )
    later_state = callback._ensure_dispatch_event(driver_dir, updated, later_event)
    later_state = callback._run_v3_callback(
        driver_dir,
        later_state,
        later_event,
        repository_root=tmp_path,
        executor_factory=FakeExecutor,
    )
    assert calls[-1][:2] == ("gemini", "delivery")
    assert later_state["active_index"] == 2


def test_ambiguous_actual_delivery_stops_before_later_entry(tmp_path: Path) -> None:
    callback = _callback_module()
    driver_dir, state, event = _v3_event_context(
        callback, tmp_path, [("codex", "one"), ("claude", "two")]
    )
    calls = []

    class FakeExecutor(AgentExecutor):
        def __init__(self, config, **_kwargs):
            super().__init__(config, stream_output=False)
            self.config = config

        def execute_event_driver(self, prompt, **kwargs):
            calls.append((self.config.cli.value, prompt))
            if kwargs.get("expected_session_id") is None:
                return _executor_result(session_id="codex-session", accepted=False, records=())
            raise callback.AgentExecutionError("truncated", error_type="incomplete_stream")

    updated = callback._run_v3_callback(
        driver_dir,
        state,
        event,
        repository_root=tmp_path,
        executor_factory=FakeExecutor,
    )

    assert [cli for cli, _prompt in calls] == ["codex", "codex"]
    event_state = updated["events"][event["event_id"]]
    assert event_state["status"] == "recovery_pending"
    assert event_state["attempts"][-1]["stage"] == "delivery"
    assert event_state["attempts"][-1]["outcome"] == "ambiguous"


def test_exhausted_suffix_retains_event_and_active_index(tmp_path: Path) -> None:
    callback = _callback_module()
    driver_dir, state, event = _v3_event_context(
        callback, tmp_path, [("claude", "one"), ("gemini", "two")]
    )
    calls = []

    class FakeExecutor(AgentExecutor):
        def __init__(self, config, **_kwargs):
            super().__init__(config, stream_output=False)
            self.config = config

        def execute_event_driver(self, _prompt, **kwargs):
            calls.append((self.config.cli.value, bool(kwargs.get("expected_session_id"))))
            return _executor_result(
                session_id=f"{self.config.cli.value}-session",
                accepted=False,
                records=(),
            )

    updated = callback._run_v3_callback(
        driver_dir,
        state,
        event,
        repository_root=tmp_path,
        executor_factory=FakeExecutor,
    )

    event_state = updated["events"][event["event_id"]]
    assert event_state["status"] == "exhausted"
    assert event_state["recovery_pending"] is True
    assert updated["active_index"] == 0
    assert len(calls) == 4


def test_acceptance_write_failure_reloads_as_pending_and_never_falls_forward(
    tmp_path: Path,
) -> None:
    callback = _callback_module()
    driver_dir, state, event = _v3_event_context(
        callback, tmp_path, [("cursor-agent", "one"), ("gemini", "two")]
    )
    calls = []

    class FakeExecutor(AgentExecutor):
        def __init__(self, config, **_kwargs):
            super().__init__(config, stream_output=False)
            self.config = config

        def execute_event_driver(self, _prompt, **kwargs):
            calls.append(self.config.cli.value)
            return _executor_result(
                session_id="cursor-session",
                accepted=kwargs.get("expected_session_id") is not None,
                records=(),
            )

    original_atomic_write = callback._atomic_write
    writes = 0

    def fail_acceptance(path, payload):
        nonlocal writes
        writes += 1
        if writes == 4:
            raise OSError("acceptance replace failed")
        original_atomic_write(path, payload)

    with patch.object(callback, "_atomic_write", side_effect=fail_acceptance):
        with pytest.raises(OSError):
            callback._run_v3_callback(
                driver_dir,
                state,
                event,
                repository_root=tmp_path,
                executor_factory=FakeExecutor,
            )

    persisted = callback._load_or_initialize_dispatch_state(
        driver_dir,
        workflow_id=state["workflow_id"],
        config=state["policy"],
    )
    event_state = persisted["events"][event["event_id"]]
    assert event_state["attempts"][-1]["stage"] == "delivery"
    assert event_state["attempts"][-1]["status"] == "pending"

    replayed = callback._run_v3_callback(
        driver_dir,
        persisted,
        event,
        repository_root=tmp_path,
        executor_factory=FakeExecutor,
    )
    assert replayed == persisted
    assert calls == ["cursor-agent", "cursor-agent"]


def test_bound_codex_delivery_uses_queue_without_bootstrap(tmp_path: Path, monkeypatch) -> None:
    callback = _callback_module()
    monkeypatch.setenv("CODEX_THREAD_ID", "bound-session")
    driver_dir, state, event = _v3_event_context(
        callback, tmp_path, [("codex", "exact"), ("claude", "fallback")]
    )

    class ForbiddenExecutor:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("bound Codex must use its host queue")

    with patch.object(callback, "_queue_host_callback") as queue:
        updated = callback._run_v3_callback(
            driver_dir,
            state,
            event,
            repository_root=tmp_path,
            executor_factory=ForbiddenExecutor,
        )

    assert queue.call_count == 1
    assert queue.call_args.kwargs["thread_id"] == "bound-session"
    assert updated["events"][event["event_id"]]["status"] == "accepted"
    assert updated["entries"][1]["session"] is None


def test_confirmed_activation_delivers_to_host_after_detaching_environment(
    tmp_path: Path, monkeypatch
) -> None:
    callback = _callback_module()
    issue_dir = tmp_path / ".cafe" / "issues" / "issue456"
    blackboard = _prepare_issue(issue_dir)
    monkeypatch.setenv("CODEX_THREAD_ID", "visible-thread")
    callback.activate_confirmed_contract_with_host_session(
        issue_dir=issue_dir,
        issue_name=issue_dir.name,
        workflow_id=blackboard.workflow_id,
        activate_contract=lambda: _activate_event_contract(
            issue_dir,
            workflow_id=blackboard.workflow_id,
            clis=[("codex", "exact")],
        ),
    )
    monkeypatch.delenv("CODEX_THREAD_ID")
    from cafe.core.blackboard import BlackboardStore

    store = BlackboardStore(issue_dir)
    event = store.prepare_workflow_callback_event(
        blackboard,
        {
            "workflow_id": blackboard.workflow_id,
            "issue": issue_dir.name,
            "event_type": "human_task",
            "step": "pr",
            "status_code": "waiting",
        },
    )

    with patch.object(callback, "_queue_host_callback") as queue:
        callback.run_callback(event, repository_root=tmp_path)

    assert queue.call_args.kwargs["thread_id"] == "visible-thread"
    assert queue.call_args.kwargs["model"] is None
    state = json.loads((issue_dir / "driver" / "dispatch_state.json").read_text(encoding="utf-8"))
    assert state["entries"][0]["session"]["source"] == "host_session"
    assert state["events"][event["event_id"]]["status"] == "accepted"


def test_confirmed_activation_binding_failure_warns_and_is_safely_retryable(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    callback = _callback_module()
    issue_dir = tmp_path / ".cafe" / "issues" / "issue456"
    blackboard = _prepare_issue(issue_dir)
    monkeypatch.setenv("CODEX_THREAD_ID", "visible-thread")
    original = callback._load_or_initialize_dispatch_state

    def fail_once(*_args, **_kwargs):
        raise OSError("simulated state write failure")

    monkeypatch.setattr(callback, "_load_or_initialize_dispatch_state", fail_once)

    def activate():
        return _activate_event_contract(
            issue_dir,
            workflow_id=blackboard.workflow_id,
            clis=[("codex", "exact")],
        )

    callback.activate_confirmed_contract_with_host_session(
        issue_dir=issue_dir,
        issue_name=issue_dir.name,
        workflow_id=blackboard.workflow_id,
        activate_contract=activate,
    )

    assert (issue_dir / "driver" / "contract.json").is_file()
    assert not (issue_dir / "driver" / "dispatch_state.json").exists()
    assert "activated without Codex host binding" in capsys.readouterr().err
    monkeypatch.setattr(callback, "_load_or_initialize_dispatch_state", original)
    callback.activate_confirmed_contract_with_host_session(
        issue_dir=issue_dir,
        issue_name=issue_dir.name,
        workflow_id=blackboard.workflow_id,
        activate_contract=activate,
    )

    state = json.loads((issue_dir / "driver" / "dispatch_state.json").read_text(encoding="utf-8"))
    assert state["entries"][0]["session"]["id"] == "visible-thread"


def test_confirmed_activation_keeps_an_existing_acquired_session(
    tmp_path: Path, monkeypatch
) -> None:
    callback = _callback_module()
    driver_dir, state, _event = _contract_event_context(
        callback, tmp_path, [("codex", "exact")], issue_name="issue456"
    )
    state["entries"][0]["session"] = {
        "id": "provider-session",
        "source": "provider",
        "acquired_at": "2026-09-07T00:00:00+00:00",
    }
    callback._write_dispatch_state(driver_dir, state)
    monkeypatch.setenv("CODEX_THREAD_ID", "different-visible-thread")
    blackboard = _prepare_issue(driver_dir.parent)

    callback.activate_confirmed_contract_with_host_session(
        issue_dir=driver_dir.parent,
        issue_name=driver_dir.parent.name,
        workflow_id=blackboard.workflow_id,
        activate_contract=lambda: _activate_event_contract(
            driver_dir.parent,
            workflow_id=blackboard.workflow_id,
            clis=[("codex", "exact")],
        ),
    )

    persisted = json.loads((driver_dir / "dispatch_state.json").read_text(encoding="utf-8"))
    assert persisted["entries"][0]["session"]["id"] == "provider-session"
    assert persisted["entries"][0]["session"]["source"] == "provider"


def test_confirmed_contract_activation_failure_still_propagates(tmp_path: Path) -> None:
    callback = _callback_module()

    def fail_activation():
        raise RuntimeError("invalid confirmed contract")

    with pytest.raises(RuntimeError, match="invalid confirmed contract"):
        callback.activate_confirmed_contract_with_host_session(
            issue_dir=tmp_path / ".cafe" / "issues" / "issue456",
            issue_name="issue456",
            workflow_id="workflow",
            activate_contract=fail_activation,
        )


def test_conclusive_bootstrap_failure_moves_to_next_entry(tmp_path: Path) -> None:
    callback = _callback_module()
    driver_dir, state, event = _v3_event_context(
        callback, tmp_path, [("codex", "one"), ("claude", "two")]
    )
    calls = []

    class FakeExecutor(AgentExecutor):
        def __init__(self, config, **_kwargs):
            super().__init__(config, stream_output=False)
            self.config = config

        def execute_event_driver(self, _prompt, **kwargs):
            stage = "delivery" if kwargs.get("expected_session_id") else "bootstrap"
            calls.append((self.config.cli.value, stage))
            if self.config.cli is AgentCLI.CODEX:
                raise callback.AgentExecutionError(
                    "exact model unavailable", error_type="model_not_found"
                )
            return _executor_result(
                session_id="claude-session",
                accepted=stage == "delivery",
                records=(),
            )

    updated = callback._run_v3_callback(
        driver_dir,
        state,
        event,
        repository_root=tmp_path,
        executor_factory=FakeExecutor,
    )

    assert calls == [
        ("codex", "bootstrap"),
        ("claude", "bootstrap"),
        ("claude", "delivery"),
    ]
    assert updated["active_index"] == 1
    assert updated["events"][event["event_id"]]["attempts"][0]["stage"] == "bootstrap"


def test_public_callback_path_executes_version_three_lifecycle(tmp_path: Path, monkeypatch) -> None:
    callback = _callback_module()
    driver_dir, state, event = _contract_event_context(callback, tmp_path, [("gemini", "exact")])
    calls = []
    models = []
    authority_events = []
    inspect_authority = callback._with_current_task_authority

    def traced_authority(event, **kwargs):
        authority_events.append(event.get("event_id"))
        return inspect_authority(event, **kwargs)

    monkeypatch.setattr(callback, "_with_current_task_authority", traced_authority)

    class FakeExecutor(AgentExecutor):
        def __init__(self, config, **_kwargs):
            super().__init__(config, stream_output=False)
            self.config = config
            models.append(config.model)

        def execute_event_driver(self, _prompt, **kwargs):
            calls.append(kwargs.get("expected_session_id"))
            return _executor_result(
                session_id="gemini-session",
                accepted=kwargs.get("expected_session_id") is not None,
                records=(),
            )

    monkeypatch.setattr(callback, "AgentExecutor", FakeExecutor)
    callback.run_callback(event, repository_root=tmp_path)

    persisted = callback._load_or_initialize_dispatch_state(
        driver_dir,
        workflow_id=state["workflow_id"],
        config=callback._contract_callback_config(
            issue_dir=driver_dir.parent,
            issue_name=driver_dir.parent.name,
            workflow_id=state["workflow_id"],
        ),
    )
    assert calls == [None, "gemini-session"]
    assert models == [None, None]
    assert authority_events == [event["event_id"]]
    assert persisted["events"][event["event_id"]]["status"] == "accepted"


def test_public_callback_path_rejects_eventless_provider_init(tmp_path: Path, monkeypatch) -> None:
    callback = _callback_module()
    driver_dir, state, event = _contract_event_context(callback, tmp_path, [("claude", "exact")])
    state["entries"][0]["session"] = {
        "id": "provider-session",
        "source": "provider",
        "acquired_at": "2026-09-04T00:00:00+00:00",
    }
    callback._write_dispatch_state(driver_dir, state)

    def emit_init_only(_executor, **kwargs):
        init = {
            "type": "system",
            "subtype": "init",
            "session_id": "provider-session",
        }
        kwargs["structured_records"].append(init)
        kwargs["structured_record_observer"](init)
        return AgentResponse(response="", token_usage=TokenUsage())

    monkeypatch.setattr(callback.AgentExecutor, "_execute_with_streaming", emit_init_only)
    callback.run_callback(event, repository_root=tmp_path)

    persisted = callback._load_or_initialize_dispatch_state(
        driver_dir,
        workflow_id=state["workflow_id"],
        config=callback._contract_callback_config(
            issue_dir=driver_dir.parent,
            issue_name=driver_dir.parent.name,
            workflow_id=state["workflow_id"],
        ),
    )
    event_state = persisted["events"][event["event_id"]]
    assert event_state["status"] == "exhausted"
    assert event_state["accepted_index"] is None


def test_status_projects_order_conformance_and_unacquired_without_writing(
    tmp_path: Path,
) -> None:
    callback = _callback_module()
    issue_dir = tmp_path / ".cafe" / "issues" / "issue457"
    blackboard = _prepare_issue(issue_dir)
    callback.write_config(
        issue_dir,
        clis=[
            ("codex", "one"),
            ("claude", "two"),
            ("gemini", "three"),
            ("cursor-agent", "four"),
            ("copilot", "five"),
        ],
    )
    driver_dir = issue_dir / "driver"
    before = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in driver_dir.iterdir()
        if path.is_file()
    }

    first = callback.read_status(issue_dir)
    second = callback.read_status(issue_dir)

    assert first == second
    assert first["configured"] is True
    assert first["schema_version"] == 3
    assert first["workflow_id"] == blackboard.workflow_id
    assert first["active_index"] == 0
    assert [entry["cli"] for entry in first["entries"]] == [
        "codex",
        "claude",
        "gemini",
        "cursor-agent",
        "copilot",
    ]
    assert [entry["model"] for entry in first["entries"]] == [
        "one",
        "two",
        "three",
        "four",
        "five",
    ]
    assert all(entry["conforming"] is True for entry in first["entries"])
    assert first["entries"][0]["active"] is True
    assert all(
        entry["acquisition"] == {"status": "unacquired", "session": None}
        for entry in first["entries"]
    )
    assert first["events"] == []
    assert first["recovery_pending"] is False
    assert (driver_dir / "dispatch_state.json").is_file()
    assert {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in driver_dir.iterdir()
        if path.is_file()
    } == before


def test_status_does_not_rebind_existing_provider_session_to_current_host(
    tmp_path: Path, monkeypatch
) -> None:
    callback = _callback_module()
    driver_dir, state, _event = _contract_event_context(callback, tmp_path, [("codex", "exact")])
    state["entries"][0]["session"] = {
        "id": "provider-session",
        "source": "provider",
        "acquired_at": "2026-09-07T00:00:00+00:00",
    }
    callback._write_dispatch_state(driver_dir, state)
    monkeypatch.setenv("CODEX_THREAD_ID", "current-host-thread")

    status = callback.read_status(driver_dir.parent)

    assert status["entries"][0]["acquisition"]["session"] == {
        "id": "provider-session",
        "source": "provider",
        "acquired_at": "2026-09-07T00:00:00+00:00",
    }


def test_status_projects_acquisition_delivery_takeover_and_recovery(
    tmp_path: Path,
) -> None:
    callback = _callback_module()
    issue_dir = tmp_path / ".cafe" / "issues" / "issue457"
    blackboard = _prepare_issue(issue_dir)
    callback.write_config(
        issue_dir,
        clis=[("codex", "one"), ("claude", "two"), ("gemini", "three")],
    )
    driver_dir = issue_dir / "driver"
    config = callback._load_config(driver_dir)
    state = callback._load_or_initialize_dispatch_state(
        driver_dir,
        workflow_id=blackboard.workflow_id,
        config=config,
    )
    state["active_index"] = 1
    state["entries"][0]["session"] = {
        "id": "codex-session",
        "source": "provider",
        "acquired_at": "2026-09-04T00:00:00+00:00",
    }
    state["entries"][1]["session"] = {
        "id": "claude-session",
        "source": "provider",
        "acquired_at": "2026-09-04T00:00:00+00:00",
    }
    state["events"] = {
        "bootstrap-pending": {
            "event": {
                "workflow_id": blackboard.workflow_id,
                "issue": "issue457",
                "event_type": "phase_terminal",
                "event_id": "bootstrap-pending",
                "sequence": 1,
                "occurred_at": "2026-09-04T00:00:01+00:00",
            },
            "starting_index": 0,
            "status": "routing",
            "attempts": [
                {
                    "index": 0,
                    "stage": "bootstrap",
                    "status": "pending",
                    "outcome": None,
                    "reason": None,
                    "session_id": None,
                    "started_at": "2026-09-04T00:00:02+00:00",
                    "finished_at": None,
                }
            ],
            "accepted_index": None,
            "takeover": None,
            "recovery_pending": False,
        },
        "delivery-pending": {
            "event": {
                "workflow_id": blackboard.workflow_id,
                "issue": "issue457",
                "event_type": "phase_terminal",
                "event_id": "delivery-pending",
                "sequence": 2,
                "occurred_at": "2026-09-04T00:00:03+00:00",
            },
            "starting_index": 1,
            "status": "routing",
            "attempts": [
                {
                    "index": 1,
                    "stage": "delivery",
                    "status": "pending",
                    "outcome": None,
                    "reason": None,
                    "session_id": None,
                    "started_at": "2026-09-04T00:00:04+00:00",
                    "finished_at": None,
                }
            ],
            "accepted_index": None,
            "takeover": None,
            "recovery_pending": False,
        },
        "accepted": {
            "event": {
                "workflow_id": blackboard.workflow_id,
                "issue": "issue457",
                "event_type": "phase_terminal",
                "event_id": "accepted",
                "sequence": 3,
                "occurred_at": "2026-09-04T00:00:05+00:00",
            },
            "starting_index": 0,
            "status": "accepted",
            "attempts": [
                {
                    "index": 0,
                    "stage": "delivery",
                    "status": "failed",
                    "outcome": "conclusive_nonacceptance",
                    "reason": "transport_rejected",
                    "session_id": "codex-session",
                    "started_at": "2026-09-04T00:00:06+00:00",
                    "finished_at": "2026-09-04T00:00:07+00:00",
                },
                {
                    "index": 1,
                    "stage": "delivery",
                    "status": "accepted",
                    "outcome": "durable_acceptance",
                    "reason": "provider_acknowledgement",
                    "session_id": "claude-session",
                    "started_at": "2026-09-04T00:00:08+00:00",
                    "finished_at": "2026-09-04T00:00:09+00:00",
                },
            ],
            "accepted_index": 1,
            "takeover": {
                "event_id": "accepted",
                "sequence": 3,
                "occurred_at": "2026-09-04T00:00:05+00:00",
                "from_index": 0,
                "to_index": 1,
                "eligible_reason": "transport_rejected",
                "accepted_at": "2026-09-04T00:00:09+00:00",
            },
            "recovery_pending": False,
        },
        "exhausted": {
            "event": {
                "workflow_id": blackboard.workflow_id,
                "issue": "issue457",
                "event_type": "workflow_completed",
                "event_id": "exhausted",
                "sequence": 4,
                "occurred_at": "2026-09-04T00:00:10+00:00",
            },
            "starting_index": 1,
            "status": "exhausted",
            "attempts": [
                {
                    "index": 1,
                    "stage": "delivery",
                    "status": "failed",
                    "outcome": "conclusive_nonacceptance",
                    "reason": "queue_rejected",
                    "session_id": "claude-session",
                    "started_at": "2026-09-04T00:00:11+00:00",
                    "finished_at": "2026-09-04T00:00:12+00:00",
                },
                {
                    "index": 2,
                    "stage": "bootstrap",
                    "status": "failed",
                    "outcome": "conclusive_nonacceptance",
                    "reason": "queue_rejected",
                    "session_id": None,
                    "started_at": "2026-09-04T00:00:13+00:00",
                    "finished_at": "2026-09-04T00:00:14+00:00",
                },
            ],
            "accepted_index": None,
            "takeover": None,
            "recovery_pending": True,
        },
    }
    (driver_dir / "dispatch_state.json").write_text(
        json.dumps(state, sort_keys=True), encoding="utf-8"
    )
    watched = [driver_dir / "config.yaml", driver_dir / "dispatch_state.json"]
    before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in watched]

    status = callback.read_status(issue_dir)
    repeated = callback.read_status(issue_dir)

    assert repeated == status
    assert status["workflow_id"] == blackboard.workflow_id
    assert status["active_index"] == 1
    assert status["entries"][0]["acquisition"]["status"] == "acquired"
    assert status["entries"][1]["acquisition"] == {
        "status": "acquired",
        "session": {
            "id": "claude-session",
            "source": "provider",
            "acquired_at": "2026-09-04T00:00:00+00:00",
        },
    }
    assert status["entries"][2]["acquisition"]["status"] == "bootstrap_failed"
    by_id = {event["event_id"]: event for event in status["events"]}
    assert by_id["delivery-pending"]["attempts"][0]["status"] == "pending"
    assert by_id["accepted"]["attempts"][0]["status"] == "failed"
    assert by_id["accepted"]["status"] == "accepted"
    assert by_id["accepted"]["takeover"]["to_index"] == 1
    assert by_id["exhausted"]["status"] == "exhausted"
    assert by_id["exhausted"]["recovery_pending"] is True
    assert status["recovery_pending"] is True
    assert [(path.read_bytes(), path.stat().st_mtime_ns) for path in watched] == before


@pytest.mark.parametrize("schema_version", [1, 2])
def test_status_reads_legacy_binding_without_mutation(tmp_path: Path, schema_version: int) -> None:
    callback = _callback_module()
    issue_dir = tmp_path / ".cafe" / "issues" / "legacy"
    driver_dir = issue_dir / "driver"
    driver_dir.mkdir(parents=True)
    document = {
        "schema_version": schema_version,
        "mode": "event-driven",
        "cli": "codex",
        "model": "exact",
    }
    if schema_version == 2:
        document["host_session"] = None
    (driver_dir / "config.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    before = (driver_dir / "config.yaml").read_bytes()

    status = callback.read_status(issue_dir)

    assert status["schema_version"] == schema_version
    assert status["mode"] == "legacy_single_transport"
    assert status["entries"][0]["acquisition"]["status"] == "unacquired"
    assert status["events"] == []
    assert (driver_dir / "config.yaml").read_bytes() == before


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


def test_callback_acquires_and_delivers_a_contract_bound_session(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    callback = _callback_module()
    driver_dir, state, event = _contract_event_context(
        callback, tmp_path, [("codex", "exact")], issue_name="issue456"
    )
    from cafe.core.blackboard import BlackboardStore

    issue_dir = driver_dir.parent
    store = BlackboardStore(issue_dir)
    board = store.load_or_create("spec")
    store.log_event(board, "develop", "large_history", "complete detail " * 20000)
    assert store.file_path.stat().st_size < 256 * 1024
    before_status = {
        path: path.read_bytes()
        for path in (store.file_path, store.receipts_path, store.audit.binding)
    }
    assert callback.read_status(issue_dir)["configured"] is True
    assert callback.main(["--status", "--issue-dir", str(issue_dir)]) == 0
    assert json.loads(capsys.readouterr().out)["configured"] is True
    assert all(path.read_bytes() == content for path, content in before_status.items())
    calls = []

    class FakeExecutor(AgentExecutor):
        def __init__(self, _config, **_kwargs):
            super().__init__(_config, stream_output=False)
            pass

        def execute_event_driver(self, _prompt, **kwargs):
            expected_session_id = kwargs.get("expected_session_id")
            calls.append(expected_session_id)
            return _executor_result(
                session_id="session-1",
                accepted=expected_session_id == "session-1",
                records=(),
            )

    monkeypatch.setattr(callback, "AgentExecutor", FakeExecutor)
    callback.run_callback(event, repository_root=tmp_path)

    persisted = json.loads((driver_dir / "dispatch_state.json").read_text(encoding="utf-8"))
    assert calls == [None, "session-1"]
    assert persisted["entries"][0]["session"]["id"] == "session-1"
    assert persisted["events"][event["event_id"]]["status"] == "accepted"
    assert store.audit.validate_callback(event["workflow_id"], event)["delivery"] == "closed"


def test_operator_sees_callback_failure_without_slack(tmp_path: Path, monkeypatch) -> None:
    from cafe.services.status_service import StatusService

    callback = _callback_module()
    driver_dir, _state, event = _contract_event_context(
        callback, tmp_path, [("codex", "exact")], issue_name="issue456"
    )
    service = StatusService(issues_root=driver_dir.parent.parent)
    before = service.load_current_state("issue456", ["spec", "develop"])
    monkeypatch.setattr(
        callback, "load_human_task_notification_settings",
        lambda: SimpleNamespace(enabled=False, code="disabled"),
    )
    callback._notify_callback_failure(
        event, repository_root=tmp_path, error=TimeoutError("delivery unavailable")
    )
    receipt = json.loads((driver_dir / "callback_failure_notifications.json").read_text())
    status = service.load_current_state("issue456", ["spec", "develop"])
    assert receipt["workflow_id"] == event["workflow_id"]
    assert status["State"] != before["State"]
    assert "dispatch_state.json" in status["Next"]


def test_callback_failure_retention_keeps_newest_records(tmp_path: Path) -> None:
    from cafe.services.status_service import StatusService

    callback = _callback_module()
    driver_dir, state, _event = _contract_event_context(
        callback, tmp_path, [("codex", "exact")], issue_name="issue456"
    )
    records = {
        f"failure_{number:03d}": {
            "occurred_at": f"2026-09-28T00:{number // 60:02d}:{number % 60:02d}+00:00",
            "outcome": "pending",
            "error_code": f"failure_{number:03d}",
        }
        for number in reversed(range(130))
    }
    callback._write_callback_failure_notifications(driver_dir, records)
    persisted = json.loads((driver_dir / "callback_failure_notifications.json").read_text())
    assert len(persisted["records"]) == callback.MAX_FAILURE_NOTIFICATIONS
    assert "failure_129" in persisted["records"]
    assert "failure_000" not in persisted["records"]
    status = StatusService(issues_root=driver_dir.parent.parent).load_current_state(
        "issue456", ["spec", "develop"]
    )
    assert status["Workflow"] == state["workflow_id"]
    assert status["Reason"] == "failure_129"


def test_interrupted_notification_keeps_failure_visible(tmp_path: Path, monkeypatch) -> None:
    from cafe.services.status_service import StatusService

    callback = _callback_module()
    driver_dir, _state, event = _contract_event_context(
        callback, tmp_path, [("codex", "exact")], issue_name="issue456"
    )
    service = StatusService(issues_root=driver_dir.parent.parent)
    before = service.load_current_state("issue456", ["spec", "develop"])

    def interrupted():
        raise OSError("notification settings unavailable")

    monkeypatch.setattr(callback, "load_human_task_notification_settings", interrupted)
    with pytest.raises(OSError):
        callback._notify_callback_failure(
            event, repository_root=tmp_path, error=TimeoutError("delivery unavailable")
        )
    receipt = json.loads((driver_dir / "callback_failure_notifications.json").read_text())
    assert next(iter(receipt["records"].values()))["outcome"] == "pending"
    callback._notify_callback_failure(
        event, repository_root=tmp_path, error=TimeoutError("delivery unavailable")
    )
    assert json.loads((driver_dir / "callback_failure_notifications.json").read_text()) == receipt
    status = service.load_current_state("issue456", ["spec", "develop"])
    assert status["State"] != before["State"]
    assert "dispatch_state.json" in status["Next"]


def test_callback_queues_the_bound_codex_host_thread(tmp_path: Path, monkeypatch) -> None:
    callback = _callback_module()
    monkeypatch.setenv("CODEX_THREAD_ID", "visible-thread")
    driver_dir, _state, event = _contract_event_context(
        callback,
        tmp_path,
        [("codex", "exact")],
        issue_name="issue456",
        event_type="human_task",
        bind_host=True,
    )
    daemon = _HostDaemon(status="notLoaded")
    proxy = _HostProxy(daemon)
    with patch.object(callback.subprocess, "Popen", return_value=proxy) as launch:
        callback.run_callback(event, repository_root=tmp_path)

    assert launch.call_args.args[0] == ["codex", "app-server", "proxy"]
    assert daemon.resume_params == {"threadId": "visible-thread", "excludeTurns": True}
    prompt = daemon.input[0]["text"]
    assert "event-driven CAFE workflow manager" in prompt
    assert '"event_type": "human_task"' in prompt
    assert str(tmp_path) in prompt
    assert daemon.starts == 0
    persisted = json.loads((driver_dir / "dispatch_state.json").read_text(encoding="utf-8"))
    assert persisted["entries"][0]["session"]["id"] == "visible-thread"


def test_bound_host_thread_queue_failure_never_creates_a_new_session(
    tmp_path: Path, monkeypatch
) -> None:
    callback = _callback_module()
    monkeypatch.setenv("CODEX_THREAD_ID", "visible-thread")
    driver_dir, _state, event = _contract_event_context(
        callback,
        tmp_path,
        [("codex", "exact")],
        issue_name="issue456",
        event_type="human_task",
        bind_host=True,
    )
    failure = subprocess.CalledProcessError(1, ["codex", "queue"], stderr="not found")
    with patch.object(callback, "_queue_host_callback", side_effect=failure) as run:
        callback.run_callback(event, repository_root=tmp_path)

    assert run.call_count == 1
    persisted = json.loads((driver_dir / "dispatch_state.json").read_text(encoding="utf-8"))
    assert persisted["entries"][0]["session"]["id"] == "visible-thread"


def test_callback_failure_sends_a_best_effort_slack_notice(tmp_path: Path, monkeypatch) -> None:
    callback = _callback_module()
    monkeypatch.chdir(tmp_path)
    prepared = _prepare_issue(tmp_path / ".cafe" / "issues" / "issue456")
    failure = subprocess.CalledProcessError(1, ["codex", "queue"])

    def fail_callback(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(callback, "run_callback", fail_callback)
    monkeypatch.setattr(
        callback,
        "load_human_task_notification_settings",
        lambda: SimpleNamespace(enabled=True),
    )
    monkeypatch.setattr(callback, "load_slack_webhook_url", lambda **_kwargs: "webhook")
    notices = []
    monkeypatch.setattr(
        callback,
        "post_slack_notification",
        lambda webhook, message, *, timeout_sec: notices.append(
            (webhook, message.to_slack_payload(), timeout_sec)
        ),
    )
    event = {
        "issue": "issue456",
        "workflow_id": prepared.workflow_id,
        "event_type": "human_task",
        "step": "spec",
    }

    with pytest.raises(subprocess.CalledProcessError):
        callback.main(["--workflow-event", json.dumps(event)])

    assert notices == [
        (
            "webhook",
            {
                "text": "\n".join(
                    (
                        "A CAFE automatic notification did not complete",
                        f"Project: {tmp_path.name}",
                        "Conversation: issue456",
                        "Current step: Requirements",
                        "Situation: CAFE could not deliver the notification to "
                        "the original conversation.",
                        "Impact: the original conversation may not have received "
                        "this update; this does not mean the workflow stopped.",
                        'Return to the "issue456" conversation in CAFE and ask the '
                        "Manager to check the current progress and next step.",
                    )
                )
            },
            4.0,
        )
    ]


def test_callback_failure_uses_canonical_repository_route_and_deduplicates(
    tmp_path: Path, monkeypatch
) -> None:
    callback = _callback_module()
    active_root = tmp_path / "linked-worktree"
    canonical_root = tmp_path / "main-repository"
    issue_dir = active_root / ".cafe" / "issues" / "issue456"
    issue_dir.mkdir(parents=True)
    prepared = _prepare_issue(issue_dir)
    canonical_root.mkdir()
    monkeypatch.setattr(
        callback,
        "resolve_human_task_notification_repository_root",
        lambda resolved_issue_dir: (
            canonical_root
            if resolved_issue_dir == issue_dir
            else pytest.fail("unexpected issue directory")
        ),
    )
    monkeypatch.setattr(
        callback,
        "load_human_task_notification_settings",
        lambda: SimpleNamespace(enabled=True),
    )
    routed_roots = []
    monkeypatch.setattr(
        callback,
        "load_slack_webhook_url",
        lambda *, repository_root: routed_roots.append(repository_root) or "webhook",
    )
    payloads = []
    monkeypatch.setattr(
        callback,
        "post_slack_notification",
        lambda _webhook, message, *, timeout_sec: payloads.append(
            (message.to_slack_payload(), timeout_sec)
        ),
    )
    event = {
        "issue": "issue456",
        "workflow_id": prepared.workflow_id,
        "event_type": "human_task",
        "step": "spec",
        "task_id": "task-one",
    }
    error = subprocess.CalledProcessError(2, ["codex", "queue"])

    callback._notify_callback_failure(event, repository_root=active_root, error=error)
    callback._notify_callback_failure(event, repository_root=active_root, error=error)

    assert routed_roots == [canonical_root]
    assert len(payloads) == 1
    assert "Project: main-repository" in payloads[0][0]["text"]
    assert (
        "Situation: CAFE could not deliver the notification to the original conversation."
        in payloads[0][0]["text"]
    )
    receipts = json.loads(
        (issue_dir / "driver" / callback.FAILURE_NOTIFICATIONS_FILENAME).read_text(encoding="utf-8")
    )
    assert list(receipts["records"].values())[0]["outcome"] == "sent"


def test_callback_failure_notice_survives_blackboard_replacement_after_pending_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    callback = _callback_module()
    driver_dir, _state, event = _contract_event_context(
        callback, tmp_path, [("codex", "exact")], issue_name="issue456"
    )
    issue_dir = driver_dir.parent
    blackboard_path = issue_dir / "blackboard.json"
    manager_dir = callback._manager_dir(issue_dir)
    write_receipts = callback._write_callback_failure_notifications
    posts = []
    monkeypatch.setattr(
        callback,
        "resolve_human_task_notification_repository_root",
        lambda _issue_dir: tmp_path,
    )
    monkeypatch.setattr(
        callback,
        "load_human_task_notification_settings",
        lambda: SimpleNamespace(enabled=True),
    )
    monkeypatch.setattr(callback, "load_slack_webhook_url", lambda **_kwargs: "webhook")
    monkeypatch.setattr(
        callback,
        "post_slack_notification",
        lambda _webhook, message, *, timeout_sec: posts.append(message),
    )

    def replace_blackboard_after_pending_write(directory, records):
        write_receipts(directory, records)
        if any(record.get("outcome") == "pending" for record in records.values()):
            blackboard_path.write_text("[]", encoding="utf-8")

    monkeypatch.setattr(
        callback,
        "_write_callback_failure_notifications",
        replace_blackboard_after_pending_write,
    )

    callback._notify_callback_failure(
        event, repository_root=tmp_path, error=TimeoutError("delivery unavailable")
    )

    callback._notify_callback_failure(
        event, repository_root=tmp_path, error=TimeoutError("delivery unavailable")
    )
    receipt = json.loads(
        (manager_dir / callback.FAILURE_NOTIFICATIONS_FILENAME).read_text(encoding="utf-8")
    )
    assert len(posts) == 1
    assert next(iter(receipt["records"].values()))["outcome"] == "sent"


def test_callback_and_slack_failure_leave_a_durable_receipt(tmp_path: Path, monkeypatch) -> None:
    callback = _callback_module()
    issue_dir = tmp_path / ".cafe" / "issues" / "issue456"
    issue_dir.mkdir(parents=True)
    prepared = _prepare_issue(issue_dir)
    monkeypatch.setattr(
        callback,
        "load_human_task_notification_settings",
        lambda: SimpleNamespace(enabled=True),
    )
    monkeypatch.setattr(callback, "load_slack_webhook_url", lambda **_kwargs: "webhook")

    def fail_slack(*_args, **_kwargs):
        raise TimeoutError("offline")

    monkeypatch.setattr(callback, "post_slack_notification", fail_slack)
    event = {
        "issue": "issue456",
        "workflow_id": prepared.workflow_id,
        "event_type": "human_task",
        "step": "spec",
    }

    with pytest.raises(TimeoutError, match="offline"):
        callback._notify_callback_failure(
            event,
            repository_root=tmp_path,
            error=subprocess.CalledProcessError(2, ["codex", "queue"]),
        )

    receipts = json.loads(
        (issue_dir / "driver" / callback.FAILURE_NOTIFICATIONS_FILENAME).read_text(encoding="utf-8")
    )
    receipt = list(receipts["records"].values())[0]
    assert receipt == {
        "error_code": "codex_queue_exit_2",
        "notification_code": "TimeoutError",
        "occurred_at": receipt["occurred_at"],
        "outcome": "failed",
    }


def test_slack_notice_failure_does_not_replace_the_callback_error(
    tmp_path: Path, monkeypatch
) -> None:
    callback = _callback_module()
    monkeypatch.chdir(tmp_path)

    def fail_callback(*_args, **_kwargs):
        raise RuntimeError("callback failed")

    def fail_notification(*_args, **_kwargs):
        raise OSError("slack unavailable")

    monkeypatch.setattr(callback, "run_callback", fail_callback)
    monkeypatch.setattr(callback, "_notify_callback_failure", fail_notification)

    with pytest.raises(RuntimeError, match="callback failed"):
        callback.main(
            [
                "--workflow-event",
                json.dumps(
                    {
                        "issue": "issue456",
                        "workflow_id": "workflow",
                        "event_type": "human_task",
                    }
                ),
            ]
        )


def test_stale_callback_is_rejected_without_a_failure_notification(
    tmp_path: Path, monkeypatch
) -> None:
    callback = _callback_module()
    monkeypatch.chdir(tmp_path)
    notifications = []

    def stale_callback(*_args, **_kwargs):
        raise callback.StaleWorkflowEventError("stale")

    monkeypatch.setattr(callback, "run_callback", stale_callback)
    monkeypatch.setattr(
        callback,
        "_notify_callback_failure",
        lambda *_args, **_kwargs: notifications.append("unexpected"),
    )

    with pytest.raises(callback.StaleWorkflowEventError, match="stale"):
        callback.main(
            [
                "--workflow-event",
                json.dumps(
                    {
                        "issue": "issue456",
                        "workflow_id": "old-workflow",
                        "event_type": "human_task",
                    }
                ),
            ]
        )

    assert notifications == []


def test_invalid_issue_is_rejected_without_writing_a_failure_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    callback = _callback_module()
    monkeypatch.chdir(tmp_path)
    escaped_driver = tmp_path / "outside" / "driver"

    with pytest.raises(callback.InvalidWorkflowEventError, match="invalid issue"):
        callback.main(
            [
                "--workflow-event",
                json.dumps(
                    {
                        "issue": "../../outside",
                        "workflow_id": "workflow",
                        "event_type": "human_task",
                    }
                ),
            ]
        )

    assert not escaped_driver.exists()


def test_callback_identity_mismatch_keeps_existing_session(tmp_path: Path, monkeypatch) -> None:
    callback = _callback_module()
    driver_dir, state, event = _contract_event_context(
        callback, tmp_path, [("codex", "exact")], issue_name="issue456"
    )
    state["entries"][0]["session"] = {
        "id": "existing",
        "source": "provider",
        "acquired_at": "2026-09-04T00:00:00+00:00",
    }
    callback._write_dispatch_state(driver_dir, state)

    class FakeExecutor(AgentExecutor):
        def __init__(self, _config, **_kwargs):
            super().__init__(_config, stream_output=False)
            pass

        def execute_event_driver(self, _prompt, **_kwargs):
            return _executor_result(session_id="different", accepted=True, records=())

    monkeypatch.setattr(callback, "AgentExecutor", FakeExecutor)
    callback.run_callback(event, repository_root=tmp_path)

    persisted = json.loads((driver_dir / "dispatch_state.json").read_text(encoding="utf-8"))
    assert persisted["entries"][0]["session"]["id"] == "existing"
    assert persisted["events"][event["event_id"]]["recovery_pending"] is True


def test_callback_session_conflict_keeps_existing_session(tmp_path: Path, monkeypatch) -> None:
    callback = _callback_module()
    driver_dir, state, event = _contract_event_context(
        callback, tmp_path, [("codex", "exact")], issue_name="issue456"
    )
    state["entries"][0]["session"] = {
        "id": "existing",
        "source": "provider",
        "acquired_at": "2026-09-04T00:00:00+00:00",
    }
    callback._write_dispatch_state(driver_dir, state)

    class FakeExecutor(AgentExecutor):
        def __init__(self, _config, **_kwargs):
            super().__init__(_config, stream_output=False)
            pass

        def execute_event_driver(self, _prompt, **_kwargs):
            raise callback.AgentExecutionError("conflict", error_type="SESSION_CONFLICT")

    monkeypatch.setattr(callback, "AgentExecutor", FakeExecutor)
    callback.run_callback(event, repository_root=tmp_path)

    persisted = json.loads((driver_dir / "dispatch_state.json").read_text(encoding="utf-8"))
    assert persisted["entries"][0]["session"]["id"] == "existing"


def test_manager_event_contract_uses_manager_state_path(tmp_path: Path) -> None:
    callback = _callback_module()
    issue_dir = tmp_path / ".cafe" / "issues" / "manager-event"
    blackboard = _prepare_issue(issue_dir)
    from cafe.manager import ActivateConfirmedContract, activate_confirmed_contract

    activate_confirmed_contract(
        ActivateConfirmedContract(
            issue_dir=issue_dir,
            issue_name=issue_dir.name,
            workflow_id=blackboard.workflow_id,
            confirmed_by="user",
            confirmed_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
            proposal={
                "delivery_contract": delivery_contract(),
                "locales": {"conversation": {"value": "en", "source": "test"}},
                "confirmation_contract": {
                    "user_required": ["spec", "plan"],
                    "manager_confirmable": [],
                    "mandatory_human_stops": ["spec", "plan"],
                },
                "reactive_user_handoffs": {
                    "need_clarification": "manager_confirmable",
                    "need_permission": "user_required",
                    "alignment_checkpoint": "manager_resolvable_when_clear",
                },
                "phases": [{"name": "develop", "chain": [{"cli": "codex", "model": "exact"}]}],
                "proactive_review": {
                    "phase_decisions": [{"phase": "develop", "decision": "not_required"}]
                },
                "manager": {"mode": "event-driven", "clis": [{"cli": "codex"}]},
                "checkout": {"kind": "current_checkout"},
                "task_contract": {"user_required": [], "manager_confirmable": []},
            },
        )
    )

    config = callback._contract_callback_config(
        issue_dir=issue_dir,
        issue_name=issue_dir.name,
        workflow_id=blackboard.workflow_id,
    )

    assert config["clis"] == [{"cli": "codex"}]
    assert (issue_dir / "manager" / "contract.json").is_file()
    assert not (issue_dir / "driver" / "contract.json").exists()
    store = callback.EventManagerSessionStore(
        issue_dir / "manager",
        workflow_id=blackboard.workflow_id,
        cli=AgentCLI.CODEX,
        model="exact",
    )
    assert store.agent_name == callback.MANAGER_AGENT_NAME
    store.save_session(store.agent_name, AgentCLI.CODEX, "manager-session")
    store.commit()
    assert store.path.is_file()


class _HostProxy:
    """In-process control-socket peer exercising actual bytes over OS pipes."""

    def __init__(self, handler, *, bad_handshake=False):
        server_read, client_write = os.pipe()
        client_read, server_write = os.pipe()
        self.stdin = os.fdopen(client_write, "wb", buffering=0)
        self.stdout = os.fdopen(client_read, "rb", buffering=0)
        self.returncode = None
        self.requests = []
        self.failure = None
        self.worker = threading.Thread(
            target=self._serve,
            args=(server_read, server_write, handler, bad_handshake),
            daemon=True,
        )
        self.worker.start()

    def _serve(self, reader_fd, writer_fd, handler, bad_handshake):
        try:
            with os.fdopen(reader_fd, "rb", buffering=0) as reader, os.fdopen(
                writer_fd, "wb", buffering=0
            ) as writer:

                def read(count):
                    data = bytearray()
                    while len(data) < count:
                        chunk = reader.read(count - len(data))
                        if not chunk:
                            raise EOFError
                        data.extend(chunk)
                    return bytes(data)

                def frame(payload, opcode=1, final=True):
                    length = len(payload)
                    prefix = bytes([(0x80 if final else 0) | opcode])
                    if length < 126:
                        prefix += bytes([length])
                    elif length < 65536:
                        prefix += bytes([126]) + struct.pack("!H", length)
                    else:
                        prefix += bytes([127]) + struct.pack("!Q", length)
                    writer.write(prefix + payload)

                headers = bytearray()
                while not headers.endswith(b"\r\n\r\n"):
                    headers.extend(read(1))
                key = next(
                    line.split(b":", 1)[1].strip()
                    for line in headers.split(b"\r\n")
                    if line.startswith(b"Sec-WebSocket-Key:")
                )
                accept = base64.b64encode(
                    hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest()
                )
                if bad_handshake:
                    accept = b"incorrect"
                writer.write(
                    b"HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\n"
                    b"Upgrade: websocket\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n"
                )
                while True:
                    first, second = read(2)
                    assert first & 0x80 and second & 0x80
                    length = second & 127
                    if length == 126:
                        length = struct.unpack("!H", read(2))[0]
                    elif length == 127:
                        length = struct.unpack("!Q", read(8))[0]
                    mask = read(4)
                    payload = bytes(
                        value ^ mask[index % 4] for index, value in enumerate(read(length))
                    )
                    if first & 15 == 10:
                        assert payload == b"ping"
                        continue
                    message = json.loads(payload)
                    self.requests.append(message)
                    if "id" not in message:
                        continue
                    result = handler(message)
                    if result is None:
                        return  # Lost response after server-side admission.
                    if isinstance(result, tuple):
                        frame(b"ping", opcode=9)
                        # Notifications and server requests must never be answered.
                        frame(
                            json.dumps(
                                {
                                    "method": "item/commandExecution/requestApproval",
                                    "id": "server-request",
                                    "params": {},
                                }
                            ).encode()
                        )
                        result = result[0]
                        payload = json.dumps({"id": message["id"], "result": result}).encode()
                        frame(payload[:5], final=False)
                        frame(payload[5:], opcode=0)
                    else:
                        envelope = {"id": message["id"]}
                        envelope["error" if isinstance(result, _RPCRejection) else "result"] = (
                            {"code": -32600, "message": "rejected"}
                            if isinstance(result, _RPCRejection)
                            else result
                        )
                        frame(json.dumps(envelope).encode())
        except (EOFError, BrokenPipeError):
            pass
        except BaseException as error:
            self.failure = error

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0
        self.stdin.close()
        self.stdout.close()

    def kill(self):
        self.terminate()

    def wait(self, timeout):
        self.worker.join(timeout)
        assert not self.worker.is_alive(), "fake host leaked its transport"
        if self.failure is not None:
            raise self.failure
        return self.returncode


class _RPCRejection:
    pass


class _HostDaemon:
    def __init__(self, *, status="idle", latest="completed", behavior="normal"):
        self.status = status
        self.latest = latest
        self.behavior = behavior
        self.resume_params = None
        self.input = None
        self.client_id = None
        self.pending = []
        self.starts = 0

    def __call__(self, message):
        method, params = message["method"], message["params"]
        if method == "initialize":
            assert params["capabilities"] == {"experimentalApi": True}
            return {}
        assert params["threadId"] == "visible-thread"
        if method == "thread/read":
            return {
                "thread": {
                    "id": "visible-thread",
                    "status": {
                        "type": self.status,
                        **(
                            {"activeFlags": ["waitingOnApproval"]}
                            if self.status == "active"
                            else {}
                        ),
                    },
                    "model": "saved-model",
                }
            }
        if method == "thread/turns/list":
            assert params == {
                "threadId": "visible-thread",
                "limit": 1,
                "sortDirection": "desc",
                "itemsView": "notLoaded",
            }
            return {"data": [{"id": "previous-turn", "status": self.latest}]}
        if method == "thread/resume":
            self.resume_params = params
            if self.behavior == "resume_rejected":
                return _RPCRejection()
            self.status = "idle"
            return {"thread": {"id": "visible-thread"}}
        if method == "thread/queue/add":
            self.input = params["input"]
            self.client_id = params["clientUserMessageId"]
            self.pending = [{"id": "queued-callback"}]
            if self.behavior == "lost_ack":
                return None
            queued = {
                "queuedSubmission": {
                    "id": "queued-callback",
                    "clientUserMessageId": self.client_id,
                    "input": self.input,
                }
            }
            if self.behavior == "corrupt_ack":
                queued["queuedSubmission"]["clientUserMessageId"] = "someone-else"
            if self.behavior == "auto_started":
                self.pending = []
                self.status = "active"
            if self.behavior == "older_message":
                self.pending.insert(0, {"id": "older-user-message"})
            if self.behavior == "unloaded_after_ack":
                self.status = "notLoaded"
            if self.behavior == "stopped_after_add":
                self.status = "idle"
                self.latest = "interrupted"
            return (queued,) if self.behavior == "fragmented" else queued
        raise AssertionError(f"Unexpected RPC {method}")


@pytest.mark.parametrize("initial", ["notLoaded", "idle", "active"])
def test_host_callback_loads_original_thread_and_defers_dispatch_to_daemon(tmp_path, initial):
    callback = _callback_module()
    daemon = _HostDaemon(status=initial)
    proxy = _HostProxy(daemon)
    with patch.object(callback.subprocess, "Popen", return_value=proxy) as launch:
        callback._queue_host_callback(
            "wake", thread_id="visible-thread", model=None, repository_root=tmp_path
        )
    assert launch.call_count == 1
    assert daemon.resume_params == (
        {"threadId": "visible-thread", "excludeTurns": True} if initial == "notLoaded" else None
    )
    assert daemon.starts == 0
    assert daemon.input == [{"type": "text", "text": "wake"}]
    assert proxy.returncode == 0
    methods = [request["method"] for request in proxy.requests]
    assert not set(methods) & {
        "thread/start",
        "turn/start",
        "thread/queue/start",
        "thread/unarchive",
        "turn/interrupt",
    }


@pytest.mark.parametrize(
    "behavior,starts",
    [
        ("auto_started", 0),
        ("older_message", 0),
        ("stopped_after_add", 0),
        ("fragmented", 0),
    ],
)
def test_host_callback_handles_dispatch_races_and_preserves_fifo(tmp_path, behavior, starts):
    callback = _callback_module()
    daemon = _HostDaemon(behavior=behavior)
    proxy = _HostProxy(daemon)
    with patch.object(callback.subprocess, "Popen", return_value=proxy):
        callback._queue_host_callback(
            "wake" * 40000, thread_id="visible-thread", model=None, repository_root=tmp_path
        )
    assert daemon.starts == starts
    assert all(request.get("id") != "server-request" for request in proxy.requests)


@pytest.mark.parametrize(
    "initial,latest,behavior",
    [
        ("idle", "interrupted", "normal"),
        ("notLoaded", "interrupted", "normal"),
        ("systemError", "completed", "normal"),
        ("notLoaded", "completed", "resume_rejected"),
    ],
)
def test_host_callback_does_not_restart_paused_or_unresumable_threads(
    tmp_path, initial, latest, behavior
):
    callback = _callback_module()
    daemon = _HostDaemon(status=initial, latest=latest, behavior=behavior)
    proxy = _HostProxy(daemon)
    with patch.object(callback.subprocess, "Popen", return_value=proxy):
        with pytest.raises(callback._HostRPCError):
            callback._queue_host_callback(
                "wake", thread_id="visible-thread", model=None, repository_root=tmp_path
            )
    assert daemon.input is None
    assert daemon.starts == 0


@pytest.mark.parametrize("behavior", ["lost_ack", "corrupt_ack", "unloaded_after_ack"])
def test_host_callback_uncertain_delivery_stops_without_replay_or_fallback(
    tmp_path, monkeypatch, behavior
):
    callback = _callback_module()
    monkeypatch.setenv("CODEX_THREAD_ID", "visible-thread")
    driver_dir, _state, event = _contract_event_context(
        callback,
        tmp_path,
        [("codex", "exact"), ("gemini", "backup")],
        issue_name="issue456",
        event_type="human_task",
        bind_host=True,
    )
    daemon = _HostDaemon(behavior=behavior)
    proxy = _HostProxy(daemon)
    with patch.object(callback.subprocess, "Popen", return_value=proxy) as launch:
        callback.run_callback(event, repository_root=tmp_path)
        callback.run_callback(event, repository_root=tmp_path)
    assert launch.call_count == 1
    state = json.loads((driver_dir / "dispatch_state.json").read_text())
    delivery = state["events"][event["event_id"]]
    assert delivery["status"] == "recovery_pending"
    assert delivery["attempts"][-1]["outcome"] == "ambiguous"
    assert delivery["recovery_pending"] is True
    assert state["entries"][1]["session"] is None
    assert len([r for r in proxy.requests if r["method"] == "thread/queue/add"]) == 1


def test_host_callback_rejects_invalid_websocket_handshake(tmp_path):
    callback = _callback_module()
    proxy = _HostProxy(_HostDaemon(), bad_handshake=True)
    with patch.object(callback.subprocess, "Popen", return_value=proxy):
        with pytest.raises(ConnectionError, match="handshake"):
            callback._queue_host_callback(
                "wake", thread_id="visible-thread", model=None, repository_root=tmp_path
            )
    assert not proxy.requests


def test_host_transport_enforces_response_timeout_and_closes_proxy(tmp_path):
    callback = _callback_module()
    original_connection = callback._HostConnection

    def slow_initialize(_message):
        threading.Event().wait(0.2)
        return {}

    proxy = _HostProxy(slow_initialize)
    with patch.object(callback.subprocess, "Popen", return_value=proxy), patch.object(
        callback,
        "_HostConnection",
        side_effect=lambda process: original_connection(process, timeout=0.05),
    ):
        with pytest.raises(TimeoutError):
            callback._queue_host_callback(
                "wake", thread_id="visible-thread", model=None, repository_root=tmp_path
            )
    assert proxy.returncode == 0
    assert proxy.stdin.closed and proxy.stdout.closed


def test_host_transport_bounds_incoming_output_and_closes_proxy(tmp_path):
    callback = _callback_module()
    proxy = _HostProxy(lambda _message: {"oversized": "x" * (2 * 1024 * 1024)})
    with patch.object(callback.subprocess, "Popen", return_value=proxy):
        with pytest.raises(ValueError, match="limit exceeded"):
            callback._queue_host_callback(
                "wake", thread_id="visible-thread", model=None, repository_root=tmp_path
            )
    assert proxy.returncode == 0
    assert proxy.stdin.closed and proxy.stdout.closed


@pytest.mark.parametrize("mismatch", ["thread", "model"])
def test_host_callback_rejects_identity_or_model_changes_before_enqueue(tmp_path, mismatch):
    callback = _callback_module()
    daemon = _HostDaemon(status="notLoaded")

    def changed_identity(message):
        result = daemon(message)
        if mismatch == "thread" and message["method"] == "thread/read":
            result["thread"]["id"] = "different-thread"
        return result

    proxy = _HostProxy(changed_identity)
    with patch.object(callback.subprocess, "Popen", return_value=proxy):
        with pytest.raises((ValueError, callback._HostRPCError)):
            callback._queue_host_callback(
                "wake",
                thread_id="visible-thread",
                model="different-model" if mismatch == "model" else None,
                repository_root=tmp_path,
            )
    assert daemon.resume_params is None
    assert daemon.input is None
