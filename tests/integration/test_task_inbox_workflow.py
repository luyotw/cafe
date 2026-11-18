"""Repository inbox journeys across durable task and workflow boundaries."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cafe.core.audit_events import AuditEventStore
from cafe.core.blackboard import ArtifactEntry, ArtifactKind, BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.playbooks.loader import PlaybookLoader
from cafe.ui.cli import app
from cafe.ui.human_tasks import apply_human_task_payload, resolve_step_human_task
from cafe.workflow_execution.automatic import automatic_executor_registry
from cafe.workflow_execution.event_callback import ResolvedWorkflowEventCallback
from cafe.workflow_execution.worker_launch import FixedWorkerLauncher, WorkerLaunchStore

pytestmark = pytest.mark.usefixtures("cached_builtin_playbook_models")

runner = CliRunner()


def _pending_issue(
    cafe_dir: Path,
    name: str,
    *,
    playbook_id: str = "standard",
    step: str = "spec",
    trigger: str = "confirm_output",
):
    issue_dir = cafe_dir / "issues" / name
    iteration_dir = issue_dir / step / "iteration_001"
    iteration_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text(f"playbook: {playbook_id}\n", encoding="utf-8")
    blackboards = BlackboardStore(issue_dir)
    state = blackboards.load_or_create(step, playbook_id=playbook_id)
    blackboards.set_current_step(state, "user")
    blackboards.update_handoff_contract(
        state,
        from_step=step,
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent(trigger),
        source="test",
    )
    policy, binding = resolve_step_human_task(
        playbook_data=PlaybookLoader().load(playbook_id),
        step_name=step,
        trigger=trigger,
    )
    task = HumanTaskRecordStore(issue_dir).materialize(
        workflow_id=state.workflow_id,
        step=step,
        iteration=1,
        trigger=trigger,
        policy_id=policy.id,
        prompt=policy.prompt,
        expected_result=policy.model_dump(mode="json"),
        continuations=binding.outcomes,
        assignee_type="user",
    )
    return issue_dir, task


def _workflow_task(project: Path, name: str, intent: HandoffIntent):
    issue_dir = project / ".cafe" / "issues" / name
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook: standard\n", encoding="utf-8")

    def execute(step, _definition, state):
        iteration = issue_dir / step / "iteration_001"
        iteration.mkdir(parents=True, exist_ok=True)
        (iteration / "output.md").write_text("# Reviewed spec\n", encoding="utf-8")
        (iteration / "questions.xml").write_text(
            '<questions><question id="scope"><title>Which scope?</title>'
            '<options><option>Small</option></options></question>'
            '<question id="platforms" type="checkbox"><title>Which platforms?</title>'
            '<options><option>CLI</option><option>API</option></options></question></questions>',
            encoding="utf-8",
        )
        BlackboardStore(issue_dir).update_handoff_contract(
            state, from_step=step, to_owner=HandoffOwner.USER, to_step="user",
            intent=intent, source="test.executor",
        )
        return StepExecutionResult(response="Ready for input", artifacts={})

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=PlaybookLoader().load("standard"), executor=execute,
        automatic_registry=automatic_executor_registry(),
    ).run(start_step="spec")
    assert not result.completed
    (task,) = HumanTaskRecordStore(issue_dir).tasks()
    assert task.status is HumanTaskStatus.PENDING and task.trigger == intent.value
    return issue_dir, task


def _inspect_snapshot(task_id: str) -> dict:
    result = runner.invoke(app, ["task", "inspect", task_id, "--json"])
    assert result.exit_code == 0, (result.stdout, result.exception)
    return json.loads(result.stdout)["data"]["snapshot"]


def _submit_snapshot(task, version: str, **response):
    return runner.invoke(app, [
        "task", "complete", task.id, "--no-resume", "--json", "--result",
        json.dumps({"snapshot_version": version, **response}),
    ])


def test_external_client_answers_the_runtime_questions_bound_to_its_pending_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir, task = _workflow_task(tmp_path, "questions", HandoffIntent.NEED_CLARIFICATION)
    newer = issue_dir / "spec" / "iteration_002"
    newer.mkdir()
    (newer / "questions.xml").write_text(
        '<questions><question id="unrelated"><title>Unrelated?</title>'
        '<options><option>No</option></options></question></questions>', encoding="utf-8",
    )

    snapshot = _inspect_snapshot(task.id)
    assert snapshot["status"] == "available"
    assert snapshot["questions"]["items"] == [
        {"id": "scope", "title": "Which scope?", "options": ["Small"], "multi_select": False},
        {"id": "platforms", "title": "Which platforms?", "options": ["CLI", "API"],
         "multi_select": True},
    ]
    assert snapshot["review_artifact"]["status"] == "not_applicable"

    invalid = _submit_snapshot(task, snapshot["version"], answers={"unrelated": "No"})
    assert invalid.exit_code != 0
    assert json.loads(invalid.stdout)["error"]["code"] == "invalid_response"
    assert HumanTaskRecordStore(issue_dir).results() == ()
    assert BlackboardStore(issue_dir).load_read_only().current_step == "user"

    response = {"answers": {"scope": "Small", "platforms": ["CLI", "API"]}}
    valid = _submit_snapshot(task, snapshot["version"], **response)
    assert valid.exit_code == 0, (valid.stdout, valid.exception)
    replay = _submit_snapshot(task, snapshot["version"], **response)
    assert json.loads(replay.stdout)["error"]["code"] == "task_not_pending"
    records = HumanTaskRecordStore(issue_dir)
    assert len(records.results()) == 1
    assert BlackboardStore(issue_dir).load_read_only().current_step == "spec"


@pytest.mark.parametrize("component", ["questions", "review_artifact"])
def test_external_client_must_refresh_changed_or_unavailable_task_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, component: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    is_review = component == "review_artifact"
    intent = HandoffIntent.CONFIRM_OUTPUT if is_review else HandoffIntent.NEED_CLARIFICATION
    issue_dir, task = _workflow_task(tmp_path, "changed", intent)
    bound_file = issue_dir / "spec" / "iteration_001" / (
        "output.md" if is_review else "questions.xml"
    )
    newer = issue_dir / "spec" / "iteration_002"
    newer.mkdir()
    (newer / bound_file.name).write_text("Unrelated data", encoding="utf-8")
    response = {"decision": "confirm"} if is_review else {
        "answers": {"scope": "Small", "platforms": ["CLI"]}
    }
    first = _inspect_snapshot(task.id)
    if is_review:
        assert first[component]["text"] == "# Reviewed spec\n"

    old_text = bound_file.read_text(encoding="utf-8")
    bound_file.write_text(
        "# Updated spec\n" if is_review else old_text.replace("Which scope?", "Updated scope?"),
        encoding="utf-8",
    )
    second = _inspect_snapshot(task.id)
    assert second["version"] and second["version"] != first["version"]
    stale = _submit_snapshot(task, first["version"], **response)
    assert stale.exit_code != 0
    assert json.loads(stale.stdout)["error"]["code"] == "stale_snapshot"

    bound_file.unlink()
    assert _inspect_snapshot(task.id)[component] == {"status": "unavailable"}
    bound_file.symlink_to(newer / bound_file.name)
    unavailable = _inspect_snapshot(task.id)
    assert unavailable["version"] is None
    assert unavailable[component] == {"status": "unavailable"}
    missing = _submit_snapshot(task, second["version"], **response)
    assert missing.exit_code != 0
    assert json.loads(missing.stdout)["error"]["code"] == "snapshot_unavailable"
    assert BlackboardStore(issue_dir).load_read_only().current_step == "user"
    assert HumanTaskRecordStore(issue_dir).results() == ()


def test_external_client_cannot_use_a_snapshot_from_another_workflow_or_stale_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir, stale_task = _workflow_task(tmp_path, "stale", HandoffIntent.CONFIRM_OUTPUT)
    other_dir, other = _workflow_task(tmp_path, "other", HandoffIntent.CONFIRM_OUTPUT)
    version = _inspect_snapshot(stale_task.id)["version"]

    wrong = _submit_snapshot(other, version, decision="confirm")
    assert wrong.exit_code != 0
    assert json.loads(wrong.stdout)["error"]["code"] == "stale_snapshot"
    assert BlackboardStore(other_dir).load_read_only().current_step == "user"
    assert HumanTaskRecordStore(other_dir).results() == ()

    wrong = _submit_snapshot(
        other, _inspect_snapshot(other.id)["version"], decision="confirm",
        workflow_id=stale_task.workflow_id,
    )
    assert json.loads(wrong.stdout)["error"]["code"] == "stale_task"

    store = BlackboardStore(issue_dir)
    state = store.load_read_only()
    store.update_handoff_contract(
        state, from_step="spec", to_owner=HandoffOwner.USER, to_step="user",
        intent=HandoffIntent.NEED_CLARIFICATION, source="test.replaced_handoff",
    )
    assert _inspect_snapshot(stale_task.id)["status"] == "unavailable"
    stale = _submit_snapshot(stale_task, version, decision="confirm")
    assert stale.exit_code != 0
    assert json.loads(stale.stdout)["error"]["code"] == "stale_task"
    assert store.load_read_only().current_step == "user"
    assert HumanTaskRecordStore(issue_dir).results() == ()


def test_external_review_uses_the_bound_artifact_and_rejects_a_repointed_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir, task = _workflow_task(tmp_path, "review", HandoffIntent.CONFIRM_OUTPUT)
    store = BlackboardStore(issue_dir)
    older = issue_dir / "spec" / "iteration_001" / "output.md"
    newer = issue_dir / "spec" / "iteration_002" / "output.md"
    newer.parent.mkdir()
    newer.write_text("# Draft outside the task\n", encoding="utf-8")

    def register(path: Path, version: int) -> None:
        store.put_artifact(store.load_read_only(), ArtifactEntry(
            name="spec", kind=ArtifactKind.DOCUMENT, version=version,
            updated_by="spec", path=str(path),
        ))

    register(older, 1)
    snapshot = _inspect_snapshot(task.id)
    assert snapshot["review_artifact"]["path"] == "spec/iteration_001/output.md"
    assert snapshot["review_artifact"]["text"] == "# Reviewed spec\n"
    register(newer, 2)
    assert _inspect_snapshot(task.id)["review_artifact"] == {"status": "unavailable"}
    stale = _submit_snapshot(task, snapshot["version"], decision="confirm")
    assert json.loads(stale.stdout)["error"]["code"] == "snapshot_unavailable"
    assert store.load_read_only().current_step == "user"
    assert HumanTaskRecordStore(issue_dir).results() == ()

    register(older, 3)
    current = _inspect_snapshot(task.id)
    assert current["version"] != snapshot["version"]
    confirmed = _submit_snapshot(task, current["version"], decision="confirm")
    assert confirmed.exit_code == 0, (confirmed.stdout, confirmed.exception)
    assert store.load_read_only().current_step == "plan"
    older.write_text("# Later edit\n", encoding="utf-8")
    accepted = HumanTaskRecordStore(issue_dir).get_result(task.id)
    assert accepted is not None
    reviewed = accepted.payload["reviewed_snapshot"]
    assert reviewed["version"] == current["version"]
    assert reviewed["review_artifact"]["sha256"] == current["review_artifact"]["sha256"]
    assert reviewed["review_artifact"]["text"] == "# Reviewed spec\n"


@pytest.mark.parametrize("replacement", ["handoff", "output", "questions"])
def test_external_reply_rechecks_replacement_at_the_durable_commit_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    intent = (HandoffIntent.NEED_CLARIFICATION if replacement == "questions"
              else HandoffIntent.CONFIRM_OUTPUT)
    issue_dir, task = _workflow_task(tmp_path, "late-change", intent)
    version = _inspect_snapshot(task.id)["version"]
    store = BlackboardStore(issue_dir)
    from cafe.core.human_task_records import atomic_write_bytes

    def replace_before_write(path, content, **kwargs):
        if not any(item["task_id"] == task.id for item in json.loads(content)["results"]):
            return atomic_write_bytes(path, content, **kwargs)
        if replacement == "handoff":
            store.update_handoff_contract(
                store.load_read_only(), from_step="spec", to_owner=HandoffOwner.USER,
                to_step="user", intent=HandoffIntent.NEED_CLARIFICATION,
                source="test.concurrent_handoff",
            )
        else:
            path = issue_dir / "spec" / "iteration_001" / (
                "questions.xml" if replacement == "questions" else "output.md"
            )
            changed = path.with_suffix(".replacement")
            changed.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            changed.replace(path)
        return atomic_write_bytes(path, content, **kwargs)

    monkeypatch.setattr("cafe.core.human_task_records.atomic_write_bytes", replace_before_write)
    response = ({"answers": {"scope": "Small", "platforms": ["CLI"]}}
                if replacement == "questions" else {"decision": "confirm"})
    submitted = _submit_snapshot(task, version, **response)
    assert submitted.exit_code != 0, submitted.stdout
    error = json.loads(submitted.stdout)["error"]
    assert error["code"] == ("stale_task" if replacement == "handoff" else "stale_snapshot")
    state = store.load_read_only()
    assert state.current_step == "user"
    assert state.handoff_contract.intent is (
        HandoffIntent.NEED_CLARIFICATION if replacement == "handoff" else intent
    )
    assert HumanTaskRecordStore(issue_dir).results() == ()


def test_external_reply_cannot_swap_two_declared_continuations_after_inspection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir, task = _workflow_task(tmp_path, "route-change", HandoffIntent.CONFIRM_OUTPUT)
    version = _inspect_snapshot(task.id)["version"]
    definition = PlaybookLoader().load("standard")
    for step in definition["steps"].values():
        step.get("initial_input", {}).pop("legacy_presentation", None)
    binding = next(
        item for item in definition["steps"]["spec"]["human_tasks"]
        if item["trigger"] == task.trigger
    )
    binding["outcomes"] = {"confirm": "spec", "revise": "plan"}
    catalog = tmp_path / ".cafe" / "playbooks"
    catalog.mkdir()
    (catalog / "standard.yaml").write_text(json.dumps(dict(definition)), encoding="utf-8")

    submitted = _submit_snapshot(task, version, decision="confirm")
    assert submitted.exit_code != 0, submitted.stdout
    assert json.loads(submitted.stdout)["error"]["code"] == "stale_snapshot"
    assert _inspect_snapshot(task.id)["status"] == "unavailable"
    assert BlackboardStore(issue_dir).load_read_only().current_step == "user"
    assert HumanTaskRecordStore(issue_dir).results() == ()


@pytest.mark.parametrize("replace_handoff", [False, True])
def test_recovery_projects_a_committed_snapshot_only_for_its_owning_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replace_handoff: bool,
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir, task = _workflow_task(tmp_path, "recover-review", HandoffIntent.CONFIRM_OUTPUT)
    version = _inspect_snapshot(task.id)["version"]

    record_event = BlackboardStore.record_event

    def interrupt_projection(self, state, event_type, payload, **kwargs):
        if event_type == "human_task_completed":
            raise OSError("interrupted workflow projection")
        return record_event(self, state, event_type, payload, **kwargs)

    with monkeypatch.context() as interrupted:
        interrupted.setattr(BlackboardStore, "record_event", interrupt_projection)
        completion = _submit_snapshot(task, version, decision="confirm")
        assert completion.exit_code != 0

    records = HumanTaskRecordStore(issue_dir)
    (accepted,) = records.results()
    assert accepted.payload["reviewed_snapshot"]["version"] == version
    store = BlackboardStore(issue_dir)
    state = store.load_read_only()
    assert state.current_step == "user"
    if replace_handoff:
        store.update_handoff_contract(
            state, from_step="spec", to_owner=HandoffOwner.USER, to_step="user",
            intent=HandoffIntent.CONFIRM_OUTPUT, source="test.replacement",
        )
    original_handoff = state.handoff_contract
    continuation = _continuation_command(issue_dir.name, task, accepted.id)
    if replace_handoff:
        assert _continuation_status([*continuation, "--resume"]) == "unknown"
    else:
        assert _continuation_status(continuation) == "pending"

    recovered = apply_human_task_payload(
        issue_dir=issue_dir, playbook_data=PlaybookLoader().load("standard"), blackboard=state,
        from_step=task.step, trigger=task.trigger,
        raw_payload={"human_task_id": task.id, "decision": "confirm"}, source="interactive",
    )
    assert records.results() == (accepted,)
    if replace_handoff:
        assert recovered.error_code == "stale_task"
        assert store.load_read_only().handoff_contract == original_handoff
    else:
        assert recovered.target == "plan"
        assert store.load_read_only().current_step == "plan"


def test_noninteractive_completion_resumes_only_bound_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test List I3: stable completion ignores active marker and resumes one owner."""
    monkeypatch.chdir(tmp_path)
    cafe_dir = tmp_path / ".cafe"
    selected_dir, selected = _pending_issue(cafe_dir, "selected")
    other_dir, other = _pending_issue(cafe_dir, "active")
    marker = cafe_dir / "active_issue"
    marker.write_bytes(b"active\n")
    other_before = (other_dir / "blackboard.json").read_bytes()
    resumed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "cafe.ui.commands.tasks._resume_issue_workflow",
        lambda issue, playbook: resumed.append((issue, playbook)),
    )

    result = runner.invoke(
        app,
        ["task", "complete", selected.id, "--result", '{"decision":"confirm"}'],
    )

    assert result.exit_code == 0
    records = HumanTaskRecordStore(selected_dir)
    assert records.get_task(selected.id).status is HumanTaskStatus.COMPLETED
    assert len(records.results()) == 1
    assert BlackboardStore(selected_dir).load_or_create("spec").current_step == "plan"
    assert resumed == [("selected", "standard")]
    assert marker.read_bytes() == b"active\n"
    assert (other_dir / "blackboard.json").read_bytes() == other_before
    assert HumanTaskRecordStore(other_dir).get_task(other.id).status is HumanTaskStatus.PENDING


