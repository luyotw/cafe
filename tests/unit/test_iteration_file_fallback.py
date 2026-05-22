"""Tests for backward-compatible iteration context file resolution.

_resolve_iteration_context_file():
  - Returns iteration.json when it exists.
  - Falls back to context.json with a deprecation WARNING when only context.json exists.
  - Returns a path pointing to iteration.json (non-existent) when neither file exists.
"""

import importlib.util
import json
import logging
from pathlib import Path

import pytest

from cafe.core.phase import Phase


class ConcretePhase(Phase):
    def __init__(self, phase_dir: Path, issue_dir: Path | None = None, **kwargs):
        super().__init__(**kwargs)
        self.phase_dir = phase_dir
        self.issue_dir = issue_dir or phase_dir.parent
        self.iteration = 1

    def execute(self):
        pass


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
    (iteration_dir / "iteration.json").write_text(json.dumps(payload), encoding="utf-8")


class TestResolveIterationContextFile:
    def test_returns_iteration_json_when_it_exists(self, tmp_path):
        iteration_dir = tmp_path / "spec" / "iteration_001"
        iteration_dir.mkdir(parents=True)
        (iteration_dir / "iteration.json").write_text("{}", encoding="utf-8")

        phase = ConcretePhase(phase_dir=tmp_path / "spec")
        result = phase._resolve_iteration_context_file(iteration_dir)

        assert result == iteration_dir / "iteration.json"

    def test_falls_back_to_context_json_with_warning(self, tmp_path, caplog):
        iteration_dir = tmp_path / "spec" / "iteration_001"
        iteration_dir.mkdir(parents=True)
        (iteration_dir / "context.json").write_text("{}", encoding="utf-8")

        phase = ConcretePhase(phase_dir=tmp_path / "spec")

        with caplog.at_level(logging.WARNING):
            result = phase._resolve_iteration_context_file(iteration_dir)

        assert result == iteration_dir / "context.json"
        assert any(r.levelno == logging.WARNING for r in caplog.records)

    def test_prefers_iteration_json_over_context_json(self, tmp_path):
        iteration_dir = tmp_path / "spec" / "iteration_001"
        iteration_dir.mkdir(parents=True)
        (iteration_dir / "iteration.json").write_text('{"source": "new"}', encoding="utf-8")
        (iteration_dir / "context.json").write_text('{"source": "old"}', encoding="utf-8")

        phase = ConcretePhase(phase_dir=tmp_path / "spec")
        result = phase._resolve_iteration_context_file(iteration_dir)

        assert result == iteration_dir / "iteration.json"

    def test_returns_iteration_json_path_when_neither_exists(self, tmp_path):
        iteration_dir = tmp_path / "spec" / "iteration_001"
        iteration_dir.mkdir(parents=True)

        phase = ConcretePhase(phase_dir=tmp_path / "spec")
        result = phase._resolve_iteration_context_file(iteration_dir)

        assert result == iteration_dir / "iteration.json"
        assert not result.exists()

    def test_no_warning_when_iteration_json_exists(self, tmp_path, caplog):
        iteration_dir = tmp_path / "spec" / "iteration_001"
        iteration_dir.mkdir(parents=True)
        (iteration_dir / "iteration.json").write_text("{}", encoding="utf-8")

        phase = ConcretePhase(phase_dir=tmp_path / "spec")

        with caplog.at_level(logging.WARNING):
            phase._resolve_iteration_context_file(iteration_dir)

        assert not any(r.levelno == logging.WARNING for r in caplog.records)


class TestPhaseEndTimeFromIterationContext:
    def test_get_phase_end_time_uses_iteration_context_without_status_file(self, tmp_path):
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        _write_iteration_context(
            issue_dir,
            "develop",
            iteration=1,
            end_time="2026-04-29T10:00:00+08:00",
            response="confirmed",
        )
        phase = ConcretePhase(phase_dir=issue_dir / "develop", issue_dir=issue_dir)

        assert phase._get_phase_end_time("develop") == "2026-04-29T10:00:00+08:00"

    def test_review_private_freshness_checks_are_removed_by_design(self):
        """Review freshness is owned by blackboard/playbook runtime transitions."""
        assert not hasattr(Phase, "_check_if_develop_is_newer")

    def test_review_execute_cafe_make_early_exit_message_is_removed_by_design(self):
        """The old legacy review execute cafe make early-exit message was class-only behavior."""
        legacy_module = ".".join(["cafe", "phases", "review" + "_phase"])
        assert importlib.util.find_spec(legacy_module) is None
