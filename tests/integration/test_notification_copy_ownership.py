"""Notification preparation follows selected declarations rather than phase IDs."""

import json
import shutil
from pathlib import Path

import pytest
import yaml

from cafe.core.blackboard import BlackboardStore
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.playbooks.loader import PlaybookLoader
from tests.unit.test_event_driver_callback import _callback_module
from tests.unit.test_human_task_notifications import _set_home, _SlackResponse, _write_credential
from tests.unit.test_skill_locale_ownership import write_owner

BUILTINS = Path(__file__).resolve().parents[2] / "src/cafe/data"


def add_notification(owner, *, step=None, task=None):
    path = owner / "SKILL.md"
    header, body = path.read_text().split("---", 2)[1:]
    metadata = yaml.safe_load(header)
    copy = {}
    values = {}
    if step is not None:
        copy["step_label"] = {"message_key": "owned.step"}
        values["owned.step"] = step
    if task is not None:
        task_id, label = task
        copy["task_labels"] = {task_id: {"message_key": "owned.action"}}
        values["owned.action"] = label
    metadata["workflow"]["notification"] = copy
    path.write_text("---\n" + yaml.safe_dump(metadata) + "---" + body)
    for locale in ("en-US", "zh-TW"):
        resource = owner / "locales" / f"{locale}.yaml"
        catalog = yaml.safe_load(resource.read_text())
        catalog.update({key: f"{value} {locale}" for key, value in values.items()})
        resource.write_text(yaml.safe_dump(catalog, allow_unicode=True))


@pytest.fixture(params=["project", "global"])
def notification_owner(tmp_path, monkeypatch, request):
    monkeypatch.chdir(tmp_path)
    global_root = tmp_path / "global"
    monkeypatch.setattr("cafe.utils.config.get_global_cafe_dir", lambda: global_root)
    selected = tmp_path / ".cafe/skills" if request.param == "project" else global_root / "skills"
    primary = selected / "cafe-spec"
    shutil.copytree(BUILTINS / "skills/cafe-spec", primary)
    add_notification(primary, step="Selected primary", task=("output-review", "Selected action"))
    shared = write_owner(selected, "shared-copy", "custom-review", "Saved prompt")
    add_notification(shared, task=("custom-review", "Shared action"))
    raw = yaml.safe_load((BUILTINS / "playbooks/standard.yaml").read_text())
    raw["playbook"]["id"] = "owned"
    step = raw["steps"].pop("spec")
    step.pop("initial_input", None)
    step["human_tasks"][0]["task_id"] = "custom-review"
    raw["steps"]["renamed"] = step

    # The other routes still point at this same practical requirements step.
    def rename(value):
        if isinstance(value, dict):
            return {
                key: child
                if key in {"input_artifacts", "output_artifacts", "initial_input"}
                else rename(child)
                for key, child in value.items()
            }
        if isinstance(value, list):
            return [rename(child) for child in value]
        return "renamed" if value == "spec" else value

    raw = rename(raw)
    raw["skills"]["workflow"]["shared"].append("shared-copy")
    path = tmp_path / ".cafe/playbooks/owned.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(raw))
    issue = tmp_path / ".cafe/issues/owned"
    issue.mkdir(parents=True)
    (issue / "issue.yaml").write_text("playbook: owned\npr:\n  auto_create: false\n")
    state = BlackboardStore(issue).load_or_create("renamed", playbook_id="owned")
    state.conversation_locale = "zh-Hant"
    BlackboardStore(issue).save(state)
    home = tmp_path / "home"
    home.mkdir()
    _set_home(monkeypatch, home)
    _write_credential(home)
    posts = []

    def transport(request, *, timeout):
        posts.append(json.loads(request.data)["text"])
        return _SlackResponse()

    monkeypatch.setattr("cafe.core.human_task_notifications._open_slack_request", transport)
    monkeypatch.delenv("CAFE_TEST_RUN_SLACK_NOTIFICATIONS", raising=False)
    return tmp_path, primary, shared, issue, posts


