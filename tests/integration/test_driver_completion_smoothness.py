"""Supported ordinary HumanTasks complete through the public Driver entry."""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from cafe.core.blackboard import (
    ArtifactEntry,
    ArtifactKind,
    BlackboardStore,
    HandoffIntent,
    HandoffOwner,
)
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.driver import DriverEntryRequest
from cafe.driver._schema import build_initial_contract, freshness_semantic_facts
from cafe.driver._store import write_contract
from cafe.driver.task_inspection import inspect_task_authority
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader
from cafe.ui.human_tasks import resolve_step_human_task
from tests.unit.test_driver_task_authority import _task_proposal

SCRIPTS = Path(__file__).parents[2] / "src/cafe/data/skills/use-cafe-workflow/scripts"


def _paused_task(tmp_path: Path, *, confirm: bool = False):
    step = "verify" if confirm else "design"
    task_name = "output-review" if confirm else "known-answer"
    trigger = "confirm_output" if confirm else "need_clarification"
    policy = {
        "id": task_name,
        "pattern": "confirm_output" if confirm else "answer_questions",
        "prompt": "Review the current output" if confirm else "Choose supported tools",
        "input_schema": "decision" if confirm else "answers",
        "required": True,
    }
    if confirm:
        policy["decisions"] = [{"id": "confirm", "label": "Confirm"}]
    else:
        policy["questions"] = [
            {"id": "storage", "prompt": "Storage?", "options": ["SQLite", "Postgres"]},
            {"id": "runner", "prompt": "Test runner?", "options": ["pytest", "unittest"]},
            {
                "id": "checks",
                "prompt": "Required checks?",
                "options": ["A", "B"],
                "multiple": True,
            },
        ]
    skill_dir = tmp_path / ".cafe/skills/custom-task"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        + yaml.safe_dump(
            {
                "name": "custom-task",
                "description": "An ordinary task",
                "workflow": {"human_tasks": [policy]},
            }
        )
        + "---\n# Custom task\n",
        encoding="utf-8",
    )
    playbook = {
        "playbook": {
            "id": "custom-task",
            "name": "Custom task",
            "conversation_locale": "en-US",
            "applicability": {"summary": "Task", "use_when": ["Task"], "avoid_when": ["None"]},
        },
        "roles": {
            "developer": {
                "description": "Developer",
                "default_agent": "David",
                "default_cli": "codex",
            }
        },
        "skills": {"workflow": {"shared": []}, "chat": {"shared": []}},
        "steps": {
            step: {
                "skill": "custom-task",
                "role": "developer",
                "assignee_type": "agent",
                "input_artifacts": ["spec"] if confirm else [],
                "output_artifact": "review_report" if confirm else "design_doc",
                "allowed_tools": ["Read"],
                "capability_requests": [],
                "behavior": {"completion": "baton"},
                "human_tasks": [
                    {
                        "trigger": trigger,
                        "task_id": task_name,
                        "outcomes": (
                            {"confirm": "_done", "revise": step} if confirm else {"submit": step}
                        ),
                    }
                ],
                "on": {trigger: step, "workflow_complete": "_done"},
            }
        },
        "commands": {"prepare": {"prompt_for_spec_plan_config": False}},
        "entry_point": step,
    }
    playbook_dir = tmp_path / ".cafe/playbooks"
    playbook_dir.mkdir(parents=True)
    (playbook_dir / "custom-task.yaml").write_text(yaml.safe_dump(playbook), encoding="utf-8")
    issue_dir = tmp_path / ".cafe/issues/issue500"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook: custom-task\n", encoding="utf-8")
    store = BlackboardStore(issue_dir)
    board = store.load_or_create(step, playbook_id="custom-task")
    spec = issue_dir / "spec/iteration_001/output.md"
    spec.parent.mkdir(parents=True)
    spec.write_text(
        "Confirmed storage: SQLite. Confirmed test runner: pytest. Required checks: A and B. "
        "Reversible technical choice allowed for storage and test runner.",
        encoding="utf-8",
    )
    board.artifacts["spec"] = ArtifactEntry(
        name="spec",
        kind=ArtifactKind.DOCUMENT,
        version=1,
        updated_by="spec",
        path=".cafe/issues/issue500/spec/iteration_001/output.md",
        content_sha256=hashlib.sha256(spec.read_bytes()).hexdigest(),
    )
    report = None
    if confirm:
        report = issue_dir / "verify/iteration_001/output.md"
        report.parent.mkdir(parents=True)
        report.write_text(
            "The existing export path writes text and every citation in the existing format. "
            "Focused checks covered populated and empty reports through the same interface. "
            "The change adds no dependency, publication, paid service, or external permission. "
            "No actionable work remains.",
            encoding="utf-8",
        )
        board.artifacts["review_report"] = ArtifactEntry(
            name="review_report",
            kind=ArtifactKind.DOCUMENT,
            version=1,
            updated_by=step,
            path=".cafe/issues/issue500/verify/iteration_001/output.md",
        )
    store.save(board)
    store.set_current_step(board, "user")
    store.update_handoff_contract(
        board,
        from_step=step,
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.CONFIRM_OUTPUT if confirm else HandoffIntent.NEED_CLARIFICATION,
    )
    proposal = _task_proposal()
    proposal["phases"] = [{"name": step, "chain": [{"cli": "codex", "model": "exact"}]}]
    proposal["proactive_review"]["phase_decisions"] = [{"phase": step, "decision": "not_required"}]
    proposal["confirmation_contract"] = {
        "user_required": [],
        "driver_confirmable": [step] if confirm else [],
        "mandatory_human_stops": [],
    }
    proposal["task_contract"] = {
        "user_required": [],
        "driver_confirmable": [{"phase": step, "task_id": task_name}],
    }
    contract = build_initial_contract(
        proposal=proposal,
        issue_name="issue500",
        workflow_id=board.workflow_id,
        confirmed_by="user",
        confirmed_at="2026-09-26T12:00:00+00:00",
    )
    digest = write_contract(issue_dir, contract, expected_predecessor_sha256=None)
    resolved, binding = resolve_step_human_task(
        playbook_data=PlaybookLoader(project_root=tmp_path).load("custom-task"),
        step_name=step,
        trigger=trigger,
    )
    task = HumanTaskRecordStore(issue_dir).materialize(
        workflow_id=board.workflow_id,
        step=step,
        iteration=1,
        trigger=trigger,
        policy_id=resolved.id,
        prompt=resolved.prompt,
        expected_result=resolved.model_dump(mode="json"),
        continuations=binding.outcomes,
        assignee_type="user",
    )
    return issue_dir, task, digest, contract, spec, report


