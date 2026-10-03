"""U14-U16: staged selection, normalization and formatter boundary input."""

from __future__ import annotations

from pathlib import Path
from argparse import Namespace
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _kickoff_test_support import load_kickoff_module


def test_unselected_graph_and_missing_decisions_remain_explicit(tmp_path: Path) -> None:
    module = load_kickoff_module("kickoff_inputs")

    report = module.assemble_kickoff(
        {"schema_version": 1, "project_root": str(tmp_path), "issue_name": "issue600", "manager_decisions": {}}
    )

    assert report["selected_playbook"] is None
    assert report["missing_decisions"][0]["owner"] == "manager_decision"
    assert report["formatter_inputs"] is None


def test_empty_lists_and_false_values_survive_normalization_without_guessing() -> None:
    module = load_kickoff_module("kickoff_inputs")
    values = {
        "playbook_id": "standard",
        "issue_name": "issue600",
        "delivery_contract": {"schema_version": 3},
        "deliver": [],
        "cleanup": [],
        "update_preflight": {"status": "current"},
        "catalog_preflight": {"status": "identical"},
        "repository_content_locale": "en-US",
        "current_checkout": True,
        "manager_mode": "unattended",
        "need_clarification": "manager_confirmable",
        "capability_choice": ["pr.auto_create=false"],
    }

    normalized = module.normalize_formatter_inputs(values)

    assert normalized["status"] == "ready", repr(normalized)
    assert normalized["values"]["deliver"] == []
    assert normalized["values"]["current_checkout"] is True
    assert normalized["values"]["need_clarification"] == "manager_confirmable"
    assert normalized["values"]["capability_choice"] == ["pr.auto_create=false"]
    assert "pr.auto_create=false" in module.formatter_argv(values)
    assert "deliver" in module.normalize_formatter_inputs({key: value for key, value in values.items() if key != "deliver"})["missing"]


def test_activation_and_unknown_formatter_fields_are_rejected() -> None:
    module = load_kickoff_module("kickoff_inputs")

    activation = module.normalize_formatter_inputs({"workflow_id": "untrusted"})
    unknown = module.normalize_formatter_inputs({"shell_command": "git push"})

    assert activation["status"] == "invalid"
    assert unknown["status"] == "invalid"


def test_argv_preserves_shell_metacharacters_as_one_json_encoded_argument() -> None:
    module = load_kickoff_module("kickoff_inputs")
    values = {
        "playbook_id": "standard", "issue_name": "issue600",
        "delivery_contract": {"schema_version": 3}, "deliver": [["git", "commit; echo unsafe"]],
        "cleanup": [], "update_preflight": {}, "catalog_preflight": {},
        "repository_content_locale": "en-US", "current_checkout": True,
    }

    argv = module.formatter_argv(values)

    assert "commit; echo unsafe" not in argv
    assert '[["git","commit; echo unsafe"]]' in argv


@pytest.mark.parametrize("mode,event_manager,poll,expected", [
    ("event-driven", None, None, "requires a primary"),
    ("event-driven", [], None, "requires a primary"),
    ("event-driven", ["codex"], None, [["manager.mode", "event-driven"], ["manager.clis[0]", "codex"]]),
    ("event-driven", ["codex:invented"], None, "primary must use CLI"),
    ("event-driven", ["codex"], 30, "rejects attached polling"),
    ("attached", None, None, "requires --poll-interval-seconds"),
    ("attached", None, 30, [["manager.mode", "attached"], ["manager.poll_interval_seconds", 30]]),
    ("attached", ["codex"], 30, "rejects event-driven fields"),
    ("unattended", None, None, [["manager.mode", "unattended"]]),
    ("unattended", ["codex"], None, "accepts no mode-specific fields"),
    ("unattended", None, 30, "accepts no mode-specific fields"),
])
def test_manager_mode_policy_without_catalog_discovery(mode, event_manager, poll, expected) -> None:
    formatter = load_kickoff_module("format_kickoff_contract")
    args = Namespace(manager_mode=mode, event_manager=event_manager, poll_interval_seconds=poll)

    if isinstance(expected, str):
        with pytest.raises(ValueError, match=expected):
            formatter._manager_policy_rows(args)
    else:
        assert formatter._manager_policy_rows(args) == expected


def test_manager_mode_field_types_without_catalog_discovery() -> None:
    inputs = load_kickoff_module("kickoff_inputs")
    for event_manager in ("codex", [None], {"cli": "codex"}):
        result = inputs.normalize_formatter_inputs({"event_manager": event_manager})
        assert "event_manager_must_be_a_string_array" in result["diagnostics"]
