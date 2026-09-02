"""Production-seam journeys for the version 2 workflow driver contract."""

from __future__ import annotations

import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from cafe.agents.executor import AgentExecutor
from cafe.core.blackboard import BlackboardStore
from cafe.core.driver_policy import DriverPolicyContract, extract_driver_policy
from cafe.core.driver_runtime import DriverCoordinator, DriverDecision
from cafe.core.driver_transport import (
    DRIVER_AGENT_NAME,
    BlackboardDriverSessionStore,
    DelegatedDriverTransport,
)
from cafe.core.issue_policy_store import IssuePolicyStore
from cafe.core.types import AgentCLI, AgentResponse, TokenUsage
from cafe.core.v2_workflow_runtime import Version2WorkflowRuntime
from cafe.core.workflow_hosting import WorkflowHost
from cafe.core.workflow_models import PlaybookRunResult, StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.playbooks.loader import PlaybookLoader
from cafe.services.summary_display import SummaryDisplay
from cafe.services.summary_service import SummaryService
from cafe.ui.cli import app


def _init_repository(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "develop"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "cafe-test@local.invalid"],
        cwd=path,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "CAFE Test"], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "Initial"],
        cwd=path,
        check=True,
        capture_output=True,
    )


def _policy(mode: str) -> DriverPolicyContract:
    driver: dict[str, object] = {"mode": mode}
    if mode == "attached":
        driver["poll_interval_seconds"] = 10
    elif mode == "delegated":
        driver.update({"cli": "codex", "model": "exact-driver-model"})
    return DriverPolicyContract.model_validate({"contract_version": 2, "driver": driver})


def _decision(packet) -> DriverDecision:
    return DriverDecision(
        workflow_id=packet.workflow_id,
        sequence=packet.sequence,
        requested_action=packet.requested_action,
        completed_phase=packet.completed_phase,
        boundary_id=packet.boundary_id,
        contract_version=packet.contract_version,
        driver_cli=packet.driver_cli,
        driver_model=packet.driver_model,
        action="advance",
    )


class _TwoPhaseRuntime:
    steps = {"spec": {}, "plan": {}}

    def __init__(self, issue_dir: Path) -> None:
        self.blackboard_store = BlackboardStore(issue_dir)
        self.blackboard = self.blackboard_store.load_or_create("spec")
        self.executed: list[str] = []

    def run(self, *, start_step=None, single_step=False, **_kwargs) -> PlaybookRunResult:
        assert single_step is True
        step = start_step or self.blackboard.current_step
        self.executed.append(step)
        if step == "spec":
            self.blackboard_store.record_event(
                self.blackboard,
                "transition",
                {
                    "from": "spec",
                    "to": "plan",
                    "status_code": "ready_for_plan",
                    "source": "journey.runtime",
                    "runtime": "single_step",
                },
            )
            self.blackboard_store.set_current_step(self.blackboard, "plan")
            return PlaybookRunResult(
                final_step="spec", final_status_code="ready_for_plan", completed=False
            )
        self.blackboard_store.set_current_step(self.blackboard, "done")
        return PlaybookRunResult(
            final_step="plan", final_status_code="workflow_complete", completed=True
        )


