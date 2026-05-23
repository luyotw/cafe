"""Tests for active issue resolution with Git health and fallback marker."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cafe.core.git import BranchHealth, GitError
from cafe.core.issue_resolution import ActiveIssueResolutionError, resolve_active_issue
from cafe.core import active_issue


@pytest.fixture
def cafe_dir(tmp_path: Path) -> Path:
    root = tmp_path / ".cafe"
    (root / "issues").mkdir(parents=True)
    return root


def _prepared(cafe_dir: Path, name: str) -> None:
    (cafe_dir / "issues" / name).mkdir(parents=True)


class TestResolveActiveIssueHealthy:
    def test_healthy_branch_with_prepared_issue_syncs_marker(self, cafe_dir: Path) -> None:
        _prepared(cafe_dir, "feature-a")
        git = MagicMock()
        git.get_branch_health.return_value = BranchHealth(is_healthy=True, branch_name="feature-a")

        resolved = resolve_active_issue(cafe_dir=cafe_dir, git_ops=git)

        assert resolved.issue_name == "feature-a"
        assert resolved.source == "branch"
        assert active_issue.read_marker(cafe_dir) == "feature-a"

    def test_healthy_branch_disagrees_with_marker_corrects_marker(self, cafe_dir: Path) -> None:
        _prepared(cafe_dir, "feature-b")
        active_issue.write_marker(cafe_dir, "stale-issue")
        git = MagicMock()
        git.get_branch_health.return_value = BranchHealth(is_healthy=True, branch_name="feature-b")

        resolved = resolve_active_issue(cafe_dir=cafe_dir, git_ops=git)

        assert resolved.issue_name == "feature-b"
        assert active_issue.read_marker(cafe_dir) == "feature-b"

    def test_healthy_branch_without_prepared_issue_errors(self, cafe_dir: Path) -> None:
        git = MagicMock()
        git.get_branch_health.return_value = BranchHealth(is_healthy=True, branch_name="orphan")

        with pytest.raises(ActiveIssueResolutionError) as exc:
            resolve_active_issue(cafe_dir=cafe_dir, git_ops=git)

        assert "orphan" in exc.value.message


class TestResolveActiveIssueUnhealthy:
    def test_detached_head_uses_valid_marker(self, cafe_dir: Path) -> None:
        _prepared(cafe_dir, "saved-issue")
        active_issue.write_marker(cafe_dir, "saved-issue")
        git = MagicMock()
        git.get_branch_health.return_value = BranchHealth(
            is_healthy=False,
            reason="detached_head",
        )

        resolved = resolve_active_issue(cafe_dir=cafe_dir, git_ops=git)

        assert resolved.issue_name == "saved-issue"
        assert resolved.source == "fallback"
        assert active_issue.read_marker(cafe_dir) == "saved-issue"

    def test_mid_rebase_uses_valid_marker_without_overwrite(self, cafe_dir: Path) -> None:
        _prepared(cafe_dir, "saved-issue")
        active_issue.write_marker(cafe_dir, "saved-issue")
        git = MagicMock()
        git.get_branch_health.return_value = BranchHealth(
            is_healthy=False,
            branch_name="feature-x",
            reason="in_progress",
        )

        resolved = resolve_active_issue(cafe_dir=cafe_dir, git_ops=git)

        assert resolved.issue_name == "saved-issue"
        assert active_issue.read_marker(cafe_dir) == "saved-issue"

    def test_git_error_uses_valid_marker(self, cafe_dir: Path) -> None:
        _prepared(cafe_dir, "saved-issue")
        active_issue.write_marker(cafe_dir, "saved-issue")
        git = MagicMock()
        git.get_branch_health.return_value = BranchHealth(is_healthy=False, reason="git_error")

        resolved = resolve_active_issue(cafe_dir=cafe_dir, git_ops=git)
        assert resolved.issue_name == "saved-issue"

    def test_unhealthy_missing_marker_errors(self, cafe_dir: Path) -> None:
        git = MagicMock()
        git.get_branch_health.return_value = BranchHealth(is_healthy=False, reason="git_error")

        with pytest.raises(ActiveIssueResolutionError):
            resolve_active_issue(cafe_dir=cafe_dir, git_ops=git)

    def test_unhealthy_empty_marker_errors(self, cafe_dir: Path) -> None:
        active_issue.marker_path(cafe_dir).write_text("\n", encoding="utf-8")
        git = MagicMock()
        git.get_branch_health.return_value = BranchHealth(is_healthy=False, reason="detached_head")

        with pytest.raises(ActiveIssueResolutionError):
            resolve_active_issue(cafe_dir=cafe_dir, git_ops=git)

    def test_unhealthy_stale_marker_errors(self, cafe_dir: Path) -> None:
        active_issue.write_marker(cafe_dir, "gone-issue")
        git = MagicMock()
        git.get_branch_health.return_value = BranchHealth(is_healthy=False, reason="detached_head")

        with pytest.raises(ActiveIssueResolutionError) as exc:
            resolve_active_issue(cafe_dir=cafe_dir, git_ops=git)

        assert "gone-issue" in exc.value.message


class TestResolveExplicitIssue:
    def test_explicit_issue_bypasses_git(self, cafe_dir: Path) -> None:
        git = MagicMock()
        git.get_branch_health.side_effect = GitError("should not be called")

        resolved = resolve_active_issue(
            cafe_dir=cafe_dir,
            git_ops=git,
            explicit_issue="manual-issue",
        )

        assert resolved.issue_name == "manual-issue"
        assert resolved.source == "explicit"
        git.get_branch_health.assert_not_called()
