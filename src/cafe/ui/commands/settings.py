"""Targeted, issue-scoped settings updates."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import typer

from cafe.core.capability_setup import update_pr_auto_create
from cafe.driver import update_driver_settings
from cafe.driver._store import load_contract
from cafe.utils.issue_config import resolve_issue_config_path

settings_app = typer.Typer(help="Preview or save supported issue settings")


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


@settings_app.command(name="update")
def settings_update(
    issue: str = typer.Argument(..., help="Explicit workflow issue name"),
    settings: list[str] = typer.Option(..., "--set", help="Exactly one PATH=JSON update"),
    preview: bool = typer.Option(False, "--preview", help="Validate without writing"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine JSON"),
) -> None:
    """Update the complete Driver object or capability-owned pr.auto_create."""
    try:
        issue_path = Path(issue)
        if issue_path.name != issue or issue in {"", ".", ".."}:
            raise ValueError("ISSUE must identify exactly one issue directory")
        if len(settings) != 1:
            raise ValueError("exactly one --set is required; cross-owner batches are unsupported")
        path, separator, encoded = settings[0].partition("=")
        if not separator:
            raise ValueError("--set uses PATH=JSON")
        try:
            value = json.loads(encoded)
        except json.JSONDecodeError as exc:
            raise ValueError(f"--set requires JSON for {path}") from exc
        if path not in {"driver", "pr.auto_create"}:
            raise ValueError(f"unsupported or protected settings path: {path}")

        config_path = resolve_issue_config_path(
            Path(".cafe") / "issues" / issue / "issue.yaml",
            require_registered_worktree=True,
        )
        if path == "driver":
            if not isinstance(value, dict):
                raise ValueError("driver must be a JSON object")
            contract, _digest = load_contract(config_path.parent, allow_legacy_upgrade=True)
            result = update_driver_settings(
                issue_dir=config_path.parent,
                issue_name=contract["identity"]["issue_name"],
                workflow_id=contract["identity"]["workflow_id"],
                driver=value,
                preview=preview,
            )
        elif path == "pr.auto_create":
            result = update_pr_auto_create(config_path=config_path, value=value, preview=preview)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    payload = {"status": result.status, "changes": _plain(result.changes)}
    if json_output:
        typer.echo(json.dumps(payload, sort_keys=True))
    else:
        typer.echo(f"{result.status}: {json.dumps(payload['changes'], sort_keys=True)}")
