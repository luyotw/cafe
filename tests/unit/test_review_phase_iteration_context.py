"""Tests for ReviewPhase fallback to iteration context without status.json."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from cafe.phases.review_phase import ReviewPhase
from cafe.core.types import PhaseStatus


def _write_iteration_context(
    issue_dir: Path,
    phase_name: str,
    *,
    iteration: int = 1,
    end_time: str,
    status_code: str | None = None,
    response: str | None = None,
) -> None:
    iteration_dir = issue_dir / phase_name / f"iteration_{iteration:03d}"
    iteration_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "iteration": iteration,
        "end_time": end_time,
    }
    if status_code is not None:
        payload["status_code"] = status_code
    if response is not None:
        payload["response"] = response
    (iteration_dir / "context.json").write_text(json.dumps(payload), encoding="utf-8")


def _make_review_phase(issue_dir: Path) -> ReviewPhase:
    phase = ReviewPhase.__new__(ReviewPhase)
    phase.issue_dir = issue_dir
    phase.spec_file = str(issue_dir / "spec" / "iteration_001" / "output.md")
    return phase


def test_get_phase_end_time_uses_iteration_context_without_status_file(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
    _write_iteration_context(
        issue_dir,
        "develop",
        iteration=1,
        end_time="2026-04-29T10:00:00+08:00",
        response="CAFE_CONFIRMED",
    )
    phase = _make_review_phase(issue_dir)

    assert phase._get_phase_end_time("develop") == "2026-04-29T10:00:00+08:00"


def test_check_if_develop_is_newer_uses_iteration_context_without_status_file(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
    _write_iteration_context(
        issue_dir,
        "develop",
        iteration=1,
        end_time="2026-04-29T10:00:00+08:00",
        response="CAFE_CONFIRMED",
    )
    _write_iteration_context(
        issue_dir,
        "review",
        iteration=1,
        end_time="2026-04-29T09:00:00+08:00",
        response="CAFE_NEEDS_CHANGES",
    )
    phase = _make_review_phase(issue_dir)

    assert phase._check_if_develop_is_newer() is True


def test_check_if_develop_is_newer_returns_false_when_review_is_latest(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
    _write_iteration_context(
        issue_dir,
        "develop",
        iteration=1,
        end_time="2026-04-29T09:00:00+08:00",
        response="CAFE_CONFIRMED",
    )
    _write_iteration_context(
        issue_dir,
        "review",
        iteration=1,
        end_time="2026-04-29T10:00:00+08:00",
        response="CAFE_NEEDS_CHANGES",
    )
    phase = _make_review_phase(issue_dir)

    assert phase._check_if_develop_is_newer() is False


def test_execute_completed_review_points_back_to_make(tmp_path: Path, capsys) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
    _write_iteration_context(
        issue_dir,
        "develop",
        iteration=1,
        end_time="2026-04-29T09:00:00+08:00",
        response="CAFE_CONFIRMED",
    )
    _write_iteration_context(
        issue_dir,
        "review",
        iteration=1,
        end_time="2026-04-29T10:00:00+08:00",
        response="CAFE_CONFIRMED",
    )
    phase = _make_review_phase(issue_dir)
    phase.git_ops = MagicMock()
    phase.git_ops.has_unpushed_commits.return_value = True
    phase.force = False

    result = phase.execute()

    captured = capsys.readouterr()
    assert result.status == PhaseStatus.COMPLETED
    assert "Continue the workflow with: 'cafe make'" in captured.out
