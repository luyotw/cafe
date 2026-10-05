"""U4/U5/I3: real Git history and workspace evidence, without Manager state."""

from pathlib import Path
import subprocess

import pytest


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.org")
    (tmp_path / "allowed").write_text("initial")
    (tmp_path / "outside").write_text("initial")
    (tmp_path / ".gitignore").write_text("ignored\n.cafe/\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "baseline")
    return tmp_path, git(tmp_path, "rev-parse", "HEAD")


def test_restored_committed_path_remains_out_of_scope(repository):
    from cafe.core.file_scope import collect_changes, compare_scope

    root, baseline = repository
    (root / "outside").write_text("changed")
    git(root, "commit", "-am", "touch")
    (root / "outside").write_text("initial")
    git(root, "commit", "-am", "restore")
    assert not git(root, "status", "--porcelain")
    result = compare_scope(collect_changes(root, baseline), ["allowed"])
    assert not result.passed
    assert {f["path"] for f in result.findings} == {"outside"}


def test_worktree_staged_unstaged_untracked_deletion_and_rename(repository):
    from cafe.core.file_scope import collect_changes, compare_scope

    root, baseline = repository
    git(root, "mv", "outside", "destination")
    (root / "allowed").unlink()
    (root / "new file\nname").write_text("new")
    (root / "ignored").write_text("ignored")
    changes = collect_changes(root, baseline)
    paths = {r["path"] for r in changes.records} | {
        r["old_path"] for r in changes.records if "old_path" in r
    }
    assert {"outside", "destination", "allowed", "new file\nname"} <= paths
    assert "ignored" not in paths
    approval = ["allowed", "outside", "destination", "new file\nname"]
    assert compare_scope(changes, approval).passed
    assert approval == ["allowed", "outside", "destination", "new file\nname"]
    assert not compare_scope(changes, ["allowed", "destination", "new file\nname"]).passed


def test_missing_or_nonancestor_baseline_fails_closed(repository):
    from cafe.core.file_scope import collect_changes, compare_scope

    root, baseline = repository
    result = compare_scope(collect_changes(root, "f" * 40), ["allowed"])
    assert not result.passed
    assert result.findings[0]["reason"] == "evidence_unavailable"


def test_current_content_fingerprint_covers_untracked_and_approved_paths(repository):
    from cafe.core.file_scope import collect_changes, content_snapshot

    root, baseline = repository
    (root / "new").write_text("one")
    first = content_snapshot(root, collect_changes(root, baseline), ["allowed", "new"])
    (root / "new").write_text("two")
    assert content_snapshot(root, collect_changes(root, baseline), ["allowed", "new"]) != first


def test_malformed_evidence_cannot_pass():
    from cafe.core.file_scope import ChangeCollection, compare_scope

    result = compare_scope(ChangeCollection(records=({"path": "../escape"},)), ["allowed"])
    assert not result.passed
    assert result.findings[0]["reason"] == "malformed_evidence"
