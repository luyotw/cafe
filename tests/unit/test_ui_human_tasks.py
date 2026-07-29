"""Tests for policy-backed human-task UI coordination."""

from __future__ import annotations

from pathlib import Path

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.skills.loader import SkillLoader
from cafe.ui.human_tasks import apply_human_task_payload, resolve_step_human_task


def test_custom_step_resolves_its_skill_owned_human_task(tmp_path: Path) -> None:
    """Resolution uses declared metadata and never infers a development step name."""
    skill_dir = tmp_path / "builtin" / "skills" / "editorial-review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: editorial-review
description: editorial review
workflow:
  human_tasks:
    - id: editorial-approval
      pattern: confirm_output
      prompt: Approve this editorial brief
      input_schema: decision
      decisions:
        - id: approve
          label: Approve
---
""",
        encoding="utf-8",
    )
    loader = SkillLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )
    policy, binding = resolve_step_human_task(
        playbook_data={
            "steps": {
                "brief": {
                    "skill": "editorial-review",
                    "human_tasks": [
                        {
                            "trigger": "confirm_output",
                            "task_id": "editorial-approval",
                            "outcomes": {"approve": "draft"},
                        }
                    ],
                }
            }
        },
        step_name="brief",
        trigger="confirm_output",
        skill_loader=loader,
    )

    assert policy.prompt == "Approve this editorial brief"
    assert binding.outcomes == {"approve": "draft"}


def test_command_completion_uses_the_same_policy_and_declared_destination(tmp_path: Path) -> None:
    """A JSON response advances only through its policy's permitted continuation."""
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("spec", playbook_id="default")
    store.update_handoff_contract(
        blackboard,
        from_step="spec",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.CONFIRM_OUTPUT,
        source="test",
    )
    playbook = {
        "steps": {
            "spec": {
                "skill": "cafe-spec",
                "human_tasks": [
                    {
                        "trigger": "confirm_output",
                        "task_id": "output-review",
                        "outcomes": {"confirm": "plan", "revise": "spec"},
                    }
                ],
            },
            "plan": {"skill": "cafe-plan"},
        }
    }

    result = apply_human_task_payload(
        issue_dir=issue_dir,
        playbook_data=playbook,
        blackboard=blackboard,
        from_step="spec",
        trigger="confirm_output",
        raw_payload='{"task":"output-review","decision":"confirm"}',
        source="command",
    )

    assert result.target == "plan"
    reloaded = store.load_or_create("spec", playbook_id="default")
    assert reloaded.current_step == "plan"
    assert reloaded.handoff_contract.to_owner == HandoffOwner.AGENT
    assert reloaded.handoff_contract.to_step == "plan"


def test_dynamic_xml_questions_reject_incomplete_command_answers(tmp_path: Path) -> None:
    """Command validation reads the same current XML question set as the UI."""
    issue_dir = tmp_path / ".cafe" / "issues" / "dynamic-questions"
    iteration_dir = issue_dir / "spec" / "iteration_001"
    iteration_dir.mkdir(parents=True)
    (iteration_dir / "questions.xml").write_text(
        (
            "<questions>\n"
            "  <question id=\"scope\"><title>Scope?</title><options><option>Small</option>"
            "</options></question>\n"
            "  <question id=\"compatibility\"><title>Compatibility?</title><options>"
            "<option>Yes</option></options></question>\n"
            "</questions>"
        ),
        encoding="utf-8",
    )
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("spec", playbook_id="default")
    store.set_current_step(blackboard, "user")
    store.update_handoff_contract(
        blackboard,
        from_step="spec",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.NEED_CLARIFICATION,
        source="test",
    )
    playbook = {
        "steps": {
            "spec": {
                "skill": "cafe-spec",
                "human_tasks": [
                    {
                        "trigger": "need_clarification",
                        "task_id": "clarification-answers",
                        "outcomes": {"submit": "spec"},
                    }
                ],
            }
        }
    }

    result = apply_human_task_payload(
        issue_dir=issue_dir,
        playbook_data=playbook,
        blackboard=blackboard,
        from_step="spec",
        trigger="need_clarification",
        raw_payload={"task": "clarification-answers", "answers": {"scope": "Small"}},
        source="command",
    )

    assert result.rejection is not None
    assert store.load_or_create("spec").current_step == "user"
