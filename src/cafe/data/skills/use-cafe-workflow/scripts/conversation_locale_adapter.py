"""Driver-owned adapter onto the generic conversation-locale contract.

The Driver may *supply* a user preference when a workflow is created, and must
declare which tier it is supplying. It is never the authority that decides the
workflow language: once a workflow exists, the Driver reads the effective
generic value and source rather than re-resolving them, so its
``locales.conversation`` snapshot mirrors the workflow authority instead of
competing with it.

The dependency runs one way only. This Driver-side module calls generic code;
nothing under ``src/cafe/core/``, ``src/cafe/phases/`` or the generic UI
commands imports it. See ``docs/language-policy.md``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from cafe.core.conversation_locale import (
    ConversationLocaleError,
    LocaleSource,
    SuppliedLocale,
    resolve_conversation_locale,
    supplied_locale_from_inputs,
)

MAX_WORKFLOW_STATE_BYTES = 8 * 1024 * 1024


def supplied_preference(
    *, value: Optional[str], source: Optional[str]
) -> Optional[SuppliedLocale]:
    """Validate a preference the Driver inferred or was explicitly told."""
    return supplied_locale_from_inputs(value=value, source=source)


def creation_locale_arguments(supplied: Optional[SuppliedLocale]) -> list[str]:
    """Render the generic creation-boundary arguments for a kickoff invocation."""
    if supplied is None:
        return []
    return [
        "--conversation-locale",
        supplied.value,
        "--conversation-locale-source",
        supplied.source.value,
    ]


def stored_conversation_locale(issue_dir: Path) -> Optional[tuple[str, str]]:
    """Return the workflow's stored locale and source, or ``None`` when absent."""
    try:
        raw = (issue_dir / "blackboard.json").read_text(encoding="utf-8")
    except OSError:
        return None
    if len(raw.encode("utf-8")) > MAX_WORKFLOW_STATE_BYTES:
        return None
    try:
        state = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(state, dict):
        return None
    value = state.get("conversation_locale")
    source = state.get("conversation_locale_source")
    if not isinstance(value, str) or not value.strip():
        return None
    if not isinstance(source, str) or not source.strip():
        return None
    return value.strip(), source.strip()


def effective_conversation_locale(
    issue_dir: Path,
    *,
    playbook_locale: Optional[str] = None,
    supplied: Optional[SuppliedLocale] = None,
) -> tuple[str, str]:
    """Resolve the value and source the Driver must mirror in its contract.

    An existing workflow's stored value wins outright: resuming never
    re-resolves, and a preference supplied on resume cannot replace it. Only a
    workflow that has no stored value — one being created, or a record written
    before the locale contract existed — falls back to the documented tiers.
    """
    stored = stored_conversation_locale(issue_dir)
    if stored is not None:
        return stored
    resolved = resolve_conversation_locale(
        supplied=() if supplied is None else (supplied,),
        playbook_default=playbook_locale,
    )
    return resolved.value, resolved.source.value


def contract_locale_snapshot(
    issue_dir: Path,
    *,
    playbook_id: str,
    playbook_locale: Optional[str] = None,
    declared_value: Optional[str] = None,
    declared_source: Optional[str] = None,
) -> dict[str, str]:
    """Build the ``locales.conversation`` snapshot the Driver contract carries.

    An existing workflow's stored value and source are mirrored verbatim, so the
    snapshot can never disagree with the workflow authority. Only a workflow
    with no stored value falls back to what the caller declared and then to the
    playbook default.
    """
    stored = stored_conversation_locale(issue_dir)
    if stored is not None:
        value, source = stored
        if source == LocaleSource.PLAYBOOK_DEFAULT.value:
            source = f"playbook:{playbook_id}"
        return {"value": value, "source": source}
    declared = (declared_value or "").strip() or (playbook_locale or "").strip()
    return {
        "value": declared,
        "source": (declared_source or "").strip() or f"playbook:{playbook_id}",
    }


def one_reply_language_request(_requested: str) -> None:
    """A request to answer one reply in another language changes nothing.

    It is deliberately not an operation on workflow state: only the explicit
    workflow-language change updates the stored value and source.
    """
    return None


__all__ = [
    "ConversationLocaleError",
    "contract_locale_snapshot",
    "creation_locale_arguments",
    "effective_conversation_locale",
    "one_reply_language_request",
    "stored_conversation_locale",
    "supplied_preference",
]
