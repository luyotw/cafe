"""Correction handoffs retain their causal batch across task and cwd changes."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cafe.core.blackboard import (
    ArtifactEntry,
    ArtifactKind,
    BlackboardStore,
    EventEntry,
    HandoffContract,
    HandoffIntent,
    HandoffOwner,
)
from cafe.core.todo import workflow_feedback_todo_items
from cafe.core.workflow_feedback import WorkflowFeedbackLedger
from cafe.phases.generic_phase import GenericPhase
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
from cafe.skills.loader import SkillLoader


@pytest.mark.parametrize("foreign_cwd", [False, True])
@pytest.mark.parametrize(("settlement", "new_correction"), [
    (None, False), (None, True), ("normal", True), ("legacy_recovery", True),
    ("legacy_changed_source", True), ("legacy_missing_projection", True),
    ("legacy_missing_artifact", True), ("legacy_later_work", True),
    ("legacy_missing_preparation", True), ("legacy_missing_receipts", True),
    ("legacy_self_recovery_marker", True), ("legacy_partial_recovery", True),
])
def test_correction_generation_and_completion_follow_current_human_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, new_correction: bool, foreign_cwd: bool, settlement
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.chdir(root)

    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "CAFE test")
    (root / ".gitignore").write_text(".cafe/\nglobal/\n", encoding="utf-8")
    (root / "product.py").write_text("fixed = True\n", encoding="utf-8")
    git("add", ".gitignore", "product.py")
    git("commit", "-qm", "Fix requested behavior")
    commit = git("rev-parse", "HEAD")

    issue_dir = root / ".cafe" / "issues" / "team" / "correction"
    ledger = WorkflowFeedbackLedger(issue_dir)
    _, old = ledger.record(
        source_identity="inspection:old",
        source_kind="inspection_note",
        target_step="repair_shop",
        content="Correct the previous issue",
    )
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("repair_shop", playbook_id="custom-corrections")
    events = [
        EventEntry(
            timestamp="2026-01-01T00:00:00Z",
            step="inspector",
            event_type="transition",
            message="",
            data={"from": "inspector", "to": "repair_shop"},
        ),
        EventEntry(
            timestamp="2026-01-01T00:00:01Z",
            step="repair_shop",
            event_type="workflow_feedback_delivered",
            message="",
            data={"source_identities": [old.source_identity]},
        ),
    ]
    for event in events:
        store.log_event(state, event.step, event.event_type, event.message, event.data)
    selected = old
    if new_correction:
        ledger.consume(old.source_identity)
        _, selected = ledger.record(
            source_identity="approval:publisher:accept-output:2",
            source_kind="approval",
            target_step="repair_shop",
            content="Apply the user's latest wording correction",
        )
        event = EventEntry(
            timestamp="2026-01-01T00:00:02Z",
            step="publisher",
            event_type="human_task_completed",
            message="",
            data={
                "task_id": "accept-output",
                "trigger": "need_permission",
                "pattern": "confirm_output",
                "to_step": "repair_shop",
            },
        )
        if settlement:
            from cafe.core.human_task_records import HumanTaskRecordStore
            records = HumanTaskRecordStore(issue_dir)
            task = records.materialize(
                workflow_id=state.workflow_id, step="publisher", iteration=2,
                trigger="need_permission", policy_id="accept-output", prompt="Review",
                expected_result={"decisions": [{"id": "fix_now", "correction": True}]},
                continuations={"fix_now": "publisher"}, assignee_type="user",
            )
            result = records.complete(
                workflow_id=state.workflow_id, task_id=task.id, source="command",
                payload={"task": task.policy_id, "decision": "fix_now", "feedback": selected.content,
                         "continuation": "repair_shop", "declared_continuation": "publisher",
                         "supervisor_handoff_to": "repair_shop"},
            )
            event.timestamp = result.completed_at
            event.data.update({"source": "command", "supervisor_handoff": True,
                               "declared_to_step": "publisher"})
        store.log_event(state, event.step, event.event_type, event.message, event.data)
    # Same-step retry changes the baton sender, but owns the same inbound work.
    state.handoff_contract = HandoffContract(
        version=1,
        from_step="repair_shop",
        to_owner=HandoffOwner.AGENT,
        to_step="repair_shop",
        intent=HandoffIntent.AWAIT_AGENT,
        source="test.retry",
    )
    state.artifacts["signals"] = ArtifactEntry(
        name="signals",
        kind=ArtifactKind.DOCUMENT,
        version=2,
        updated_by="human_task",
        path=str(ledger.path.relative_to(root)),
    )
    store.save(state)

    skill = root / ".cafe" / "skills" / "repair-kit"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        """---
