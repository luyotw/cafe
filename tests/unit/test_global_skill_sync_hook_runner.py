"""Tests for the minimal global skill Git-hook runner."""

from unittest.mock import MagicMock, patch

from cafe.skills import global_sync_hook


def test_hook_runner_is_silent_when_every_copy_is_unchanged(capsys) -> None:
    summary = MagicMock(failed_count=0, changed_count=0)
    with patch.object(global_sync_hook, "sync_global_skills", return_value=summary):
        result = global_sync_hook.main()

    assert result == 0
    assert capsys.readouterr().out == ""


def test_hook_runner_reports_changes_compactly(capsys) -> None:
    summary = MagicMock(failed_count=0, changed_count=5)
    with patch.object(
        global_sync_hook,
        "sync_global_skills",
        return_value=summary,
    ):
        result = global_sync_hook.main()

    assert result == 0
    assert "Synchronized 5" in capsys.readouterr().out


def test_hook_runner_returns_failure_with_destination_details(capsys) -> None:
    failure = MagicMock(
        status="failed",
        cli="codex",
        skill="use-cafe-workflow",
        reason="permission denied",
    )
    summary = MagicMock(failed_count=1, results=[failure])
    with patch.object(
        global_sync_hook,
        "sync_global_skills",
        return_value=summary,
    ):
        result = global_sync_hook.main()

    assert result == 1
    assert "codex/use-cafe-workflow: permission denied" in capsys.readouterr().err
