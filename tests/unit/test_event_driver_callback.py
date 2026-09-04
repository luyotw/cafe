"""Skill-owned event driver binding and session persistence tests."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

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


def test_callback_prompt_reconciles_live_proactive_review_without_worker_control(
    tmp_path: Path,
) -> None:
    """U14 — a wakeup reports current review work but remains notification-only."""
    callback = _callback_module()
    proactive = callback._proactive_review_module()
    issue_dir = tmp_path / ".cafe" / "issues" / "issue456"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook_id: standard\n", encoding="utf-8")
    policy = {
        "playbook_id": "standard",
        "phases": [
            {
                "phase": phase,
                "selected": phase == "develop",
                "rationale": "The driver assessed this phase.",
                "factors": {
                    name: "assessed"
                    for name in (
                        "ambiguity", "novelty", "blast_radius", "protected_risk",
                        "durable_contract", "downstream_review", "late_correction", "cost",
                    )
                },
                **(
                    {
                        "reviewer": {"cli": "codex", "model": "gpt-5.6-sol"},
                        "ordering": "non_gating",
                        "initial_review_cost": {
                            "tokens": {"estimate": "2k"},
                            "latency": {"estimate": "one minute"},
                            "assumptions": "one output",
                            "delay_impact": "acceptance only",
                        },
                        "rereview_cost": {"foreseeable": False, "reason": "unknown"},
                    }
                    if phase == "develop"
                    else {}
                ),
            }
            for phase in ("spec", "plan", "develop", "review", "pr")
        ],
    }
    proactive.activate_contract(
        issue_dir=issue_dir,
        project_root=tmp_path,
        policy=policy,
        confirmation={
            "schema_version": 1,
            "issue_name": "issue456",
            "playbook_id": "standard",
            "proposal_digest": proactive.policy_digest(policy),
            "confirmed_by": "user",
            "confirmed_at": "2026-09-04T12:00:00+00:00",
        },
    )

    prompt = callback._callback_prompt(
        {"issue": "issue456", "workflow_id": "workflow", "event_type": "phase_terminal"},
        repository_root=tmp_path,
    )

    assert "proactive review obligations" in prompt
    assert "non_gating" in prompt
    assert "not a workflow advancement gate" in prompt

    # A pre-marker active contract is valid after upgrade. Its first current
    # read must establish loss-detection evidence before any later deletion.
    activation_marker = proactive.contract_path(issue_dir).with_name(
        proactive.ACTIVATION_FILENAME
    )
    activation_marker.unlink()
    upgraded_prompt = callback._callback_prompt(
        {"issue": "issue456", "workflow_id": "workflow", "event_type": "phase_terminal"},
        repository_root=tmp_path,
    )
    assert "proactive review obligations" in upgraded_prompt
    assert activation_marker.is_file()

    proactive.contract_path(issue_dir).unlink()
    upgraded_missing_contract_prompt = callback._callback_prompt(
        {"issue": "issue456", "workflow_id": "workflow", "event_type": "phase_terminal"},
        repository_root=tmp_path,
    )
    assert "reconfirmation-required" in upgraded_missing_contract_prompt

    proactive.activate_contract(
        issue_dir=issue_dir,
        project_root=tmp_path,
        policy=policy,
        confirmation={
            "schema_version": 1,
            "issue_name": "issue456",
            "playbook_id": "standard",
            "proposal_digest": proactive.policy_digest(policy),
            "confirmed_by": "user",
            "confirmed_at": "2026-09-04T12:00:00+00:00",
        },
    )

    proactive.contract_path(issue_dir).write_text("[]\n", encoding="utf-8")
    stale_prompt = callback._callback_prompt(
        {"issue": "issue456", "workflow_id": "workflow", "event_type": "phase_terminal"},
        repository_root=tmp_path,
    )
    assert "reconfirmation-required" in stale_prompt

    proactive.contract_path(issue_dir).unlink()
    missing_contract_prompt = callback._callback_prompt(
        {"issue": "issue456", "workflow_id": "workflow", "event_type": "phase_terminal"},
        repository_root=tmp_path,
    )
    assert "reconfirmation-required" in missing_contract_prompt

    proactive.contract_path(issue_dir).mkdir()
    malformed_path_prompt = callback._callback_prompt(
        {"issue": "issue456", "workflow_id": "workflow", "event_type": "phase_terminal"},
        repository_root=tmp_path,
    )
    assert "reconfirmation-required" in malformed_path_prompt

    legacy_issue_dir = tmp_path / ".cafe" / "issues" / "legacy"
    legacy_issue_dir.mkdir(parents=True)
    assert callback._proactive_review_reconciliation("legacy", repository_root=tmp_path) is None
    assert not (legacy_issue_dir / "driver").exists()

    legacy_review_dir = legacy_issue_dir / "driver" / "proactive_review"
    legacy_review_dir.mkdir(parents=True)
    assert callback._proactive_review_reconciliation("legacy", repository_root=tmp_path) is None
    assert list(legacy_review_dir.iterdir()) == []
