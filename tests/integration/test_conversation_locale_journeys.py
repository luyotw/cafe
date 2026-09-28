"""User journeys for the workflow conversation language.

Delivery is mocked only at the Slack transport seam; preparation, the workflow
command, task materialization, rendering and answer validation all run through
production code.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from cafe.core.blackboard import BlackboardStore
from cafe.core.conversation_locale import LocaleSource, SuppliedLocale
from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.core.human_tasks import HumanTaskPolicy, validate_human_task_completion
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.playbooks.loader import PlaybookLoader
from cafe.ui.cli import app

pytestmark = pytest.mark.usefixtures("cached_builtin_playbook_models")

runner = CliRunner()
VALID_WEBHOOK = "https://hooks.slack.com/services/T00000000/B00000000/journey-secret"


class _SlackResponse:
    status = 200

    def __enter__(self) -> "_SlackResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _limit: int = -1) -> bytes:
        return b"ok"


@pytest.fixture(autouse=True)
def _exercise_normal_notification_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAFE_TEST_RUN_SLACK_NOTIFICATIONS", raising=False)


@pytest.fixture
def slack_posts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list:
    """Install a private credential and capture every outbound Slack request."""
    import cafe.core.human_task_notifications as notification_mod

    home = tmp_path / "slack-home"
    home.mkdir()
    for name in (".slack-webhook", ".slack-webhook-test"):
        credential = home / name
        credential.write_text(VALID_WEBHOOK, encoding="utf-8")
        credential.chmod(0o600)
    monkeypatch.setattr(notification_mod, "_trusted_user_home", lambda: home)
    posts: list = []
    monkeypatch.setattr(
        notification_mod,
        "_open_slack_request",
        lambda request, *, timeout: posts.append(request) or _SlackResponse(),
    )
    return posts


def _delivered_text(posts: list, index: int = -1) -> str:
    return json.loads(posts[index].data)["text"]


def _issue_dir(repo_root: Path, name: str) -> Path:
    return repo_root / ".cafe" / "issues" / name


def _pause_for_output_review(issue_dir: Path, *, playbook_overrides=None):
    """Run the real runtime until it materializes a pending output-review task."""
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "issue.yaml").write_text("pr:\n  auto_create: false\n", encoding="utf-8")
    playbook = PlaybookLoader().load("standard")
    if playbook_overrides:
        playbook["playbook"].update(playbook_overrides)
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=lambda *_args: StepExecutionResult(
            response="ready_for_review",
            artifacts={},
            status_code="ready_for_review",
            auto_continue=False,
        ),
    )
    return runtime.run(start_step="spec")


def _store_locale(issue_dir: Path, value: str | None, source: str | None = "explicit") -> None:
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")
    state.conversation_locale = value
    state.conversation_locale_source = source if value else None
    store.save(state)


def _callback_module():
    path = (
        Path(__file__).parents[2]
        / "src/cafe/data/skills/use-cafe-workflow/scripts/workflow_event_callback.py"
    )
    spec = importlib.util.spec_from_file_location("workflow_event_callback_journey", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_english_repository_with_a_traditional_chinese_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slack_posts: list
) -> None:
    """Integration 1: engineering prose and human interaction use different languages."""
    repo_root = tmp_path / "repo"
    (repo_root / ".cafe").mkdir(parents=True)
    (repo_root / ".cafe" / "strategic_context.yaml").write_text(
        "version: 1\nrepository_language:\n  content_locale: en-US\n", encoding="utf-8"
    )
    monkeypatch.chdir(repo_root)
    issue_dir = _issue_dir(repo_root, "bilingual")
    _pause_for_output_review(issue_dir)
    _store_locale(issue_dir, "zh-TW")
    slack_posts.clear()

    _pause_for_output_review(issue_dir)

    task = HumanTaskRecordStore(issue_dir).tasks()[-1]
    assert task.status is HumanTaskStatus.PENDING
    assert task.prompt == "檢視需求規格，並選擇如何繼續。"
    presented = HumanTaskPolicy.model_validate(task.expected_result)
    assert [item.label for item in presented.decisions] == ["確認並繼續", "要求修訂"]
    assert presented.prompt_locales == {}
    assert [item.id for item in presented.decisions] == ["confirm", "revise"]
    assert "確認結果" in _delivered_text(slack_posts)

    from cafe.phases.generic_phase import GenericPhase
    from cafe.skills.loader import SkillLoader

    prompt = GenericPhase(SkillLoader()).build_prompt(
        skill_name="cafe-spec",
        skill_invocation="/cafe-spec",
        context={"conversation_locale": "zh-TW", "repository_content_locale": "en-US"},
    )
    assert "zh-TW" in prompt
    assert "en-US" in prompt


def test_the_notification_language_does_not_depend_on_the_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slack_posts: list
) -> None:
    """Integration 2: agent identity changes nothing about the workflow language."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.chdir(repo_root)
    issue_dir = _issue_dir(repo_root, "agent-independent")
    _pause_for_output_review(issue_dir)
    _store_locale(issue_dir, "en-US")
    slack_posts.clear()

    _pause_for_output_review(issue_dir)

    delivered = _delivered_text(slack_posts)
    assert delivered.isascii()
    task = HumanTaskRecordStore(issue_dir).tasks()[-1]
    assert task.prompt.isascii()