def test_noninteractive_completion_can_defer_workflow_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A durable completion can be handed to the fixed background worker separately."""
    monkeypatch.chdir(tmp_path)
    issue_dir, task = _pending_issue(tmp_path / ".cafe", "deferred")
    _other_dir, _other_task = _pending_issue(tmp_path / ".cafe", "active")
    (tmp_path / ".cafe" / "active_issue").write_text("active\n", encoding="utf-8")
    resumed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "cafe.ui.commands.tasks._resume_issue_workflow",
        lambda issue, playbook: resumed.append((issue, playbook)),
    )

    result = runner.invoke(
        app,
        [
            "task",
            "complete",
            task.id,
            "--result",
            '{"decision":"confirm"}',
            "--no-resume",
        ],
    )

    assert result.exit_code == 0
    assert HumanTaskRecordStore(issue_dir).get_task(task.id).status is HumanTaskStatus.COMPLETED
    assert BlackboardStore(issue_dir).load_or_create("spec").current_step == "plan"
    assert resumed == []
    assert "--issue deferred --execute --background" in result.stdout


def _continuation_command(issue: str, task, result_id: str) -> list[str]:
    return [
        "task", "continuation", task.id, "--issue", issue,
        "--workflow-id", task.workflow_id, "--result-id", result_id, "--json",
    ]


def _continuation_status(command: list[str]) -> str:
    response = runner.invoke(app, command)
    assert response.exit_code == 0, (response.stdout, response.exception)
    return json.loads(response.stdout)["data"]["status"]


@pytest.fixture
def saved_continuation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    issue_dir, task = _workflow_task(tmp_path, "continuation", HandoffIntent.CONFIRM_OUTPUT)
    completed = _submit_snapshot(task, _inspect_snapshot(task.id)["version"], decision="confirm")
    assert completed.exit_code == 0, (completed.stdout, completed.exception)
    result_id = json.loads(completed.stdout)["data"]["task"]["result"]["id"]
    return issue_dir, task, _continuation_command(issue_dir.name, task, result_id)


class _ContinuationProvider:
    def __init__(self, calls=()):
        self.calls = calls

    def execute_step(self, step, _definition, state, *, extra_prompt=None):
        for call in self.calls:
            call()
        issue_dir = Path(".cafe/issues/continuation")
        iteration = issue_dir / step / "iteration_001"
        iteration.mkdir(parents=True, exist_ok=True)
        (iteration / "output.md").write_text("# Plan ready\n", encoding="utf-8")
        BlackboardStore(issue_dir).update_handoff_contract(
            state, from_step=step, to_owner=HandoffOwner.USER, to_step="user",
            intent=HandoffIntent.CONFIRM_OUTPUT, source="test.provider",
        )
        return StepExecutionResult(response="Plan ready", artifacts={})


@pytest.fixture
def continuation_provider(monkeypatch):
    calls = []
    monkeypatch.setattr("cafe.ui.cli._check_agent_clis_available", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "cafe.ui.cli._build_workflow_step_executor",
        lambda *_args, **_kwargs: _ContinuationProvider(calls),
    )
    return calls


def test_saved_result_survives_a_lost_resume_acknowledgement(
    saved_continuation, continuation_provider,
):
    issue_dir, task, command = saved_continuation
    executions = []
    continuation_provider.append(lambda: executions.append("plan"))
    assert _continuation_status(command) == "pending"

    # Let the real CLI and runtime run, then discard the reply at the client boundary.
    assert runner.invoke(app, [*command, "--resume"]).exit_code == 0
    assert _continuation_status(command) == "advanced"
    assert _continuation_status([*command, "--resume"]) == "advanced"
    records = HumanTaskRecordStore(issue_dir)
    assert executions == ["plan"]
    assert [result.task_id for result in records.results()] == [task.id]
    assert [item.step for item in records.tasks() if item.status is HumanTaskStatus.PENDING] == ["plan"]


def test_saved_result_lost_background_launch_acknowledgement_uses_durable_evidence(
    saved_continuation, monkeypatch,
):
    issue_dir, task, command = saved_continuation
    children = []
    source_root = Path(__file__).resolve().parents[2]
    child_code = """
