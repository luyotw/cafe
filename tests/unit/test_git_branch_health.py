"""Tests for Git branch health detection."""

from pathlib import Path
from unittest.mock import patch

import pytest

from cafe.core.git import GitOperations


class TestBranchHealth:
    def test_healthy_branch(self, tmp_path: Path) -> None:
        git = GitOperations(str(tmp_path))
        with patch.object(git, "get_current_branch", return_value="feature"), patch.object(
            git, "has_in_progress_operation", return_value=False
        ):
            health = git.get_branch_health()
        assert health.is_healthy is True
        assert health.branch_name == "feature"

    def test_detached_head_unhealthy(self, tmp_path: Path) -> None:
        git = GitOperations(str(tmp_path))
        with patch.object(git, "get_current_branch", return_value=""):
            health = git.get_branch_health()
        assert health.is_healthy is False
        assert health.reason == "detached_head"

    def test_in_progress_unhealthy(self, tmp_path: Path) -> None:
        git = GitOperations(str(tmp_path))
        with patch.object(git, "get_current_branch", return_value="feature"), patch.object(
            git, "has_in_progress_operation", return_value=True
        ):
            health = git.get_branch_health()
        assert health.is_healthy is False
        assert health.reason == "in_progress"
        assert health.branch_name == "feature"

    def test_has_in_progress_operation_detects_merge_head(self, tmp_path: Path) -> None:
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "MERGE_HEAD").write_text("abc", encoding="utf-8")
        git = GitOperations(str(tmp_path))
        with patch.object(git, "_git_dir_path", return_value=git_dir):
            assert git.has_in_progress_operation() is True