def test_actual_materialization_and_callback_use_selected_primary_and_shared_owner(
    notification_owner,
):
    project, primary, shared, issue, posts = notification_owner
    playbook = PlaybookLoader(project_root=project).load("owned")
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue,
        playbook=playbook,
        executor=lambda *_args: StepExecutionResult(
            response="ready_for_review",
            artifacts={},
            status_code="ready_for_review",
            auto_continue=False,
        ),
    )
    runtime.run(start_step="renamed")
    task = HumanTaskRecordStore(issue).tasks()[0]
    saved = task.expected_result
    assert task.policy_id == "custom-review" and task.step == "renamed"
    assert "Selected primary zh-TW" in posts[0] and "Shared action zh-TW" in posts[0]
    callback = _callback_module()
    callback._notify_callback_failure(
        {
            "issue": issue.name,
            "step": "renamed",
            "event_type": "phase_terminal",
            "workflow_id": task.workflow_id,
        },
        repository_root=project,
        error=ValueError("private"),
    )
    assert "Selected primary zh-TW" in posts[1]
    # Retry is durable and deduplicated, even when the authoring files disappear.
    shutil.rmtree(primary / "locales")
    shutil.rmtree(shared / "locales")
    runtime._notify_new_human_task(task)
    assert len(posts) == 2
    callback._notify_callback_failure(
        {"issue": issue.name, "step": "renamed", "event_type": "other_failure"},
        repository_root=project,
        error=ValueError("private"),
    )
    assert len(posts) == 3 and "renamed" in posts[2]
    assert "Selected primary" not in posts[2]
    assert HumanTaskRecordStore(issue).get_task(task.id).expected_result == saved
    assert BlackboardStore(issue).load_or_create("renamed").conversation_locale == "zh-Hant"


@pytest.mark.parametrize("mutation", ["missing", "malformed", "unknown-key", "placeholder"])
def test_notification_declaration_rejects_invalid_selected_owner(notification_owner, mutation):
    project, primary, _shared, _issue, posts = notification_owner
    resource = primary / "locales/zh-TW.yaml"
    if mutation == "missing":
        resource.unlink()
    elif mutation == "malformed":
        resource.write_text("owned.step: [")
    else:
        document = yaml.safe_load(resource.read_text())
        if mutation == "unknown-key":
            document.pop("owned.step")
        else:
            document["owned.step"] = "Argument {unavailable}"
        resource.write_text(yaml.safe_dump(document))
    with pytest.raises(ValueError) as rejected:
        PlaybookLoader(project_root=project).load("owned")
    assert "cafe-spec" in str(rejected.value)
    assert posts == []


def test_iteration_selector_and_primary_producer_reach_both_notifications(notification_owner):
    project, primary, _shared, issue, posts = notification_owner
    alternate = primary.parent / "alternate-owner"
    shutil.copytree(primary, alternate)
    path = alternate / "SKILL.md"
    path.write_text(path.read_text().replace("name: cafe-spec", "name: alternate-owner"))
    add_notification(alternate, step="Second owner", task=("output-review", "Primary action"))
    playbook_path = project / ".cafe/playbooks/owned.yaml"
    raw = yaml.safe_load(playbook_path.read_text())
    raw["steps"]["renamed"]["skill"] = {"1": "cafe-spec", "2": "alternate-owner"}
    raw["steps"]["renamed"]["human_tasks"][0]["task_id"] = "output-review"
    playbook_path.write_text(yaml.safe_dump(raw))
    (issue / "renamed/iteration_002").mkdir(parents=True)
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue,
        playbook=PlaybookLoader(project_root=project).load("owned"),
        executor=lambda *_args: StepExecutionResult(
            response="ready_for_review",
            artifacts={},
            status_code="ready_for_review",
            auto_continue=False,
        ),
    )
    runtime.run(start_step="renamed")
    task = HumanTaskRecordStore(issue).tasks()[0]
    assert task.iteration == 2
    assert "Second owner zh-TW" in posts[0] and "Primary action zh-TW" in posts[0]
    _callback_module()._notify_callback_failure(
        {"issue": issue.name, "step": "renamed", "event_type": "phase_terminal"},
        repository_root=project,
        error=ValueError("private"),
    )
    assert "Second owner zh-TW" in posts[1]


