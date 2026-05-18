"""Tests for cafe crew set-primary command."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from cafe.ui.cli import app

runner = CliRunner()


class TestCrewSetPrimaryPreset:
    """Tests for `cafe crew set-primary --preset` non-interactive mode."""

    def test_preset_applies_valid_preset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--preset with a valid name writes preset content to crew.yaml."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        preset_content = {"pm": {"name": "Roger", "cli": "claude"}}
        preset_file = tmp_path / "my_preset.yaml"
        preset_file.write_text(yaml.dump(preset_content))

        with patch("cafe.ui.commands.crew.PresetManager") as mock_pm_cls:
            mock_pm = MagicMock()
            mock_pm_cls.return_value = mock_pm
            mock_pm.apply.return_value = None

            result = runner.invoke(app, ["crew", "set-primary", "--preset", "claude-opus"])

        assert result.exit_code == 0
        mock_pm.apply.assert_called_once_with("claude-opus", cafe_dir=Path(".cafe"))
        assert "claude-opus" in result.stdout

    def test_preset_not_found_exits_with_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--preset with unknown name prints error and exits 1."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        from cafe.utils.preset import PresetNotFoundError

        with patch("cafe.ui.commands.crew.PresetManager") as mock_pm_cls:
            mock_pm = MagicMock()
            mock_pm_cls.return_value = mock_pm
            mock_pm.apply.side_effect = PresetNotFoundError("Preset 'unknown' not found.")

            result = runner.invoke(app, ["crew", "set-primary", "--preset", "unknown"])

        assert result.exit_code == 1
        assert "not found" in result.stdout.lower() or "error" in result.stdout.lower()


class TestCrewSetPrimaryInteractive:
    """Tests for `cafe crew set-primary` interactive flow."""

    def _make_preset_info(self, name: str, tmp_path: Path, cli: str = "claude") -> object:
        from cafe.utils.preset import PresetInfo
        preset_file = tmp_path / f"{name}.yaml"
        preset_file.write_text(yaml.dump({
            "pm": {"name": "Roger", "cli": cli},
            "developer": {"name": "David", "cli": cli},
            "reviewer": {"name": "Richard", "cli": cli},
        }))
        return PresetInfo(name=name, path=preset_file, source="built-in")

    def test_interactive_applies_confirmed_preset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """User selects preset, confirms → preset primary applied to crew.yaml."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        preset_info = self._make_preset_info("default", tmp_path, cli="claude")

        with (
            patch("cafe.ui.commands.crew.PresetManager") as mock_pm_cls,
            patch("cafe.ui.init_helpers.check_available_clis", return_value=["claude"]),
            patch("cafe.ui.commands.crew.prompt_list", return_value="default  [built-in]") as mock_list,
            patch("cafe.ui.commands.crew.prompt_confirm", return_value=True),
        ):
            mock_pm = MagicMock()
            mock_pm_cls.return_value = mock_pm
            mock_pm.list.return_value = [preset_info]

            result = runner.invoke(app, ["crew", "set-primary"])

        assert result.exit_code == 0
        crew = yaml.safe_load((cafe_dir / "crew.yaml").read_text())
        for role in ["pm", "developer", "reviewer"]:
            assert crew[role]["clis"][0]["cli"] == "claude"

    def test_interactive_preserves_existing_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Interactive preset apply preserves user's existing fallback entries."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        (cafe_dir / "crew.yaml").write_text(yaml.dump({
            "developer": {
                "name": "Nick",
                "clis": [
                    {"cli": "codex", "model": "gpt-5.5"},
                    {"cli": "gemini", "model": "2.5-pro", "plan": "2.5-pro"},
                ],
            },
        }))

        preset_info = self._make_preset_info("default", tmp_path, cli="claude")

        with (
            patch("cafe.ui.commands.crew.PresetManager") as mock_pm_cls,
            patch("cafe.ui.init_helpers.check_available_clis", return_value=["claude"]),
            patch("cafe.ui.commands.crew.prompt_list", return_value="default  [built-in]"),
            patch("cafe.ui.commands.crew.prompt_confirm", return_value=True),
        ):
            mock_pm = MagicMock()
            mock_pm_cls.return_value = mock_pm
            mock_pm.list.return_value = [preset_info]

            result = runner.invoke(app, ["crew", "set-primary"])

        assert result.exit_code == 0
        crew = yaml.safe_load((cafe_dir / "crew.yaml").read_text())
        clis = crew["developer"]["clis"]
        assert clis[0]["cli"] == "claude"
        cli_names = [e["cli"] for e in clis]
        assert "codex" in cli_names
        assert "gemini" in cli_names
        gemini_entry = next(e for e in clis if e["cli"] == "gemini")
        assert gemini_entry.get("plan") == "2.5-pro"

    def test_interactive_loops_back_when_not_confirmed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """User rejects first time, confirms second time → crew.yaml updated once."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        preset_info = self._make_preset_info("default", tmp_path, cli="claude")

        confirm_calls = [False, True]

        with (
            patch("cafe.ui.commands.crew.PresetManager") as mock_pm_cls,
            patch("cafe.ui.init_helpers.check_available_clis", return_value=["claude"]),
            patch("cafe.ui.commands.crew.prompt_list", return_value="default  [built-in]"),
            patch("cafe.ui.commands.crew.prompt_confirm", side_effect=confirm_calls),
        ):
            mock_pm = MagicMock()
            mock_pm_cls.return_value = mock_pm
            mock_pm.list.return_value = [preset_info]

            result = runner.invoke(app, ["crew", "set-primary"])

        assert result.exit_code == 0
        crew = yaml.safe_load((cafe_dir / "crew.yaml").read_text())
        assert crew["developer"]["clis"][0]["cli"] == "claude"

    def test_interactive_keyboard_interrupt_exits_cleanly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ctrl+C during interactive flow causes clean exit (exit code 0)."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        with (
            patch("cafe.ui.init_helpers.check_available_clis", return_value=["claude"]),
            patch("cafe.ui.commands.crew.PresetManager") as mock_pm_cls,
        ):
            mock_pm = MagicMock()
            mock_pm_cls.return_value = mock_pm
            mock_pm.list.side_effect = KeyboardInterrupt

            result = runner.invoke(app, ["crew", "set-primary"])

        assert result.exit_code == 0


class TestCrewSetPrimaryCliFlags:
    """Tests for `cafe crew set-primary --cli` non-interactive mode."""

    def test_cli_flag_sets_primary_for_all_roles(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--cli codex sets codex as primary for pm, developer, reviewer."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        crew_file = cafe_dir / "crew.yaml"
        crew_file.write_text(yaml.dump({
            "pm": {"name": "Roger", "cli": "claude", "model": "sonnet",
                   "spec": {"model": "sonnet"}},
        }))

        result = runner.invoke(app, ["crew", "set-primary", "--cli", "codex", "--model", "gpt-5.5"])

        assert result.exit_code == 0
        assert "codex" in result.stdout

        crew_data = yaml.safe_load(crew_file.read_text())
        for role in ["pm", "developer", "reviewer"]:
            assert role in crew_data
            clis = crew_data[role]["clis"]
            assert clis[0]["cli"] == "codex"
            assert clis[0]["model"] == "gpt-5.5"

    def test_cli_flag_removes_old_format_keys(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--cli cleans up old-format keys (cli, model, spec, plan, etc.)."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        crew_file = cafe_dir / "crew.yaml"
        crew_file.write_text(yaml.dump({
            "developer": {"name": "Nick", "cli": "claude", "model": "sonnet",
                          "plan": {"model": "opus"}, "develop": {"model": "sonnet"}},
        }))

        result = runner.invoke(app, ["crew", "set-primary", "--cli", "codex"])

        assert result.exit_code == 0
        crew_data = yaml.safe_load(crew_file.read_text())
        dev = crew_data["developer"]
        assert "cli" not in dev
        assert "model" not in dev
        assert "plan" not in dev
        assert "develop" not in dev
        assert dev["clis"][0]["cli"] == "codex"

    def test_cli_flag_preserves_existing_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--cli keeps existing fallback entries when changing primary."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        crew_file = cafe_dir / "crew.yaml"
        crew_data = {
            "developer": {
                "name": "Nick",
                "clis": [
                    {"cli": "claude", "model": "sonnet"},
                    {"cli": "codex", "model": "o4-mini"},
                ],
            },
        }
        crew_file.write_text(yaml.dump(crew_data, default_flow_style=False))

        result = runner.invoke(app, ["crew", "set-primary", "--cli", "gemini", "--model", "2.5-pro"])

        assert result.exit_code == 0
        updated = yaml.safe_load(crew_file.read_text())
        clis = updated["developer"]["clis"]
        assert clis[0]["cli"] == "gemini"
        assert clis[0]["model"] == "2.5-pro"
        # Old primary (claude) is demoted to fallback #1, old fallback (codex) follows
        assert clis[1]["cli"] == "claude"
        assert clis[1]["model"] == "sonnet"
        assert clis[2]["cli"] == "codex"
        assert clis[2]["model"] == "o4-mini"

    def test_cli_flag_preserves_phase_models_on_matching_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--cli on a CLI already in the chain preserves its existing phase models."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        crew_file = cafe_dir / "crew.yaml"
        crew_data = {
            "developer": {
                "name": "Nick",
                "clis": [
                    {"cli": "claude", "model": "sonnet"},
                    {"cli": "codex", "model": "gpt-5.5",
                     "plan": "gpt-5.5", "develop": "gpt-5.3-codex"},
                ],
            },
        }
        crew_file.write_text(yaml.dump(crew_data, default_flow_style=False))

        result = runner.invoke(app, ["crew", "set-primary", "--cli", "codex"])

        assert result.exit_code == 0
        updated = yaml.safe_load(crew_file.read_text())
        primary = updated["developer"]["clis"][0]
        assert primary["cli"] == "codex"
        assert primary["model"] == "gpt-5.5"
        assert primary["plan"] == "gpt-5.5"
        assert primary["develop"] == "gpt-5.3-codex"

    def test_phase_model_overrides(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--phase-model developer.plan=opus adds phase override to clis entry."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, [
            "crew", "set-primary", "--cli", "codex", "--model", "gpt-5.5",
            "--phase-model", "developer.plan=gpt-5.5",
            "--phase-model", "developer.develop=gpt-5.3-codex",
        ])

        assert result.exit_code == 0
        crew_data = yaml.safe_load((cafe_dir / "crew.yaml").read_text())
        dev_primary = crew_data["developer"]["clis"][0]
        assert dev_primary["plan"] == "gpt-5.5"
        assert dev_primary["develop"] == "gpt-5.3-codex"

    def test_invalid_cli_exits_with_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--cli with unsupported CLI name exits 1."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["crew", "set-primary", "--cli", "nonexistent"])

        assert result.exit_code == 1
        assert "not a supported CLI" in result.stdout

    def test_invalid_phase_model_format_exits_with_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--phase-model without = or without role.phase exits 1."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, [
            "crew", "set-primary", "--cli", "codex", "--phase-model", "badformat",
        ])

        assert result.exit_code == 1

    def test_invalid_phase_model_role_exits_with_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--phase-model with unknown role exits 1."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, [
            "crew", "set-primary", "--cli", "codex", "--phase-model", "unknown.plan=opus",
        ])

        assert result.exit_code == 1
