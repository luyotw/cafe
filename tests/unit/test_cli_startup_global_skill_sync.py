"""Tests for cross-machine global helper skill synchronization at CLI startup."""

from unittest.mock import MagicMock, patch

from cafe.ui import cli


def test_main_auto_syncs_helpers_without_installing_runtime_updates(monkeypatch) -> None:
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


def test_startup_auto_sync_skips_explicit_sync_command(monkeypatch) -> None:
    monkeypatch.setattr(cli.sys, "argv", ["cafe", "skill", "sync-global"])

    with patch("cafe.skills.global_installer.auto_sync_global_skills") as mock_sync:
        cli._auto_sync_global_helper_skills()

    mock_sync.assert_not_called()


def test_startup_auto_sync_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setenv("CAFE_SKIP_GLOBAL_SKILL_SYNC", "1")

    with patch("cafe.skills.global_installer.auto_sync_global_skills") as mock_sync:
        cli._auto_sync_global_helper_skills()

    mock_sync.assert_not_called()


def test_startup_auto_sync_reports_compact_change_summary(monkeypatch) -> None:
    monkeypatch.setattr(cli.sys, "argv", ["cafe", "status"])
    summary = MagicMock(failed_count=0, changed_count=5)

    with (
        patch(
            "cafe.skills.global_installer.auto_sync_global_skills",
            return_value=summary,
        ),
        patch.object(cli, "console") as mock_console,
    ):
        cli._auto_sync_global_helper_skills()

    assert "Synchronized 5" in str(mock_console.print.call_args)


def test_startup_auto_sync_warns_without_blocking_the_command(monkeypatch) -> None:
    monkeypatch.setattr(cli.sys, "argv", ["cafe", "status"])

    with (
        patch(
            "cafe.skills.global_installer.auto_sync_global_skills",
            side_effect=RuntimeError("lock timeout"),
        ),
        patch.object(cli, "console") as mock_console,
    ):
        cli._auto_sync_global_helper_skills()

    assert "auto-sync failed: lock timeout" in str(mock_console.print.call_args)
