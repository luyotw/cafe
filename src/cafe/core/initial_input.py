"""Trusted initial-input provider declarations and resolution primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

MANUAL_TEXT_PROVIDER = "manual_text"
GITHUB_ISSUE_PROVIDER = "github_issue"
SUPPORTED_INITIAL_INPUT_PROVIDERS = frozenset(
    {MANUAL_TEXT_PROVIDER, GITHUB_ISSUE_PROVIDER}
)


@dataclass(frozen=True)
class InitialInputResult:
    """Content resolved by a trusted host-side provider."""

    content: str
    provider: str
    source: str


def registered_initial_input_providers() -> frozenset[str]:
    """Return names implemented by the trusted host provider registry.

    Provider execution is registered by the native hook. Keeping this small,
    explicit registry fail-closes authoring validation without loading plugins
    or granting playbooks arbitrary host execution.
    """
    return SUPPORTED_INITIAL_INPUT_PROVIDERS


def normalize_initial_input_provider(value: Optional[str]) -> Optional[str]:
    """Normalize legacy prepare values to the provider contract names."""
    if value is None:
        return None
    normalized = value.strip().lower()
    return {"manual": MANUAL_TEXT_PROVIDER, "github": GITHUB_ISSUE_PROVIDER}.get(
        normalized, normalized
    )


def load_initial_input_selection(
    issue_config: dict[str, Any],
) -> tuple[Optional[str], Optional[int]]:
    """Read canonical selection first, then preserve legacy spec configuration."""
    canonical = issue_config.get("initial_input")
    if isinstance(canonical, dict):
        provider = normalize_initial_input_provider(canonical.get("provider"))
        raw_issue_id = canonical.get("issue_id")
    else:
        legacy_spec = issue_config.get("spec")
        legacy_spec = legacy_spec if isinstance(legacy_spec, dict) else {}
        provider = normalize_initial_input_provider(legacy_spec.get("input_method"))
        raw_issue_id = legacy_spec.get("issue_id")

    try:
        issue_id = int(raw_issue_id) if raw_issue_id not in (None, "") else None
    except (TypeError, ValueError):
        issue_id = None
    return provider, issue_id