import sys
sys.path.insert(0, sys.argv.pop(1))
assert sys.stdin.buffer.read(1) == b'1'
from cafe.ui import cli
from tests.integration.test_task_inbox_workflow import _ContinuationProvider
cli._check_agent_clis_available = lambda *args, **kwargs: []
cli._build_workflow_step_executor = lambda **kwargs: _ContinuationProvider()
cli.app()
"""

    def launch_child(args, **options):
        options.update(stdin=subprocess.PIPE, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
        child = subprocess.Popen([args[0], "-c", child_code, str(source_root), *args[3:]], **options)
        children.append(child)
        return child

    monkeypatch.setattr(
        "cafe.ui.commands.workflow.FixedWorkerLauncher",
        lambda directory: FixedWorkerLauncher(directory, popen_factory=launch_child),
    )
    try:
        # Drop the launch reply while the actual worker is alive but has not entered the CLI.
        assert runner.invoke(
            app, ["workflow", "--issue", issue_dir.name, "--execute", "--background"],
        ).exit_code == 0
        (child,) = children
        assert _continuation_status(command) == "pending"
        assert _continuation_status([*command, "--resume"]) == "unknown"
        stdout, stderr = child.communicate(input=b"1", timeout=30)
        assert child.returncode == 0, (stdout[-2000:], stderr[-2000:])
        assert _continuation_status([*command, "--resume"]) == "advanced"
        records = HumanTaskRecordStore(issue_dir)
        assert [result.task_id for result in records.results()] == [task.id]
        assert [item.step for item in records.tasks() if item.status is HumanTaskStatus.PENDING] == ["plan"]
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)


def test_saved_result_retry_defers_to_a_cli_resume_that_owns_advancement(
    saved_continuation, continuation_provider,
):
    issue_dir, task, command = saved_continuation
    during_run = []

    def offer_continuation_while_cli_owns_the_lock():
        during_run.append(_continuation_status(command))
        during_run.append(_continuation_status([*command, "--resume"]))

    continuation_provider.append(offer_continuation_while_cli_owns_the_lock)

    # The provider barrier runs while the ordinary CLI holds the real host lock.
    resumed = runner.invoke(app, ["workflow", "--issue", issue_dir.name, "--execute"])
    assert resumed.exit_code == 0, (resumed.stdout, resumed.exception)
    assert during_run == ["pending", "unknown"]
    assert _continuation_status([*command, "--resume"]) == "advanced"
    assert [result.task_id for result in HumanTaskRecordStore(issue_dir).results()] == [task.id]


@pytest.mark.parametrize("replacement_target", ["spec", "plan"])
@pytest.mark.parametrize("boundary", ["adapter", "runtime", "dispatch"])
def test_saved_result_resume_leaves_a_replacement_handoff_unconsumed(
    saved_continuation, continuation_provider, monkeypatch, boundary, replacement_target,
):
    issue_dir, task, command = saved_continuation
    subprocess.run(["git", "init", "-q"], check=True, capture_output=True)
    with Path(".git/info/exclude").open("a", encoding="utf-8") as excludes:
        excludes.write("\n/.cafe/\n")
    executions = []
    continuation_provider.append(lambda: executions.append(True))
    store = BlackboardStore(issue_dir)
    replacements = []

    def publish_replacement():
        if replacements:
            return
        state = store.load_read_only()
        replacements.append(store.update_handoff_contract(
            state, from_step="plan", to_owner=HandoffOwner.AGENT,
            to_step=replacement_target, intent=HandoffIntent.AWAIT_AGENT,
            source="chat.manual_handoff",
        ))

    owner, method = {
        "adapter": (PlaybookLoader, "load"),
        "runtime": (BlackboardWorkflowRuntime, "run"),
        "dispatch": (BlackboardWorkflowRuntime, "_run_from_current_step"),
    }[boundary]
    original = getattr(owner, method)

    def publish_before_use(self, *args, **kwargs):
        publish_replacement()
        return original(self, *args, **kwargs)

    before = store.load_read_only()
    monkeypatch.setattr(owner, method, publish_before_use)
    assert _continuation_status([*command, "--resume"]) == "unknown"
    (replacement,) = replacements
    after = store.load_read_only()
    assert not executions
    assert after.handoff_contract == replacement
    assert after.current_step == before.current_step
    assert after.events == before.events
    assert [result.task_id for result in HumanTaskRecordStore(issue_dir).results()] == [task.id]


def test_saved_result_retry_cannot_advance_a_replacement_workflow(
    tmp_path, saved_continuation,
):
    issue_dir, task, command = saved_continuation
    previous = issue_dir.rename(tmp_path / "previous-workflow")
    _, replacement = _workflow_task(tmp_path, issue_dir.name, HandoffIntent.CONFIRM_OUTPUT)
    before = BlackboardStore(issue_dir).load_read_only()

    assert _continuation_status(command) == "stale_workflow"
    assert _continuation_status([*command, "--resume"]) == "stale_workflow"
    assert BlackboardStore(issue_dir).load_read_only().to_dict() == before.to_dict()
    assert replacement.workflow_id != task.workflow_id
    assert not HumanTaskRecordStore(issue_dir).results()
    assert [result.task_id for result in HumanTaskRecordStore(previous).results()] == [task.id]


def test_saved_result_reports_a_retryable_startup_failure(
    saved_continuation, monkeypatch, continuation_provider,
):
    issue_dir, task, command = saved_continuation
    with monkeypatch.context() as failure:
        def missing_playbook(*_args, **_kwargs):
            raise FileNotFoundError("simulated unavailable playbook")

        failure.setattr(PlaybookLoader, "load", missing_playbook)
        assert _continuation_status([*command, "--resume"]) == "retryable_failure"
        assert _continuation_status(command) == "retryable_failure"

    assert _continuation_status([*command, "--resume"]) == "advanced"
    assert [result.task_id for result in HumanTaskRecordStore(issue_dir).results()] == [task.id]


def test_saved_result_retry_requires_the_exact_result_id(saved_continuation):
    issue_dir, task, command = saved_continuation
    wrong_result = _continuation_command(issue_dir.name, task, "different-result")
    response = runner.invoke(app, [*wrong_result, "--resume"])
    assert response.exit_code == 1
    assert json.loads(response.stdout)["error"]["code"] == "result_mismatch"
    assert _continuation_status(command) == "pending"


@pytest.mark.parametrize("audit_committed", [False, True])
def test_saved_result_can_be_projected_after_an_exit_during_completion(
    tmp_path, monkeypatch, continuation_provider, audit_committed,
):
    monkeypatch.chdir(tmp_path)
    issue_dir, task = _workflow_task(tmp_path, "continuation", HandoffIntent.CONFIRM_OUTPUT)
    record_event = BlackboardStore.record_event
    commit_event = AuditEventStore.commit

    def stop_before_projection(self, state, event_type, payload, **kwargs):
        if event_type == "human_task_completed":
            raise OSError("simulated exit before publishing the continuation")
        return record_event(self, state, event_type, payload, **kwargs)

    def stop_before_checkpoint(self, workflow_id, record):
        commit_event(self, workflow_id, record)
        if record["event_type"] == "human_task_completed":
            raise OSError("simulated exit after committing the continuation to the audit")

    with monkeypatch.context() as failure:
        if audit_committed:
            failure.setattr(AuditEventStore, "commit", stop_before_checkpoint)
        else:
            failure.setattr(BlackboardStore, "record_event", stop_before_projection)
        response = _submit_snapshot(task, _inspect_snapshot(task.id)["version"], decision="confirm")
        assert response.exit_code == 1

    inspected = runner.invoke(app, ["task", "inspect", task.id, "--json"])
    assert inspected.exit_code == 0, (inspected.stdout, inspected.exception)
    result_id = json.loads(inspected.stdout)["data"]["task"]["result"]["id"]
    command = _continuation_command(issue_dir.name, task, result_id)
    assert _continuation_status(command) == ("unknown" if audit_committed else "pending")
    assert _continuation_status([*command, "--resume"]) == "advanced"
    assert [result.id for result in HumanTaskRecordStore(issue_dir).results()] == [result_id]


def test_task_command_accepts_and_inspects_a_work_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir, task = _pending_issue(tmp_path / ".cafe", "reported")

    completed = runner.invoke(
        app,
        [
            "task",
            "complete",
            task.id,
            "--result",
            json.dumps(
                {
                    "decision": "confirm",
                    "work_report": {
                        "summary": "Updated the requested files.",
                        "outcome": "Targeted checks pass.",
                    },
                }
            ),
            "--no-resume",
            "--json",
        ],
    )
    inspected = runner.invoke(app, ["task", "inspect", task.id])

    assert completed.exit_code == 0, (completed.stdout, completed.exception)
    assert inspected.exit_code == 0
    assert "Updated the requested files." in inspected.stdout
    stored = HumanTaskRecordStore(issue_dir).get_result(task.id)
    assert stored is not None
    assert stored.payload["work_report"]["outcome"] == "Targeted checks pass."


def test_task_command_allows_supervisor_to_handoff_to_any_playbook_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir, task = _pending_issue(tmp_path / ".cafe", "supervisor-handoff")

    completed = runner.invoke(
        app,
        [
            "task",
            "complete",
            task.id,
            "--result",
            json.dumps(
                {
                    "decision": "confirm",
                    "work_report": {
                        "summary": "Completed work outside the normal phase order.",
                        "outcome": "The change is ready for review.",
                    },
                }
            ),
            "--handoff-to",
            "review",
            "--no-resume",
            "--json",
        ],
    )

    assert completed.exit_code == 0, (completed.stdout, completed.exception)
    payload = json.loads(completed.stdout)
    assert payload["data"]["workflow"]["continuation"] == "review"
    assert BlackboardStore(issue_dir).load_or_create("spec").current_step == "review"
    stored = HumanTaskRecordStore(issue_dir).get_result(task.id)
    assert stored is not None
    assert stored.payload["continuation"] == "review"
    assert stored.payload["declared_continuation"] == "plan"
    assert stored.payload["supervisor_handoff_to"] == "review"
    receipt = (issue_dir / "review" / "iteration_001" / "user_input.md").read_text(encoding="utf-8")
    handoff = json.loads(receipt.splitlines()[1])
    assert handoff["type"] == "supervisor_handoff"
    assert handoff["from_step"] == "spec"
    assert handoff["declared_continuation"] == "plan"
    assert handoff["continuation"] == "review"
    assert handoff["response"] == {"decision": "confirm"}
    assert "Completed work outside the normal phase order." in receipt


@pytest.mark.parametrize("target", [None, "develop", "deliver"])
def test_supervisor_redirect_does_not_retry_a_missing_agent_session(tmp_path, monkeypatch, target):
    """A pre-invocation interruption can leave its step without creating a session."""
    from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
    from cafe.workflow_execution.automatic import automatic_executor_registry

    monkeypatch.chdir(tmp_path)
    issue = tmp_path / ".cafe/issues/pre-invocation-error"
    directory = issue / "develop/iteration_001"
    directory.mkdir(parents=True)
    (issue / "issue.yaml").write_text("playbook_id: direct-qa\n")
    context = b'{"agent_invoked":false,"workflow_completion_trusted":false}'
    (directory / "iteration.json").write_bytes(context)

    def fail_before_agent(*args, **kwargs):
        raise RuntimeError("Input preparation failed before provider invocation")

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue,
        playbook=PlaybookLoader().load("direct-qa"),
        executor=fail_before_agent,
        automatic_registry=automatic_executor_registry(),
    )
    monkeypatch.setattr(runtime, "_notify_new_human_task", lambda task: None)
    assert not runtime.run(start_step="develop").completed
    records = HumanTaskRecordStore(issue)
    task = next(task for task in records.tasks() if task.status is HumanTaskStatus.PENDING)
    assert task.policy_id == "agent-execution-interrupted"
    context = (directory / "iteration.json").read_bytes()
    argv = [
        "task",
        "complete",
        task.id,
        "--result",
        '{"decision":"retry"}',
        "--no-resume",
        "--json",
    ]
    if target:
        argv.extend(["--handoff-to", target])
    result = runner.invoke(app, argv)
    if target == "deliver":
        assert result.exit_code == 0, result.stdout
        saved = records.get_result(task.id)
        assert saved.payload["continuation"] == "deliver"
        assert saved.payload["declared_continuation"] == "develop"
        assert "session_continuation" not in saved.payload
        assert "restart_recovery" not in saved.payload
    else:
        assert result.exit_code == 1
        assert json.loads(result.stdout)["error"]["code"] == "invalid_response"
        assert records.get_task(task.id).status is HumanTaskStatus.PENDING
    assert (directory / "iteration.json").read_bytes() == context


def test_task_command_rejects_supervisor_handoff_outside_playbook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir, task = _pending_issue(tmp_path / ".cafe", "invalid-supervisor-handoff")

    completed = runner.invoke(
        app,
        [
            "task",
            "complete",
            task.id,
            "--result",
            '{"decision":"confirm"}',
            "--handoff-to",
            "not-a-phase",
            "--no-resume",
            "--json",
        ],
    )

    assert completed.exit_code == 1
    assert HumanTaskRecordStore(issue_dir).get_task(task.id).status is HumanTaskStatus.PENDING
    assert BlackboardStore(issue_dir).load_or_create("spec").current_step == "user"


def test_supervisor_handoff_projects_feedback_to_the_selected_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir, task = _pending_issue(tmp_path / ".cafe", "feedback-handoff", step="pr")

    completed = runner.invoke(
        app,
        [
            "task",
            "complete",
            task.id,
            "--result",
            '{"decision":"fix_now","feedback":"Correct the specification requirement."}',
            "--handoff-to",
            "spec",
            "--no-resume",
        ],
    )

    assert completed.exit_code == 0, (completed.stdout, completed.exception)
    receipt = (issue_dir / "spec" / "iteration_001" / "user_input.md").read_text(encoding="utf-8")
    assert "Correct the specification requirement." in receipt
    assert '"type": "supervisor_handoff"' in receipt


def test_supervisor_handoff_projects_answers_to_the_selected_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir, task = _pending_issue(
        tmp_path / ".cafe",
        "answers-handoff",
        playbook_id="editorial",
        step="brief",
        trigger="need_clarification",
    )

    completed = runner.invoke(
        app,
        [
            "task",
            "complete",
            task.id,
            "--result",
            '{"answers":{"audience":"Editors"}}',
            "--handoff-to",
            "draft",
            "--no-resume",
        ],
    )

    assert completed.exit_code == 0, (completed.stdout, completed.exception)
    receipt = (issue_dir / "draft" / "iteration_001" / "user_input.md").read_text(encoding="utf-8")
    assert '"answers": {"audience": ["Editors"]}' in receipt
    assert '"type": "supervisor_handoff"' in receipt


def test_deferred_self_loop_completion_persists_the_decision_for_the_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--no-resume leaves a pure decision where the detached worker will read it."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "deferred-self-loop"
    iteration_dir = issue_dir / "spec" / "iteration_001"
    iteration_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook: standard\n", encoding="utf-8")
    playbook = PlaybookLoader().load("standard")
    playbook["steps"]["spec"]["human_tasks"][0]["outcomes"] = {"confirm": "spec"}
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec", playbook_id="standard")
    store.set_current_step(state, "user")
    store.update_handoff_contract(
        state,
        from_step="spec",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.CONFIRM_OUTPUT,
        source="test",
    )
    policy, binding = resolve_step_human_task(
        playbook_data=playbook,
        step_name="spec",
        trigger="confirm_output",
    )
    task = HumanTaskRecordStore(issue_dir).materialize(
        workflow_id=state.workflow_id,
        step="spec",
        iteration=1,
        trigger="confirm_output",
        policy_id=policy.id,
        prompt=policy.prompt,
        expected_result=policy.model_dump(mode="json"),
        continuations=binding.outcomes,
        assignee_type="user",
    )
    monkeypatch.setattr(
        "cafe.playbooks.loader.PlaybookLoader.load",
        lambda _self, _playbook_id: playbook,
    )

    result = runner.invoke(
        app,
        [
            "task",
            "complete",
            task.id,
            "--result",
            '{"decision":"confirm"}',
            "--no-resume",
        ],
    )

    continuation_input = (iteration_dir / "user_input.md").read_text(encoding="utf-8")
    receipt = json.loads(continuation_input.splitlines()[1])
    assert result.exit_code == 0, (result.stdout, result.exception)
    assert receipt == {
        "schema_version": 1,
        "type": "human_task_completion",
        "human_task_id": task.id,
        "task": "output-review",
        "decision": "confirm",
        "continuation": "spec",
    }
    assert "workflow execute" not in continuation_input
    assert HumanTaskRecordStore(issue_dir).get_task(task.id).status is HumanTaskStatus.COMPLETED
    assert store.load_or_create("spec").current_step == "spec"


def test_deferred_terminal_completion_wakes_driver_from_callback_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    from tests.integration.test_human_task_workflow import legacy_development_graph

    graph = legacy_development_graph()
    graph["playbook"]["id"] = "legacy-inbox"
    for definition in graph["steps"].values():
        definition.get("initial_input", {}).pop("legacy_presentation", None)
    catalog = tmp_path / ".cafe" / "playbooks"
    catalog.mkdir(parents=True)
    (catalog / "legacy-inbox.yaml").write_text(json.dumps(dict(graph)))
    issue_dir, task = _pending_issue(
        tmp_path / ".cafe",
        "deferred-terminal",
        playbook_id="legacy-inbox",
        step="pr",
    )

    completed = runner.invoke(
        app,
        [
            "task",
            "complete",
            task.id,
            "--result",
            '{"decision":"continue_without_issue"}',
            "--no-resume",
        ],
    )

    assert completed.exit_code == 0, (completed.stdout, completed.exception)
    state = BlackboardStore(issue_dir).load_or_create("pr", playbook_id="standard")
    assert state.current_step == "done"
    assert state.handoff_contract is not None
    assert state.handoff_contract.to_owner is HandoffOwner.DONE
    assert state.handoff_contract.intent is HandoffIntent.WORKFLOW_COMPLETE
    (result,) = HumanTaskRecordStore(issue_dir).results()
    continuation = _continuation_command(issue_dir.name, task, result.id)
    assert _continuation_status([*continuation, "--resume"]) == "advanced"

    launch_store = WorkerLaunchStore(issue_dir)
    launch = launch_store.start()
    launch_store.mark(launch["worker_id"], "started")
    callback_id = "builtin:use-cafe-workflow:workflow_event_callback"
    callback = ResolvedWorkflowEventCallback(callback_id, tmp_path / "callback.py")
    dispatched: list[dict[str, object]] = []

    class FakeGitOperations:
        def get_current_branch(self) -> str:
            return "deferred-terminal"

    def capture_callback(_binding, event, *, cwd: Path) -> None:
        assert cwd == tmp_path
        dispatched.append(dict(event))

    monkeypatch.setattr("cafe.ui.cli.GitOperations", FakeGitOperations)
    monkeypatch.setattr(
        "cafe.ui.commands.workflow.resolve_builtin_workflow_event_callback",
        lambda *_args, **_kwargs: callback,
    )
    monkeypatch.setattr(
        "cafe.ui.commands.workflow.dispatch_workflow_event_callback",
        capture_callback,
    )
    monkeypatch.setattr(
        "cafe.ui.cli._find_incomplete_workflow_step",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "cafe.ui.cli._find_external_resume_step",
        lambda **_kwargs: None,
    )

    def resume_worker(worker: dict[str, object]):
        return runner.invoke(
            app,
            [
                "workflow",
                "--issue",
                "deferred-terminal",
                "--playbook",
                "standard",
                "--execute",
                "--internal-worker-id",
                worker["worker_id"],
                "--internal-worker-token",
                worker["worker_token"],
                "--on-workflow-event",
                callback_id,
            ],
        )

    resumed = resume_worker(launch)

    assert resumed.exit_code == 0, (resumed.stdout, resumed.exception)
    assert [event["event_type"] for event in dispatched] == ["workflow_completed"]
    assert dispatched[0]["step"] == "pr"
    assert dispatched[0]["status_code"] == "BATON_WORKFLOW_COMPLETE"
    assert isinstance(dispatched[0]["event_id"], str)
    assert isinstance(dispatched[0]["sequence"], int)
    assert "Executing step=" not in resumed.stdout

    replay_launch = launch_store.start()
    launch_store.mark(replay_launch["worker_id"], "started")
    replayed = resume_worker(replay_launch)

    assert replayed.exit_code == 0, (replayed.stdout, replayed.exception)
    assert [event["event_type"] for event in dispatched] == [
        "workflow_completed",
        "workflow_completed",
    ]
    assert dispatched[1] == dispatched[0]
    assert "Executing step=" not in replayed.stdout


def test_interactive_completion_uses_same_durable_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test List I4: prompt collection feeds the same validator and transition."""
    monkeypatch.chdir(tmp_path)
    issue_dir, task = _pending_issue(tmp_path / ".cafe", "interactive")
    monkeypatch.setattr(
        "cafe.ui.commands.tasks.collect_human_task_payload",
        lambda _policy, **_kwargs: {"task": "output-review", "decision": "confirm"},
    )
    monkeypatch.setattr("cafe.ui.commands.tasks._resume_issue_workflow", lambda *_args: None)

    result = runner.invoke(app, ["task", "complete", task.id])

    assert result.exit_code == 0
    assert HumanTaskRecordStore(issue_dir).get_task(task.id).status is HumanTaskStatus.COMPLETED
    assert BlackboardStore(issue_dir).load_or_create("spec").current_step == "plan"


