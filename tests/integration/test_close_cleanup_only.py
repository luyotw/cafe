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


@pytest.mark.parametrize(
    ("configured_base", "target"),
    [
        ("develop", "develop"),
        ("origin/develop", "develop"),
        ("refs/remotes/origin/develop", "develop"),
        ("refs/heads/develop", "develop"),
        ("origin/release/team", "release/team"),
        ("origin/local-base", "origin/local-base"),
    ],
)
def test_integrated_close_archives_and_removes_without_new_commits(
    feature, tmp_path, configured_base, target
):
    source = integrate(feature)
    root, worktree, issue, _, linked = feature
    if target != "develop":
        git(root, "branch", target, source)
        git(root, "push", "origin", f"refs/heads/{target}")
    config_path = issue / "issue.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["base_branch"] = configured_base
    config_path.write_text(yaml.safe_dump(config))
    result = CliRunner().invoke(app, ["close"])
    assert result.exit_code == 0, result.output
    assert git(root, "branch", "--show-current") == target
    assert git(root, "rev-parse", f"refs/heads/{target}") == source
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


def test_remote_tracking_cleanup_requires_the_configured_origin(feature):
    integrate(feature)
    root, _, issue, _, _ = feature
    config_path = issue / "issue.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["base_branch"] = "origin/develop"
    config_path.write_text(yaml.safe_dump(config))
    git(root, "remote", "rename", "origin", "unrelated")

    result = CliRunner().invoke(app, ["close"])

    assert result.exit_code != 0, result.output
    assert "cleanup base refers to an unconfigured origin remote" in result.output
    git(root, "remote", "rename", "unrelated", "origin")
    assert_retained(feature)


def test_different_push_endpoint_cannot_be_cleaned(feature, tmp_path):
    integrate(feature)
    root = feature[0]
    other = tmp_path / "other.git"
    git(root, "init", "--bare", str(other))
    git(root, "remote", "set-url", "--push", "origin", str(other))
    result = CliRunner().invoke(app, ["close"])
    assert result.exit_code != 0
    assert_retained(feature)


@pytest.mark.parametrize("integrated_endpoint", ["authorized", "rewritten"])
def test_cleanup_observes_and_deletes_only_the_pinned_endpoint(feature, tmp_path, integrated_endpoint):
    root, worktree, issue, source, linked = feature
    authorized = tmp_path / "remote.git"
    rewritten = tmp_path / "rewritten.git"
    baseline = git(root, "rev-parse", "develop")
    git(root, "clone", "--bare", str(authorized), str(rewritten))
    integrated = authorized if integrated_endpoint == "authorized" else rewritten
    git(integrated, "update-ref", "refs/heads/develop", source)
    git(root, "remote", "set-url", "origin", "cafe-original:")
    git(root, "config", f"url.{authorized}.insteadOf", "cafe-original:")
    git(root, "config", f"url.{rewritten}.insteadOf", str(authorized))
    assert git(root, "remote", "get-url", "origin") == str(authorized)

    result = CliRunner().invoke(app, ["close"])

    # Inspect repositories directly: resolving either URL again would hide drift.
    assert git(rewritten, "rev-parse", "feature") == source
    if integrated_endpoint == "authorized":
        assert result.exit_code == 0, result.output
        assert not git(authorized, "branch", "--list", "feature")
        assert not git(root, "branch", "--list", "feature")
        assert git(root, "rev-parse", "develop") == source
        assert not issue.exists()
        assert worktree.exists() is (not linked)
    else:
        assert result.exit_code != 0, result.output
        assert git(authorized, "rev-parse", "feature") == source
        assert git(authorized, "rev-parse", "develop") == baseline
        assert git(root, "rev-parse", "develop") == baseline
        assert worktree.is_dir() and issue.is_dir()
        assert git(root, "rev-parse", "feature") == source


def test_pinned_cleanup_accepts_an_already_absent_remote_source(feature, tmp_path):
    integrate(feature)
    root, _, issue, source, _ = feature
    authorized = tmp_path / "remote.git"
    rewritten = tmp_path / "rewritten.git"
    git(root, "clone", "--bare", str(authorized), str(rewritten))
    git(authorized, "update-ref", "-d", "refs/heads/feature")
    git(root, "remote", "set-url", "origin", "cafe-original:")
    git(root, "config", f"url.{authorized}.insteadOf", "cafe-original:")
    git(root, "config", f"url.{rewritten}.insteadOf", str(authorized))
    result = CliRunner().invoke(app, ["close"])
    assert result.exit_code == 0, result.output
    assert not issue.exists()
    assert not git(root, "branch", "--list", "feature")
    assert not git(authorized, "branch", "--list", "feature")
    assert git(rewritten, "rev-parse", "feature") == source


