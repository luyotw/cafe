"""U01-U03: scoped, explicit preference behavior."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _kickoff_test_support import load_kickoff_module


def test_precedence_and_scope_clear_reveal_lower_preference_without_cross_scope_loss(
    tmp_path: Path,
) -> None:
    module = load_kickoff_module("kickoff_preferences")
    preferences = module.PreferenceStore(tmp_path / "config", repository_root=tmp_path)
    preferences.set("manager.mode", "attached", scope="user", origin="explicit")
    preferences.set("manager.mode", "unattended", scope="repository", origin="explicit")

    assert preferences.effective("manager.mode", explicit="event-driven").value == "event-driven"
    assert preferences.effective("manager.mode").value == "unattended"
    preferences.clear("manager.mode", scope="repository")
    assert preferences.effective("manager.mode").value == "attached"
    assert preferences.effective("manager.mode").scope == "user"
    preferences.clear("manager.mode", scope="user")
    assert preferences.effective("manager.mode").value is None


def test_one_off_values_and_non_reusable_values_are_not_persisted(tmp_path: Path) -> None:
    module = load_kickoff_module("kickoff_preferences")
    preferences = module.PreferenceStore(tmp_path / "config", repository_root=tmp_path)

    resolved = preferences.effective("conversation.locale", explicit="ja-JP")

    assert (resolved.value, resolved.scope, resolved.origin) == ("ja-JP", "current", "explicit")
    assert preferences.inspect(scope="user") == {}
    assert preferences.inspect(scope="repository") == {}


def test_language_input_preserves_explicit_scope_and_inferred_origin() -> None:
    module = load_kickoff_module("kickoff_preferences")

    explicit = module.canonical_language_input(
        {"value": "zh-tw", "scope": "repository", "origin": "explicit"}
    )
    inferred = module.canonical_language_input(
        {"value": "ja-JP", "scope": "user", "origin": "inferred"}
    )

    assert explicit == {"value": "zh-TW", "source": "explicit", "scope": "repository"}
    assert inferred == {"value": "ja-JP", "source": "inferred", "scope": "user"}


def test_incompatible_preferences_remain_unresolved_and_never_add_authority(
    tmp_path: Path,
) -> None:
    module = load_kickoff_module("kickoff_preferences")
    result = module.validate_reusable_value(
        "confirmation.assignments",
        {"mandatory_task": False, "execute": ["git", "push"]},
        declarations={"mandatory_tasks": ["confirm_output"]},
    )

    assert result.value is None
    assert result.diagnostic
    assert "execute" not in result.value_or_diagnostics