@pytest.mark.parametrize("case", ["invalid", "stale"])
def test_unsafe_completion_stops_without_workflow_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """Test List I5: invalid and terminal attempts do not resume a workflow."""
    monkeypatch.chdir(tmp_path)
    issue_dir, task = _pending_issue(tmp_path / ".cafe", case)
    if case == "stale":
        HumanTaskRecordStore(issue_dir).cancel(
            workflow_id=task.workflow_id, task_id=task.id, reason="test"
        )
    resumed: list[str] = []
    monkeypatch.setattr(
        "cafe.ui.commands.tasks._resume_issue_workflow",
        lambda issue, _playbook: resumed.append(issue),
    )
    payload = '{"decision":"unknown"}' if case == "invalid" else '{"decision":"confirm"}'

    result = runner.invoke(app, ["task", "complete", task.id, "--result", payload, "--json"])

    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] in {
        "invalid_response",
        "task_not_pending",
    }
    assert resumed == []
    assert BlackboardStore(issue_dir).load_or_create("spec").current_step == "user"
    assert HumanTaskRecordStore(issue_dir).results() == ()


def test_completion_json_stdout_is_one_result_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test List I6: continuation progress cannot contaminate machine output."""
    monkeypatch.chdir(tmp_path)
    _issue_dir, task = _pending_issue(tmp_path / ".cafe", "json")

    def noisy_resume(_issue: str, _playbook: str) -> None:
        print("workflow progress")

    monkeypatch.setattr("cafe.ui.commands.tasks._resume_issue_workflow", noisy_resume)

    result = runner.invoke(
        app,
        [
            "task",
            "complete",
            task.id,
            "--result",
            '{"decision":"confirm"}',
            "--json",
        ],
    )

    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["operation"] == "complete"
    assert envelope["data"]["task"]["id"] == task.id
    assert envelope["data"]["workflow"]["issue"] == "json"


def test_resume_failure_reports_committed_completion_and_direct_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test List I3/I6: post-commit resume failure is not presented as retryable."""
    monkeypatch.chdir(tmp_path)
    issue_dir, task = _pending_issue(tmp_path / ".cafe", "resume-failure")

    def unavailable_resume(_issue: str, _playbook: str) -> None:
        raise RuntimeError("runner unavailable")

    monkeypatch.setattr("cafe.ui.commands.tasks._resume_issue_workflow", unavailable_resume)

    result = runner.invoke(
        app,
        [
            "task",
            "complete",
            task.id,
            "--result",
            '{"decision":"confirm"}',
            "--json",
        ],
    )

    assert result.exit_code != 0
    envelope = json.loads(result.stdout)
    assert envelope["error"]["code"] == "workflow_resume_failed"
    assert envelope["error"]["task_id"] == task.id
    assert envelope["error"]["issue"] == "resume-failure"
    assert envelope["error"]["workflow_id"] == task.workflow_id
    assert "cafe workflow" in envelope["error"]["recovery"]
    records = HumanTaskRecordStore(issue_dir)
    assert records.get_task(task.id).status is HumanTaskStatus.COMPLETED
    assert len(records.results()) == 1
    assert BlackboardStore(issue_dir).load_or_create("spec").current_step == "plan"


