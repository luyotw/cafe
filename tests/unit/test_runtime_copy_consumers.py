"""Localized public consumers obtain authored copy from packaged resources."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from cafe.core import runtime_locales
from cafe.core.human_task_notifications import (
    HumanTaskSlackMessage,
    WorkflowCallbackFailureSlackMessage,
)
from cafe.core.human_tasks import HumanTaskPolicy, HumanTaskQuestion
from cafe.skills.loader import SkillLoader
from tests.unit._kickoff_test_support import load_kickoff_module

BUILTINS = Path(__file__).resolve().parents[2] / "src/cafe/data/skills"


@pytest.fixture
def authored_copy(monkeypatch, tmp_path):
    """Replace the resource I/O boundary, never the renderer or its consumers."""
    catalogs = runtime_locales.load_catalogs()
    root = tmp_path / "resources"
    directory = root / "data/locales"
    directory.mkdir(parents=True)

    def replace(key):
        for locale, catalog in catalogs.items():
            document = dict(catalog)
            document[key] = "CATALOG " + document[key]
            (directory / f"{locale}.yaml").write_text(
                yaml.safe_dump(document, allow_unicode=True), encoding="utf-8"
            )
        runtime_locales.load_catalogs.cache_clear()

    monkeypatch.setattr(runtime_locales, "files", lambda package: root)
    yield replace
    runtime_locales.load_catalogs.cache_clear()


@pytest.mark.parametrize("locale", ["en-US", "zh-TW", "zh-Hant", "zh-CN", "zh//TW", None])
def test_human_notification_reads_catalog_at_payload_boundary(authored_copy, locale):
    authored_copy("notification.task_headline")
    message = HumanTaskSlackMessage(
        repository="repo",
        issue="owner.{issue}",
        workflow_id="flow",
        task_id="task",
        step="develop",
        task_type="output-review",
        locale=locale,
    )
    payload = message.to_slack_payload()["text"]
    assert payload.startswith("CATALOG ")
    assert "owner.{issue}" in payload
    assert message.locale == locale


@pytest.mark.parametrize(
    "code,key",
    [
        ("callback_ValueError", "notification.reason_state"),
        ("codex_queue_failure", "notification.reason_queue"),
        ("other", "notification.reason_generic"),
    ],
)
def test_callback_notification_reads_catalog_for_selected_reason(authored_copy, code, key):
    authored_copy(key)
    message = WorkflowCallbackFailureSlackMessage(
        repository="repo",
        issue="owner.{issue}",
        step="review",
        event_type="event",
        error_code=code,
        locale="zh-TW",
    )
    assert "CATALOG " in message.to_slack_payload()["text"]


@pytest.mark.parametrize("locale", ["en-US", "zh-TW", "zh-Hant", "zh-HK", "zh//TW"])
def test_manager_progress_reads_catalog_without_mutating_inputs(authored_copy, locale):
    authored_copy("manager.progress.status.pending")
    progress = load_kickoff_module("render_workflow_progress")
    playbook = {"steps": {"inspect": {"on": {"await_agent": "_done"}}}}
    before = yaml.safe_dump(playbook)
    text = progress.render_progress(
        playbook=playbook,
        locale=locale,
        manager_state={"deliver": "pending", "cleanup": "pending"},
    )
    assert "CATALOG " in text
    assert yaml.safe_dump(playbook) == before


@pytest.mark.parametrize("locale", ["en-US", "zh-TW"])
def test_manager_kickoff_reads_catalog_through_public_render(authored_copy, locale):
    from tests.integration.test_kickoff_preparation import _formatter_inputs

    authored_copy("manager.kickoff.confirmation")
    inputs = _formatter_inputs("runtime-copy-catalog-test")
    inputs.update(effective_locale=locale, locale_source="explicit")
    report = load_kickoff_module("kickoff_inputs").render_kickoff(inputs)
    assert "CATALOG " in report["output"]


def test_every_builtin_localized_declaration_uses_keys_and_materializes_plain_copy(tmp_path):
    loader = SkillLoader(project_root=tmp_path, global_root=tmp_path / "global")
    found = 0
    for path in sorted(BUILTINS.glob("cafe-*/SKILL.md")):
        metadata = yaml.safe_load(path.read_text().split("---", 2)[1])
        tasks = metadata.get("workflow", {}).get("human_tasks", [])
        if not tasks:
            continue
        declared = loader.get_workflow_declaration(path.parent.name)
        for raw, policy in zip(tasks, declared.human_tasks, strict=True):
            assert isinstance(raw["prompt"], dict)
            assert raw["prompt"].keys() == {"message_key"}
            for tag, reference in raw.get("prompt_locales", {}).items():
                assert reference == raw["prompt"]
                assert policy.for_locale(tag).prompt == runtime_locales.render_text(
                    reference["message_key"], locale=tag
                )
            for decision in raw.get("decisions", []):
                assert decision["label"].keys() == {"message_key"}
            for question in raw.get("questions", []):
                assert question["prompt"].keys() == {"message_key"}
            snapshot = policy.for_locale("zh-TW").model_dump(mode="json")
            assert isinstance(snapshot["prompt"], str) and snapshot["prompt_locales"] == {}
            assert snapshot["id"] == raw["id"]
            assert snapshot["input_schema"] == raw["input_schema"]
            assert [d["id"] for d in snapshot["decisions"]] == [
                d["id"] for d in raw.get("decisions", [])
            ]
            found += 1
    assert found > 0


def test_catalog_reference_resolves_once_before_human_task_snapshot(authored_copy):
    key = "human_task.cafe_spec.output_review.prompt"
    policy = HumanTaskPolicy.model_validate(
        {
            "id": "feedback",
            "pattern": "revision_feedback",
            "input_schema": "feedback",
            "prompt": {"message_key": key},
            "prompt_locales": {"zh-TW": {"message_key": key}},
            "correction_guidance": {"message_key": key},
            "correction_guidance_locales": {"zh-TW": {"message_key": key}},
        }
    )
    snapshot = policy.for_locale("zh-TW").model_dump(mode="json")
    authored_copy(key)
    assert HumanTaskPolicy.model_validate(snapshot).model_dump(mode="json") == snapshot
    assert "CATALOG " not in snapshot["prompt"]


def test_custom_inline_copy_and_exact_declared_variant_fallback_remain_unchanged():
    policy = HumanTaskPolicy.model_validate(
        {
            "id": "custom",
            "pattern": "revision_feedback",
            "input_schema": "feedback",
            "prompt": "Custom {literal}",
            "prompt_locales": {"zh-TW": "自訂 {literal}"},
        }
    )
    assert policy.for_locale("zh-TW").prompt == "自訂 {literal}"
    for locale in (None, "zh-Hant", "zh-HK", "zh-CN", "ja-JP", "zh//TW"):
        assert policy.for_locale(locale).prompt == "Custom {literal}"


@pytest.mark.parametrize(
    "reference",
    [
        {"message_key": "missing.key"},
        {"message_key": 42},
        {"message_key": "workspace.correction"},
        {"message_key": "human_task.cafe_spec.output_review.prompt", "extra": "ignored"},
    ],
)
def test_invalid_or_parameterized_declaration_references_are_rejected(reference):
    with pytest.raises(ValidationError):
        HumanTaskPolicy.model_validate(
            {
                "id": "custom",
                "pattern": "revision_feedback",
                "input_schema": "feedback",
                "prompt": reference,
            }
        )


def test_question_reference_translates_prompt_and_preserves_answer_identity():
    reference = {
        "message_key": "human_task.cafe_brief_first.editorial_clarification."
        "questions.audience.prompt"
    }
    question = HumanTaskQuestion.model_validate(
        {
            "id": "audience",
            "prompt": reference,
            "prompt_locales": {"zh-TW": reference},
            "options": ["reader", "editor"],
            "multiple": True,
        }
    )
    localized = question.for_locale("zh-TW")
    assert localized.prompt == runtime_locales.render_text(reference["message_key"], locale="zh-TW")
    assert (localized.id, localized.options, localized.multiple) == (
        "audience",
        ("reader", "editor"),
        True,
    )
    with pytest.raises(ValidationError):
        HumanTaskQuestion.model_validate(
            {"id": "audience", "prompt": "Inline", "options": [reference]}
        )
