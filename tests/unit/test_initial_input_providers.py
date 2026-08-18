"""Unit tests for initial-input provider selection primitives."""

from cafe.core.initial_input import (
    GITHUB_ISSUE_PROVIDER,
    MANUAL_TEXT_PROVIDER,
    load_initial_input_selection,
    normalize_initial_input_provider,
)


def test_provider_selection_normalizes_legacy_aliases_without_step_names() -> None:
    """U7/U8 — provider aliases remain independent of a ``spec`` destination."""
    assert normalize_initial_input_provider("manual") == MANUAL_TEXT_PROVIDER
    assert normalize_initial_input_provider("github") == GITHUB_ISSUE_PROVIDER


def test_canonical_provider_selection_precedes_legacy_spec_configuration() -> None:
    """U8 — new config wins while old development configuration remains readable."""
    provider, issue_id = load_initial_input_selection(
        {
            "initial_input": {"provider": "github_issue", "issue_id": "346"},
            "spec": {"input_method": "manual"},
        }
    )

    assert provider == GITHUB_ISSUE_PROVIDER
    assert issue_id == 346


def test_legacy_spec_selection_remains_available_when_canonical_config_is_absent() -> None:
    """U8 — legacy issue config still resolves through the generic selection helper."""
    provider, issue_id = load_initial_input_selection(
        {"spec": {"input_method": "github", "issue_id": "350"}}
    )

    assert provider == GITHUB_ISSUE_PROVIDER
    assert issue_id == 350
