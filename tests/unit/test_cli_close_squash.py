"""Removed lifecycle integration options are rejected before touching resources."""

import pytest
from typer.testing import CliRunner
from cafe.ui.cli import app
from tests.integration.test_close_cleanup_only import feature, assert_retained
from tests.unit.test_manager_closeout_contract import historical_close_contract


@pytest.mark.parametrize("options", [
    ["--squash"], ["--message", "merge"], ["-m", "merge"],
    ["--squash", "--message", "merge"], ["--archive-only", "--squash"],
])
def test_removed_merge_options_do_not_mutate_source(feature, options):
    result = CliRunner().invoke(app, ["close", *options])
    assert result.exit_code != 0
    assert_retained(feature)


def test_loaded_historical_argv_cannot_restore_removed_execution_options(feature, historical_close_contract):
    from cafe.manager._store import load_contract

    issue, path, content, _, argv = historical_close_contract
    recorded, _ = load_contract(issue)
    command = recorded["delivery_contract"]["closeout_plan"]["cleanup"][0]["argv"]
    assert command == argv
    result = CliRunner().invoke(app, command[1:])
    assert result.exit_code != 0
    assert_retained(feature)
    assert path.read_bytes() == content
