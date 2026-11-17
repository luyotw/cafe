"""Manager output confirmation preserves nested issue identity end to end."""

import pytest

from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.manager.delivery_comparison import comparison_packet_from_contract, decide
from cafe.manager.task_completion import complete_manager_task
from cafe.manager.task_inspection import inspect_task_authority
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader
from tests.integration.test_driver_completion_smoothness import _paused_task


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
