"""Tests for resolve_sync_github_config helper function."""

import pytest
from cafe.utils.config import resolve_sync_github_config


class TestResolveSyncGithubConfig:
    """Test resolve_sync_github_config utility function."""

    def test_cli_value_takes_precedence_over_config(self):
        """Test that CLI value takes precedence over config value."""
        result = resolve_sync_github_config(
            cli_value=False,
            config_value=True,
            has_issue_id=True
        )
        assert result is False

    def test_cli_value_takes_precedence_over_default(self):
        """Test that CLI value takes precedence over default."""
        result = resolve_sync_github_config(
            cli_value=True,
            config_value=None,
            has_issue_id=False
        )
        assert result is True

    def test_config_value_used_when_no_cli_value(self):
        """Test that config value is used when CLI value is not provided."""
        result = resolve_sync_github_config(
            cli_value=None,
            config_value=True,
            has_issue_id=False
        )
        assert result is True

    def test_config_value_false_when_no_cli_value(self):
        """Test that config value False is respected."""
        result = resolve_sync_github_config(
            cli_value=None,
            config_value=False,
            has_issue_id=True
        )
        assert result is False

    def test_default_to_true_when_issue_id_present(self):
        """Test default to True when issue_id is present (backward compatibility)."""
        result = resolve_sync_github_config(
            cli_value=None,
            config_value=None,
            has_issue_id=True
        )
        assert result is True

    def test_default_to_false_when_no_issue_id(self):
        """Test default to False when no issue_id."""
        result = resolve_sync_github_config(
            cli_value=None,
            config_value=None,
            has_issue_id=False
        )
        assert result is False

    def test_all_none_values_returns_false(self):
        """Test that all None values with no issue_id returns False."""
        result = resolve_sync_github_config(
            cli_value=None,
            config_value=None,
            has_issue_id=False
        )
        assert result is False

    def test_cli_value_none_explicitly_vs_not_provided(self):
        """Test that None CLI value is treated as not provided."""
        # When CLI value is None, should fall back to config
        result = resolve_sync_github_config(
            cli_value=None,
            config_value=True,
            has_issue_id=False
        )
        assert result is True
