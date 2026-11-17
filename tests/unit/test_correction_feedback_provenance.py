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


@pytest.mark.parametrize("new_correction", [False, True])
@pytest.mark.parametrize("foreign_cwd", [False, True])
def test_correction_generation_and_completion_follow_current_human_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, new_correction: bool, foreign_cwd: bool
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
    state = store.load_or_create("repair_shop")
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
                "human_tasks": [
                    {
                        "task_id": "accept-output",
                        "outcomes": {"fix_now": "repair_shop"},
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
    # Causal source content remains authoritative even after the producing cwd changes.
    ledger.path.unlink()
    assert not executor._validate_projected_todo_completion(checklist)
