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

    def dispatch(path, request):
        observed.update(path=path, request=request)
        return SimpleNamespace(status="proposed", changes={path: request.value})

    monkeypatch.setattr("cafe.ui.commands.settings.dispatch_setting_update", dispatch)
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
    assert observed["path"] == "driver"
    assert observed["request"].preview is True
    assert observed["request"].value["clis"] == [{"cli": "claude"}]
    assert json.loads(result.stdout)["status"] == "proposed"


def test_settings_cli_rejects_unknown_paths_and_batches_before_owner_calls(monkeypatch) -> None:
    called = []

    def dispatch(path, _request):
        called.append(path)
        raise ValueError(f"unsupported or protected settings path: {path}")

    monkeypatch.setattr("cafe.ui.commands.settings.dispatch_setting_update", dispatch)

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
    assert called == ["phases"]

    traversal = runner.invoke(
        app,
        ["settings", "update", "../outside", "--set", "pr.auto_create=false"],
    )
    assert traversal.exit_code == 1
    assert called == ["phases"]


def test_settings_cli_routes_exact_pr_boolean_in_human_mode(monkeypatch) -> None:
    observed = {}
    monkeypatch.setattr(
        "cafe.ui.commands.settings.resolve_issue_config_path",
        lambda path, **_kwargs: path,
    )

    def dispatch(path, request):
        observed.update(path=path, request=request)
        return SimpleNamespace(
            status="saved",
            changes={path: {"before": True, "after": request.value}},
        )

    monkeypatch.setattr("cafe.ui.commands.settings.dispatch_setting_update", dispatch)
    result = runner.invoke(
        app,
        ["settings", "update", "demo", "--set", "pr.auto_create=false"],
    )

    assert result.exit_code == 0
    assert observed["path"] == "pr.auto_create"
    assert observed["request"].value is False
    assert result.stdout.startswith("saved:")
