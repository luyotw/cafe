"""Tests for normalize_role_config helper."""

import pytest

from cafe.core.types import AgentCLI, CliEntry
from cafe.utils.crew import normalize_role_config


class TestNormalizeRoleConfigNewFormat:
    """New clis list format is returned as-is."""

    def test_new_format_single_entry(self) -> None:
        role = {
            "name": "David",
            "clis": [{"cli": "claude", "model": "opus"}],
        }
        result = normalize_role_config(role)
        assert len(result) == 1
        assert result[0].cli == AgentCLI.CLAUDE
        assert result[0].model == "opus"

    def test_new_format_with_phase_overrides(self) -> None:
        role = {
            "clis": [
                {"cli": "claude", "model": "opus", "plan": "sonnet", "develop": "sonnet"},
                {"cli": "gemini", "model": "gemini-2.5-pro"},
            ]
        }
        result = normalize_role_config(role)
        assert len(result) == 2
        assert result[0].phase_models == {"plan": "sonnet", "develop": "sonnet"}
        assert result[1].cli == AgentCLI.GEMINI
        assert result[1].phase_models == {}

    def test_new_format_entry_without_model(self) -> None:
        role = {"clis": [{"cli": "copilot"}]}
        result = normalize_role_config(role)
        assert result[0].model is None
        assert result[0].phase_models == {}

    def test_new_format_wins_over_old_fields_when_both_present(self) -> None:
        """clis takes precedence over cli/backup/models."""
        role = {
            "cli": "copilot",
            "backup": ["gemini"],
            "clis": [{"cli": "claude", "model": "opus"}],
        }
        result = normalize_role_config(role)
        assert len(result) == 1
        assert result[0].cli == AgentCLI.CLAUDE


class TestNormalizeRoleConfigOldFormat:
    """Old cli/model/backup/models format is normalized to clis."""

    def test_old_format_cli_only(self) -> None:
        role = {"name": "David", "cli": "claude"}
        result = normalize_role_config(role)
        assert len(result) == 1
        assert result[0].cli == AgentCLI.CLAUDE
        assert result[0].model is None

    def test_old_format_cli_with_model(self) -> None:
        role = {"cli": "claude", "model": "opus"}
        result = normalize_role_config(role)
        assert result[0].model == "opus"

    def test_old_format_with_backup(self) -> None:
        role = {"cli": "claude", "backup": ["gemini", "copilot"]}
        result = normalize_role_config(role)
        assert len(result) == 3
        assert result[0].cli == AgentCLI.CLAUDE
        assert result[1].cli == AgentCLI.GEMINI
        assert result[2].cli == AgentCLI.COPILOT

    def test_old_format_with_models_phase_overrides(self) -> None:
        role = {
            "cli": "claude",
            "model": "opus",
            "backup": ["gemini"],
            "models": {
                "claude": {"plan": "opus", "develop": "sonnet"},
                "gemini": {"plan": "gemini-2.5-pro-preview"},
            },
        }
        result = normalize_role_config(role)
        assert result[0].phase_models == {"plan": "opus", "develop": "sonnet"}
        assert result[1].phase_models == {"plan": "gemini-2.5-pro-preview"}

    def test_old_format_models_missing_for_backup_cli(self) -> None:
        """Backup CLI with no models entry gets empty phase_models."""
        role = {
            "cli": "claude",
            "backup": ["copilot"],
            "models": {"claude": {"plan": "opus"}},
        }
        result = normalize_role_config(role)
        assert result[1].cli == AgentCLI.COPILOT
        assert result[1].phase_models == {}

    def test_old_format_empty_models_dict_does_not_raise(self) -> None:
        role = {"cli": "claude", "models": {"claude": {}}}
        result = normalize_role_config(role)
        assert result[0].phase_models == {}


class TestNormalizeRoleConfigEdgeCases:
    """Edge cases: unknown CLIs, duplicates, empty inputs."""

    def test_unknown_cli_string_is_ignored(self) -> None:
        role = {"cli": "claude", "backup": ["unknown-cli-xyz"]}
        result = normalize_role_config(role)
        # Only primary; unknown backup is dropped
        assert len(result) == 1
        assert result[0].cli == AgentCLI.CLAUDE

    def test_primary_cli_duplicate_in_backup_is_skipped(self) -> None:
        role = {"cli": "claude", "backup": ["claude", "gemini"]}
        result = normalize_role_config(role)
        clis = [e.cli for e in result]
        assert clis.count(AgentCLI.CLAUDE) == 1
        assert AgentCLI.GEMINI in clis

    def test_duplicate_cli_in_backup_list_only_first_kept(self) -> None:
        role = {"cli": "claude", "backup": ["gemini", "gemini"]}
        result = normalize_role_config(role)
        clis = [e.cli for e in result]
        assert clis.count(AgentCLI.GEMINI) == 1

    def test_new_format_unknown_cli_entry_ignored(self) -> None:
        role = {"clis": [{"cli": "claude"}, {"cli": "not-a-cli"}]}
        result = normalize_role_config(role)
        assert len(result) == 1
        assert result[0].cli == AgentCLI.CLAUDE

    def test_new_format_duplicate_cli_entries_deduplicated(self) -> None:
        role = {"clis": [{"cli": "claude"}, {"cli": "claude"}]}
        result = normalize_role_config(role)
        assert len(result) == 1

    def test_empty_role_dict_returns_empty_list(self) -> None:
        result = normalize_role_config({})
        assert result == []

    def test_role_with_only_name_returns_empty_list(self) -> None:
        result = normalize_role_config({"name": "David"})
        assert result == []