def test_supported_app_prepares_updates_and_corrects_policy_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.conftest import create_minimal_config

    create_minimal_config(tmp_path)
    _init_repository(tmp_path)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    prepared = runner.invoke(
        app,
        [
            "prepare",
            "journey",
            "--base",
            "develop",
            "--no-check",
            "--driver-contract-version",
            "2",
            "--driver-mode",
            "unattended",
        ],
    )

    assert prepared.exit_code == 0, (prepared.stdout, prepared.exception)
    config_path = tmp_path / ".cafe" / "issues" / "journey" / "issue.yaml"
    assert extract_driver_policy(
        yaml.safe_load(config_path.read_text(encoding="utf-8"))
    ) == _policy("unattended")

    for args, expected in (
        (
            ["--driver-mode", "attached", "--poll-interval-seconds", "15"],
            DriverPolicyContract.model_validate(
                {
                    "contract_version": 2,
                    "driver": {"mode": "attached", "poll_interval_seconds": 15},
                }
            ),
        ),
        (
            [
                "--driver-mode",
                "delegated",
                "--delegated-cli",
                "codex",
                "--delegated-model",
                "exact-driver-model",
            ],
            _policy("delegated"),
        ),
    ):
        updated = runner.invoke(
            app,
            [
                "update-driver-policy",
                "journey",
                "--contract-version",
                "2",
                *args,
            ],
        )
        assert updated.exit_code == 0, (updated.stdout, updated.exception)
        assert (
            extract_driver_policy(yaml.safe_load(config_path.read_text(encoding="utf-8")))
            == expected
        )

    before_invalid = config_path.read_bytes()
    rejected = runner.invoke(
        app,
        [
            "update-driver-policy",
            "journey",
            "--contract-version",
            "2",
            "--driver-mode",
            "delegated",
            "--delegated-cli",
            "codex",
        ],
    )
    assert rejected.exit_code != 0
    assert config_path.read_bytes() == before_invalid


def test_attached_polling_is_read_only_and_all_modes_hold_live_authority(
    tmp_path: Path,
) -> None:
    issues_root = tmp_path / ".cafe" / "issues"
    for mode in ("attached", "unattended", "delegated"):
        issue_dir = issues_root / mode
        issue_dir.mkdir(parents=True)
        config_path = issue_dir / "issue.yaml"
        configured = _policy(mode)
        config_path.write_text(yaml.safe_dump(configured.model_dump(mode="json")), encoding="utf-8")
        phase_entered = Event()
        release_phase = Event()

        class BlockingRuntime(_TwoPhaseRuntime):
            def run(self, **kwargs) -> PlaybookRunResult:
                phase_entered.set()
                assert release_phase.wait(timeout=2)
                return super().run(**kwargs)

        runtime = BlockingRuntime(issue_dir)
        v2 = Version2WorkflowRuntime(
            runtime,
            configured,
            delegated_decision_provider=(
                (lambda packet: _decision(packet)) if mode == "delegated" else None
            ),
            policy_authority=IssuePolicyStore(config_path).locked_policy,
        )
        replacement = _policy("unattended" if mode != "unattended" else "attached")

        with ThreadPoolExecutor(max_workers=2) as pool:
            running = pool.submit(v2.run, single_step=True)
            assert phase_entered.wait(timeout=2)
            replacing = pool.submit(IssuePolicyStore(config_path).replace, replacement)
            time.sleep(0.05)
            assert replacing.done() is False
            release_phase.set()
            running.result(timeout=2)
            replacing.result(timeout=2)

        if mode == "attached":
            IssuePolicyStore(config_path).replace(configured)
            before = BlackboardStore(issue_dir).load_or_create("spec").to_dict()
            service = SummaryService(issues_root=issues_root)
            first = service.load_driver_status(mode)
            second = service.load_driver_status(mode)
            after = BlackboardStore(issue_dir).load_or_create("spec").to_dict()
            assert first == second
            assert before == after
            assert first["policy"]["driver"]["mode"] == "attached"
            assert "session" not in json.dumps(first)


@pytest.mark.parametrize("hosting", ["foreground", "background"])
def test_unattended_hosting_keeps_runtime_truth_and_heartbeat_lease(
    tmp_path: Path, hosting: str
) -> None:
    issue_dir = tmp_path / hosting
    playbook = PlaybookLoader().load("direct", strict=True)

    def executor(_step_name: str, _step_def: dict, _state: object) -> StepExecutionResult:
        time.sleep(0.06)
        return StepExecutionResult(response="confirmed", artifacts={}, status_code="confirmed")

    runner = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    host = WorkflowHost(
        issue_dir,
        lease_ttl_seconds=1,
        lease_renew_interval_seconds=0.01,
    )

    result = host.run_worker(
        lambda: Version2WorkflowRuntime(runner, _policy("unattended")).run(
            start_step="develop", single_step=True
        ),
        hosting=hosting,
    )

    assert result.result.final_step == "develop"
    state = BlackboardStore(issue_dir).load_or_create("develop")
    assert state.driver_state["advancement_lease"] is None
    assert state.driver_state["worker"]["status"] == "stopped"
    assert any(event.step == "develop" for event in state.events)


