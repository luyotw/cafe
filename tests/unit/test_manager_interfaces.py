"""Canonical Manager command names and explicit Driver aliases."""

import importlib.util
from pathlib import Path

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

    assert actions["--manager-mode"].dest == actions["--driver-mode"].dest == "manager_mode"
    assert actions["--event-manager"].dest == actions["--event-driver"].dest == "event_manager"
    assert actions["--manager-confirmable"].dest == actions["--driver-confirmable"].dest
    assert (
        actions["--task-manager-confirmable"].dest
        == actions["--task-driver-confirmable"].dest
    )
    assert kickoff._manager_owner("driver_confirmable") == "manager_confirmable"


def test_runner_parser_accepts_legacy_mode_as_manager_alias():
    runner = _module("run_workflow.py")
    actions = runner._parser()._option_string_actions

    assert actions["--manager-mode"].dest == actions["--driver-mode"].dest == "manager_mode"
