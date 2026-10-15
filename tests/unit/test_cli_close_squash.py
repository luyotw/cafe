"""Removed lifecycle integration options are rejected before touching resources."""

import pytest
from typer.testing import CliRunner
from cafe.ui.cli import app
from tests.integration.test_close_cleanup_only import feature, assert_retained


@pytest.mark.parametrize("options", [
    ["--squash"], ["--message", "merge"], ["-m", "merge"],
    ["--squash", "--message", "merge"], ["--archive-only", "--squash"],
])
def test_removed_merge_options_do_not_mutate_source(feature, options):
    result = CliRunner().invoke(app, ["close", *options])
    assert result.exit_code != 0
    assert_retained(feature)