@pytest.mark.parametrize(
    "case", ["extra-ref-field", "non-string-key", "undeclared-task", "shared-step"]
)
def test_notification_metadata_is_strict_and_owned_by_actual_declaration(notification_owner, case):
    project, primary, shared, _issue, _posts = notification_owner
    path = (shared if case == "shared-step" else primary) / "SKILL.md"
    header, body = path.read_text().split("---", 2)[1:]
    metadata = yaml.safe_load(header)
    notification = metadata["workflow"]["notification"]
    if case == "extra-ref-field":
        notification["step_label"]["extra"] = "ignored"
    elif case == "non-string-key":
        notification["step_label"]["message_key"] = 42
    elif case == "undeclared-task":
        notification["task_labels"]["unowned"] = {"message_key": "owned.step"}
    else:
        notification["step_label"] = {"message_key": "owned.action"}
    path.write_text("---\n" + yaml.safe_dump(metadata) + "---" + body)
    with pytest.raises(ValueError):
        PlaybookLoader(project_root=project).load("owned")


@pytest.mark.parametrize(
    "value",
    ["x" * 129, "private\n<!channel>", "<https://private.invalid>", "@here", "text\x00tail"],
)
def test_actual_preparation_bounds_project_authored_labels(notification_owner, value):
    project, primary, shared, issue, posts = notification_owner
    add_notification(primary, step=value)
    add_notification(shared, task=("custom-review", value))
    playbook = PlaybookLoader(project_root=project).load("owned")
    BlackboardWorkflowRuntime(
        issue_dir=issue,
        playbook=playbook,
        executor=lambda *_args: StepExecutionResult(
            response="ready_for_review",
            artifacts={},
            status_code="ready_for_review",
            auto_continue=False,
        ),
    ).run(start_step="renamed")
    assert len(posts) == 1 and posts[0].count("\n") == 5
    assert (
        value not in posts[0] and "<!channel>" not in posts[0] and "private.invalid" not in posts[0]
    )


@pytest.mark.parametrize(
    "label,accepted",
    [("x" * 122, True), ("x" * 123, False), ("Literal {{copy}}", True)],
)
def test_real_label_limit_and_literal_braces(notification_owner, label, accepted):
    project, primary, shared, issue, posts = notification_owner
    add_notification(primary, step=label)
    add_notification(shared, task=("custom-review", label))
    BlackboardWorkflowRuntime(
        issue_dir=issue,
        playbook=PlaybookLoader(project_root=project).load("owned"),
        executor=lambda *_args: StepExecutionResult(
            response="ready_for_review",
            artifacts={},
            status_code="ready_for_review",
            auto_continue=False,
        ),
    ).run(start_step="renamed")
    rendered = label.replace("{{", "{").replace("}}", "}") + " zh-TW"
    assert (rendered in posts[0]) is accepted
    assert posts[0].count("\n") == 5


@pytest.mark.parametrize(
    "locale,selected",
    [
        ("zh-TW", "zh-TW"),
        ("zh-HK", "zh-TW"),
        ("zh-Hant", "zh-TW"),
        ("zh-CN", "en-US"),
        ("ja-JP", "en-US"),
        ("zh//TW", "en-US"),
        (None, "en-US"),
    ],
)
def test_selected_declaration_uses_notification_locale_authority(
    notification_owner, locale, selected
):
    project, _primary, _shared, issue, posts = notification_owner
    store = BlackboardStore(issue)
    state = store.load_or_create("renamed")
    state.conversation_locale = locale
    store.save(state)
    BlackboardWorkflowRuntime(
        issue_dir=issue,
        playbook=PlaybookLoader(project_root=project).load("owned"),
        executor=lambda *_args: StepExecutionResult(
            response="ready_for_review",
            artifacts={},
            status_code="ready_for_review",
            auto_continue=False,
        ),
    ).run(start_step="renamed")
    assert f"Selected primary {selected}" in posts[0] and f"Shared action {selected}" in posts[0]
    assert store.load_or_create("renamed").conversation_locale == locale
