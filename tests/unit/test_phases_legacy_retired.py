"""Guardrails ensuring phases_legacy hidden CLI aliases stay removed (issue #315)."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cafe.ui.cli import app

runner = CliRunner()


def test_phases_legacy_module_is_removed() -> None:
    assert not Path("src/cafe/ui/commands/phases_legacy.py").exists()


def test_cli_source_does_not_reference_phases_legacy() -> None:
    cli_source = Path("src/cafe/ui/cli.py").read_text(encoding="utf-8")
    assert "phases_legacy" not in cli_source


@pytest.mark.parametrize("legacy_command", ["spec", "plan", "develop", "review", "pr"])
def test_legacy_phase_commands_are_unknown(legacy_command: str) -> None:
    result = runner.invoke(app, [legacy_command])
    assert result.exit_code != 0
    combined = (result.stdout or "") + (result.stderr or "")
    assert "No such command" in combined
