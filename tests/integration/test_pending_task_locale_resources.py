"""Durable answers retain their saved contract across owner-copy changes."""

import json
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from cafe.core.blackboard import BlackboardStore
from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader
from cafe.ui.cli import app
from cafe.ui.human_tasks import apply_human_task_payload


@pytest.fixture
def pending_owner(tmp_path, monkeypatch, request):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAFE_TEST_RUN_SLACK_NOTIFICATIONS", "0")
    monkeypatch.setattr("cafe.utils.config.get_global_cafe_dir", lambda: tmp_path / "global")
    source = Path(__file__).resolve().parents[2] / "src/cafe/data/skills/cafe-spec"
    owner = tmp_path / ".cafe/skills/cafe-spec"
    shutil.copytree(source, owner)
    issue = tmp_path / ".cafe/issues/pending-copy"
    issue.mkdir(parents=True)
    (issue / "issue.yaml").write_text("playbook: standard\npr:\n  auto_create: false\n")
    store = BlackboardStore(issue)
    state = store.load_or_create("spec", playbook_id="standard")
    state.conversation_locale = "zh-TW"
    state.conversation_locale_source = "explicit"
    store.save(state)
    playbook = PlaybookLoader(project_root=tmp_path).load("standard")
    status = (
        "need_clarification"
        if getattr(request, "param", None) == "need_clarification"
        else "ready_for_review"
    )
    BlackboardWorkflowRuntime(
        issue_dir=issue,
        playbook=playbook,
        executor=lambda *_args: StepExecutionResult(
            response=status,
            artifacts={},
            status_code=status,
            auto_continue=False,
        ),
    ).run(start_step="spec")
    task = HumanTaskRecordStore(issue).tasks()[0]
    assert task.status is HumanTaskStatus.PENDING
    return tmp_path, owner, issue, playbook, task


def remove_or_change_copy(owner, mutation):
    if mutation == "remove":
        shutil.rmtree(owner / "locales")
    else:
        for resource in owner.glob("locales/*.yaml"):
            resource.write_text("invalid.copy: Updated owner presentation\n")