@pytest.mark.parametrize("proof", ["source", "pr_commit"])
@pytest.mark.parametrize("history", ["replacement", "graft", "environment_graft"])
def test_local_alternative_history_cannot_prove_unintegrated_cleanup(feature, monkeypatch, history, proof):
    root, worktree, _, source, _ = feature
    target = git(root, "rev-parse", "develop")
    claimed_commit = source
    if proof == "pr_commit":
        # A distinct same-repository PR commit also needs original ancestry.
        claimed_commit = git(root, "commit-tree", f"{source}^{{tree}}", "-p", target, "-m", "claimed integration")
        pr = {"state": "MERGED", "isCrossRepository": False, "headRefName": "feature",
              "headRefOid": source, "baseRefName": "develop", "mergeCommit": {"oid": claimed_commit}}
        real_run = subprocess.run

        def observe(argv, **kwargs):
            if argv[:3] == ["gh", "pr", "list"]:
                import json
                return subprocess.CompletedProcess(argv, 0, json.dumps([pr]), "")
            return real_run(argv, **kwargs)

        monkeypatch.setattr(subprocess, "run", observe)
    if history == "replacement":
        git(root, "replace", "--graft", target, claimed_commit)
    else:
        path = Path(git(worktree, "rev-parse", "--path-format=absolute", "--git-path", "info/grafts"))
        path.parent.mkdir(exist_ok=True)
        path.write_text(f"{target} {claimed_commit}\n")
        if history == "environment_graft":
            alternative = path.with_name("alternative-grafts")
            path.rename(alternative)
            monkeypatch.setenv("GIT_GRAFT_FILE", str(alternative))
    # Demonstrate that the local view would falsely authorize this deletion.
    git(worktree, "merge-base", "--is-ancestor", claimed_commit, target)
    result = CliRunner().invoke(app, ["close"])
    assert result.exit_code != 0, result.output
    assert_retained(feature)
    assert git(root, "rev-parse", "develop") == target


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