name: repair-kit
description: custom correction consumer
workflow:
  checklist:
    variants:
      - when: {feedback: true}
        sections:
          - {todo_projection: {artifact: active_work, causal: true}}
    include_role_guidance: false
---
# Repair kit
""",
        encoding="utf-8",
    )
    playbook = {
        "playbook": {"id": "custom-corrections"},
        "steps": {
            "inspector": {
                "behavior": {
                    "feedback_target": "repair_shop",
                    "feedback_artifact": "signals",
                    "feedback_source_kind": "inspection_note",
                    "feedback_todo_source": "bespoke",
                    "feedback_todo_id_prefix": "INS",
                }
            },
            "publisher": {
                "allowed_goto": ["repair_shop"],
                "human_tasks": [
                    {
                        "task_id": "accept-output",
                        "trigger": "need_permission",
                        "outcomes": {"fix_now": "publisher" if settlement else "repair_shop"},
                        "feedback_delivery": {
                            "artifact": "signals",
                            "source_kind": "approval",
                            "todo_source": "bespoke",
                            "todo_id_prefix": "USR",
                        },
                    }
                ]
            },
            "repair_shop": {
                "skill": "repair-kit",
                "role": "artisan",
                "input_artifacts": ["signals"],
                "output_artifact": "work",
                "on": {"await_agent": "_done"},
            },
        },
    }
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="team/correction",
        playbook=playbook,
        generic_phase=GenericPhase(SkillLoader(project_root=root, global_root=root / "global")),
        agent_manager=MagicMock(),
        git_ops=SimpleNamespace(get_default_base_branch=lambda: "main"),
        role_agent_map={"artisan": "David"},
    )
    executor.phase_name = "repair_shop"
    executor.phase_dir = issue_dir / "repair_shop"
    executor.iteration = 1
    iteration = executor.phase_dir / "iteration_001"
    checklist = iteration / "checklist.md"
    output = iteration / "output.md"
    executor._generate_checklist(
        step_name="repair_shop",
        skill_name="repair-kit",
        agent_name="David",
        step_def=playbook["steps"]["repair_shop"],
        blackboard_state=state,
        checklist_file=checklist,
        output_file=output,
        questions_xml_file=iteration / "questions.xml",
    )
    binding = json.loads((iteration / "iteration.json").read_text())["effective_checklist"][
        "projections"
    ][0]
    item = workflow_feedback_todo_items(
        ledger.path,
        target_step="repair_shop",
        source_by_kind={"inspection_note": "bespoke", "approval": "bespoke"},
        id_prefix_by_kind={"inspection_note": "INS", "approval": "USR"},
        source_identities=(selected.source_identity,),
    )[0]
    assert binding["producer_ids"] == [item.item_id]
    checklist.write_text(checklist.read_text().replace("[ ]", "[x]"), encoding="utf-8")
    output.write_text(
        f"## Todo Progress\n\n### {binding['handles'][0]}\n"
        f"- Status: completed\n- Source fingerprint: `{item.fingerprint}`\n"
        f"- Files: `product.py`\n- Commit: `{commit}`\n"
        "- Remaining work: None.\n- Next action: Review.\n",
        encoding="utf-8",
    )
    if foreign_cwd:
        monkeypatch.chdir(tmp_path)
    passed, detail = executor._validate_projected_todo_completion_detail(checklist)
    assert passed, detail
    if settlement:
        from cafe.core.workflow_models import StepExecutionResult
        from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
        from cafe.ui.cli_shared import _consume_pending_chat_handoff

        batch = ledger.write_pending_snapshot(
            path=iteration / "workflow_feedback_batch.json", target_step="repair_shop", limit=100,
        )
        metadata_path = iteration / "iteration.json"
        metadata = json.loads(metadata_path.read_text())
        metadata.update({"iteration": 1, "step_name": "repair_shop", "agent_invoked": True})
        metadata_path.write_text(json.dumps(metadata))
        calls = []

        def completed_consumer(name, declaration, current):
            calls.append(name)
            store.update_handoff_contract(current, from_step=name, to_owner=HandoffOwner.DONE,
                                          to_step="done", intent=HandoffIntent.WORKFLOW_COMPLETE, source="test")
            return StepExecutionResult(response="completed", artifacts={"work": str(output)},
                                       agent_invoked=True, feedback_source_identities=batch)

        runtime = BlackboardWorkflowRuntime(issue_dir=issue_dir, playbook=playbook, executor=completed_consumer)
        if settlement.startswith("legacy_"):
            with monkeypatch.context() as legacy:
                legacy.setattr(runtime, "_commit_delivered_feedback", lambda **kwargs:
                               "the feedback completion evidence is invalid: workflow feedback source kind is not declared")
                assert runtime.run(start_step="repair_shop").final_status_code == "INVALID_FEEDBACK_DELIVERY"
            assert _consume_pending_chat_handoff(
                issue_dir=issue_dir, playbook_data=playbook, requested_start_step=None,
            ) is None
            def no_replay(*args):
                raise AssertionError("Completed consumer must not run again")
            runtime = BlackboardWorkflowRuntime(issue_dir=issue_dir, playbook=playbook, executor=no_replay)
            if settlement == "legacy_changed_source":
                raw = json.loads(ledger.path.read_text())
                raw["entries"][-1]["content"] = "Changed correction."
                ledger.path.write_text(json.dumps(raw))
            elif settlement == "legacy_missing_projection":
                raw = json.loads(metadata_path.read_text())
                raw.pop("effective_checklist")
                metadata_path.write_text(json.dumps(raw))
            elif settlement == "legacy_missing_artifact":
                runtime._latest_feedback_delivery_preparation(current_step="repair_shop")["artifact"] = {}
            elif settlement == "legacy_later_work":
                runtime.blackboard_store.record_event(runtime.blackboard, "step_started", {
                    "step": "repair_shop", "iteration": 2,
                })
            elif settlement == "legacy_missing_preparation":
                next(event for event in runtime.blackboard.events
                     if event.event_type == "workflow_feedback_delivery_prepared").event_type = "observation"
            elif settlement == "legacy_missing_receipts":
                runtime._latest_feedback_delivery_preparation(current_step="repair_shop").pop("supervisor_sources")
            elif settlement == "legacy_self_recovery_marker":
                prepared = runtime._latest_feedback_delivery_preparation(current_step="repair_shop")
                prepared["recovered_from_delivery_id"] = prepared["delivery_id"]
            elif settlement == "legacy_partial_recovery":
                original_record = runtime.blackboard_store.record_event
                def interrupted_record(current, event_type, data):
                    original_record(current, event_type, data)
                    if event_type == "workflow_feedback_delivery_prepared" and data.get("recovered_from_delivery_id"):
                        raise RuntimeError("Recovery publication interrupted")
                with monkeypatch.context() as interrupted:
                    interrupted.setattr(runtime.blackboard_store, "record_event", interrupted_record)
                    with pytest.raises(RuntimeError, match="publication interrupted"):
                        runtime.run()
                runtime = BlackboardWorkflowRuntime(issue_dir=issue_dir, playbook=playbook, executor=no_replay)
            if settlement not in {"legacy_recovery", "legacy_partial_recovery"}:
                assert runtime.run().final_status_code == "INVALID_FEEDBACK_DELIVERY"
                assert calls == ["repair_shop"]
                assert ledger.pending(target_step="repair_shop")
                return
            assert runtime.run().completed
            assert runtime.run().completed
        else:
            assert runtime.run(start_step="repair_shop").completed
        assert calls == ["repair_shop"]
        assert not ledger.pending(target_step="repair_shop")
        deliveries = [event for event in runtime.blackboard.events
                      if event.event_type == "workflow_feedback_delivered"]
        assert len(deliveries) == 2  # Retained old completion plus this exact batch.
    # Causal source content remains authoritative even after the producing cwd changes.
    ledger.path.unlink()
    assert not executor._validate_projected_todo_completion(checklist)
