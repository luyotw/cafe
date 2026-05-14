"""Tests for backward-compatible iteration context file resolution.

_resolve_iteration_context_file():
  - Returns iteration.json when it exists.
  - Falls back to context.json with a deprecation WARNING when only context.json exists.
  - Returns a path pointing to iteration.json (non-existent) when neither file exists.
"""

import json
import logging
from pathlib import Path

import pytest

from cafe.core.phase import Phase


class ConcretePhase(Phase):
    def __init__(self, phase_dir: Path, **kwargs):
        super().__init__(**kwargs)
        self.phase_dir = phase_dir
        self.iteration = 1

    def execute(self):
        pass


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