def test_resume_and_a_changed_playbook_default_leave_the_stored_language_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slack_posts: list
) -> None:
    """Integration 3: resume never re-resolves, and the background path agrees."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.chdir(repo_root)
    issue_dir = _issue_dir(repo_root, "resumed")
    _pause_for_output_review(issue_dir)
    _store_locale(issue_dir, "zh-TW", "inferred")

    _pause_for_output_review(issue_dir, playbook_overrides={"conversation_locale": "ja-JP"})
    reloaded = BlackboardStore(issue_dir).load_or_create("spec")

    assert (reloaded.conversation_locale, reloaded.conversation_locale_source) == (
        "zh-TW",
        "inferred",
    )
    assert "確認結果" in _delivered_text(slack_posts)

    slack_posts.clear()
    _callback_module()._notify_callback_failure(
        {"issue": "resumed", "step": "develop", "event_type": "phase_terminal"},
        repository_root=repo_root,
        error=ValueError("state unreadable"),
    )

    assert "無法讀取自動通知所需的狀態或設定" in _delivered_text(slack_posts)
    assert BlackboardStore(issue_dir).load_or_create("spec").conversation_locale == "zh-TW"


def test_a_legacy_record_keeps_no_language_through_a_full_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slack_posts: list
) -> None:
    """Integration 6: absence is not a preference and is never backfilled."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.chdir(repo_root)
    issue_dir = _issue_dir(repo_root, "legacy")
    _pause_for_output_review(issue_dir)
    _store_locale(issue_dir, None)
    slack_posts.clear()

    store = BlackboardStore(issue_dir)
    store.load_or_create(
        "spec",
        supplied_locale=SuppliedLocale(value="zh-TW", source=LocaleSource.EXPLICIT),
        playbook_conversation_locale="ja-JP",
    )
    _pause_for_output_review(issue_dir)

    persisted = json.loads((issue_dir / "blackboard.json").read_text(encoding="utf-8"))
    assert persisted.get("conversation_locale") is None
    assert persisted.get("conversation_locale_source") is None
    assert _delivered_text(slack_posts).isascii()