def _public_completion(
    tmp_path: Path, issue_dir: Path, task_id: str, digest: str, response, evidence
):
    facts = inspect_task_authority(issue_dir, task_id, response=response, evidence=evidence)
    assessment = tmp_path / "assessment.json"
    assessment.write_text(
        json.dumps({"response": response, "evidence": evidence}), encoding="utf-8"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "complete_driver_task.py"),
            "--issue-dir",
            str(issue_dir),
            "--task-id",
            task_id,
            "--assessment",
            str(assessment),
            "--contract-sha256",
            digest,
            "--sources-sha256",
            facts["sources_sha256"],
            "--json",
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src")},
        text=True,
        capture_output=True,
        check=False,
    )
    return result


@pytest.mark.parametrize(
    "case", ["single", "multiple", "mixed", "multi_select", "missing", "ambiguous"]
)
def test_public_completion_resolves_each_supported_question(tmp_path: Path, monkeypatch, case: str):
    monkeypatch.chdir(tmp_path)
    issue_dir, task, digest, _, spec, _ = _paused_task(tmp_path)
    spec_text = spec.read_text()
    answers = {"storage": "SQLite", "runner": "pytest", "checks": ["A", "B"]}
    response = {"task": task.policy_id, "human_task_id": task.id, "answers": answers}
    candidates = [
        {
            "field": "answers.storage",
            "value": "SQLite",
            "precedent": False,
            "footprint": 1,
            "reversible": True,
        },
        {
            "field": "answers.storage",
            "value": "Postgres",
            "precedent": False,
            "footprint": 5,
            "reversible": True,
        },
        {
            "field": "answers.runner",
            "value": "pytest",
            "precedent": False,
            "footprint": 1,
            "reversible": True,
        },
        {
            "field": "answers.runner",
            "value": "unittest",
            "precedent": False,
            "footprint": 5,
            "reversible": True,
        },
    ]
    if case == "single":
        candidates = candidates[:2]
        exact_fields = {"answers.runner": "pytest", "answers.checks": ["A", "B"]}
    elif case == "multi_select":
        candidates = [
            {
                "field": "answers.checks",
                "value": value,
                "precedent": False,
                "footprint": 1,
                "reversible": True,
            }
            for value in ("A", "B")
        ]
        exact_fields = {"answers.storage": "SQLite", "answers.runner": "pytest"}
    elif case == "mixed":
        candidates = candidates[2:]
        exact_fields = {"answers.storage": "SQLite", "answers.checks": ["A", "B"]}
    elif case == "missing":
        candidates = candidates[:2]
        exact_fields = {"answers.checks": ["A", "B"]}
    else:
        exact_fields = {"answers.checks": ["A", "B"]}
    if case == "ambiguous":
        candidates[1]["footprint"] = 1
    citations = [
        {"field": field, "value": value, "source": "artifact:spec", "excerpt": spec_text}
        for field, values in exact_fields.items()
        for value in (values if isinstance(values, list) else [values])
    ]
    evidence = {
        "basis": "reversible_technical",
        "category": "technical",
        "exhaustive": True,
        "authority": {"source": "artifact:spec", "excerpt": "Reversible technical choice allowed"},
        "candidates": candidates,
        "citations": citations,
    }
    result = _public_completion(tmp_path, issue_dir, task.id, digest, response, evidence)
    if case in {"ambiguous", "missing"}:
        assert result.returncode != 0
        assert HumanTaskRecordStore(issue_dir).get_task(task.id).status.value == "pending"
    else:
        assert result.returncode == 0, result.stderr
        assert HumanTaskRecordStore(issue_dir).get_task(task.id).status.value == "completed"


