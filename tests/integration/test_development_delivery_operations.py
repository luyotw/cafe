"""I2/I5/I7: real local integration, restart and changed destinations."""

import subprocess

import pytest

from cafe.delivery.contracts import approve_selection
from cafe.delivery.operations import execute_action
from tests.unit.test_development_delivery import authority, proposal


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def local_action(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "develop")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / ".gitignore").write_text(".cafe/\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "Baseline")
    target = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-b", "feature")
    destination = tmp_path / "destination"
    git(root, "worktree", "add", "--detach", str(destination), "HEAD")
    git(destination, "checkout", "develop")
    (root / "result.txt").write_text("Reviewed change")
    git(root, "add", ".")
    git(root, "commit", "-m", "Feature")
    p = proposal(
        mode="local",
        strategy="ff-only",
        pr_number=None,
        repository=str(root / ".git"),
        destination=str(destination),
        source_oid=git(root, "rev-parse", "HEAD"),
        target_oid=target,
        issue_repository="",
        proposals=[],
    )
    snapshot = approve_selection(p, authority(p, "integrate_only", ""))
    return root, destination, tmp_path / "issue", snapshot


def test_local_integration_reports_real_commit_and_restart_reuses_it(local_action, monkeypatch):
    root, dest, issue, snapshot = local_action
    result = execute_action(root, issue, snapshot, "integration", timeout=20)
    assert result["state"] == "succeeded"
    assert result["commit"] == git(dest, "rev-parse", "HEAD") == snapshot.proposal.source_oid
    assert (dest / "result.txt").read_text() == "Reviewed change"
    # A second successful attempt must observe the result rather than rerun merge.
    original = subprocess.Popen

    def no_merge(argv, *args, **kwargs):
        assert argv[:1] != ["gh"]
        assert "merge" not in argv or "merge-base" in argv
        return original(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", no_merge)
    assert (
        execute_action(root, issue, snapshot, "integration", timeout=20)["commit"]
        == result["commit"]
    )


def test_dirty_destination_is_blocked_without_changing_it(local_action):
    root, dest, issue, snapshot = local_action
    (dest / "unrelated.txt").write_text("Keep")
    result = execute_action(root, issue, snapshot, "integration", timeout=20)
    assert result["state"] == "blocked"
    assert git(dest, "rev-parse", "HEAD") == snapshot.proposal.target_oid
    assert (dest / "unrelated.txt").read_text() == "Keep"