@pytest.mark.parametrize("cli", ["claude", "codex", "gemini", "copilot", "cursor-agent"])
def test_five_adapters_acquire_and_resume_exact_blackboard_identity(
    tmp_path: Path, cli: str
) -> None:
    issue_dir = tmp_path / cli
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")
    policy = DriverPolicyContract.model_validate(
        {
            "contract_version": 2,
            "driver": {"mode": "delegated", "cli": cli, "model": "exact-model"},
        }
    )
    packet = DriverCoordinator(store, state).open_boundary(
        completed_phase="spec", requested_action="plan", policy=policy
    )
    sessions = BlackboardDriverSessionStore(
        store,
        state,
        acquisition_sequence=packet.sequence,
        requested_model="exact-model",
    )
    transport = DelegatedDriverTransport(policy, sessions)
    attempted: list[tuple[str | None, str | None]] = []
    cli_enum = AgentCLI(cli)

    def execute(executor, *_args, **_kwargs):
        attempted.append((executor.config.session_id, executor.config.model))
        return AgentResponse(
            response=json.dumps(_decision(packet).model_dump(mode="json")),
            token_usage=TokenUsage(),
            cli=cli_enum,
            session_id=f"driver-{cli}",
        )

    with patch.object(AgentExecutor, "execute", execute):
        assert transport.request_decision(packet).action == "advance"
        assert transport.request_decision(packet).action == "advance"

    assert attempted == [(None, "exact-model"), (f"driver-{cli}", "exact-model")]
    saved = sessions.load_session(DRIVER_AGENT_NAME, cli_enum)
    assert saved is not None
    assert saved.session_id == f"driver-{cli}"


