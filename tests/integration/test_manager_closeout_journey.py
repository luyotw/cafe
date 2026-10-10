"""Result acceptance cannot change Manager-owned terminal selection or replay effects."""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cafe.core.blackboard import BlackboardStore
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.manager import ActivateConfirmedContract, activate_confirmed_contract
from cafe.manager.closeout import inspect_closeout, accepted_delivery_result
from cafe.manager.task_completion import complete_manager_task
from cafe.ui.human_tasks import apply_human_task_payload
from tests.integration import test_development_delivery_journey as journey
from tests.unit._kickoff_test_support import load_kickoff_module
from tests.unit.test_manager_contract_application import _manager_proposal


@pytest.fixture
def context(tmp_path):
    local = journey.local_action.__wrapped__(tmp_path)
    return journey.setup_action(local, tmp_path)


def activate(context, command, choice="cleanup"):
    root, _, issue, state = context[:4]
    proposal = _manager_proposal()
    proposal["delivery_contract"].update(schema_version=6)
    proposal["delivery_contract"].pop("closeout_plan")
    proposal["closeout_contract"] = {
        "schema_version": 1,
        "choice": choice,
        "plan": {"cleanup": [{"argv": command}]},
        "delivery_result": {
            "step": context[-1],
            "task_id": "delivery-outcome",
            "artifact": "delivery_result",
        },
    }
    proposal["confirmation_contract"] = {
        "user_required": [],
        "manager_confirmable": [context[-1]],
        "mandatory_human_stops": [],
    }
    activate_confirmed_contract(
        ActivateConfirmedContract(
            issue_dir=issue,
            issue_name="journey",
            workflow_id=state.workflow_id,
            proposal=proposal,
            confirmed_by="user",
            confirmed_at=datetime.now(timezone.utc),
        )
    )
    return proposal


def accept(context, monkeypatch, *, manager=False):
    root, _, issue, state, _, data, engine, kwargs, _, delivery = context
    journey.approve_action(context)
    completed = journey.hook(engine, "prepare_input", "DevelopmentDeliveryExecutor", kwargs)
    assert completed.context_updates["delivery_complete"] == "true"
    journey.hook(engine, "publish_output", "DevelopmentDeliveryOutcome", kwargs)
    task = HumanTaskRecordStore(issue).tasks()[-1]
    assert "Closeout plan" not in task.prompt
    assert set(task.continuations) == {"confirm", "revise"}
    assert not (issue / "delivery/closeout.json").exists()
    journey.pause(issue, state, delivery)
    response = {"task": task.policy_id, "human_task_id": task.id, "decision": "confirm"}
    if manager:
        # Isolate authority evaluation (covered by task-authority tests); exercise
        # the actual Manager API, neutral completion and atomic provenance writer.
        owner_module = (
            "cafe.driver.task_completion" if manager == "driver" else "cafe.manager.task_completion"
        )
        if manager == "driver":
            from cafe.driver.task_completion import complete_driver_task as completion_api
        else:
            completion_api = complete_manager_task
        monkeypatch.setattr(
            owner_module + ".inspect_task_authority",
            lambda *a, **k: {
                "allowed": True,
                "contract_sha256": "a" * 64,
                "sources_sha256": "b" * 64,
            },
        )
        completed = completion_api(
            issue,
            task.id,
            response=response,
            evidence={},
            contract_sha256="a" * 64,
            sources_sha256="b" * 64,
        )
        assert completed["continuation"] == "done"
        assert accepted_delivery_result(issue, state.workflow_id)["authority"] == {
            "kind": "manager_proxy",
            "contract_sha256": "a" * 64,
            "sources_sha256": "b" * 64,
        }
    else:
        reply = apply_human_task_payload(
            issue_dir=issue,
            playbook_data=data,
            blackboard=state,
            from_step=delivery,
            trigger="confirm_output",
            raw_payload=response,
            source="test",
        )
        assert reply.target == "done", reply.rejection
        BlackboardStore(issue).set_current_step(state, "done")
    return task


def executor(context):
    root, _, issue, state = context[:4]
    script = (
        Path(__file__).resolve().parents[2]
        / "src/cafe/data/skills/use-cafe-workflow/scripts/execute_closeout.py"
    )
    return [
        sys.executable,
        str(script),
        "--project-root",
        str(root),
        "--issue-dir",
        str(issue),
        "--issue-name",
        "journey",
        "--workflow-id",
        state.workflow_id,
    ]