@pytest.mark.parametrize("consumer", ["command", "loaded-playbook"])
@pytest.mark.parametrize("mutation", ["remove", "change"])
def test_pending_original_answer_ignores_current_owner_presentation(
    pending_owner, consumer, mutation
):
    project, owner, issue, playbook, task = pending_owner
    declaration = (owner / "SKILL.md").read_bytes()
    expected = task.expected_result
    remove_or_change_copy(owner, mutation)
    assert (owner / "SKILL.md").read_bytes() == declaration
    with pytest.raises(ValueError):
        SkillLoader(project_root=project).get_workflow_declaration("cafe-spec")
    runner = CliRunner()
    inspected = runner.invoke(app, ["task", "inspect", task.id, "--json"])
    assert inspected.exit_code == 0, inspected.stdout
    assert HumanTaskRecordStore(issue).get_task(task.id).expected_result == expected
    if consumer == "command":
        result = runner.invoke(
            app,
            [
                "task",
                "complete",
                task.id,
                "--result",
                '{"decision":"confirm"}',
                "--no-resume",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.stdout
    else:
        state = BlackboardStore(issue).load_or_create("spec")
        applied = apply_human_task_payload(
            issue_dir=issue,
            playbook_data=playbook,
            blackboard=state,
            from_step="spec",
            trigger="confirm_output",
            source="test",
            raw_payload={
                "task": task.policy_id,
                "human_task_id": task.id,
                "decision": "confirm",
            },
        )
        assert applied.rejection is None and applied.target == "plan"
        assert applied.policy.model_dump(mode="json") == expected
    records = HumanTaskRecordStore(issue)
    assert records.get_task(task.id).status is HumanTaskStatus.COMPLETED
    assert records.get_task(task.id).expected_result == expected
    assert len(records.results()) == 1
    state = BlackboardStore(issue).load_or_create("spec")
    assert state.current_step == "plan" and state.conversation_locale == "zh-TW"


def test_pending_snapshot_recovers_one_result_after_interrupted_transition(
    pending_owner, monkeypatch
):
    _project, owner, issue, playbook, task = pending_owner
    remove_or_change_copy(owner, "remove")
    payload = {"task": task.policy_id, "human_task_id": task.id, "decision": "confirm"}
    original = BlackboardStore.set_current_step

    def interrupted(store, state, step):
        if step == "plan":
            raise OSError("interrupted transition write")
        return original(store, state, step)

    monkeypatch.setattr(BlackboardStore, "set_current_step", interrupted)
    with pytest.raises(OSError):
        apply_human_task_payload(
            issue_dir=issue,
            playbook_data=playbook,
            blackboard=BlackboardStore(issue).load_or_create("spec"),
            from_step="spec",
            trigger="confirm_output",
            raw_payload=payload,
            source="test",
        )
    result_id = HumanTaskRecordStore(issue).get_result(task.id).id
    monkeypatch.setattr(BlackboardStore, "set_current_step", original)
    recovered = apply_human_task_payload(
        issue_dir=issue,
        playbook_data=playbook,
        blackboard=BlackboardStore(issue).load_or_create("spec"),
        from_step="spec",
        trigger="confirm_output",
        raw_payload=payload,
        source="test",
    )
    assert recovered.rejection is None and recovered.target == "plan"
    reopened = HumanTaskRecordStore(issue)
    assert reopened.get_result(task.id).id == result_id
    assert len(reopened.results()) == 1


def test_saved_answer_does_not_override_changed_machine_contract(pending_owner):
    _project, owner, issue, playbook, task = pending_owner
    skill_file = owner / "SKILL.md"
    _opening, frontmatter, body = skill_file.read_text().split("---", 2)
    metadata = yaml.safe_load(frontmatter)
    metadata["workflow"]["human_tasks"][0]["decisions"][0]["requires_feedback"] = True
    skill_file.write_text("---\n" + yaml.safe_dump(metadata) + "---" + body)
    remove_or_change_copy(owner, "remove")
    applied = apply_human_task_payload(
        issue_dir=issue,
        playbook_data=playbook,
        blackboard=BlackboardStore(issue).load_or_create("spec"),
        from_step="spec",
        trigger="confirm_output",
        source="test",
        raw_payload={
            "task": task.policy_id,
            "human_task_id": task.id,
            "decision": "confirm",
        },
    )
    assert applied.rejection is not None and applied.target is None
    assert HumanTaskRecordStore(issue).get_task(task.id).status is HumanTaskStatus.PENDING
    assert HumanTaskRecordStore(issue).results() == ()


def test_workflow_reentry_uses_saved_policy_before_validating_an_answer(pending_owner):
    _project, owner, issue, _playbook, task = pending_owner
    remove_or_change_copy(owner, "remove")
    payload = json.dumps(
        {
            "task": task.policy_id,
            "human_task_id": task.id,
            "decision": "invalid-choice",
        }
    )
    result = CliRunner().invoke(
        app,
        [
            "workflow",
            "--execute",
            "--issue",
            issue.name,
            "--user-input",
            payload,
            "--single-step",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert task.expected_result["correction_guidance"] in result.stdout
    assert HumanTaskRecordStore(issue).get_task(task.id).status is HumanTaskStatus.PENDING
    assert HumanTaskRecordStore(issue).results() == ()


def test_interactive_completion_presents_the_saved_policy(pending_owner, monkeypatch):
    from cafe.ui import cli_shared

    _project, owner, issue, playbook, task = pending_owner
    remove_or_change_copy(owner, "remove")

    def participant(prompt, choices, **kwargs):
        assert prompt == task.expected_result["prompt"]
        authored_choices = {
            item["value"]: item["name"] for item in choices if item["value"] != "chat"
        }
        assert authored_choices == {
            item["id"]: item["label"] for item in task.expected_result["decisions"]
        }
        return "confirm"

    monkeypatch.setattr("cafe.ui.inquirer_prompts.prompt_list", participant)
    target = cli_shared._handle_declared_human_task_handoff(
        issue_name=issue.name,
        issue_dir=issue,
        blackboard=BlackboardStore(issue).load_or_create("spec"),
        from_step="spec",
        summary="",
        playbook_data=playbook,
        trigger="confirm_output",
    )
    assert target == "plan"
    assert HumanTaskRecordStore(issue).get_task(task.id).status is HumanTaskStatus.COMPLETED


@pytest.mark.parametrize("pending_owner", ["need_clarification"], indirect=True)
def test_dynamic_answers_use_existing_question_file_without_reloading_copy(pending_owner):
    _project, owner, issue, playbook, task = pending_owner
    assert task.expected_result["questions_from_xml"] is True
    directory = issue / "spec" / f"iteration_{task.iteration:03d}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "questions.xml").write_text(
        '<questions><question id="1"><title>Audience?</title><options>'
        "<option>reader</option><option>editor</option></options></question></questions>"
    )
    remove_or_change_copy(owner, "remove")
    applied = apply_human_task_payload(
        issue_dir=issue,
        playbook_data=playbook,
        blackboard=BlackboardStore(issue).load_or_create("spec"),
        from_step="spec",
        trigger=task.trigger,
        source="test",
        raw_payload={
            "task": task.policy_id,
            "human_task_id": task.id,
            "answers": {"1": "reader"},
        },
    )
    assert applied.rejection is None and applied.target == "spec"
    assert HumanTaskRecordStore(issue).get_task(task.id).status is HumanTaskStatus.COMPLETED