@pytest.mark.parametrize(
    "feature,scenario",
    [(True, name) for name in (
        "ff-only", "merge-commit", "pending", "leave", "dirty", "active", "stale-receipt",
        "moved-target", "archive-failure", "qualified-base", "wrong-base", "archive-retry",
        "archive-retry-new-evidence", "ref-retry", "ref-retry-moved", "last-observation",
        "late-writer", "inventory-retry", "inventory-retry-recreated",
        "inventory-retry-recreated-worktree", "cross-volume", "cross-volume-retry",
        "cross-volume-after-copy", "cross-volume-after-deletion",
        "cross-volume-final-comparison", "cross-volume-before-settlement", "cross-volume-legacy-retry",
        "branch-upstream", "late-checkout-switch", "checkout-during-remove",
    )] + [(False, name) for name in ("detach-retry", "cross-volume", "cross-volume-root", "cross-volume-root-retry")],
    indirect=["feature"],
)
def test_accepted_local_delivery_closes_exact_feature_with_diverged_remote(
    feature, tmp_path, monkeypatch, scenario,
):
    from contextlib import nullcontext
    import json

    from cafe.manager import ActivateConfirmedContract, activate_confirmed_contract
    from cafe.ui.commands import lifecycle
    from cafe.utils.file_lock import advancement_execution_lock
    from tests.integration import test_automatic_delivery as delivery
    from tests.unit.test_manager_closeout_contract import proposal as manager_proposal

    root, worktree, issue, source, linked = feature
    base = git(root, "rev-parse", "develop")
    git(root, "branch", "unrelated", base)
    destination = root if linked else tmp_path / "destination"
    if not linked:
        git(root, "worktree", "add", str(destination), "develop")
    git(destination, "checkout", "--detach", base)
    git(destination, "commit", "--allow-empty", "-m", "Remote-only history")
    git(destination, "push", "origin", "HEAD:develop")
    git(destination, "checkout", "develop")

    config = yaml.safe_load((issue / "issue.yaml").read_text())
    (issue / "issue.yaml").unlink()
    (issue / "evidence").unlink()
    issue.rmdir()
    ctx = delivery.setup((worktree, destination, tmp_path / "actions", None), tmp_path, issue_name=issue.name)
    config |= yaml.safe_load((issue / "issue.yaml").read_text())
    if scenario in {"qualified-base", "wrong-base"}:
        config["base_branch"] = "refs/heads/" + ("develop" if scenario == "qualified-base" else "unrelated")
    if scenario == "merge-commit":
        config[ctx[-1]]["strategy"] = "merge-commit"
    (issue / "issue.yaml").write_text(yaml.safe_dump(config))
    runtime, result = delivery.run(ctx)
    assert result.final_status_code == "BATON_NEED_PERMISSION", result
    proposal = manager_proposal()
    proposal["checkout"] = ({"kind": "worktree", "path": str(worktree)}
                            if linked else {"kind": "current_checkout"})
    proposal["closeout_contract"].update(
        choice="leave" if scenario == "leave" else "cleanup",
        plan={"cleanup": [{"argv": ["cafe", "close"]}]},
    )
    from datetime import datetime, timezone

    activate_confirmed_contract(ActivateConfirmedContract(
        issue_dir=issue, issue_name=issue.name, workflow_id=runtime.blackboard.workflow_id,
        proposal=proposal, confirmed_by="user", confirmed_at=datetime.now(timezone.utc),
    ))
    delivery.answer(ctx, runtime, "integrate_only")
    runtime, result = delivery.run(ctx)
    assert result.final_status_code == "BATON_CONFIRM_OUTPUT", result
    if scenario != "pending":
        delivery.answer(ctx, runtime, "confirm")
    target = git(root, "rev-parse", "develop")
    if scenario == "stale-receipt":
        receipt = next((issue / "delivery").glob("*/integration.json"))
        receipt.write_text(json.dumps({**json.loads(receipt.read_text()), "commit": base}))
    if scenario == "moved-target":
        git(root, "commit", "--allow-empty", "-m", "Moved after acceptance")
    if scenario == "dirty":
        (worktree / "dirty").write_text("keep")
    if scenario == "archive-failure":
        def fail_archive(*args, **kwargs):
            raise OSError("Archive storage unavailable")
        monkeypatch.setattr(lifecycle, "_backup_issue_directory", fail_archive)

    if scenario.startswith(("archive-retry", "ref-retry", "detach-retry", "inventory-retry")):
        from cafe.core.git import GitOperations, GitError
        from cafe.core.active_issue import read_marker, write_marker

        inventory = root / ".cafe" / "issues" / issue.name
        if scenario.startswith("inventory"):
            inventory.mkdir(parents=True, exist_ok=True)
            (inventory / "issue.yaml").write_text(yaml.safe_dump({
                "issue_name": issue.name, "worktree_path": str(worktree),
            }))
            write_marker(destination / ".cafe", issue.name)

        def unavailable(*args, **kwargs):
            raise GitError("Git operation temporarily unavailable")

        with monkeypatch.context() as failure:
            if scenario.startswith("inventory"):
                failure.setattr(lifecycle, "_remove_worktree_inventory_pointer", unavailable)
            else:
                method = "remove_worktree" if scenario.startswith("archive") else "delete_cleanup_branch"
                failure.setattr(GitOperations, method, unavailable)
            result = CliRunner().invoke(app, ["close"])
        assert result.exit_code != 0
        if scenario.startswith("inventory"):
            assert git(root, "branch", "--list", "feature") == ""
            assert inventory.is_dir() and read_marker(destination / ".cafe") == issue.name
            if "recreated" in scenario:
                git(root, "branch", "feature", source)
                git(root, "branch", "--set-upstream-to=origin/develop", "feature")
            if scenario.endswith("recreated-worktree"):
                git(root, "worktree", "add", str(worktree), "feature")
        else:
            assert git(root, "rev-parse", "feature") == source
        if scenario == "archive-retry-new-evidence":
            (issue / "recovery-note.md").write_text("preserve the only live copy")
        if scenario == "ref-retry-moved":
            git(root, "update-ref", "refs/heads/feature", base)
    archive = lifecycle._get_issue_archive_path(issue.name)
    transferred = []
    if scenario.startswith("cross-volume"):
        import errno

        rename = Path.rename

        def split_volume(path, target):
            outside_volume = (not Path(target).is_relative_to(worktree)
                              if scenario.startswith("cross-volume-root")
                              else Path(target).is_relative_to(archive))
            if path == issue and outside_volume:
                raise OSError(errno.EXDEV, "cross-device rename")
            relocated = rename(path, target)
            if path == issue:
                transferred.append(relocated)
            return relocated

        monkeypatch.setattr(Path, "rename", split_volume)
    if scenario == "last-observation":
        from cafe.delivery import local_cleanup

        observe = local_cleanup.observe_local_cleanup

        def write_after_archive(*args, **kwargs):
            result = observe(*args, **kwargs)
            if archive.is_dir():
                (issue / "last-run-note.md").write_text("preserve the only live copy")
            return result

        monkeypatch.setattr(local_cleanup, "observe_local_cleanup", write_after_archive)
    if scenario == "late-checkout-switch":
        from cafe.delivery import local_cleanup

        observe = local_cleanup.observe_local_cleanup

        def switch_after_observation(*args, **kwargs):
            result = observe(*args, **kwargs)
            if not issue.exists():
                git(worktree, "checkout", "unrelated")
            return result

        monkeypatch.setattr(local_cleanup, "observe_local_cleanup", switch_after_observation)
    if scenario == "checkout-during-remove":
        from cafe.core.git import GitOperations

        native = GitOperations.run_git

        def attempt_at_removal(backend, *args):
            if args[:2] == ("worktree", "remove"):
                for mutation in (
                    ["checkout", "unrelated"],
                    ["symbolic-ref", "HEAD", "refs/heads/unrelated"],
                    ["update-ref", "refs/heads/feature", base, source],
                ):
                    switched = subprocess.run(
                        ["git", "-C", str(worktree), *mutation], capture_output=True,
                    )
                    assert switched.returncode != 0
            return native(backend, *args)

        monkeypatch.setattr(GitOperations, "run_git", attempt_at_removal)
    if scenario in {"late-writer", "cross-volume", "cross-volume-retry"} and linked:
        import os
        from cafe.core.git import GitOperations

        remove = GitOperations.remove_worktree
        issue_fd = os.open(issue, os.O_RDONLY)

        def write_through_open_directory(backend, path):
            try:
                note_fd = os.open("last-run-note.md", os.O_CREAT | os.O_WRONLY, dir_fd=issue_fd)
                with os.fdopen(note_fd, "w") as note:
                    note.write("preserve the only live copy")
            finally:
                os.close(issue_fd)
            issue.mkdir(parents=True, exist_ok=True)
            (issue / "last-path-note.md").write_text("preserve the path-based write")
            return remove(backend, path)

        monkeypatch.setattr(GitOperations, "remove_worktree", write_through_open_directory)
    remote = tmp_path / "remote.git"
    before = git(remote, "show-ref")
    attempts = []
    run = subprocess.run

    def capture_remote(argv, **kwargs):
        if argv[0] == "gh" or (argv[0] == "git" and "push" in argv):
            attempts.append(argv)
        return run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", capture_remote)
    before_delete_retry = {"cross-volume-retry", "cross-volume-root-retry", "cross-volume-legacy-retry"}
    post_copy_cases = before_delete_retry | {"cross-volume-after-copy", "cross-volume-after-deletion"}
    final_write_cases = {"cross-volume-final-comparison", "cross-volume-before-settlement"}
    if scenario in final_write_cases:
        import os
        from cafe.core import packet_io
        from cafe.core.git import GitOperations

        published_file = archive / ".local-close-evidence" / "comparison-evidence.md"
        (issue / published_file.name).write_text("already copied\n")
        directory_fd = os.open(issue, os.O_RDONLY)
        delete = GitOperations.delete_cleanup_branch
        open_file = Path.open
        atomic_write = packet_io.atomic_write_bytes
        deleted = False
        written = False

        def observe_delete(backend, *args, **kwargs):
            nonlocal deleted
            delete(backend, *args, **kwargs)
            deleted = True

        def write_late_evidence():
            nonlocal written
            note_fd = os.open("completion-note.md", os.O_CREAT | os.O_WRONLY, dir_fd=directory_fd)
            with os.fdopen(note_fd, "w") as note:
                note.write("written during archive finalization\n")
            written = True

        def write_during_comparison(path, *args, **kwargs):
            if deleted and not written and path == published_file and args == ("rb",):
                write_late_evidence()
            return open_file(path, *args, **kwargs)

        def write_during_journal_publish(path, content, *, before_replace=None):
            if not (deleted and not written and path == archive / ".local-close.json"
                    and b'"archive_sha256"' in content):
                return atomic_write(path, content, before_replace=before_replace)

            def arrive_then_verify():
                write_late_evidence()
                before_replace()

            return atomic_write(path, content, before_replace=arrive_then_verify)

        try:
            with monkeypatch.context() as arrival:
                arrival.setattr(GitOperations, "delete_cleanup_branch", observe_delete)
                if scenario == "cross-volume-final-comparison":
                    arrival.setattr(Path, "open", write_during_comparison)
                else:
                    arrival.setattr(packet_io, "atomic_write_bytes", write_during_journal_publish)
                result = CliRunner().invoke(app, ["close"])
        finally:
            os.close(directory_fd)
        assert written
    if scenario in post_copy_cases:
        import os
        from cafe.core.git import GitOperations, GitError

        delete = GitOperations.delete_cleanup_branch
        (issue / "changing-evidence.md").write_text("before\n")
        with os.fdopen(os.open(issue / "changing-evidence.md", os.O_RDWR), "r+") as evidence_handle:
            post_copy_fd = os.open(issue, os.O_RDONLY)

            def write_after_publication(backend, *args, **kwargs):
                assert (archive / ".local-close-evidence" / "changing-evidence.md").read_text() == "before\n"
                if scenario not in before_delete_retry:
                    delete(backend, *args, **kwargs)
                note_fd = os.open("post-copy-note.md", os.O_CREAT | os.O_WRONLY, dir_fd=post_copy_fd)
                with os.fdopen(note_fd, "w") as note:
                    note.write("arrived after publication\n")
                evidence_handle.write("after\n")
                evidence_handle.truncate()
                evidence_handle.flush()
                if scenario != "cross-volume-after-copy":
                    raise GitError("interrupted at ref deletion")

            try:
                with monkeypatch.context() as failure:
                    failure.setattr(GitOperations, "delete_cleanup_branch", write_after_publication)
                    interrupted = CliRunner().invoke(app, ["close"])
            finally:
                os.close(post_copy_fd)
        if scenario == "cross-volume-after-copy":
            result = interrupted
        else:
            assert interrupted.exit_code != 0
            assert worktree.exists() == (not linked)
            if not linked:
                assert git(worktree, "branch", "--show-current") == ""
            if scenario in before_delete_retry:
                assert git(root, "rev-parse", "feature") == source
            else:
                assert git(root, "branch", "--list", "feature") == ""
        if scenario in {"cross-volume-retry", "cross-volume-legacy-retry"}:
            if scenario == "cross-volume-legacy-retry":
                # The pre-flag close producer recorded only this checkout binding.
                journal = archive / ".local-close.json"
                record = json.loads(journal.read_text())
                journal.write_text(json.dumps({key: record[key] for key in (
                    "checkout", "destination", "linked", "snapshot",
                )}))
            # Simulate disconnecting the source volume after the first copy.
            # The old canonical snapshot cannot settle a close with unseen writes.
            staged, = transferred
            offline = staged.rename(tmp_path / "temporarily-offline")
            try:
                monkeypatch.chdir(root)
                unavailable = CliRunner().invoke(app, ["close", "--issue", "feature"])
                assert unavailable.exit_code != 0
                assert git(root, "rev-parse", "feature") == source
            finally:
                offline.rename(staged)
    lock = advancement_execution_lock(issue) if scenario == "active" else nullcontext()
    with lock:
        if scenario.startswith(("ref-retry", "inventory-retry")) or scenario in {
            "cross-volume-retry", "cross-volume-legacy-retry", "cross-volume-after-deletion",
        }:
            monkeypatch.chdir(root)
            result = CliRunner().invoke(app, ["close", "--issue", "feature"])
        elif scenario != "cross-volume-after-copy" and scenario not in final_write_cases:
            result = CliRunner().invoke(app, ["close"])
    if scenario in {"cross-volume-retry", "cross-volume-legacy-retry"} and result.exit_code == 0:
        offline = staged.rename(tmp_path / "temporarily-offline")
        try:
            settled = CliRunner().invoke(app, ["close", "--issue", "feature"])
            assert settled.exit_code == 0, settled.output
            published = archive / ".local-close-evidence" / "post-copy-note.md"
            content = published.read_bytes()
            published.write_bytes(content + b"changed archive")
            try:
                assert CliRunner().invoke(app, ["close", "--issue", "feature"]).exit_code != 0
            finally:
                published.write_bytes(content)
        finally:
            offline.rename(staged)
    assert git(remote, "show-ref") == before
    assert attempts == []
    assert git(root, "rev-parse", "unrelated") == base
    if scenario in {"ff-only", "merge-commit", "qualified-base", "archive-retry", "ref-retry",
                    "detach-retry", "inventory-retry", "inventory-retry-recreated",
                    "inventory-retry-recreated-worktree", "late-writer", "cross-volume",
                    "cross-volume-retry", "cross-volume-root", "cross-volume-root-retry", "cross-volume-after-copy",
                    "cross-volume-after-deletion", "cross-volume-final-comparison", "cross-volume-before-settlement",
                    "cross-volume-legacy-retry",
                    "branch-upstream", "checkout-during-remove"}:
        assert result.exit_code == 0, result.output + repr(result.exception)
        assert archive.is_dir()
        assert worktree.exists() == (not linked or scenario.endswith("recreated-worktree"))
        if "recreated" in scenario:
            assert git(root, "rev-parse", "feature") == source
            assert git(root, "for-each-ref", "--format=%(upstream:short)", "refs/heads/feature") == "origin/develop"
        else:
            assert git(root, "branch", "--list", "feature") == ""
        assert git(root, "rev-parse", "develop") == target
        assert (target == source) == (scenario != "merge-commit")
        if scenario in {"late-writer", "cross-volume", "cross-volume-retry"} and linked:
            assert any(p.read_text() == "preserve the only live copy"
                       for p in archive.rglob("last-run-note.md"))
            assert any(p.read_text() == "preserve the path-based write"
                       for p in archive.rglob("last-path-note.md"))
        if scenario in post_copy_cases:
            published = archive / ".local-close-evidence"
            assert (published / "post-copy-note.md").read_text() == "arrived after publication\n"
            assert (published / "changing-evidence.md").read_text() == "after\n"
        if scenario in final_write_cases:
            note = archive / ".local-close-evidence" / "completion-note.md"
            assert note.read_text() == "written during archive finalization\n"
        if scenario.endswith("recreated-worktree"):
            assert git(worktree, "branch", "--show-current") == "feature"
            assert inventory.is_dir() and read_marker(destination / ".cafe") == issue.name
        elif scenario.startswith("inventory"):
            assert not inventory.exists() and read_marker(destination / ".cafe") is None
        if not linked:
            assert git(worktree, "branch", "--show-current") == ""
            assert (worktree / "change").read_text() == "delivered\n"
            assert not issue.exists()
        if scenario == "branch-upstream":
            git(root, "branch", "feature", base)
            assert git(root, "for-each-ref", "--format=%(upstream:short)", "refs/heads/feature") == ""
    elif scenario == "late-checkout-switch":
        assert result.exit_code != 0
        assert worktree.is_dir() and git(worktree, "branch", "--show-current") == "unrelated"
        assert git(root, "rev-parse", "feature") == source
        assert (issue / "issue.yaml").is_file()
    elif scenario == "archive-retry-new-evidence":
        assert result.exit_code != 0
        assert archive.is_dir() and (issue / "recovery-note.md").is_file()
        assert git(root, "rev-parse", "feature") == source
    elif scenario == "ref-retry-moved":
        assert result.exit_code != 0
        assert archive.is_dir() and not worktree.exists()
        assert git(root, "rev-parse", "feature") == base
    elif scenario == "last-observation":
        assert (archive / "last-run-note.md").is_file() or (
            result.exit_code != 0 and (issue / "last-run-note.md").is_file()
        )
    else:
        assert result.exit_code != 0
        assert not archive.exists() and worktree.exists()
        assert git(root, "rev-parse", "feature") == source