@pytest.mark.parametrize("manager", [False, True])
def test_confirmed_cleanup_survives_result_confirmation_and_executes_once(
    context, tmp_path, monkeypatch, manager
):
    marker = tmp_path / "cleanup-count"
    command = [
        sys.executable,
        "-c",
        f"from pathlib import Path; p=Path({str(marker)!r}); p.write_text(p.read_text()+'x' if p.exists() else 'x')",
    ]
    activate(context, command)
    accept(context, monkeypatch, manager=manager)
    selected = inspect_closeout(context[2], context[3].workflow_id)
    assert selected["status"] == "accepted" and selected["selection"]["choice"] == "cleanup"
    assert not marker.exists()
    argv = executor(context)
    initialized = subprocess.run([*argv, "--initialize"], capture_output=True, text=True)
    assert initialized.returncode == 0, initialized.stderr
    run = subprocess.run(
        [*argv, "--execute", "--stage", "cleanup", "--index", "0"], capture_output=True, text=True
    )
    assert run.returncode == 0, run.stderr
    assert marker.read_text() == "x"
    replay = subprocess.run(
        [*argv, "--execute", "--stage", "cleanup", "--index", "0"], capture_output=True, text=True
    )
    assert replay.returncode != 0 and marker.read_text() == "x"


@pytest.mark.parametrize("choice", ["archive", "leave", "pending"])
def test_confirmation_preserves_other_closeout_choices(context, tmp_path, monkeypatch, choice):
    command = [sys.executable, "-c", "raise RuntimeError('unselected cleanup must not execute')"]
    activate(context, command, choice)
    accept(context, monkeypatch, manager=True)
    result = inspect_closeout(context[2], context[3].workflow_id)
    if choice == "pending":
        assert result == {"status": "pending", "selection": None}
    else:
        assert result["selection"]["choice"] == choice
    from cafe.manager._store import load_contract
    from cafe.manager.closeout import execution_plan

    plan = execution_plan(load_contract(context[2])[0])
    assert plan == {
        "cleanup": [{"argv": ["cafe", "close", "--archive-only"]}] if choice == "archive" else []
    }
    if choice != "archive":
        run = subprocess.run(
            [*executor(context), "--execute", "--stage", "cleanup", "--index", "0"],
            capture_output=True,
        )
        assert run.returncode != 0


