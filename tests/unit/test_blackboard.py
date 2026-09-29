"""Invariants for the conversation locale the blackboard owns."""

import json

import pytest

from cafe.core.blackboard import BlackboardState, BlackboardStore, _merge_generic_state
from cafe.core.conversation_locale import (
    ConversationLocaleError,
    LocaleSource,
    SuppliedLocale,
)


def test_creation_stores_the_resolved_locale_with_the_supplying_tier(tmp_path):
    store = BlackboardStore(tmp_path / "issue")

    state = store.load_or_create(
        "spec",
        supplied_locale=SuppliedLocale(value="zh-TW", source=LocaleSource.INFERRED),
        playbook_conversation_locale="en-US",
    )

    assert state.conversation_locale == "zh-TW"
    assert state.conversation_locale_source == LocaleSource.INFERRED.value


def test_creation_without_supplied_evidence_falls_through_to_the_playbook_default(tmp_path):
    store = BlackboardStore(tmp_path / "issue")

    state = store.load_or_create("spec", playbook_conversation_locale="ja-JP")

    assert state.conversation_locale == "ja-JP"
    assert state.conversation_locale_source == LocaleSource.PLAYBOOK_DEFAULT.value


def test_creation_rejects_a_supplied_locale_that_declares_no_tier(tmp_path):
    with pytest.raises(ConversationLocaleError):
        SuppliedLocale(value="zh-TW", source=LocaleSource.FALLBACK)


def test_an_existing_record_keeps_its_locale_across_reload_and_repeated_preparation(tmp_path):
    issue_dir = tmp_path / "issue"
    store = BlackboardStore(issue_dir)
    store.load_or_create(
        "spec",
        supplied_locale=SuppliedLocale(value="zh-TW", source=LocaleSource.EXPLICIT),
    )

    reloaded = store.load_or_create(
        "spec",
        supplied_locale=SuppliedLocale(value="ja-JP", source=LocaleSource.EXPLICIT),
        playbook_conversation_locale="en-US",
    )
    again = BlackboardStore(issue_dir).load_or_create("spec")

    assert reloaded.conversation_locale == "zh-TW"
    assert reloaded.conversation_locale_source == LocaleSource.EXPLICIT.value
    assert again.conversation_locale == "zh-TW"


def test_a_legacy_record_with_no_locale_is_never_backfilled(tmp_path):
    issue_dir = tmp_path / "issue"
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")
    state.conversation_locale = None
    state.conversation_locale_source = None
    store.save(state)

    resumed = store.load_or_create(
        "spec",
        supplied_locale=SuppliedLocale(value="zh-TW", source=LocaleSource.EXPLICIT),
        playbook_conversation_locale="ja-JP",
    )
    resumed.handoff_summary = "resumed"
    store.save(resumed)
    persisted = json.loads(store.file_path.read_text(encoding="utf-8"))

    assert resumed.conversation_locale is None
    assert resumed.conversation_locale_source is None
    assert persisted.get("conversation_locale") is None


def test_the_generic_merge_preserves_a_changed_locale(tmp_path):
    persisted = BlackboardState(current_step="spec")
    baseline = persisted.to_dict()
    desired = BlackboardState.from_dict(baseline, initial_step="spec")
    desired.conversation_locale = "zh-TW"
    desired.conversation_locale_source = LocaleSource.EXPLICIT.value

    merged = _merge_generic_state(
        persisted=persisted,
        desired=desired,
        baseline=baseline,
        capability_receipts_authoritative=False,
    )

    assert merged.conversation_locale == "zh-TW"
    assert merged.conversation_locale_source == LocaleSource.EXPLICIT.value


def test_the_explicit_change_operation_replaces_the_stored_value_and_source(tmp_path):
    store = BlackboardStore(tmp_path / "issue")
    state = store.load_or_create(
        "spec",
        supplied_locale=SuppliedLocale(value="en-US", source=LocaleSource.INFERRED),
    )

    store.set_conversation_locale(
        state, SuppliedLocale(value="zh-tw", source=LocaleSource.EXPLICIT)
    )
    reloaded = store.load_or_create("spec")

    assert reloaded.conversation_locale == "zh-TW"
    assert reloaded.conversation_locale_source == LocaleSource.EXPLICIT.value


def test_the_explicit_change_operation_refuses_an_unusable_tag(tmp_path):
    store = BlackboardStore(tmp_path / "issue")
    state = store.load_or_create(
        "spec",
        supplied_locale=SuppliedLocale(value="en-US", source=LocaleSource.EXPLICIT),
    )

    with pytest.raises(ConversationLocaleError):
        store.set_conversation_locale(
            state, SuppliedLocale(value="auto", source=LocaleSource.EXPLICIT)
        )

    assert store.load_or_create("spec").conversation_locale == "en-US"


def test_the_stored_locale_survives_a_schema_round_trip(tmp_path):
    state = BlackboardState(
        current_step="spec",
        conversation_locale="zh-TW",
        conversation_locale_source=LocaleSource.EXPLICIT.value,
    )

    restored = BlackboardState.from_dict(state.to_dict(), initial_step="spec")

    assert restored.conversation_locale == "zh-TW"
    assert restored.conversation_locale_source == LocaleSource.EXPLICIT.value
