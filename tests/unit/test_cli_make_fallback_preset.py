"""Tests for cafe make --fallback-preset flag."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from cafe.ui.cli import app


runner = CliRunner()


class TestMakeFallbackPreset:
    """Test that --fallback-preset is forwarded to the workflow subprocess."""

    def test_make_passes_fallback_preset_to_workflow(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        (cafe_dir / "config.yaml").write_text(
            "agents:\n  pm:\n    name: Roger\n    cli: claude\n"
            "  developer:\n    name: David\n    cli: claude\n"
            "  reviewer:\n    name: Richard\n    cli: claude\n"
        )
        (cafe_dir / "crew.yaml").write_text(
            "pm:\n  name: Roger\n  cli: claude\n"
            "developer:\n  name: David\n  cli: claude\n"
            "reviewer:\n  name: Richard\n  cli: claude\n"
        )

        captured_cmd: list = []

        def fake_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            mock_result = MagicMock()
            mock_result.returncode = 0
            return mock_result

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("subprocess.run", side_effect=fake_run),
        ):
            runner.invoke(app, ["make", "--fallback-preset", "gemini-team"])

        assert "--fallback-preset" in captured_cmd
        idx = captured_cmd.index("--fallback-preset")
        assert captured_cmd[idx + 1] == "gemini-team"