def test_capability_task_inspection_exposes_exact_approval_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test List I6: inbox inspection exposes the stable capability boundary."""
    from cafe.core.capabilities import CapabilityManifest, ExecutionRequest
    from cafe.core.capability_approvals import CapabilityApprovalService

    monkeypatch.chdir(tmp_path)
    issue_dir, _ordinary = _pending_issue(tmp_path / ".cafe", "capability-inspect")
    state = BlackboardStore(issue_dir).load_or_create("spec")
    manifest = CapabilityManifest.model_validate(
        {
            "id": "demo.inspect",
            "version": 1,
            "implementation": "open_current_pr",
            "arguments": {
                "required": ["target"],
                "properties": {"target": {"type": "string"}},
            },
            "outputs": {
                "required": ["receipt_path"],
                "properties": {"receipt_path": {"type": "string"}},
            },
            "effects": {
                "writes": ["approval.txt"],
                "network_destinations": ["api.example.test"],
                "browser_open": [],
            },
            "credentials": ["example-token"],
            "permissions": {"network": ["api.example.test"]},
            "idempotency": "unsafe",
            "risk": "high",
            "approval": "required",
            "policy": "allow",
        }
    )
    request = ExecutionRequest.model_validate(
        {
            "capability": manifest.id,
            "args": {"target": "reviewed"},
            "effects": {
                "writes": ["approval.txt"],
                "network_destinations": ["api.example.test"],
                "browser_open": [],
            },
            "credentials": ["example-token"],
            "permissions": {"network": ["api.example.test"]},
        }
    )
    task = CapabilityApprovalService(
        issue_dir=issue_dir,
        workflow_id=state.workflow_id,
        step="spec",
        iteration=1,
    ).request_approval(request=request, manifest=manifest)

    result = runner.invoke(app, ["task", "inspect", task.id, "--json"])
    data = json.loads(result.stdout)["data"]
    approval = data["task"]["capability_approval"]

    assert approval["state"] == "pending"
    assert approval["capability"] == manifest.id
    assert approval["request"]["capability"] == manifest.id
    assert data["snapshot"]["status"] == "not_applicable"
    guarded = _submit_snapshot(
        task, "0" * 64, decision="deny", task_id=task.id, workflow_id=state.workflow_id,
        request_fingerprint=approval["fingerprint"], correlation_id=approval["correlation_id"],
    )
    assert json.loads(guarded.stdout)["error"]["code"] == "snapshot_unavailable"
    assert _inspect_snapshot(task.id)["status"] == "not_applicable"

    readable = runner.invoke(app, ["task", "inspect", task.id])

    assert readable.exit_code == 0
    for reviewed_value in (
        "demo.inspect",
        "high",
        "reviewed",
        "approval.txt",
        "api.example.test",
        "example-token",
        "network",
        "receipt_path",
    ):
        assert reviewed_value in readable.stdout

    monkeypatch.setattr("cafe.ui.commands.tasks._resume_issue_workflow", lambda *_args: None)
    interactive = runner.invoke(app, ["task", "complete", task.id], input="deny\n")

    assert interactive.exit_code == 0
    assert "demo.inspect" in interactive.stdout
    assert "approval.txt" in interactive.stdout
    assert interactive.stdout.index("demo.inspect") < interactive.stdout.index(
        "Capability decision"
    )


def test_capability_task_cancel_command_persists_terminal_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test List I2: the production inbox cancel path releases the exact task."""
    from cafe.core.capabilities import CapabilityManifest, ExecutionRequest
    from cafe.core.capability_approvals import CapabilityApprovalService

    monkeypatch.chdir(tmp_path)
    issue_dir, _ordinary = _pending_issue(tmp_path / ".cafe", "capability-cancel")
    state = BlackboardStore(issue_dir).load_or_create("spec")
    manifest = CapabilityManifest.model_validate(
        {
            "id": "demo.cancel",
            "version": 1,
            "implementation": "open_current_pr",
            "arguments": {"required": [], "properties": {}},
            "outputs": {"required": [], "properties": {}},
            "effects": {"writes": [], "network_destinations": [], "browser_open": []},
            "credentials": [],
            "permissions": {},
            "idempotency": "unsafe",
            "risk": "high",
            "approval": "required",
            "policy": "allow",
        }
    )
    request = ExecutionRequest.model_validate(
        {
            "capability": manifest.id,
            "args": {},
            "effects": {"writes": [], "network_destinations": [], "browser_open": []},
            "credentials": [],
            "permissions": {},
        }
    )
    service = CapabilityApprovalService(
        issue_dir=issue_dir,
        workflow_id=state.workflow_id,
        step="spec",
        iteration=1,
    )
    task = service.request_approval(request=request, manifest=manifest)
    monkeypatch.setattr("cafe.ui.commands.tasks._resume_issue_workflow", lambda *_args: None)

    result = runner.invoke(
        app,
        [
            "task",
            "cancel",
            task.id,
            "--reason",
            "operator stopped request",
            "--no-resume",
            "--json",
        ],
    )
    resumed = service.resume(
        task.id,
        correlation_id=service.inspect(task.id)["correlation_id"],
        request=request,
        registry={manifest.id: manifest},
        repo_root=tmp_path,
        output_file=tmp_path / "output.md",
    )

    assert result.exit_code == 0
    assert resumed["outcome"] == "cancelled"
    assert resumed["executed"] is False
    assert resumed["correlation_id"] == service.inspect(task.id)["correlation_id"]


