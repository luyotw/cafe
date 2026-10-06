"""Discoverable, read-only integration CLI contracts (U10/I13)."""

from typer.testing import CliRunner

from cafe.ui.cli import app

runner = CliRunner()


def test_integration_commands_are_publicly_discoverable():
    result = runner.invoke(app, ["integration", "--help"])
    assert result.exit_code == 0
    for command in ("select", "status", "verify"):
        assert command in result.stdout


def test_status_missing_issue_fails_without_creating_runtime_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["integration", "status", "--issue", "missing", "--json"])
    assert result.exit_code != 0
    assert not (tmp_path / ".cafe").exists()
