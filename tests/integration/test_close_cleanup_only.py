"""Public close journeys prove integration before removing exact feature resources."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from cafe.ui.cli import app


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture(params=[False, True], ids=["checkout", "worktree"])
def feature(tmp_path, monkeypatch, request):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    git(root, "init", "-b", "develop")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.test")
    (root / ".gitignore").write_text(".cafe/\n")
    git(root, "add", ".gitignore")
    git(root, "commit", "-m", "baseline")
    remote = tmp_path / "remote.git"
    git(root, "init", "--bare", str(remote))
    git(root, "remote", "add", "origin", str(remote))
    git(root, "push", "origin", "develop")
    worktree = tmp_path / "feature" if request.param else root
    if request.param:
        git(root, "worktree", "add", "-b", "feature", str(worktree))
    else:
        git(root, "checkout", "-b", "feature")
    (worktree / "change").write_text("delivered\n")
    git(worktree, "add", "change")
    git(worktree, "commit", "-m", "feature")
    source = git(worktree, "rev-parse", "HEAD")
    git(worktree, "push", "-u", "origin", "feature")
    issue = worktree / ".cafe/issues/feature"
    issue.mkdir(parents=True)
    config = {"base_branch": "develop", "feature_branch": "feature",
              "pr": {"auto_create": False}}
    if request.param:
        config["worktree_path"] = str(worktree)
    (issue / "issue.yaml").write_text(yaml.safe_dump(config))
    (issue / "evidence").write_text("retain delivery evidence")
    monkeypatch.chdir(worktree)
    real_run = subprocess.run
    def without_gh(argv, **kwargs):
        if argv[0] == "gh":
            raise FileNotFoundError("gh executable is not installed")
        return real_run(argv, **kwargs)
    monkeypatch.setattr(subprocess, "run", without_gh)
    yield root, worktree, issue, source, request.param


def integrate(feature):
    root, worktree, _, source, linked = feature
    if not linked:
        git(root, "checkout", "develop")
    git(root, "merge", "--ff-only", "feature")
    git(root, "push", "origin", "develop")
    if not linked:
        git(root, "checkout", "feature")
    return source


def assert_retained(feature):
    root, worktree, issue, source, _ = feature
    assert worktree.exists() and issue.is_dir()
    assert git(root, "rev-parse", "feature") == source
    assert git(root, "ls-remote", "--heads", "origin", "refs/heads/feature")


def test_unintegrated_close_never_merges_and_retains_source(feature):
    root, _, _, _, _ = feature
    before = git(root, "rev-parse", "develop")
    result = CliRunner().invoke(app, ["close"])
    assert result.exit_code != 0
    assert git(root, "rev-parse", "develop") == before
    assert_retained(feature)


def test_integrated_close_archives_and_removes_without_new_commits(feature, tmp_path):
    source = integrate(feature)
    root, worktree, issue, _, linked = feature
    result = CliRunner().invoke(app, ["close"])
    assert result.exit_code == 0, result.output
    assert git(root, "rev-parse", "develop") == source
    assert git(root, "branch", "--list", "feature") == ""
    assert not git(root, "ls-remote", "--heads", "origin", "refs/heads/feature")
    assert not issue.exists()
    assert worktree.exists() is (not linked)
    archives = list((tmp_path / "home").glob(".cafe/projects/*/archived/feature/evidence"))
    assert len(archives) == 1 and archives[0].read_text() == "retain delivery evidence"


@pytest.mark.parametrize("failure", ["dirty", "remote_tip", "source_identity", "target_unintegrated"])
def test_failed_preflight_preserves_every_resource(feature, failure):
    integrate(feature)
    root, worktree, issue, _, linked = feature
    if failure == "dirty":
        (worktree / "unsaved").write_text("retain me")
    elif failure == "remote_tip":
        git(root, "push", "origin", "develop~1:refs/heads/feature", "--force")
    elif failure == "source_identity":
        config = yaml.safe_load((issue / "issue.yaml").read_text())
        config["feature_branch"] = "another-source"
        (issue / "issue.yaml").write_text(yaml.safe_dump(config))
    else:
        git(root, "push", "origin", "develop~1:refs/heads/develop", "--force")
    result = CliRunner().invoke(app, ["close"])
    assert result.exit_code != 0, result.output
    assert_retained(feature)


def test_archive_only_retains_unintegrated_dirty_source(feature):
    root, worktree, issue, source, _ = feature
    (worktree / "unsaved").write_text("retain me")
    result = CliRunner().invoke(app, ["close", "--archive-only"])
    assert result.exit_code == 0, result.output
    assert not issue.exists() and worktree.exists()
    assert git(root, "rev-parse", "feature") == source
    assert (worktree / "unsaved").read_text() == "retain me"


def test_arbitrary_merged_pr_cannot_authorize_deleting_another_source(feature):
    with patch("cafe.ui.cli.GitHubOps") as gh:
        gh.return_value.get_pr_for_branch.return_value = {"state": "MERGED"}
        result = CliRunner().invoke(app, ["close"])
    assert result.exit_code != 0
    assert_retained(feature)


@pytest.mark.parametrize("mismatch", [None, "head", "base", "commit", "fork"])
def test_squash_cleanup_requires_exact_pr_identity_and_reachable_merge(feature, monkeypatch, mismatch):
    from cafe.ui.commands import lifecycle

    root, worktree, _, source, linked = feature
    if not linked:
        git(root, "checkout", "develop")
    git(root, "merge", "--squash", "feature")
    git(root, "commit", "-m", "squash delivery")
    target = git(root, "rev-parse", "HEAD")
    git(root, "push", "origin", "develop")
    if not linked:
        git(root, "checkout", "feature")
    pr = {"state": "MERGED", "isCrossRepository": False, "headRefName": "feature",
          "headRefOid": source, "baseRefName": "develop", "mergeCommit": {"oid": target}}
    if mismatch == "head":
        pr["headRefOid"] = target
    elif mismatch == "base":
        pr["baseRefName"] = "other"
    elif mismatch == "commit":
        pr["mergeCommit"] = {"oid": source}
    elif mismatch == "fork":
        pr["isCrossRepository"] = True
    real_run = subprocess.run
    queried_endpoints = []

    def observe(argv, **kwargs):
        if argv[:3] == ["gh", "pr", "list"]:
            import json
            if "--repo" in argv:
                queried_endpoints.append(argv[argv.index("--repo") + 1])
            return subprocess.CompletedProcess(argv, 0, json.dumps([pr]), "")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(lifecycle.subprocess, "run", observe)
    result = CliRunner().invoke(app, ["close"])
    assert queried_endpoints == [str(root.parent / "remote.git")]
    if mismatch:
        assert result.exit_code != 0
        assert_retained(feature)
    else:
        assert result.exit_code == 0, result.output
        assert git(root, "rev-parse", "develop") == target
        assert not git(root, "branch", "--list", "feature")


def test_archive_failure_preserves_source_before_remote_deletion(feature, monkeypatch):
    from cafe.ui.commands import lifecycle

    integrate(feature)
    def fail(*args, **kwargs):
        raise OSError("archive storage unavailable")
    monkeypatch.setattr(lifecycle, "_backup_issue_directory", fail)
    result = CliRunner().invoke(app, ["close"])
    assert result.exit_code != 0
    assert_retained(feature)


def test_source_advancing_during_cleanup_cannot_be_deleted(feature, monkeypatch):
    from cafe.core.git import GitOperations

    integrate(feature)
    root, worktree, issue, source, linked = feature
    original = GitOperations.delete_cleanup_branch

    def changed(self, snapshot):
        # Simulate another actor moving the source between proof and ref deletion.
        git(root, "update-ref", "refs/heads/feature", source + "~1")
        return original(self, snapshot)

    monkeypatch.setattr(GitOperations, "delete_cleanup_branch", changed)
    result = CliRunner().invoke(app, ["close"])
    assert result.exit_code != 0
    assert git(root, "rev-parse", "feature") == git(root, "rev-parse", source + "~1")


def test_remote_advancing_during_cleanup_preserves_live_source(feature, monkeypatch):
    from cafe.core.git import GitOperations

    integrate(feature)
    root = feature[0]
    original = GitOperations.delete_cleanup_remote

    def changed(self, snapshot):
        git(root, "push", "origin", "develop~1:refs/heads/feature", "--force")
        return original(self, snapshot)

    monkeypatch.setattr(GitOperations, "delete_cleanup_remote", changed)
    result = CliRunner().invoke(app, ["close"])
    assert result.exit_code != 0
    assert_retained(feature)


def test_offline_local_integration_can_be_cleaned_without_origin(feature):
    integrate(feature)
    root = feature[0]
    git(root, "remote", "remove", "origin")
    result = CliRunner().invoke(app, ["close"])
    assert result.exit_code == 0, result.output
    assert not git(root, "branch", "--list", "feature")


def test_different_push_endpoint_cannot_be_cleaned(feature, tmp_path):
    integrate(feature)
    root = feature[0]
    other = tmp_path / "other.git"
    git(root, "init", "--bare", str(other))
    git(root, "remote", "set-url", "--push", "origin", str(other))
    result = CliRunner().invoke(app, ["close"])
    assert result.exit_code != 0
    assert_retained(feature)


def test_pending_git_operation_blocks_cleanup_even_with_clean_index(feature):
    integrate(feature)
    _, worktree, _, source, _ = feature
    marker = Path(git(worktree, "rev-parse", "--path-format=absolute", "--git-path", "MERGE_HEAD"))
    marker.write_text(source + "\n")
    result = CliRunner().invoke(app, ["close"])
    assert result.exit_code != 0
    assert_retained(feature)


def test_existing_archive_is_never_overwritten_by_full_cleanup(feature):
    from cafe.ui.commands import lifecycle

    integrate(feature)
    archive = lifecycle._get_issue_archive_path("feature")
    archive.mkdir(parents=True)
    (archive / "previous-evidence").write_text("preserve older work")
    result = CliRunner().invoke(app, ["close"])
    assert result.exit_code != 0
    assert_retained(feature)
    assert (archive / "previous-evidence").read_text() == "preserve older work"


@pytest.mark.parametrize("dirty_destination", [False, True])
def test_cleanup_retains_the_checkout_already_owning_the_target(feature, tmp_path, dirty_destination):
    root, source_checkout, issue, source, linked = feature
    integrate(feature)
    destination = root if linked else tmp_path / "delivery-destination"
    if not linked:
        git(root, "worktree", "add", str(destination), "develop")
    marker = source_checkout / ".cafe/active_issue"
    marker.write_text("feature\n")
    if dirty_destination:
        (destination / "unsaved").write_text("retain destination work")
    result = CliRunner().invoke(app, ["close"])
    if dirty_destination:
        assert result.exit_code != 0
        assert_retained(feature)
        assert marker.read_text() == "feature\n"
        assert (destination / "unsaved").read_text() == "retain destination work"
    else:
        assert result.exit_code == 0, result.output
        assert destination.is_dir()
        assert git(destination, "branch", "--show-current") == "develop"
        assert git(destination, "rev-parse", "HEAD") == source
        assert not issue.exists() and not marker.exists()
        if not linked:
            assert source_checkout.is_dir()
            assert git(source_checkout, "branch", "--show-current") == ""
            assert git(source_checkout, "rev-parse", "HEAD") == source
            assert (source_checkout / "change").read_text() == "delivered\n"
