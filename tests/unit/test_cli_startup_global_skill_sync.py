"""Tests for safe global helper installation at CLI startup."""

from unittest.mock import MagicMock, patch

import pytest

from cafe.ui import cli


def test_main_checks_startup_policy_before_dispatch(monkeypatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(cli, "_check_dependencies", lambda: events.append("dependencies"))
    monkeypatch.setattr(cli, "_check_repo_entrypoint_alignment", lambda: True)
    monkeypatch.setattr(
        cli,
        "_auto_sync_global_helper_skills",
        lambda: events.append("global-skills"),
    )
    monkeypatch.setattr(cli, "app", lambda: events.append("app"))

    assert cli.main() is None
    assert events == ["dependencies", "global-skills", "app"]


@pytest.mark.parametrize(
    "argv",
    [
        ["status"],
        ["show", "plan"],
        ["catalog", "check"],
        ["verification", "check"],
        ["workflow", "--issue", "issue466"],
        ["workflow", "--execute", "--dry-run"],
        ["--help"],
        ["version"],
        ["skill", "list"],
        ["skill", "sync-global"],
        ["unknown-command"],
    ],
)
def test_startup_policy_skips_observational_dry_run_and_unknown_paths(argv) -> None:
    assert cli._should_auto_install_global_helper_skills(argv) is False


@pytest.mark.parametrize(
    "argv",
    [
        ["prepare"],
        ["workflow", "--issue", "issue466", "--execute"],
        ["task", "complete", "task-id"],
    ],
)
def test_startup_policy_allows_only_declared_mutating_paths(argv) -> None:
    assert cli._should_auto_install_global_helper_skills(argv) is True


def test_startup_policy_is_checked_before_importing_installer(monkeypatch) -> None:
    monkeypatch.setattr(cli.sys, "argv", ["cafe", "status"])

    with patch("cafe.skills.global_installer.auto_sync_global_skills") as mock_sync:
        cli._auto_sync_global_helper_skills()

    mock_sync.assert_not_called()


def test_startup_auto_install_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setenv("CAFE_SKIP_GLOBAL_SKILL_SYNC", "1")
    monkeypatch.setattr(cli.sys, "argv", ["cafe", "prepare"])

    with patch("cafe.skills.global_installer.auto_sync_global_skills") as mock_sync:
        cli._auto_sync_global_helper_skills()

    mock_sync.assert_not_called()


def test_startup_auto_install_reports_helpers_clis_and_source(monkeypatch) -> None:
    monkeypatch.delenv("CAFE_SKIP_GLOBAL_SKILL_SYNC", raising=False)
    monkeypatch.setattr(cli.sys, "argv", ["cafe", "prepare"])
    summary = MagicMock(
        failed_count=0,
        installed_skill_count=1,
        changed_cli_count=5,
        source_root="/trusted/cafe/src/cafe/data/skills",
    )

    with (
        patch(
            "cafe.skills.global_installer.auto_sync_global_skills",
            return_value=summary,
        ),
        patch.object(cli, "console") as mock_console,
    ):
        cli._auto_sync_global_helper_skills()

    rendered = str(mock_console.print.call_args)
    assert "1 missing global helper" in rendered
    assert "5 CLI" in rendered
    assert str(summary.source_root) in rendered


def test_startup_auto_install_warns_without_blocking_the_command(monkeypatch) -> None:
    monkeypatch.delenv("CAFE_SKIP_GLOBAL_SKILL_SYNC", raising=False)
    monkeypatch.setattr(cli.sys, "argv", ["cafe", "prepare"])

    with (
        patch(
            "cafe.skills.global_installer.auto_sync_global_skills",
            side_effect=RuntimeError("trusted source unavailable"),
        ),
        patch.object(cli, "console") as mock_console,
    ):
        cli._auto_sync_global_helper_skills()

    assert "trusted source unavailable" in str(mock_console.print.call_args)