@pytest.mark.parametrize(
    "change", ["not_done", "report", "contract", "unknown", "dirty", "worker_active"]
)
def test_changed_or_incomplete_evidence_cannot_execute_cleanup(
    context, tmp_path, monkeypatch, change
):
    marker = tmp_path / "effect"
    command = [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"]
    activate(context, command)
    accept(context, monkeypatch)
    issue = context[2]
    initialized = subprocess.run(
        [*executor(context), "--initialize"], capture_output=True, text=True
    )
    assert initialized.returncode == 0, initialized.stderr
    if change == "not_done":
        board = json.loads((issue / "blackboard.json").read_text())
        board["current_step"] = context[-1]
        (issue / "blackboard.json").write_text(json.dumps(board))
    elif change == "report":
        report = next((issue / "delivery").glob("*/result.json"))
        value = json.loads(report.read_text())
        value["complete"] = False
        report.write_text(json.dumps(value))
    elif change == "contract":
        path = issue / "manager/contract.json"
        value = json.loads(path.read_text())
        value["closeout_contract"]["choice"] = "leave"
        path.write_text(json.dumps(value))
    elif change == "dirty":
        (context[0] / "unsaved-user-work").write_text("retain me")
    elif change == "worker_active":
        import fcntl

        lock = (issue / ".workflow-advancement.lock").open("a+b")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:
        helper = load_kickoff_module("execute_closeout")
        argv = executor(context)
        initialized = subprocess.run([*argv, "--initialize"], capture_output=True, text=True)
        assert initialized.returncode == 0, initialized.stderr
        path, _ = helper._paths(context[0], "journey", context[3].workflow_id)
        receipt = json.loads(path.read_text())
        receipt["commands"]["cleanup"][0]["status"] = "unknown"
        path.write_text(json.dumps(receipt))
    run = subprocess.run(
        [*executor(context), "--execute", "--stage", "cleanup", "--index", "0"],
        capture_output=True,
        text=True,
    )
    assert run.returncode != 0
    if change == "worker_active":
        lock.close()
        assert "not quiescent" in run.stderr
    if change == "dirty":
        assert (context[0] / "unsaved-user-work").read_text() == "retain me"
        assert "uncommitted or untracked" in run.stderr
    assert not marker.exists()


@pytest.mark.parametrize("kind", ["manager_proxy", "unknown", "user_submission"])
def test_legacy_combined_result_uses_host_provenance_not_source(
    context, tmp_path, monkeypatch, kind
):
    from tests.integration.test_delivery_combined_decisions import activate_closeout, terminal_reply

    command = [sys.executable, "-c", "pass"]
    activate_closeout(context, command)
    journey.approve_action(context)
    journey.hook(context[6], "prepare_input", "DevelopmentDeliveryExecutor", context[7])
    actor = None if kind == "unknown" else {"kind": kind}
    if kind == "manager_proxy":
        actor.update(contract_sha256="a" * 64, sources_sha256="b" * 64)
    reply = terminal_reply(context, "confirm", completion_authority=actor)
    assert reply.target == "done", reply.rejection
    BlackboardStore(context[2]).set_current_step(context[3], "done")
    result = inspect_closeout(context[2], context[3].workflow_id)
    assert result["status"] == ("accepted" if kind == "user_submission" else "pending")
    if kind == "user_submission":
        assert result["selection"]["choice"] == "leave"
    assert HumanTaskRecordStore(context[2]).results()[-1].source == "test"


def test_completed_delivery_acceptance_can_precede_another_step(context, monkeypatch):
    activate(context, [sys.executable, "-c", "pass"])
    # A declared extra step follows acceptance; closeout still waits for all work.
    context[5]["steps"]["finalize"] = {"assignee_type": "agent", "on": {"await_agent": "_done"}}
    outcome = next(
        t
        for t in context[5]["steps"][context[-1]]["human_tasks"]
        if t["task_id"] == "delivery-outcome"
    )
    outcome["outcomes"]["confirm"] = "finalize"
    journey.approve_action(context)
    journey.hook(context[6], "prepare_input", "DevelopmentDeliveryExecutor", context[7])
    journey.hook(context[6], "publish_output", "DevelopmentDeliveryOutcome", context[7])
    task = HumanTaskRecordStore(context[2]).tasks()[-1]
    journey.pause(context[2], context[3], context[-1])
    response = {"task": task.policy_id, "human_task_id": task.id, "decision": "confirm"}
    reply = apply_human_task_payload(
        issue_dir=context[2],
        playbook_data=context[5],
        blackboard=context[3],
        from_step=context[-1],
        trigger="confirm_output",
        raw_payload=response,
        source="test",
    )
    assert reply.target == "finalize", reply.rejection
    with pytest.raises(ValueError, match="completion"):
        inspect_closeout(context[2], context[3].workflow_id)
    BlackboardStore(context[2]).set_current_step(context[3], "done")
    assert inspect_closeout(context[2], context[3].workflow_id)["selection"]["choice"] == "cleanup"


def test_old_contract_new_pure_result_has_pending_manager_closeout(context, monkeypatch):
    from tests.integration.test_delivery_combined_decisions import activate_closeout

    activate_closeout(context, [sys.executable, "-c", "pass"])
    # Simulate upgrade before outcome creation: only the new phase declaration runs.
    import shutil

    shutil.rmtree(context[0] / ".cafe/skills/cafe-deliver_development")
    outcome = next(
        t
        for t in context[5]["steps"][context[-1]]["human_tasks"]
        if t["task_id"] == "delivery-outcome"
    )
    outcome["outcomes"].pop("confirm_cleanup")
    outcome["outcomes"].pop("confirm_archive")
    journey.approve_action(context)
    journey.hook(context[6], "prepare_input", "DevelopmentDeliveryExecutor", context[7])
    journey.hook(context[6], "publish_output", "DevelopmentDeliveryOutcome", context[7])
    task = HumanTaskRecordStore(context[2]).tasks()[-1]
    assert "Closeout plan" not in task.prompt
    journey.pause(context[2], context[3], context[-1])
    reply = apply_human_task_payload(
        issue_dir=context[2],
        playbook_data=context[5],
        blackboard=context[3],
        from_step=context[-1],
        trigger="confirm_output",
        source="test",
        raw_payload={"task": task.policy_id, "human_task_id": task.id, "decision": "confirm"},
        completion_authority={"kind": "user_submission"},
    )
    assert reply.target == "done", reply.rejection
    BlackboardStore(context[2]).set_current_step(context[3], "done")
    assert inspect_closeout(context[2], context[3].workflow_id) == {
        "status": "pending",
        "selection": None,
    }


def test_archive_selection_has_durable_receipt_and_archived_cost_validation(
    context, tmp_path, monkeypatch
):
    import shutil
    from cafe.manager.costs import _archive

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root, _, issue, state = context[:4]
    activate(context, [sys.executable, "-c", "raise RuntimeError('do not run cleanup')"], "archive")
    accept(context, monkeypatch, manager=True)
    script = load_kickoff_module("execute_closeout")
    argv = executor(context)
    initialized = subprocess.run([*argv, "--initialize"], capture_output=True, text=True)
    assert initialized.returncode == 0, initialized.stderr
    archive = _archive(root, "journey")
    real_run = subprocess.run
    effects = []

    def run(command, **kwargs):
        if command == ["cafe", "close", "--archive-only"]:
            effects.append(list(command))
            archive.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(issue, archive)
            return subprocess.CompletedProcess(command, 0)
        return real_run(command, **kwargs)

    monkeypatch.setattr(script.subprocess, "run", run)
    monkeypatch.setattr(sys, "argv", [*argv[1:], "--execute", "--stage", "cleanup", "--index", "0"])
    assert script.main() == 0
    assert effects == [["cafe", "close", "--archive-only"]]
    assert (
        inspect_closeout(archive, state.workflow_id, archived=True)["selection"]["choice"]
        == "archive"
    )
    costs = load_kickoff_module("report_closeout_cost")
    assert costs.verify_closeout(root, "journey", state.workflow_id, issue, "archive") == archive
    path, _ = script._paths(root, "journey", state.workflow_id)
    receipt = json.loads(path.read_text())
    receipt["commands"]["cleanup"][0]["status"] = "unknown"
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="receipt"):
        costs.verify_closeout(root, "journey", state.workflow_id, issue, "archive")
    assert effects == [["cafe", "close", "--archive-only"]]


