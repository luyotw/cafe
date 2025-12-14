"""Tests for the `cafe agent ls` CLI command."""

from pathlib import Path
from typer.testing import CliRunner

from cafe.ui.cli import app

runner = CliRunner()


def test_agent_ls_no_agents(tmp_path, monkeypatch):
    """Test `agent ls` when no agents exist."""
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home_dir)
    agents_dir = home_dir / ".cafe" / "agents"
    agents_dir.mkdir(parents=True)

    result = runner.invoke(app, ["agent", "ls"])
    assert result.exit_code == 0
    assert "No agents found" in result.stdout


def test_agent_ls_with_agents(tmp_path, monkeypatch):
    """Test `agent ls` with multiple agents in different roles."""
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home_dir)
    agents_dir = home_dir / ".cafe" / "agents"
    (agents_dir / "pm").mkdir(parents=True)
    (agents_dir / "pm" / "Roger.md").write_text("PM Roger")
    (agents_dir / "developer").mkdir()
    (agents_dir / "developer" / "David.md").write_text("Dev David")

    result = runner.invoke(app, ["agent", "ls"])

    assert result.exit_code == 0
    assert "developer/" in result.stdout
    assert "David.md" in result.stdout
    assert "pm/" in result.stdout
    assert "Roger.md" in result.stdout