def test_stale_ordinary_task_can_be_cancelled_without_resuming_the_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery names one obsolete task and cannot disturb the active baton."""
    monkeypatch.chdir(tmp_path)
    issue_dir, task = _pending_issue(tmp_path / ".cafe", "stale-ordinary-cancel")
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec", playbook_id="standard")
    store.update_handoff_contract(
        state,
        from_step="review",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.NEED_CLARIFICATION,
        source="test",
    )
    resumed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "cafe.ui.commands.tasks._resume_issue_workflow",
        lambda issue, playbook: resumed.append((issue, playbook)),
    )

    result = runner.invoke(
        app,
        ["task", "cancel", task.id, "--reason", "superseded by a confirmed restart", "--json"],
    )

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["data"]["workflow"]["continuation"] is None
    assert HumanTaskRecordStore(issue_dir).get_task(task.id).status is HumanTaskStatus.CANCELLED
    assert BlackboardStore(issue_dir).load_or_create("spec").current_step == "user"
    assert resumed == []


def test_current_ordinary_task_cannot_be_cancelled_as_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recovery path is fail-closed for the active ordinary task."""
    monkeypatch.chdir(tmp_path)
    issue_dir, task = _pending_issue(tmp_path / ".cafe", "active-ordinary-cancel")

    result = runner.invoke(
        app,
        ["task", "cancel", task.id, "--reason", "incorrectly trying to dismiss it", "--json"],
    )

    payload = json.loads(result.stdout)
    assert result.exit_code == 1
    assert payload["error"]["code"] == "active_task"
    assert HumanTaskRecordStore(issue_dir).get_task(task.id).status is HumanTaskStatus.PENDING