@pytest.mark.parametrize(
    "case", ["grounded", "bare", "stale", "foreign_task", "no_current_coverage"]
)
def test_public_clean_confirmation_requires_grounded_comparison(
    tmp_path: Path, monkeypatch, case: str
):
    monkeypatch.chdir(tmp_path)
    issue_dir, task, digest, contract, spec, report = _paused_task(tmp_path, confirm=True)
    response = {"task": task.policy_id, "human_task_id": task.id, "decision": "confirm"}
    evidence = {"basis": "confirmed_exact", "exhaustive": True}
    if case != "bare":
        spec_module = importlib.util.spec_from_file_location(
            "delivery_comparison", SCRIPTS / "compare_delivery_contract.py"
        )
        assert spec_module is not None and spec_module.loader is not None
        comparison = importlib.util.module_from_spec(spec_module)
        spec_module.loader.exec_module(comparison)
        packet = comparison.comparison_packet(
            entry=DriverEntryRequest(
                issue_dir,
                "issue500",
                task.workflow_id,
                {"semantic_facts": freshness_semantic_facts(contract)},
            ),
            model=PlaybookLoader(project_root=tmp_path).load_model("custom-task").model,
            skill_loader=SkillLoader(project_root=tmp_path),
            boundary={
                "step": "verify",
                "task_id": task.id,
                "iteration": 1,
                "intent": "confirm_output",
                "owner": "user",
                "active": True,
            },
            artifacts={"spec": spec.read_text(), "review_report": report.read_text()},
        )
        quote = report.read_text()
        coverage = {}
        for name in packet["data"]["obligations"]:
            item = {
                "status": "preserved",
                "source": "review_report",
                "quote": quote,
                "reason": "Reviewed implementation and verification against this requirement.",
            }
            if name.startswith("acceptance_invariants["):
                item.update(
                    implementation="Current export behavior",
                    verification="Reviewed output and focused tests",
                )
            coverage[name] = item
        assessment = {
            "snapshot_sha256": packet["snapshot_sha256"],
            "coverage": coverage,
            "deviation": {
                "status": "clear",
                "source": "review_report",
                "quote": quote,
                "reason": "No unauthorized scope or capability change in reviewed output.",
            },
        }
        assert comparison.decide(packet, assessment)["decision"] == "accept"
        if case == "stale":
            packet["data"]["artifacts"]["review_report"] += " Unsaved claim."
        elif case == "foreign_task":
            packet["data"]["boundary"]["task_id"] = "other-task"
            packet["snapshot_sha256"] = comparison._digest(packet["data"])
            assessment["snapshot_sha256"] = packet["snapshot_sha256"]
        elif case == "no_current_coverage":
            for record in assessment["coverage"].values():
                record["source"] = "spec"
                record["quote"] = spec.read_text()
        evidence["delivery_comparison"] = {"packet": packet, "assessment": assessment}
    else:
        evidence["citations"] = [
            {
                "field": "decision",
                "value": "confirm",
                "source": "current_output:review_report",
                "excerpt": "No actionable work remains.",
            }
        ]
    result = _public_completion(tmp_path, issue_dir, task.id, digest, response, evidence)
    assert (result.returncode == 0) is (case == "grounded"), result.stderr
    assert (HumanTaskRecordStore(issue_dir).get_task(task.id).status.value == "completed") is (
        case == "grounded"
    )