def test_a_pending_confirmation_survives_a_deliberate_language_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slack_posts: list
) -> None:
    """Integration 5/9: the outstanding task keeps its presentation and its answer."""
    from cafe.ui.cli_shared import _pending_task_presentation

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.chdir(repo_root)
    issue_dir = _issue_dir(repo_root, "pending")
    _pause_for_output_review(issue_dir)
    _store_locale(issue_dir, "en-US")
    slack_posts.clear()
    _pause_for_output_review(issue_dir)
    pending = HumanTaskRecordStore(issue_dir).tasks()[-1]
    captured_prompt = pending.prompt
    captured_result = dict(pending.expected_result)

    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")
    store.set_conversation_locale(
        state, SuppliedLocale(value="zh-TW", source=LocaleSource.EXPLICIT)
    )

    records = HumanTaskRecordStore(issue_dir)
    unchanged = records.get_task(pending.id)
    presented = _pending_task_presentation(
        record_store=records, task_id=pending.id, declared=HumanTaskPolicy.model_validate(
            captured_result
        )
    )
    inspected = runner.invoke(app, ["task", "inspect", pending.id, "--json"])

    assert unchanged.prompt == captured_prompt
    assert unchanged.expected_result == captured_result
    assert presented.prompt == captured_prompt
    assert inspected.exit_code == 0
    assert json.loads(inspected.stdout)["data"]["task"]["prompt"] == captured_prompt

    # The canonical answer captured at materialization is still accepted.
    policy = HumanTaskPolicy.model_validate(captured_result)
    completion = validate_human_task_completion(
        policy, {"task": policy.id, "decision": policy.decisions[0].id}
    )
    assert completion.decision == policy.decisions[0].id

    slack_posts.clear()
    _store_locale(issue_dir, None)


def test_preparation_creates_the_workflow_with_the_supplied_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slack_posts: list
) -> None:
    """Integration 8: the real kickoff chain carries a supplied preference through."""
    from tests.conftest import create_minimal_config

    create_minimal_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.utils.git_utils.is_github_repo", return_value=False),
        patch("cafe.ui.phase_prompts.is_github_repo", return_value=False),
    ):
        git = MagicMock()
        git.get_current_branch.return_value = "main"
        git.has_uncommitted_changes.return_value = False
        git.branch_exists.return_value = False
        git.worktree_exists.return_value = False
        mock_git_cls.return_value = git

        prepared = runner.invoke(
            app,
            [
                "prepare",
                "kickoff-locale",
                "--playbook",
                "standard",
                "--no-interactive",
                "--input-method=manual",
                "--no-auto-create-pr",
                "--conversation-locale",
                "zh-TW",
                "--conversation-locale-source",
                "inferred",
            ],
        )

        assert prepared.exit_code == 0, prepared.stdout
        issue_dir = _issue_dir(tmp_path, "kickoff-locale")
        created = json.loads((issue_dir / "blackboard.json").read_text(encoding="utf-8"))
        assert created["conversation_locale"] == "zh-TW"
        assert created["conversation_locale_source"] == "inferred"

        # A repeated preparation writes no new language.
        runner.invoke(
            app,
            [
                "prepare",
                "kickoff-locale",
                "--playbook",
                "standard",
                "--no-interactive",
                "--input-method=manual",
                "--no-auto-create-pr",
                "--conversation-locale",
                "ja-JP",
                "--conversation-locale-source",
                "explicit",
            ],
        )

    repeated = json.loads((issue_dir / "blackboard.json").read_text(encoding="utf-8"))
    assert repeated["conversation_locale"] == "zh-TW"
    assert repeated["conversation_locale_source"] == "inferred"

    _pause_for_output_review(issue_dir)
    assert "確認結果" in _delivered_text(slack_posts)


def test_preparation_rejects_a_locale_without_a_declared_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rejected locale input leaves no prepared state behind."""
    from tests.conftest import create_minimal_config

    create_minimal_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    with (
        patch("cafe.ui.cli.GitOperations") as mock_git_cls,
        patch("cafe.utils.git_utils.is_github_repo", return_value=False),
    ):
        mock_git_cls.return_value = MagicMock()
        result = runner.invoke(
            app,
            [
                "prepare",
                "rejected-locale",
                "--playbook",
                "standard",
                "--no-interactive",
                "--input-method=manual",
                "--no-auto-create-pr",
                "--conversation-locale",
                "zh-TW",
            ],
        )

    assert result.exit_code != 0
    assert not (_issue_dir(tmp_path, "rejected-locale") / "blackboard.json").exists()
