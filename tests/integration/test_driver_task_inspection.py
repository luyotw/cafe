"""Current task inspection keeps route, owner and evidence separate."""

import importlib.util
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from cafe.core.blackboard import (
    ArtifactEntry,
    ArtifactKind,
    BlackboardStore,
    HandoffIntent,
    HandoffOwner,
)
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.task_inbox import TaskInboxService
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.driver._schema import build_initial_contract
from cafe.driver._store import write_contract
from cafe.driver.task_authority import decide_task_authority
from cafe.driver.task_inspection import inspect_task_authority
from cafe.playbooks.loader import PlaybookLoader
from cafe.ui.human_tasks import resolve_step_human_task
from tests.unit.test_driver_contract_application import _proposal
from tests.unit.test_driver_task_authority import _task_proposal


@pytest.mark.parametrize(
    "driver",
    [
        {"mode": "unattended"},
        {"mode": "attached", "poll_interval_seconds": 30},
        {"mode": "event-driven", "clis": [{"cli": "codex"}]},
    ],
)
def test_custom_clarification_current_task_has_independent_driver_facts(
    tmp_path: Path, driver: dict
):
    issue_dir = tmp_path / ".cafe" / "issues" / "issue500"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook: standard\n", encoding="utf-8")
    store = BlackboardStore(issue_dir)
    board = store.load_or_create("develop", playbook_id="standard")
    source = issue_dir / "spec" / "iteration_001" / "output.md"
    source.parent.mkdir(parents=True)
    source.write_text("All required outcomes: A and B", encoding="utf-8")
    board.artifacts["spec"] = ArtifactEntry(
        name="spec",
        kind=ArtifactKind.DOCUMENT,
        version=1,
        updated_by="spec",
        path=".cafe/issues/issue500/spec/iteration_001/output.md",
    )
    store.save(board)
    store.set_current_step(board, "user")
    store.update_handoff_contract(
        board,
        from_step="develop",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.NEED_CLARIFICATION,
        status_code="BATON_NEED_CLARIFICATION",
    )
    proposal = _task_proposal()
    proposal["driver"] = driver
    contract = build_initial_contract(
        proposal=proposal,
        issue_name="issue500",
        workflow_id=board.workflow_id,
        confirmed_by="user",
        confirmed_at="2026-09-26T12:00:00+00:00",
    )
    write_contract(issue_dir, contract, expected_predecessor_sha256=None)
    questions_file = issue_dir / "develop" / "iteration_001" / "questions.xml"
    questions_file.parent.mkdir(parents=True)
    questions_file.write_text(
        '<questions><question id="q1" type="checkbox"><title>Which outcomes?</title>'
        "<options><option>A</option><option>B</option></options></question></questions>",
        encoding="utf-8",
    )
    policy = {
        "id": "known-answer",
        "pattern": "answer_questions",
        "prompt": "Which outcomes?",
        "input_schema": "answers",
        "questions_from_xml": True,
    }
    task = HumanTaskRecordStore(issue_dir).materialize(
        workflow_id=board.workflow_id,
        step="develop",
        iteration=1,
        trigger="need_clarification",
        policy_id="known-answer",
        prompt="Which outcomes?",
        expected_result=policy,
        continuations={"await_agent": "develop"},
        assignee_type="user",
    )
    before = {
        name: (issue_dir / name).read_bytes()
        for name in ("human_tasks.json", "blackboard.json", "driver/contract.json")
    }
    facts = inspect_task_authority(issue_dir, task.id)
    assert all((issue_dir / name).read_bytes() == content for name, content in before.items())
    assert facts["route_status"] == "need_clarification"
    assert facts["pause_status"] == "BATON_NEED_CLARIFICATION"
    assert facts["resolution_owner"] == "driver_confirmable"
    assert facts["evidence_reason"] == "evidence_unevaluated"
    response = {"task": "known-answer", "human_task_id": task.id, "answers": {"q1": ["A", "B"]}}
    evidence = {
        "basis": "confirmed_exact",
        "exhaustive": True,
        "citations": [
            {"field": "answers.q1", "value": value, "source": "artifact:spec", "excerpt": "A and B"}
            for value in ("A", "B")
        ],
    }
    allowed = inspect_task_authority(issue_dir, task.id, response=response, evidence=evidence)
    assert allowed["allowed"] is True
    assert allowed["evidence_reason"] == "confirmed_exact_evidence"

    scripts = (
        Path(__file__).parents[2]
        / "src"
        / "cafe"
        / "data"
        / "skills"
        / "use-cafe-workflow"
        / "scripts"
    )
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src")}
    neutral_inspection = subprocess.run(
        ["cafe", "task", "inspect", task.id, "--json"],
        cwd=tmp_path,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )
    assert json.loads(neutral_inspection.stdout)["data"]["task"]["id"] == task.id
    inspected = subprocess.run(
        [
            sys.executable,
            str(scripts / "inspect_task_authority.py"),
            "--issue-dir",
            str(issue_dir),
            "--task-id",
            task.id,
            "--json",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    assert json.loads(inspected.stdout)["resolution_owner"] == "driver_confirmable"
    progress = subprocess.run(
        [
            sys.executable,
            str(scripts / "render_workflow_progress.py"),
            "--project-root",
            str(tmp_path),
            "--issue-dir",
            str(issue_dir),
            "--driver-state",
            json.dumps({"deliver": "pending", "cleanup": "pending"}),
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    assert "route_status=need_clarification" in progress.stdout
    assert "resolution_owner=driver_confirmable" in progress.stdout
    assert "evidence_reason=evidence_unevaluated" in progress.stdout

    callback_spec = importlib.util.spec_from_file_location(
        "task_authority_callback", scripts / "workflow_event_callback.py"
    )
    assert callback_spec is not None and callback_spec.loader is not None
    callback = importlib.util.module_from_spec(callback_spec)
    callback_spec.loader.exec_module(callback)
    event = callback._with_current_task_authority(
        {"task_id": task.id, "event_type": "human_task_materialized"},
        issue_dir=issue_dir,
        repository_root=tmp_path,
    )
    assert event["route_status"] == "need_clarification"
    assert event["resolution_owner"] == "driver_confirmable"
    assert event["evidence_reason"] == "evidence_unevaluated"


def test_known_clarification_uses_structured_completion_and_rejects_stale_id(tmp_path: Path):
    issue_dir = tmp_path / ".cafe" / "issues" / "issue500"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook: standard\n", encoding="utf-8")
    store = BlackboardStore(issue_dir)
    board = store.load_or_create("develop", playbook_id="standard")
    source = issue_dir / "spec" / "iteration_001" / "output.md"
    source.parent.mkdir(parents=True)
    source.write_text("Confirmed answer: A", encoding="utf-8")
    board.artifacts["spec"] = ArtifactEntry(
        name="spec",
        kind=ArtifactKind.DOCUMENT,
        version=1,
        updated_by="spec",
        path=".cafe/issues/issue500/spec/iteration_001/output.md",
    )
    store.save(board)
    store.set_current_step(board, "user")
    store.update_handoff_contract(
        board,
        from_step="develop",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.NEED_CLARIFICATION,
    )
    proposal = _task_proposal()
    proposal["task_contract"]["driver_confirmable"].append(
        {"phase": "develop", "task_id": "clarification-feedback"}
    )
    contract = build_initial_contract(
        proposal=proposal,
        issue_name="issue500",
        workflow_id=board.workflow_id,
        confirmed_by="user",
        confirmed_at="2026-09-26T12:00:00+00:00",
    )
    write_contract(issue_dir, contract, expected_predecessor_sha256=None)
    policy, binding = resolve_step_human_task(
        playbook_data=PlaybookLoader().load("standard"),
        step_name="develop",
        trigger="need_clarification",
    )
    task = HumanTaskRecordStore(issue_dir).materialize(
        workflow_id=board.workflow_id,
        step="develop",
        iteration=1,
        trigger="need_clarification",
        policy_id=policy.id,
        prompt=policy.prompt,
        expected_result=policy.model_dump(mode="json"),
        continuations=binding.outcomes,
        assignee_type="user",
    )
    response = {"task": policy.id, "feedback": "A", "human_task_id": task.id}
    facts = inspect_task_authority(
        issue_dir,
        task.id,
        response=response,
        evidence={
            "basis": "confirmed_exact",
            "exhaustive": True,
            "citations": [
                {
                    "field": "feedback",
                    "value": "A",
                    "source": "artifact:spec",
                    "excerpt": "Confirmed answer: A",
                }
            ],
        },
    )
    assert facts["allowed"] is True
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src")}
    command = [
        "cafe",
        "task",
        "complete",
        task.id,
        "--result",
        json.dumps(response),
        "--no-resume",
        "--json",
    ]
    completed = subprocess.run(
        command,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert HumanTaskRecordStore(issue_dir).get_task(task.id).status.value == "completed"
    stale = subprocess.run(
        command,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert stale.returncode != 0
    assert inspect_task_authority(issue_dir, task.id)["allowed"] is False


def test_custom_phase_and_task_keep_clarification_route_with_task_owner(
    tmp_path: Path, monkeypatch
):
    skill_dir = tmp_path / ".cafe" / "skills" / "custom-design"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: custom-design
description: A custom design phase.
workflow:
  human_tasks:
    - id: known-answer
      pattern: answer_questions
      prompt: Which outcomes?
      input_schema: answers
      questions:
        - id: q1
          prompt: Which outcomes?
          options: [A, B]
          multiple: true
---
# Custom design
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue500"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook: custom-design\n", encoding="utf-8")
    playbook = {
        "playbook": {
            "id": "custom-design",
            "name": "Custom Design",
            "conversation_locale": "en-US",
            "applicability": {
                "summary": "Custom task authority",
                "use_when": ["Design"],
                "avoid_when": ["No design"],
            },
        },
        "roles": {
            "developer": {
                "description": "Developer",
                "default_agent": "David",
                "default_cli": "claude",
            }
        },
        "skills": {"workflow": {"shared": []}, "chat": {"shared": []}},
        "steps": {
            "design": {
                "skill": "custom-design",
                "role": "developer",
                "assignee_type": "agent",
                "input_artifacts": [],
                "allowed_tools": ["Read"],
                "capability_requests": [],
                "behavior": {"completion": "baton"},
                "human_tasks": [
                    {
                        "trigger": "need_clarification",
                        "task_id": "known-answer",
                        "outcomes": {"submit": "design"},
                    }
                ],
                "on": {"await_agent": "_done", "need_clarification": "design"},
            }
        },
        "commands": {"prepare": {"prompt_for_spec_plan_config": False}},
        "entry_point": "design",
    }

    def executor(step_name, step_def, state):
        BlackboardStore(issue_dir).update_handoff_contract(
            state,
            from_step="design",
            to_owner=HandoffOwner.USER,
            to_step="user",
            intent=HandoffIntent.NEED_CLARIFICATION,
            source="test",
        )
        return StepExecutionResult(response="Needs established answer", artifacts={})

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    ).run(start_step="design")
    assert result.final_status_code == "BATON_NEED_CLARIFICATION"
    tasks = HumanTaskRecordStore(issue_dir).tasks()
    assert len(tasks) == 1
    task = tasks[0]
    assert task.policy_id == "known-answer"
    assert task.trigger == "need_clarification"
    assert task.continuations["submit"] == "design"
    proposal = _task_proposal()
    proposal["task_contract"]["driver_confirmable"] = [
        {"phase": "design", "task_id": "known-answer"}
    ]
    proposal["phases"] = [{"name": "design", "chain": [{"cli": "codex", "model": "exact"}]}]
    proposal["proactive_review"]["phase_decisions"] = [
        {"phase": "design", "decision": "not_required"}
    ]
    contract = build_initial_contract(
        proposal=proposal,
        issue_name="issue500",
        workflow_id=task.workflow_id,
        confirmed_by="user",
        confirmed_at="2026-09-26T12:00:00+00:00",
    )
    digest = write_contract(issue_dir, contract, expected_predecessor_sha256=None)
    facts = inspect_task_authority(issue_dir, task.id)
    assert facts["route_status"] == "need_clarification"
    assert facts["resolution_owner"] == "driver_confirmable"
    assert facts["evidence_reason"] == "evidence_unevaluated"
    legacy = _proposal()
    legacy["phases"] = proposal["phases"]
    legacy["proactive_review"] = proposal["proactive_review"]
    legacy["confirmation_contract"]["driver_confirmable"] = ["design"]
    legacy["reactive_user_handoffs"]["need_clarification"] = "driver_confirmable"
    old_contract = build_initial_contract(
        proposal=legacy,
        issue_name="issue500",
        workflow_id=task.workflow_id,
        confirmed_by="user",
        confirmed_at="2026-09-26T12:00:00+00:00",
    )
    old_facts = decide_task_authority(
        task=TaskInboxService(tmp_path / ".cafe").inspect_read_only(task.id).to_dict(),
        contract=old_contract,
        current_task_id=task.id,
    )
    assert old_facts["resolution_owner"] == "user_required"
    assert old_facts["route_status"] == "need_clarification"
    rejected_proposal = deepcopy(proposal)
    rejected_proposal["task_contract"]["user_required"] = [
        {"phase": "design", "task_id": "known-answer"}
    ]
    rejected_proposal["task_contract"]["driver_confirmable"] = []
    rejected_contract = build_initial_contract(
        proposal=rejected_proposal,
        issue_name="issue500",
        workflow_id=task.workflow_id,
        confirmed_by="user",
        confirmed_at="2026-09-26T12:30:00+00:00",
        revision=2,
        previous_contract_sha256=digest,
        provenance_kind="user_reconfirmation",
    )
    write_contract(issue_dir, rejected_contract, expected_predecessor_sha256=digest)
    rejected = inspect_task_authority(issue_dir, task.id)
    assert rejected["route_status"] == "need_clarification"
    assert rejected["resolution_owner"] == "user_required"
    assert rejected["evidence_reason"] == "declared_user_required"
    scripts = (
        Path(__file__).parents[2]
        / "src"
        / "cafe"
        / "data"
        / "skills"
        / "use-cafe-workflow"
        / "scripts"
    )
    progress_spec = importlib.util.spec_from_file_location(
        "rejected_task_progress", scripts / "render_workflow_progress.py"
    )
    assert progress_spec is not None and progress_spec.loader is not None
    progress_module = importlib.util.module_from_spec(progress_spec)
    progress_spec.loader.exec_module(progress_module)
    progress = progress_module.render_progress(
        playbook=playbook,
        contract=rejected_contract,
        issue_dir=issue_dir,
        driver_state={"deliver": "pending", "cleanup": "pending"},
    )
    assert "resolution_owner=user_required" in progress
    assert "evidence_reason=declared_user_required" in progress
    callback_spec = importlib.util.spec_from_file_location(
        "rejected_task_callback", scripts / "workflow_event_callback.py"
    )
    assert callback_spec is not None and callback_spec.loader is not None
    callback_module = importlib.util.module_from_spec(callback_spec)
    callback_spec.loader.exec_module(callback_module)
    event = callback_module._with_current_task_authority(
        {"task_id": task.id, "event_type": "human_task_materialized"},
        issue_dir=issue_dir,
        repository_root=tmp_path,
    )
    assert event["resolution_owner"] == "user_required"
    assert event["evidence_reason"] == "declared_user_required"
