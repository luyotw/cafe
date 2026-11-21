"""Manager output confirmation preserves nested issue identity end to end."""

import json
import subprocess
import sys

import pytest

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.manager.clarification_context import MAX_CONTEXT_BYTES
from cafe.manager.delivery_comparison import comparison_packet_from_contract, decide
from cafe.manager.task_completion import complete_manager_task
from cafe.manager.task_inspection import inspect_task_authority
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader
from tests.integration.test_driver_completion_smoothness import SCRIPTS, _paused_task


@pytest.mark.parametrize("issue_name", ["issue500", "codex/issue500", "team/codex/issue500"])
def test_manager_confirms_current_output_for_full_issue_identity(tmp_path, monkeypatch, issue_name):
    monkeypatch.chdir(tmp_path)
    issue_dir, task, contract_sha, contract, spec, report = _paused_task(
        tmp_path, confirm=True, issue_name=issue_name, manager=True
    )
    initial = inspect_task_authority(issue_dir, task.id)
    assert initial["resolution_owner"] == "manager_confirmable"
    assert initial["allowed"] is False

    packet = comparison_packet_from_contract(
        issue_dir=issue_dir,
        issue_name=issue_name,
        workflow_id=task.workflow_id,
        contract=contract,
        contract_sha256=contract_sha,
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
    assert packet["data"]["eligible"] is True
    coverage = {}
    for name in packet["data"]["obligations"]:
        item = {
            "status": "preserved",
            "source": "review_report",
            "quote": report.read_text(),
            "reason": "Existing text export and citations are preserved with focused checks.",
        }
        if name.startswith("acceptance_invariants["):
            item.update(
                implementation="Existing export interface", verification="Focused export checks"
            )
        coverage[name] = item
    assessment = {
        "snapshot_sha256": packet["snapshot_sha256"],
        "coverage": coverage,
        "deviation": {
            "status": "clear",
            "source": "review_report",
            "quote": report.read_text(),
            "reason": "No dependency, publication or paid-service change.",
        },
    }
    assert decide(packet, assessment)["decision"] == "accept"
    response = {"task": task.policy_id, "human_task_id": task.id, "decision": "confirm"}
    evidence = {
        "basis": "confirmed_exact",
        "exhaustive": True,
        "delivery_comparison": {
            "snapshot_sha256": packet["snapshot_sha256"],
            "assessment": assessment,
        },
        "repository_sources": ["README.md"],
    }
    (tmp_path / "README.md").write_text("Repository guidance")
    facts = inspect_task_authority(issue_dir, task.id, response=response, evidence=evidence)
    assert facts["allowed"] is True
    (tmp_path / "README.md").write_text("Revised repository guidance")
    changed = inspect_task_authority(issue_dir, task.id, response=response, evidence=evidence)
    assert changed["sources_sha256"] != facts["sources_sha256"]
    with pytest.raises(ValueError, match="authority changed"):
        complete_manager_task(
            issue_dir,
            task.id,
            response=response,
            evidence=evidence,
            contract_sha256=contract_sha,
            sources_sha256=facts["sources_sha256"],
        )
    assert HumanTaskRecordStore(issue_dir).get_task(task.id).status.value == "pending"
    completed = complete_manager_task(
        issue_dir,
        task.id,
        response=response,
        evidence=evidence,
        contract_sha256=contract_sha,
        sources_sha256=changed["sources_sha256"],
    )
    assert completed["status"] == "completed"
    assert HumanTaskRecordStore(issue_dir).get_task(task.id).status.value == "completed"


def test_manager_rejects_task_from_sibling_namespace_with_same_basename(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    issue_dir, task, *_ = _paused_task(
        tmp_path, confirm=True, issue_name="codex/issue500", manager=True
    )
    sibling = issue_dir.parent.parent / "other/issue500"
    sibling.mkdir(parents=True)
    with pytest.raises(ValueError, match="different issue"):
        inspect_task_authority(sibling, task.id)


@pytest.mark.parametrize("feedback_clarification", [False, True])
def test_clarification_context_cli_preserves_authority_and_exact_sources(
    tmp_path, monkeypatch, feedback_clarification,
):
    monkeypatch.chdir(tmp_path)
    issue, task, _, _, spec, _ = _paused_task(
        tmp_path, manager=True, feedback_clarification=feedback_clarification, exact_handoff=True,
    )
    iteration = issue / task.step / "iteration_001"
    iteration.mkdir(parents=True)
    question = (
        '<questions><question id="q"><title>How should SQLite storage work?</title>'
        '<options><option>SQLite</option><option>Text</option></options>'
        '</question></questions>'
    )
    question_path = iteration / "questions.xml"
    question_path.write_text(question, encoding="utf-8")
    checkpoint = iteration / "output.md"
    checkpoint.write_text("SQLite remains pending.\n" + "SQLite " * 5000, encoding="utf-8")
    before = inspect_task_authority(issue, task.id)
    public = subprocess.run(
        [
            sys.executable, str(SCRIPTS / "inspect_task_authority.py"), "--issue-dir", str(issue),
            "--task-id", task.id, "--clarification-context", "--json",
        ],
        cwd=tmp_path, check=True, capture_output=True, text=True,
    )
    facts = json.loads(public.stdout)
    context = facts.pop("clarification_context")
    assert facts == before
    assert facts["allowed"] is False
    assert context["status"] == "available"
    assert context["questions"][0]["options"] == [
        "SQLite", "Text" if feedback_clarification else "Postgres",
    ]
    assert len(json.dumps(context, ensure_ascii=False).encode("utf-8")) <= MAX_CONTEXT_BYTES
    sources = {"artifact:spec": spec, "worker_checkpoint": checkpoint}
    assert {excerpt["source"] for excerpt in context["excerpts"]} == set(sources)
    for excerpt in context["excerpts"]:
        lines = sources[excerpt["source"]].read_text().splitlines(keepends=True)
        assert excerpt["text"] == "".join(lines[excerpt["start_line"] - 1 : excerpt["end_line"]])
    assert "SQLite " * 100 not in public.stdout
    assert HumanTaskRecordStore(issue).get_task(task.id).status.value == "pending"

    # A changed artifact must leave the confirmed source catalog.
    spec.write_text("SQLite statement from an unaccepted edit", encoding="utf-8")
    context = inspect_task_authority(
        issue, task.id, include_clarification_context=True
    )["clarification_context"]
    assert all(source["source"] != "artifact:spec" for source in context["source_catalog"])

    # The same phase and route labels cannot authorize a preview for a new baton.
    store = BlackboardStore(issue)
    board = store.load_read_only()
    store.update_handoff_contract(
        board, from_step=task.step, to_owner=HandoffOwner.USER, to_step="user",
        intent=HandoffIntent.NEED_CLARIFICATION,
    )
    facts = inspect_task_authority(issue, task.id, include_clarification_context=True)
    assert facts["clarification_context"]["reason"] == "clarification_not_current"


@pytest.mark.parametrize("user_owned", [False, True])
def test_clarification_context_rejects_unbound_or_user_owned_task(
    tmp_path, monkeypatch, user_owned,
):
    monkeypatch.chdir(tmp_path)
    issue, task, *_ = _paused_task(
        tmp_path, manager=True, task_user_owned=user_owned, exact_handoff=user_owned,
    )
    iteration = issue / task.step / "iteration_001"
    iteration.mkdir(parents=True)
    (iteration / "questions.xml").write_text(
        '<questions><question id="q"><title>Private question</title>'
        '<options><option>SQLite</option></options></question></questions>',
        encoding="utf-8",
    )
    facts = inspect_task_authority(issue, task.id, include_clarification_context=True)
    assert facts["resolution_owner"] == ("user_required" if user_owned else "manager_confirmable")
    assert facts["allowed"] is False
    assert facts["clarification_context"] == {
        "status": "unavailable",
        "reason": "clarification_not_eligible" if user_owned else "clarification_not_current",
    }
