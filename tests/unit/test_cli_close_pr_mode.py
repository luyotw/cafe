"""Publication preference never lets close perform integration."""

from unittest.mock import patch
import pytest
import yaml
from typer.testing import CliRunner
from cafe.ui.cli import app
from tests.integration.test_close_cleanup_only import feature, integrate, git, assert_retained


@pytest.mark.parametrize("auto_create", [None, False, True])
@pytest.mark.parametrize("integrated", [False, True])
def test_close_requires_integration_in_every_publication_mode(feature, auto_create, integrated):
    root, worktree, issue, source, linked = feature
    config = yaml.safe_load((issue / "issue.yaml").read_text())
    if auto_create is None:
        config.pop("pr")
    else:
        config["pr"]["auto_create"] = auto_create
    (issue / "issue.yaml").write_text(yaml.safe_dump(config))
    if integrated:
        integrate(feature)
    before = git(root, "rev-parse", "develop")
    result = CliRunner().invoke(app, ["close"])
    assert (result.exit_code == 0) is integrated, result.output
    assert git(root, "rev-parse", "develop") == before
    if not integrated:
        assert_retained(feature)


@pytest.mark.parametrize("state,draft", [("OPEN", False), ("MERGED", True)])
def test_open_or_draft_pr_still_blocks_integrated_cleanup(feature, state, draft):
    integrate(feature)
    with patch("cafe.ui.cli.GitHubOps") as gh:
        gh.return_value.get_pr_for_branch.return_value = {"state": state, "isDraft": draft}
        result = CliRunner().invoke(app, ["close"])
    assert result.exit_code != 0
    assert_retained(feature)
