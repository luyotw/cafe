"""Manager-side adapter invariants for the workflow conversation language."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from cafe.core.blackboard import BlackboardStore
from cafe.core.conversation_locale import ConversationLocaleError, LocaleSource, SuppliedLocale

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _adapter():
    path = (
        PROJECT_ROOT
        / "src/cafe/data/skills/use-cafe-workflow/scripts/conversation_locale_adapter.py"
    )
    spec = importlib.util.spec_from_file_location("conversation_locale_adapter_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workflow(issue_dir: Path, *, locale: str | None, source: str | None) -> None:
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")
    state.conversation_locale = locale
    state.conversation_locale_source = source
    store.save(state)


def test_a_kickoff_supplies_the_value_and_tier_to_the_creation_boundary() -> None:
    adapter = _adapter()

    supplied = adapter.supplied_preference(value="zh-tw", source="inferred")

    assert adapter.creation_locale_arguments(supplied) == [
        "--conversation-locale",
        "zh-TW",
        "--conversation-locale-source",
        "inferred",
    ]
    assert adapter.creation_locale_arguments(None) == []


def test_the_adapter_never_claims_an_explicit_tier_for_an_inferred_preference() -> None:
    adapter = _adapter()

    supplied = adapter.supplied_preference(value="zh-TW", source="inferred")

    assert supplied.source is LocaleSource.INFERRED
    with pytest.raises(ConversationLocaleError):
        adapter.supplied_preference(value="zh-TW", source=None)


def test_resuming_reads_the_effective_generic_value_instead_of_re_resolving(
    tmp_path: Path,
) -> None:
    adapter = _adapter()
    issue_dir = tmp_path / "issue"
    _workflow(issue_dir, locale="zh-TW", source="inferred")

    effective = adapter.effective_conversation_locale(
        issue_dir,
        playbook_locale="ja-JP",
        supplied=SuppliedLocale(value="ko-KR", source=LocaleSource.EXPLICIT),
    )

    assert effective == ("zh-TW", "inferred")


def test_the_contract_snapshot_equals_the_effective_generic_value_and_source(
    tmp_path: Path,
) -> None:
    adapter = _adapter()
    issue_dir = tmp_path / "issue"
    _workflow(issue_dir, locale="zh-TW", source="explicit")

    snapshot = adapter.contract_locale_snapshot(
        issue_dir,
        playbook_id="standard",
        playbook_locale="en-US",
        declared_value="ja-JP",
        declared_source="explicit",
    )

    assert snapshot == {
        "value": "zh-TW",
        "source": "explicit",
    }
    assert adapter.effective_conversation_locale(issue_dir) == ("zh-TW", "explicit")


def test_a_playbook_sourced_value_is_reported_with_its_playbook(tmp_path: Path) -> None:
    adapter = _adapter()
    issue_dir = tmp_path / "issue"
    _workflow(issue_dir, locale="en-US", source=LocaleSource.PLAYBOOK_DEFAULT.value)

    snapshot = adapter.contract_locale_snapshot(issue_dir, playbook_id="standard")

    assert snapshot == {"value": "en-US", "source": "playbook:standard"}


def test_a_workflow_with_no_stored_value_falls_back_without_being_written(
    tmp_path: Path,
) -> None:
    adapter = _adapter()
    issue_dir = tmp_path / "issue"
    _workflow(issue_dir, locale=None, source=None)

    snapshot = adapter.contract_locale_snapshot(
        issue_dir,
        playbook_id="standard",
        playbook_locale="ja-JP",
        declared_value="zh-TW",
        declared_source="inferred",
    )
    effective = adapter.effective_conversation_locale(
        issue_dir,
        playbook_locale="ja-JP",
        supplied=SuppliedLocale(value="zh-TW", source=LocaleSource.INFERRED),
    )
    persisted = json.loads((issue_dir / "blackboard.json").read_text(encoding="utf-8"))

    assert snapshot == {"value": "en-US", "source": "fallback"}
    assert effective == ("en-US", "fallback")
    assert persisted.get("conversation_locale") is None
    assert persisted.get("conversation_locale_source") is None
    assert adapter.stored_conversation_locale(issue_dir) is None


def test_a_new_workflow_still_resolves_the_supplied_creation_preference(tmp_path: Path) -> None:
    adapter = _adapter()
    issue_dir = tmp_path / "new"
    supplied = SuppliedLocale(value="zh-TW", source=LocaleSource.INFERRED)

    assert adapter.effective_conversation_locale(
        issue_dir, playbook_locale="ja-JP", supplied=supplied
    ) == ("zh-TW", "inferred")
    assert adapter.contract_locale_snapshot(
        issue_dir,
        playbook_id="standard",
        playbook_locale="ja-JP",
        declared_value=supplied.value,
        declared_source=supplied.source.value,
    ) == {"value": "zh-TW", "source": "inferred"}
    assert not (issue_dir / "blackboard.json").exists()


def test_an_absent_or_unreadable_record_does_not_raise(tmp_path: Path) -> None:
    adapter = _adapter()
    missing = tmp_path / "nowhere"
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "blackboard.json").write_text("{not json", encoding="utf-8")

    assert adapter.stored_conversation_locale(missing) is None
    assert adapter.stored_conversation_locale(broken) is None


def test_a_one_reply_language_request_changes_neither_state_nor_snapshot(
    tmp_path: Path,
) -> None:
    adapter = _adapter()
    issue_dir = tmp_path / "issue"
    _workflow(issue_dir, locale="en-US", source="explicit")
    before = json.loads((issue_dir / "blackboard.json").read_text(encoding="utf-8"))

    adapter.one_reply_language_request("zh-TW")

    after = json.loads((issue_dir / "blackboard.json").read_text(encoding="utf-8"))
    assert after["conversation_locale"] == before["conversation_locale"]
    assert after["conversation_locale_source"] == before["conversation_locale_source"]
    assert adapter.contract_locale_snapshot(issue_dir, playbook_id="standard") == {
        "value": "en-US",
        "source": "explicit",
    }


def test_no_generic_module_imports_the_manager_adapter() -> None:
    """The dependency runs one way only: Manager-side code calls generic code."""
    generic_roots = ("core", "phases", "ui", "services", "workflow_execution")
    offenders = [
        path
        for root in generic_roots
        for path in (PROJECT_ROOT / "src" / "cafe" / root).rglob("*.py")
        if "conversation_locale_adapter" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []
