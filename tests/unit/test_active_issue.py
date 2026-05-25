"""Tests for active issue runtime marker helpers."""

from pathlib import Path

import pytest

from cafe.core import active_issue


@pytest.fixture
def cafe_dir(tmp_path: Path) -> Path:
    root = tmp_path / ".cafe"
    root.mkdir()
    return root


class TestActiveIssueMarker:
    def test_write_and_read_marker(self, cafe_dir: Path) -> None:
        active_issue.write_marker(cafe_dir, "issue-298")
        assert active_issue.read_marker(cafe_dir) == "issue-298"

    def test_read_missing_marker_returns_none(self, cafe_dir: Path) -> None:
        assert active_issue.read_marker(cafe_dir) is None

    def test_read_empty_marker_returns_none(self, cafe_dir: Path) -> None:
        active_issue.marker_path(cafe_dir).write_text("\n", encoding="utf-8")
        assert active_issue.read_marker(cafe_dir) is None

    def test_read_trims_whitespace(self, cafe_dir: Path) -> None:
        active_issue.marker_path(cafe_dir).write_text("  issue-a  \n", encoding="utf-8")
        assert active_issue.read_marker(cafe_dir) == "issue-a"

    def test_clear_marker_removes_file(self, cafe_dir: Path) -> None:
        active_issue.write_marker(cafe_dir, "issue-a")
        active_issue.clear_marker(cafe_dir)
        assert not active_issue.marker_path(cafe_dir).exists()

    def test_clear_if_matches_only_when_equal(self, cafe_dir: Path) -> None:
        active_issue.write_marker(cafe_dir, "issue-a")
        assert active_issue.clear_marker_if_matches(cafe_dir, "issue-a") is True
        assert active_issue.read_marker(cafe_dir) is None

    def test_clear_if_matches_skips_other_issue(self, cafe_dir: Path) -> None:
        active_issue.write_marker(cafe_dir, "issue-a")
        assert active_issue.clear_marker_if_matches(cafe_dir, "issue-b") is False
        assert active_issue.read_marker(cafe_dir) == "issue-a"

    def test_issue_exists_checks_prepared_directory(self, cafe_dir: Path) -> None:
        (cafe_dir / "issues" / "prepared").mkdir(parents=True)
        assert active_issue.issue_exists(cafe_dir, "prepared") is True
        assert active_issue.issue_exists(cafe_dir, "missing") is False


class TestActiveIssueGitignore:
    def test_cafe_directory_is_gitignored(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        gitignore = (repo_root / ".gitignore").read_text(encoding="utf-8")
        assert ".cafe" in gitignore
