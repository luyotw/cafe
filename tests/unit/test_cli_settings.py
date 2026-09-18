"""CLI coverage for targeted settings owner routing."""

import json
from types import SimpleNamespace

from typer.testing import CliRunner

from cafe.ui.cli import app

runner = CliRunner()


def test_settings_cli_routes_one_driver_object(monkeypatch) -> None:
    observed = {}
    monkeypatch.setattr(
        "cafe.ui.commands.settings.resolve_issue_config_path",
        lambda path, **_kwargs: path,
    )
    monkeypatch.setattr(
        "cafe.ui.commands.settings.load_contract",
        lambda *_args, **_kwargs: (
            {"identity": {"issue_name": "demo", "workflow_id": "workflow-1"}},
            "digest",
        ),
    )

    def update(**kwargs):
        observed.update(kwargs)
        return SimpleNamespace(status="proposed", changes={"driver": kwargs["driver"]})

    monkeypatch.setattr("cafe.ui.commands.settings.update_driver_settings", update)
    result = runner.invoke(
        app,
        [
            "settings",
            "update",
            "demo",
            "--set",
            'driver={"mode":"event-driven","clis":[{"cli":"claude"}]}',
            "--preview",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert observed["preview"] is True
    assert observed["driver"]["clis"] == [{"cli": "claude"}]
    assert json.loads(result.stdout)["status"] == "proposed"


def test_settings_cli_rejects_unknown_paths_and_batches_before_owner_calls(monkeypatch) -> None:
    called = []
    monkeypatch.setattr(
        "cafe.ui.commands.settings.update_driver_settings",
        lambda **_kwargs: called.append("driver"),
    )
    monkeypatch.setattr(
        "cafe.ui.commands.settings.update_pr_auto_create",
        lambda **_kwargs: called.append("pr"),
    )

    unknown = runner.invoke(app, ["settings", "update", "demo", "--set", "phases=[]"])
    batch = runner.invoke(
        app,
        [
            "settings",
            "update",
            "demo",
            "--set",
            'driver={"mode":"unattended"}',
            "--set",
            "pr.auto_create=false",
        ],
    )

    assert unknown.exit_code == 1
    assert batch.exit_code == 1
    assert called == []

    traversal = runner.invoke(
        app,
        ["settings", "update", "../outside", "--set", "pr.auto_create=false"],
    )
    assert traversal.exit_code == 1
    assert called == []


def test_settings_cli_routes_exact_pr_boolean_in_human_mode(monkeypatch) -> None:
    observed = {}
    monkeypatch.setattr(
        "cafe.ui.commands.settings.resolve_issue_config_path",
        lambda path, **_kwargs: path,
    )

    def update(**kwargs):
        observed.update(kwargs)
        return SimpleNamespace(
            status="saved",
            changes={"pr.auto_create": {"before": True, "after": kwargs["value"]}},
        )

    monkeypatch.setattr("cafe.ui.commands.settings.update_pr_auto_create", update)
    result = runner.invoke(
        app,
        ["settings", "update", "demo", "--set", "pr.auto_create=false"],
    )

    assert result.exit_code == 0
    assert observed["value"] is False
    assert result.stdout.startswith("saved:")
