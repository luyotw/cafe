"""Canonical Manager command names and explicit Driver aliases."""

import importlib.util
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parents[2]
SCRIPT_ROOT = PROJECT_ROOT / "src/cafe/data/skills/use-cafe-workflow/scripts"


def _module(script: str):
    path = SCRIPT_ROOT / script
    spec = importlib.util.spec_from_file_location(f"{path.stem}_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_kickoff_parser_accepts_manager_names_and_legacy_aliases():
    kickoff = _module("format_kickoff_contract.py")
    actions = kickoff._parser()._option_string_actions

    assert actions["--manager-mode"].dest == "manager_mode"
    assert actions["--driver-mode"].dest == "legacy_manager_mode"
    assert actions["--event-manager"].dest == "event_manager"
    assert actions["--event-driver"].dest == "legacy_event_manager"
    assert actions["--manager-confirmable"].dest == "manager_confirmable"
    assert actions["--driver-confirmable"].dest == "legacy_manager_confirmable"
    assert (
        actions["--task-manager-confirmable"].dest
        == "task_manager_confirmable"
    )
    assert actions["--task-driver-confirmable"].dest == "legacy_task_manager_confirmable"
    assert kickoff._manager_owner("driver_confirmable") == "manager_confirmable"
    assert kickoff._resolve_alias("attached", "attached", "mode") == "attached"
    with pytest.raises(ValueError, match="inputs conflict"):
        kickoff._resolve_alias("attached", "unattended", "mode")


def test_runner_parser_accepts_legacy_mode_as_manager_alias():
    runner = _module("run_workflow.py")
    actions = runner._parser()._option_string_actions

    assert actions["--manager-mode"].dest == "manager_mode"
    assert actions["--driver-mode"].dest == "legacy_manager_mode"
    launched = []
    result = runner.run(
        [
            "--issue", "issue574", "--playbook", "direct", "--manager-mode", "attached",
            "--driver-mode", "unattended", "--fresh-facts", "{}",
        ],
        process_factory=lambda *args, **kwargs: launched.append(True),
    )
    assert result == 2
    assert launched == []
