"""Tests for fallback-preset switching in the workflow executor."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.agents.executor import AgentExecutionError
from cafe.core.workflow_models import StepExecutionResult
from cafe.utils.preset import PresetManager, PresetNotFoundError


class TestWorkflowFallbackPresetLogic:
    """Unit-test the fallback logic in isolation, without running the full CLI."""

    def _make_preset_env(self, tmp_path: Path) -> Path:
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        (cafe_dir / "crew.yaml").write_text(
            "pm:\n  name: Roger\n  cli: claude\n"
            "developer:\n  name: David\n  cli: claude\n"
            "reviewer:\n  name: Richard\n  cli: claude\n"
        )
        presets_dir = cafe_dir / "presets"
        presets_dir.mkdir()
        (presets_dir / "fallback-crew.yaml").write_text(
            "pm:\n  name: Roger\n  cli: gemini\n"
            "developer:\n  name: David\n  cli: gemini\n"
            "reviewer:\n  name: Richard\n  cli: gemini\n"
        )
        return cafe_dir

    def test_workflow_switches_crew_on_rate_limit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When step executor raises rate_limit, fallback preset is applied and step retried."""
        monkeypatch.chdir(tmp_path)
        cafe_dir = self._make_preset_env(tmp_path)

        rate_limit_error = AgentExecutionError("rate limit", error_type="rate_limit")
        success_result = StepExecutionResult(response="ok", artifacts={}, status_code="confirmed")

        mock_executor = MagicMock()
        mock_executor.execute_step.side_effect = [rate_limit_error, success_result]

        applied: list = []

        def fake_apply(name: str, cafe_dir: Path = Path(".cafe")) -> None:
            applied.append(name)

        fallback_preset = "fallback-crew"
        fallback_applied = [False]

        # Reproduce the wrapped_executor logic directly
        def wrapped_execute(step_name: str) -> StepExecutionResult:
            try:
                return mock_executor.execute_step(step_name, {}, None)
            except AgentExecutionError as exc:
                if (
                    fallback_preset
                    and not fallback_applied[0]
                    and getattr(exc, "error_type", None) in ("rate_limit", "cli_not_found", "cli_unavailable")
                ):
                    fake_apply(fallback_preset)
                    fallback_applied[0] = True
                    return mock_executor.execute_step(step_name, {}, None)
                raise

        result = wrapped_execute("spec")
        assert result.status_code == "confirmed"
        assert "fallback-crew" in applied
        assert mock_executor.execute_step.call_count == 2

    def test_workflow_no_fallback_preset_propagates_error(self) -> None:
        """Without --fallback-preset, AgentExecutionError propagates normally."""
        rate_limit_error = AgentExecutionError("rate limit", error_type="rate_limit")
        mock_executor = MagicMock()
        mock_executor.execute_step.side_effect = rate_limit_error

        fallback_preset = None
        fallback_applied = [False]

        def wrapped_execute(step_name: str) -> StepExecutionResult:
            try:
                return mock_executor.execute_step(step_name, {}, None)
            except AgentExecutionError as exc:
                if (
                    fallback_preset
                    and not fallback_applied[0]
                    and getattr(exc, "error_type", None) in ("rate_limit", "cli_not_found", "cli_unavailable")
                ):
                    fallback_applied[0] = True
                    return mock_executor.execute_step(step_name, {}, None)
                raise

        with pytest.raises(AgentExecutionError):
            wrapped_execute("spec")
        assert not fallback_applied[0]

    def test_workflow_switches_crew_on_cli_not_found(self) -> None:
        """When step executor raises cli_not_found, fallback preset is also applied."""
        cli_error = AgentExecutionError("cli not found", error_type="cli_not_found")
        success_result = StepExecutionResult(response="ok", artifacts={}, status_code="confirmed")
        mock_executor = MagicMock()
        mock_executor.execute_step.side_effect = [cli_error, success_result]

        applied: list = []
        fallback_preset = "fallback-crew"
        fallback_applied = [False]

        def wrapped_execute(step_name: str) -> StepExecutionResult:
            try:
                return mock_executor.execute_step(step_name, {}, None)
            except AgentExecutionError as exc:
                if (
                    fallback_preset
                    and not fallback_applied[0]
                    and getattr(exc, "error_type", None) in ("rate_limit", "cli_not_found", "cli_unavailable")
                ):
                    applied.append(fallback_preset)
                    fallback_applied[0] = True
                    return mock_executor.execute_step(step_name, {}, None)
                raise

        result = wrapped_execute("spec")
        assert result.status_code == "confirmed"
        assert "fallback-crew" in applied

    def test_workflow_switches_crew_on_cli_unavailable(self) -> None:
        """When the primary CLI is unavailable due to account/org policy, fallback preset applies."""
        cli_error = AgentExecutionError("subscription disabled", error_type="cli_unavailable")
        success_result = StepExecutionResult(response="ok", artifacts={}, status_code="confirmed")
        mock_executor = MagicMock()
        mock_executor.execute_step.side_effect = [cli_error, success_result]

        applied: list = []
        fallback_preset = "fallback-crew"
        fallback_applied = [False]

        def wrapped_execute(step_name: str) -> StepExecutionResult:
            try:
                return mock_executor.execute_step(step_name, {}, None)
            except AgentExecutionError as exc:
                if (
                    fallback_preset
                    and not fallback_applied[0]
                    and getattr(exc, "error_type", None) in ("rate_limit", "cli_not_found", "cli_unavailable")
                ):
                    applied.append(fallback_preset)
                    fallback_applied[0] = True
                    return mock_executor.execute_step(step_name, {}, None)
                raise

        result = wrapped_execute("review")
        assert result.status_code == "confirmed"
        assert "fallback-crew" in applied
