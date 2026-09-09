"""Legacy execution configuration surface guards (UT-007 / IT-004)."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cafe.ui.cli import app
from cafe.ui.menu import InteractiveMenu


runner = CliRunner()
pytestmark = pytest.mark.usefixtures("cached_builtin_playbook_models")


@pytest.mark.parametrize(
    "arguments",
    [
        ["crew"],
        ["preset"],
        ["init", "--preset", "default"],
        ["prepare", "issue-407", "--preset", "default"],
        ["make", "--fallback-preset", "default"],
        ["workflow", "--fallback-preset", "default"],
    ],
)
def test_legacy_cli_operations_are_unavailable(arguments: list[str], tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, arguments)
    assert result.exit_code != 0
    assert not (tmp_path / ".cafe" / "crew.yaml").exists()


def test_settings_menu_does_not_advertise_legacy_configuration() -> None:
    choices = InteractiveMenu.__new__(InteractiveMenu)._build_settings_menu_choices()
    labels = {str(choice["name"]).lower() for choice in choices}
    values = {str(choice["value"]).lower() for choice in choices}
    assert not any("crew" in value or "preset" in value for value in labels | values)


def test_runtime_source_has_no_legacy_configuration_imports() -> None:
    project_root = Path(__file__).resolve().parents[2]
    source_root = project_root / "src" / "cafe"
    prohibited = ("cafe.utils.crew", "cafe.utils.preset", "fallback-preset", "crew.yaml")
    offenders: list[str] = []
    for path in source_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in prohibited):
            offenders.append(path.relative_to(project_root).as_posix())
    assert offenders == []
