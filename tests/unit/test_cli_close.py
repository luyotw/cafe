"""Tests for close CLI command."""

from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from cafe.core.git import GitError
from cafe.ui.cli import app

runner = CliRunner()


@pytest.mark.parametrize("invalid", [None, "traversal", "symlink", "wrong_checkout", "wrong_identity", "missing", "normal_close"])
def test_explicit_archive_on_different_branch_uses_only_current_registered_issue(tmp_path, monkeypatch, invalid):
    from cafe.core.git import GitOperations

    def git(root, *args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    root, worktree = tmp_path / "repo", tmp_path / "feature"
    root.mkdir()
    git(root, "init", "-b", "develop")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.com")
    (root / ".gitignore").write_text(".cafe/\n")
    git(root, "add", ".gitignore")
    git(root, "commit", "-m", "base")
    git(root, "worktree", "add", "-b", "issue474-date-fix", str(worktree))
    issue = worktree / ".cafe/issues/issue474"
    issue.mkdir(parents=True)
    config = {"issue_name": "issue474", "worktree_path": str(worktree), "base_branch": "develop"}
    if invalid == "wrong_checkout":
        config["worktree_path"] = str(root)
    if invalid == "wrong_identity":
        config["issue_name"] = "another"
    (issue / "issue.yaml").write_text(yaml.safe_dump(config))
    (issue / "evidence.txt").write_text("retain")
    selected = "../issue474" if invalid == "traversal" else "absent" if invalid == "missing" else "issue474"
    if invalid == "symlink":
        selected = "alias"
        (issue.parent / selected).symlink_to(issue, target_is_directory=True)
    monkeypatch.chdir(worktree)
    argv = ["close", "--issue", selected]
    if invalid != "normal_close":
        argv.append("--archive-only")
    with patch("cafe.ui.cli.GitOperations", GitOperations), patch("cafe.ui.cli.GitHubOps") as gh:
        result = runner.invoke(app, argv)
    gh.assert_not_called()
    assert worktree.is_dir() and git(worktree, "branch", "--show-current") == "issue474-date-fix"
    archived = list(home.glob(".cafe/projects/*/archived/issue474/evidence.txt"))
    if invalid:
        assert result.exit_code != 0, result.output
        assert issue.exists() and not archived
    else:
        assert result.exit_code == 0, result.output
        assert not issue.exists() and len(archived) == 1
        assert archived[0].read_text() == "retain"


@pytest.mark.parametrize("linked_base", [False, True])
def test_close_external_worktree_uses_the_checkout_owning_base(tmp_path, monkeypatch, linked_base):
    """真 Git 重現外部／巢狀 worktree，保留其他 checkout 並完成歸檔。"""
    from cafe.core.git import GitOperations

    def git(cwd, *args):
        return subprocess.check_output(["git", "-C", str(cwd), *args], text=True).strip()

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "test@example.test")
    git(repo, "config", "user.name", "Test")
    (repo / ".gitignore").write_text(".cafe/\n")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-m", "baseline")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")
    base = repo
    if linked_base:
        git(repo, "checkout", "-b", "other-work")
        base = tmp_path / "linked-base"
        git(repo, "worktree", "add", str(base), "main")
    feature = (base / ".cafe/worktrees/fix-close") if linked_base else tmp_path / "external"
    git(repo, "worktree", "add", "-b", "fix-close", str(feature))
    issue = feature / ".cafe/issues/fix-close"
    issue.mkdir(parents=True)
    (issue / "issue.yaml").write_text(yaml.safe_dump({
        "base_branch": "main", "feature_branch": "fix-close",
        "worktree_path": str(feature), "pr": {"auto_create": True},
    }))
    (issue / "evidence.txt").write_text("preserve review evidence")
    (feature / ".cafe/active_issue").write_text("fix-close\n")
    monkeypatch.chdir(feature)
    with patch("cafe.ui.cli.GitOperations", GitOperations), patch("cafe.ui.cli.GitHubOps") as gh:
        gh.return_value.get_pr_for_branch.return_value = {"state": "MERGED"}
        result = runner.invoke(app, ["close"])
    assert result.exit_code == 0, result.output
    assert Path.cwd() == base
    assert not feature.exists()
    assert git(base, "branch", "--show-current") == "main"
    assert "refs/heads/fix-close" not in git(repo, "show-ref")
    assert git(repo, "branch", "--show-current") == ("other-work" if linked_base else "main")
    archives = list(home.glob(".cafe/projects/*/archived/fix-close/evidence.txt"))
    assert len(archives) == 1
    assert archives[0].read_text() == "preserve review evidence"



# Real repositories exercise failure retention rather than mocking integration proof.
from tests.integration.test_close_cleanup_only import feature, integrate, git, assert_retained


@pytest.mark.parametrize("boundary", ["checkout", "remote", "archive"])
def test_cleanup_failure_keeps_live_issue_and_local_source(feature, monkeypatch, boundary):
    from cafe.core.git import GitOperations
    from cafe.ui.commands import lifecycle

    integrate(feature)
    root, worktree, issue, source, linked = feature
    def fail(*args, **kwargs):
        raise GitError("injected boundary failure")
    if boundary == "checkout":
        monkeypatch.setattr(GitOperations, "checkout_branch", fail)
    elif boundary == "remote":
        monkeypatch.setattr(GitOperations, "delete_cleanup_remote", fail)
    else:
        monkeypatch.setattr(lifecycle, "_backup_issue_directory", fail)
    result = runner.invoke(app, ["close"])
    assert result.exit_code != 0
    assert_retained(feature)
    assert Path.cwd() == worktree


def test_worktree_remove_failure_retains_live_issue_and_backup(feature, monkeypatch, tmp_path):
    from cafe.core.git import GitOperations
    root, worktree, issue, source, linked = feature
    if not linked:
        pytest.skip("normal checkout is retained by design")
    integrate(feature)
    def fail(*args, **kwargs):
        raise GitError("worktree removal failed")
    monkeypatch.setattr(GitOperations, "remove_worktree", fail)
    result = runner.invoke(app, ["close"])
    assert result.exit_code != 0
    assert issue.exists() and worktree.exists()
    assert git(root, "rev-parse", "feature") == source
    assert list((tmp_path / "home").glob(".cafe/projects/*/archived/feature/evidence"))


def test_archive_only_removes_only_matching_inventory_pointer(feature):
    root, worktree, issue, source, linked = feature
    if not linked:
        pytest.skip("normal checkout has no inventory pointer or separate target")
    pointer = root / ".cafe/issues/feature"
    pointer.mkdir(parents=True)
    (pointer / "issue.yaml").write_text(yaml.safe_dump({
        "issue_name": "feature", "worktree_path": str(worktree),
    }))
    result = runner.invoke(app, ["close", "--archive-only"])
    assert result.exit_code == 0, result.output
    assert not pointer.exists() and worktree.exists()
    assert git(root, "rev-parse", "feature") == source


def test_dirty_retained_checkout_stops_before_cleanup(feature):
    root, worktree, issue, source, linked = feature
    if not linked:
        pytest.skip("normal checkout has no inventory pointer or separate target")
    integrate(feature)
    (root / "unsaved").write_text("unrelated work")
    result = runner.invoke(app, ["close"])
    assert result.exit_code != 0
    assert_retained(feature)
    assert (root / "unsaved").read_text() == "unrelated work"
