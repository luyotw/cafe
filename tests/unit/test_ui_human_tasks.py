"""Tests for policy-backed human-task UI coordination."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.human_tasks import HumanTaskPolicy
from cafe.skills.loader import SkillLoader
from cafe.ui.human_tasks import (
    _validate_packet_contracts_before_confirmation,
    apply_human_task_payload,
    collect_human_task_payload,
    resolve_step_human_task,
)


def test_packet_confirmation_uses_next_runnable_consumer_iteration_for_contract_validation(
    tmp_path: Path, monkeypatch
) -> None:
    """IT-001: confirmation uses the consumer's next runnable skill contract."""
    builtin_root = tmp_path / "builtin"
    for name, prompt_inputs in (
        ("first-consumer", "  prompt_inputs: []\n"),
        (
            "current-consumer",
            """  prompt_inputs:
    - artifacts: [spec]
      placeholder: spec_file
      load_policy:
        - when: {}
          mode: packet
          contract_kind: spec
""",
        ),
    ):
        skill_dir = builtin_root / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            f"name: {name}\n"
            "description: test skill\n"
            "workflow:\n"
            f"{prompt_inputs}"
            "---\n",
            encoding="utf-8",
        )
    loader = SkillLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    monkeypatch.setattr("cafe.ui.human_tasks.SkillLoader", lambda: loader)
    invalid_spec = tmp_path / "spec.md"
    invalid_spec.write_text("# Missing downstream contract\n", encoding="utf-8")

    rejection = _validate_packet_contracts_before_confirmation(
        playbook_data={
            "steps": {
                "spec": {"output_artifact": "spec"},
                "develop": {
                    "input_artifacts": ["spec"],
                    "skill": {"1": "first-consumer", "2": "current-consumer"},
                },
            }
        },
        blackboard=SimpleNamespace(artifacts={"spec": SimpleNamespace(path=invalid_spec)}),
        issue_dir=_iteration_history(tmp_path, producer=1, consumer=1),
        producer_step="spec",
        correction_guidance="repair the contract",
    )

    assert rejection is not None
    assert "spec -> develop" in rejection.message


def _iteration_history(tmp_path: Path, *, producer: int, consumer: int) -> Path:
    """Create completed producer and consumer iteration histories."""
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    for step_name, count in (("spec", producer), ("develop", consumer)):
        for iteration in range(1, count + 1):
            iteration_dir = issue_dir / step_name / f"iteration_{iteration:03d}"
            iteration_dir.mkdir(parents=True)
            (iteration_dir / "iteration.json").write_text(
                json.dumps({"iteration": iteration, "end_time": "done"}),
                encoding="utf-8",
            )
    return issue_dir


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


def test_iteration_mapped_step_uses_the_selected_iteration_skill(tmp_path: Path) -> None:
    """Iteration-specific policies must match the skill that executed the step."""
    builtin_root = tmp_path / "builtin"
    for skill_name, decision_id in (("first-brief", "approve"), ("revised-brief", "revise")):
        skill_dir = builtin_root / "skills" / skill_name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"""---
name: {skill_name}
description: test skill
workflow:
  human_tasks:
    - id: review-brief
      pattern: confirm_output
      prompt: Review the brief
      input_schema: decision
      decisions:
        - id: {decision_id}
          label: {decision_id.title()}
---
""",
            encoding="utf-8",
        )
    loader = SkillLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    policy, _binding = resolve_step_human_task(
        playbook_data={
            "steps": {
                "brief": {
                    "skill": {"1": "first-brief", "default": "revised-brief"},
                    "human_tasks": [
                        {
                            "trigger": "confirm_output",
                            "task_id": "review-brief",
                            "outcomes": {"approve": "draft"},
                        }
                    ],
                }
            }
        },
        step_name="brief",
        trigger="confirm_output",
        skill_loader=loader,
        iteration=1,
    )

    assert [decision.id for decision in policy.decisions] == ["approve"]


def test_inline_multiple_choice_uses_checkbox_answers(monkeypatch) -> None:
    """Interactive inline multi-select responses retain every selected option."""
    calls: list[tuple[str, list[str]]] = []

    def collect_checkbox(message: str, choices: list[str], default=None) -> list[str]:
        calls.append((message, choices))
        return ["Email", "Events"]

    monkeypatch.setattr("cafe.ui.inquirer_prompts.prompt_checkbox", collect_checkbox)
    monkeypatch.setattr(
        "cafe.ui.inquirer_prompts.prompt_multiline", lambda *_args, **_kwargs: "Email"
    )
    policy = HumanTaskPolicy.model_validate(
        {
            "id": "channels",
            "pattern": "answer_questions",
            "prompt": "Select channels",
            "input_schema": "answers",
            "questions": [
                {
                    "id": "channel",
                    "prompt": "Choose all channels",
                    "options": ["Email", "Events"],
                    "multiple": True,
                }
            ],
        }
    )

    payload = collect_human_task_payload(policy)

    assert payload == {
        "task": "channels",
        "answers": {"channel": ["Email", "Events"]},
    }
    assert calls == [("Choose all channels", ["Email", "Events"])]


def test_inline_single_choice_uses_declared_options(monkeypatch) -> None:
    """Interactive single-choice responses come from the policy allowlist."""
    calls: list[tuple[str, list[str]]] = []

    def collect_list(message: str, choices: list[str], default=None) -> str:
        calls.append((message, choices))
        return "Email"

    monkeypatch.setattr("cafe.ui.inquirer_prompts.prompt_list", collect_list)
    monkeypatch.setattr(
        "cafe.ui.inquirer_prompts.prompt_multiline", lambda *_args, **_kwargs: "Unlisted"
    )
    policy = HumanTaskPolicy.model_validate(
        {
            "id": "channel",
            "pattern": "answer_questions",
            "prompt": "Select a channel",
            "input_schema": "answers",
            "questions": [
                {
                    "id": "primary",
                    "prompt": "Choose one channel",
                    "options": ["Email", "Events"],
                }
            ],
        }
    )

    payload = collect_human_task_payload(policy)

    assert payload == {"task": "channel", "answers": {"primary": "Email"}}
    assert calls == [("Choose one channel", ["Email", "Events"])]


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
