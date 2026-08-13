from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cafe.ui import cli_shared
from cafe.utils.config import ConfigManager


def _config_manager(tmp_path: Path) -> ConfigManager:
    return ConfigManager(str(tmp_path / ".cafe" / "config.yaml"))


def test_setup_agents_rejects_missing_phase_chain_before_manager_construction(
    tmp_path: Path, monkeypatch
) -> None:
    manager_factory = MagicMock()
    monkeypatch.setattr(cli_shared, "AgentManager", manager_factory)
    monkeypatch.setattr(cli_shared, "get_git_toplevel", lambda: tmp_path)
    monkeypatch.setattr(cli_shared, "get_repo_root", lambda: tmp_path)

    with pytest.raises(ValueError) as exc_info:
        cli_shared.setup_agents(
            _config_manager(tmp_path),
            issue_name="issue-407",
            phase_name="develop",
        )

    assert "step='develop'" in str(exc_info.value)
    manager_factory.assert_not_called()


def test_setup_agents_registers_only_the_phase_configured_chain(
    tmp_path: Path, monkeypatch
) -> None:
    cafe_dir = tmp_path / ".cafe"
    cafe_dir.mkdir()
    (cafe_dir / "phases.yaml").write_text(
        "develop:\n"
        "  name: PhaseDavid\n"
        "  role: developer\n"
        "  clis:\n"
        "    - cli: codex\n"
        "      model: gpt-5.6-sol\n"
        "    - cli: claude\n"
        "      model: claude-opus-5\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_shared, "get_git_toplevel", lambda: tmp_path)
    monkeypatch.setattr(cli_shared, "get_repo_root", lambda: tmp_path)

    manager = cli_shared.setup_agents(
        _config_manager(tmp_path),
        issue_name="issue-407",
        phase_name="develop",
    )

    assert list(manager.agents) == ["PhaseDavid"]
    config = manager.agents["PhaseDavid"].config
    assert [(entry.cli.value, entry.model) for entry in config.clis] == [
        ("codex", "gpt-5.6-sol"),
        ("claude", "claude-opus-5"),
    ]


def test_cli_preflight_does_not_consult_role_or_default_configuration(
    tmp_path: Path, monkeypatch
) -> None:
    config_manager = MagicMock()
    phase_config = tmp_path / "phases.yaml"

    with pytest.raises(ValueError) as exc_info:
        cli_shared.check_agent_clis_available(
            config_manager,
            active_step="review",
            active_role="reviewer",
            phase_config_local_path=phase_config,
        )

    assert "step='review'" in str(exc_info.value)
    config_manager.get.assert_not_called()
