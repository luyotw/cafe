"""Generic workflow conversation-language contract.

The workflow conversation language is the language the system talks to *this
user* in, for this workflow run. It is distinct from the repository content
language, which governs documentation, code comments and engineering prose.

Resolution precedence, highest first:

1. An explicit user instruction supplied by a caller.
2. A preference the caller reliably inferred from the user's own
   natural-language messages.
3. The playbook's explicit ``conversation_locale`` default.
4. The ``en-US`` floor.

Only the first two tiers may be supplied by a caller, and a caller must declare
which of them it is supplying: an inferred preference is persisted as inferred
and is never relabelled as explicit. Callers do the inferring; this module owns
the tiers, the tag rules and the selection of authored text. A caller inferring
a preference must exclude quoted material, code, stack traces, logs, generated
artifacts and isolated tokens such as ``1`` or ``ok`` — none of those is
evidence of a language preference. A caller with no reliable evidence supplies
nothing and resolution falls through.

``auto`` is only the unresolved marker; it is never an effective value. A
malformed or unrecognized tag is not usable evidence either, so it falls through
to the next tier rather than corrupting the result.

Accepting a tag is not the same as supporting it: authored text exists for
``en-US`` and Traditional Chinese only, and any other locale selects the English
text for that message without changing the stored locale. Simplified and
Traditional Chinese are never treated as interchangeable.

This module is generic core and imports no Driver code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional, Sequence

UNRESOLVED_LOCALE_TAG = "auto"
DEFAULT_CONVERSATION_LOCALE = "en-US"
TRADITIONAL_CHINESE_TEXT_LOCALE = "zh-TW"
SUPPORTED_TEXT_LOCALES: tuple[str, ...] = (
    DEFAULT_CONVERSATION_LOCALE,
    TRADITIONAL_CHINESE_TEXT_LOCALE,
)

_LANGUAGE_SUBTAG = re.compile(r"[A-Za-z]{2,3}\Z")
_SCRIPT_SUBTAG = re.compile(r"[A-Za-z]{4}\Z")
_REGION_SUBTAG = re.compile(r"([A-Za-z]{2}|[0-9]{3})\Z")
_VARIANT_SUBTAG = re.compile(r"([A-Za-z0-9]{5,8}|[0-9][A-Za-z0-9]{3})\Z")

# Traditional Chinese regions and scripts share one authored text catalog.
# Simplified Chinese deliberately does not appear here.
_TRADITIONAL_CHINESE_SCRIPTS = frozenset({"Hant"})
_TRADITIONAL_CHINESE_REGIONS = frozenset({"TW", "HK", "MO"})


class ConversationLocaleError(ValueError):
    """A caller supplied a locale input that the contract cannot accept."""


class LocaleSource(str, Enum):
    """The tier that supplied the effective conversation locale."""

    EXPLICIT = "explicit"
    INFERRED = "inferred"
    PLAYBOOK_DEFAULT = "playbook_default"
    FALLBACK = "fallback"


#: Only these tiers describe evidence a caller can supply.
SUPPLIABLE_LOCALE_SOURCES: tuple[LocaleSource, ...] = (
    LocaleSource.EXPLICIT,
    LocaleSource.INFERRED,
)

_SOURCE_RANK = {source: rank for rank, source in enumerate(SUPPLIABLE_LOCALE_SOURCES)}


@dataclass(frozen=True)
class SuppliedLocale:
    """One caller-supplied preference together with the tier that produced it."""

    value: str
    source: LocaleSource

    def __post_init__(self) -> None:
        if self.source not in SUPPLIABLE_LOCALE_SOURCES:
            raise ConversationLocaleError(
                "conversation locale source must be one of "
                f"{[source.value for source in SUPPLIABLE_LOCALE_SOURCES]}"
            )
        if not isinstance(self.value, str) or not self.value.strip():
            raise ConversationLocaleError("conversation locale value must be a non-empty tag")


@dataclass(frozen=True)
class ResolvedConversationLocale:
    """The effective conversation locale plus the tier it came from."""

    value: str
    source: LocaleSource


def normalize_locale_tag(raw: Optional[str]) -> Optional[str]:
    """Return the canonical tag, or ``None`` when it supplies no usable value."""
    if raw is None:
        return None
    candidate = raw.strip().replace("_", "-")
    if not candidate or candidate.casefold() == UNRESOLVED_LOCALE_TAG:
        return None
    parts = candidate.split("-")
    if not _LANGUAGE_SUBTAG.fullmatch(parts[0]):
        return None
    canonical = [parts[0].lower()]
    index = 1
    if index < len(parts) and _SCRIPT_SUBTAG.fullmatch(parts[index]):
        canonical.append(parts[index].capitalize())
        index += 1
    if index < len(parts) and _REGION_SUBTAG.fullmatch(parts[index]):
        canonical.append(parts[index].upper())
        index += 1
    for part in parts[index:]:
        if not _VARIANT_SUBTAG.fullmatch(part):
            return None
        canonical.append(part.lower())
    return "-".join(canonical)


def select_text_locale(locale: Optional[str]) -> str:
    """Select the authored text catalog for a locale without changing it."""
    normalized = normalize_locale_tag(locale)
    if normalized is None:
        return DEFAULT_CONVERSATION_LOCALE
    parts = normalized.split("-")
    if parts[0] != "zh":
        return DEFAULT_CONVERSATION_LOCALE
    script = next((part for part in parts[1:] if _SCRIPT_SUBTAG.fullmatch(part)), None)
    region = next((part for part in parts[1:] if _REGION_SUBTAG.fullmatch(part)), None)
    if script is not None:
        traditional = script in _TRADITIONAL_CHINESE_SCRIPTS
    else:
        traditional = region in _TRADITIONAL_CHINESE_REGIONS
    return TRADITIONAL_CHINESE_TEXT_LOCALE if traditional else DEFAULT_CONVERSATION_LOCALE


def supplied_locale_from_inputs(
    *, value: Optional[str], source: Optional[str]
) -> Optional[SuppliedLocale]:
    """Build a supplied preference from raw inputs, rejecting partial ones.

    A value without a declared tier is rejected rather than defaulted, and a
    tier without a value is rejected too; neither leaves partial state behind.
    """
    has_value = bool(value and value.strip())
    has_source = bool(source and source.strip())
    if not has_value and not has_source:
        return None
    if not has_value:
        raise ConversationLocaleError("a conversation locale source requires a locale value")
    if not has_source:
        raise ConversationLocaleError("a conversation locale requires a declared source tier")
    try:
        declared = LocaleSource(str(source).strip())
    except ValueError as exc:
        raise ConversationLocaleError(
            "conversation locale source must be one of "
            f"{[item.value for item in SUPPLIABLE_LOCALE_SOURCES]}"
        ) from exc
    # An unusable tag is kept verbatim so resolution falls through a tier
    # instead of the contract rejecting the caller outright.
    return SuppliedLocale(
        value=normalize_locale_tag(value) or str(value).strip(),
        source=declared,
    )


def resolve_conversation_locale(
    *,
    supplied: Iterable[SuppliedLocale] = (),
    playbook_default: Optional[str] = None,
) -> ResolvedConversationLocale:
    """Resolve the effective conversation locale through the documented tiers."""
    candidates: Sequence[SuppliedLocale] = tuple(supplied)
    for candidate in sorted(candidates, key=lambda item: _SOURCE_RANK[item.source]):
        normalized = normalize_locale_tag(candidate.value)
        if normalized is not None:
            return ResolvedConversationLocale(value=normalized, source=candidate.source)
    normalized_default = normalize_locale_tag(playbook_default)
    if normalized_default is not None:
        return ResolvedConversationLocale(
            value=normalized_default, source=LocaleSource.PLAYBOOK_DEFAULT
        )
    return ResolvedConversationLocale(
        value=DEFAULT_CONVERSATION_LOCALE, source=LocaleSource.FALLBACK
    )