def test_legacy_driver_api_records_proxy_provenance(context, monkeypatch):
    activate(context, [sys.executable, "-c", "pass"])
    accept(context, monkeypatch, manager="driver")
    assert inspect_closeout(context[2], context[3].workflow_id)["selection"]["choice"] == "cleanup"


def test_reconfirmed_split_contract_supersedes_pending_combined_task_without_reintegration(
    context, monkeypatch
):
    from copy import deepcopy
    from cafe.manager import ReplaceConfirmedContract, replace_confirmed_contract
    from cafe.manager._store import load_contract
    from cafe.delivery.closeout import plan_text, read_plan
    from tests.integration.test_delivery_combined_decisions import activate_closeout

    activate_closeout(context, [sys.executable, "-c", "pass"])
    journey.approve_action(context)
    journey.hook(context[6], "prepare_input", "DevelopmentDeliveryExecutor", context[7])
    journey.hook(context[6], "publish_output", "DevelopmentDeliveryOutcome", context[7])
    issue, state = context[2:4]
    records = HumanTaskRecordStore(issue)
    original = records.tasks()[-1]
    original = records.refresh_pending_contract(
        workflow_id=state.workflow_id,
        task_id=original.id,
        prompt=original.prompt + "\n\n" + plan_text(read_plan(issue, state.workflow_id)),
        expected_result=original.expected_result,
        continuations=original.continuations,
    )
    report_path = next((issue / "delivery").glob("*/result.json"))
    original_report = report_path.read_bytes()
    # Pending legacy acceptance resumes unchanged under its original declaration.
    baton_path = issue / "next_step.txt"
    original_baton = baton_path.read_bytes() if baton_path.exists() else None
    resumed = journey.hook(context[6], "publish_output", "DevelopmentDeliveryOutcome", context[7])
    assert resumed.override_status_code is None
    assert (baton_path.read_bytes() if baton_path.exists() else None) == original_baton
    assert records.get_task(original.id) == original
    contract, sha = load_contract(issue)
    proposal = {
        k: deepcopy(v)
        for k, v in contract.items()
        if k not in {"schema_version", "identity", "revision", "provenance"}
    }
    plan = proposal["delivery_contract"].pop("closeout_plan")
    proposal["delivery_contract"].pop("terminal_selection")
    proposal["delivery_contract"]["schema_version"] = 6
    proposal["closeout_contract"] = {
        "schema_version": 1,
        "choice": "cleanup",
        "plan": plan,
        "delivery_result": {
            "step": context[-1],
            "task_id": "delivery-outcome",
            "artifact": "delivery_result",
        },
    }
    replace_confirmed_contract(
        ReplaceConfirmedContract(
            issue_dir=issue,
            issue_name="journey",
            workflow_id=state.workflow_id,
            proposal=proposal,
            confirmed_by="user",
            confirmed_at=datetime.now(timezone.utc),
            expected_predecessor_sha256=sha,
            kind="user_reconfirmation",
        )
    )
    import shutil

    shutil.rmtree(context[0] / ".cafe/skills/cafe-deliver_development")
    outcome = next(
        t
        for t in context[5]["steps"][context[-1]]["human_tasks"]
        if t["task_id"] == "delivery-outcome"
    )
    outcome["outcomes"].pop("confirm_cleanup")
    outcome["outcomes"].pop("confirm_archive")
    journey.hook(context[6], "publish_output", "DevelopmentDeliveryOutcome", context[7])
    replacement = records.tasks()[-1]
    assert replacement.id != original.id
    assert set(replacement.continuations) == {"confirm", "revise"}
    old = records.get_task(original.id)
    assert old.superseded_by_task_id == replacement.id
    assert old.prompt == original.prompt and old.expected_result == original.expected_result
    assert report_path.read_bytes() == original_report
