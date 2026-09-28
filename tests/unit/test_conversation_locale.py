"""Invariant coverage for the generic workflow conversation-locale contract."""

from __future__ import annotations

import pytest

from cafe.core.conversation_locale import (
    DEFAULT_CONVERSATION_LOCALE,
    ConversationLocaleError,
    LocaleSource,
    SuppliedLocale,
    normalize_locale_tag,
    resolve_conversation_locale,
    select_text_locale,
    supplied_locale_from_inputs,
)


def test_explicit_preference_outranks_inferred_and_playbook_default() -> None:
    resolved = resolve_conversation_locale(
        supplied=[
            SuppliedLocale(value="ja-JP", source=LocaleSource.INFERRED),
            SuppliedLocale(value="zh-TW", source=LocaleSource.EXPLICIT),
        ],
        playbook_default="en-US",
    )

    assert resolved.value == "zh-TW"
    assert resolved.source is LocaleSource.EXPLICIT


def test_inferred_preference_outranks_playbook_default() -> None:
    resolved = resolve_conversation_locale(
        supplied=[SuppliedLocale(value="zh-TW", source=LocaleSource.INFERRED)],
        playbook_default="ja-JP",
    )

    assert resolved.value == "zh-TW"
    assert resolved.source is LocaleSource.INFERRED


def test_playbook_default_outranks_the_floor() -> None:
    resolved = resolve_conversation_locale(playbook_default="ja-JP")

    assert resolved.value == "ja-JP"
    assert resolved.source is LocaleSource.PLAYBOOK_DEFAULT


def test_absent_evidence_at_every_tier_falls_through_to_the_floor() -> None:
    resolved = resolve_conversation_locale()

    assert resolved.value == DEFAULT_CONVERSATION_LOCALE
    assert resolved.source is LocaleSource.FALLBACK


def test_inferred_preference_is_never_relabelled_as_explicit() -> None:
    resolved = resolve_conversation_locale(
        supplied=[SuppliedLocale(value="zh-TW", source=LocaleSource.INFERRED)]
    )

    assert resolved.source is LocaleSource.INFERRED


def test_auto_is_unresolved_and_never_becomes_an_effective_value() -> None:
    assert normalize_locale_tag("auto") is None
    assert normalize_locale_tag("AUTO") is None

    resolved = resolve_conversation_locale(
        supplied=[SuppliedLocale(value="auto", source=LocaleSource.EXPLICIT)],
        playbook_default="auto",
    )

    assert resolved.value == DEFAULT_CONVERSATION_LOCALE
    assert resolved.source is LocaleSource.FALLBACK


def test_malformed_tag_falls_through_instead_of_corrupting_the_result() -> None:
    assert normalize_locale_tag("not a locale!") is None

    resolved = resolve_conversation_locale(
        supplied=[SuppliedLocale(value="not a locale!", source=LocaleSource.EXPLICIT)],
        playbook_default="ja-JP",
    )

    assert resolved.value == "ja-JP"
    assert resolved.source is LocaleSource.PLAYBOOK_DEFAULT


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("zh-tw", "zh-TW"),
        ("ZH_TW", "zh-TW"),
        ("zh-hant", "zh-Hant"),
        ("zh-hans", "zh-Hans"),
        ("zh-cn", "zh-CN"),
        ("en_us", "en-US"),
    ],
)
def test_tag_normalization_is_canonical(raw: str, expected: str) -> None:
    assert normalize_locale_tag(raw) == expected


def test_simplified_and_traditional_chinese_are_distinct_values() -> None:
    assert normalize_locale_tag("zh-Hans") != normalize_locale_tag("zh-Hant")
    assert select_text_locale("zh-Hant") != select_text_locale("zh-Hans")


def test_traditional_chinese_variants_select_authored_chinese_text() -> None:
    for tag in ("zh-TW", "zh-HK", "zh-Hant", "zh-Hant-TW"):
        assert select_text_locale(tag) == "zh-TW"


def test_unsupported_locales_select_the_english_text_without_changing_the_value() -> None:
    for tag in ("ja-JP", "zh-CN", "zh-Hans", "auto", "", None):
        assert select_text_locale(tag) == DEFAULT_CONVERSATION_LOCALE


def test_supplied_locale_requires_a_declared_tier() -> None:
    with pytest.raises(ConversationLocaleError):
        supplied_locale_from_inputs(value="zh-TW", source=None)

    with pytest.raises(ConversationLocaleError):
        supplied_locale_from_inputs(value=None, source="explicit")


def test_supplied_locale_rejects_a_tier_no_caller_may_claim() -> None:
    with pytest.raises(ConversationLocaleError):
        supplied_locale_from_inputs(value="zh-TW", source="fallback")

    with pytest.raises(ConversationLocaleError):
        supplied_locale_from_inputs(value="zh-TW", source="playbook_default")


def test_supplied_locale_from_inputs_accepts_the_two_caller_tiers() -> None:
    explicit = supplied_locale_from_inputs(value="zh-tw", source="explicit")
    inferred = supplied_locale_from_inputs(value="ja-jp", source="inferred")

    assert (explicit.value, explicit.source) == ("zh-TW", LocaleSource.EXPLICIT)
    assert (inferred.value, inferred.source) == ("ja-JP", LocaleSource.INFERRED)
    assert supplied_locale_from_inputs(value=None, source=None) is None