def test_delegated_restart_recovers_missing_boundary_and_consumes_once(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / "delegated-restart"
    staged = _TwoPhaseRuntime(issue_dir)
    staged.blackboard_store.record_event(
        staged.blackboard,
        "transition",
        {
            "from": "spec",
            "to": "plan",
            "status_code": "ready_for_plan",
            "source": "journey.crash",
            "runtime": "single_step",
        },
    )
    staged.blackboard_store.set_current_step(staged.blackboard, "plan")
    calls: list[int] = []

    def decide(packet):
        calls.append(packet.sequence)
        return _decision(packet)

    resumed = _TwoPhaseRuntime(issue_dir)
    result = Version2WorkflowRuntime(
        resumed,
        _policy("delegated"),
        delegated_decision_provider=decide,
    ).run()

    assert result.completed is True
    assert resumed.executed == ["plan"]
    state = BlackboardStore(issue_dir).load_or_create("spec")
    assert calls == [1]
    assert state.driver_state["consumed_sequences"] == [1]
    assert len(state.driver_state["decisions"]) == 1


def test_delegated_restart_skips_phase_with_durable_transition_before_pointer(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / "transition-pointer-crash"
    playbook = {
        "playbook": {"id": "transition-pointer-crash"},
        "steps": {
            "spec": {
                "skill": "spec_first",
                "role": "pm",
                "valid_intents": ["await_agent"],
                "on": {"await_agent": "plan"},
            },
            "plan": {
                "skill": "spec_first",
                "role": "developer",
                "valid_intents": ["workflow_complete"],
                "on": {"workflow_complete": "_done"},
            },
        },
    }
    executed: list[str] = []

    def executor(step_name: str, _step_def: dict, _state: object) -> StepExecutionResult:
        executed.append(step_name)
        status_code = "await_agent" if step_name == "spec" else "workflow_complete"
        return StepExecutionResult(response=status_code, artifacts={}, status_code=status_code)

    decisions: list[int] = []

    def decide(packet):
        decisions.append(packet.sequence)
        return _decision(packet)

    interrupted = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    publish_current_step = interrupted.blackboard_store.set_current_step

    def interrupt_before_plan(state, step: str) -> None:
        if step == "plan":
            raise RuntimeError("interrupted before current-step publication")
        publish_current_step(state, step)

    with patch.object(
        interrupted.blackboard_store,
        "set_current_step",
        side_effect=interrupt_before_plan,
    ):
        with pytest.raises(RuntimeError, match="current-step publication"):
            Version2WorkflowRuntime(
                interrupted,
                _policy("delegated"),
                delegated_decision_provider=decide,
            ).run(start_step="spec")

    crashed = BlackboardStore(issue_dir).load_or_create("spec")
    assert crashed.current_step == "spec"
    assert [
        (event.data["from"], event.data["to"])
        for event in crashed.events
        if event.event_type == "transition"
    ] == [("spec", "plan")]

    resumed = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = Version2WorkflowRuntime(
        resumed,
        _policy("delegated"),
        delegated_decision_provider=decide,
    ).run()

    assert result.completed is True
    assert executed == ["spec", "plan"]
    assert decisions == [1]


@pytest.mark.parametrize("mode", ["attached", "unattended", "delegated"])
@pytest.mark.parametrize(
    "interrupt_after",
    ["event", "next_step", "blackboard_contract", "pointer", "legacy_pointer"],
)
def test_each_mode_restarts_terminally_across_completion_publication_boundaries(
    tmp_path: Path,
    mode: str,
    interrupt_after: str,
) -> None:
    issue_dir = tmp_path / "completion-pointer-crash"
    playbook = {
        "playbook": {"id": "completion-pointer-crash"},
        "steps": {
            "spec": {
                "skill": "spec_first",
                "role": "pm",
                "valid_intents": ["await_agent"],
                "on": {"await_agent": "plan"},
            },
            "plan": {
                "skill": "spec_first",
                "role": "developer",
                "valid_intents": ["workflow_complete"],
                "on": {"workflow_complete": "_done"},
            },
        },
    }
    executed: list[str] = []

    def executor(step_name: str, _step_def: dict, _state: object) -> StepExecutionResult:
        executed.append(step_name)
        status_code = "await_agent" if step_name == "spec" else "workflow_complete"
        return StepExecutionResult(response=status_code, artifacts={}, status_code=status_code)

    decisions: list[int] = []

    def decide(packet):
        decisions.append(packet.sequence)
        return _decision(packet)

    interrupted = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    store = interrupted.blackboard_store
    record_event = store.record_event
    save = store.save
    update_handoff = store.update_handoff_contract
    publish_current_step = interrupted.blackboard_store.set_current_step

    def interrupt_after_event(state, event_type: str, data: dict) -> None:
        record_event(state, event_type, data)
        if event_type == "workflow_completed":
            raise RuntimeError("interrupted after event publication")

    def interrupt_during_contract_save(
        state,
        *,
        capability_receipts_authoritative: bool = False,
    ) -> None:
        contract = state.handoff_contract
        if contract is not None and contract.to_step == "done":
            if interrupt_after == "blackboard_contract":
                save(
                    state,
                    capability_receipts_authoritative=capability_receipts_authoritative,
                )
            raise RuntimeError(f"interrupted after {interrupt_after} publication")
        save(
            state,
            capability_receipts_authoritative=capability_receipts_authoritative,
        )

    def interrupt_after_pointer(state, step: str) -> None:
        publish_current_step(state, step)
        if step == "done":
            raise RuntimeError("interrupted after pointer publication")

    def interrupt_with_legacy_pointer(state, **kwargs) -> None:
        if kwargs.get("to_step") == "done":
            publish_current_step(state, "done")
            raise RuntimeError("interrupted after legacy_pointer publication")
        update_handoff(state, **kwargs)

    if interrupt_after == "event":
        interruption = patch.object(store, "record_event", side_effect=interrupt_after_event)
    elif interrupt_after in {"next_step", "blackboard_contract"}:
        interruption = patch.object(store, "save", side_effect=interrupt_during_contract_save)
    elif interrupt_after == "pointer":
        interruption = patch.object(
            store,
            "set_current_step",
            side_effect=interrupt_after_pointer,
        )
    else:
        interruption = patch.object(
            store,
            "update_handoff_contract",
            side_effect=interrupt_with_legacy_pointer,
        )
    with interruption:
        if mode == "attached":
            first = Version2WorkflowRuntime(interrupted, _policy(mode)).run(start_step="spec")
            assert first.completed is False
        with pytest.raises(RuntimeError, match=f"after {interrupt_after} publication"):
            Version2WorkflowRuntime(
                interrupted,
                _policy(mode),
                delegated_decision_provider=decide if mode == "delegated" else None,
            ).run(start_step=None if mode == "attached" else "spec")

    crashed_payload = json.loads((issue_dir / "blackboard.json").read_text(encoding="utf-8"))
    crashed_contract = crashed_payload["handoff_contract"]
    next_step = json.loads((issue_dir / "next_step.txt").read_text(encoding="utf-8"))
    publication_reached = {
        "event": ("plan", "plan", "plan"),
        "next_step": ("plan", "done", "plan"),
        "blackboard_contract": ("plan", "done", "done"),
        "pointer": ("done", "done", "done"),
        "legacy_pointer": ("done", "plan", "plan"),
    }
    assert (
        crashed_payload["current_step"],
        next_step["to_step"],
        crashed_contract["to_step"],
    ) == publication_reached[interrupt_after]
    assert [
        event["data"]["step"]
        for event in crashed_payload["events"]
        if event["event_type"] == "workflow_completed"
    ] == ["plan"]

    resumed = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = Version2WorkflowRuntime(
        resumed,
        _policy(mode),
        delegated_decision_provider=decide if mode == "delegated" else None,
    ).run()

    assert result.completed is True
    assert executed == ["spec", "plan"]
    assert decisions == ([1] if mode == "delegated" else [])
    recovered = BlackboardStore(issue_dir).load_or_create("spec")
    assert recovered.current_step == "done"
    terminal_handoff = BlackboardStore(issue_dir).load_handoff_contract(
        recovered,
        allowed_steps=list(playbook["steps"]),
    )
    assert terminal_handoff.to_owner.value == "done"
    assert terminal_handoff.to_step == "done"
    assert terminal_handoff.intent.value == "workflow_complete"
    assert len(
        [event for event in recovered.events if event.event_type == "workflow_completed"]
    ) == 1

    for _ in range(2):
        restarted = BlackboardWorkflowRuntime(
            issue_dir=issue_dir,
            playbook=playbook,
            executor=executor,
        )
        restarted_result = Version2WorkflowRuntime(
            restarted,
            _policy(mode),
            delegated_decision_provider=decide if mode == "delegated" else None,
        ).run()

        assert restarted_result.completed is True
        assert executed == ["spec", "plan"]
        assert decisions == ([1] if mode == "delegated" else [])


def test_lifecycle_and_later_inspection_remain_session_safe(tmp_path: Path) -> None:
    issues_root = tmp_path / ".cafe" / "issues"
    issue_dir = issues_root / "lifecycle-inspection"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text(
        yaml.safe_dump(_policy("delegated").model_dump(mode="json")),
        encoding="utf-8",
    )
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("review")
    with store.driver_transaction(state) as persisted:
        persisted.driver_state["session"] = {"session_id": "secret-session"}
    DriverCoordinator(store, state).record_lifecycle(
        "permission", reason="operator approval required"
    )

    status = SummaryService(issues_root=issues_root).load_driver_status(issue_dir.name)
    rendered = SummaryDisplay().format_driver_status(status)

    assert status["reason"] == "operator approval required"
    assert "secret-session" not in json.dumps(status)
    assert "Reason: operator approval required" in rendered
    assert "notification_receipts" not in store.load_or_create("review").driver_state
