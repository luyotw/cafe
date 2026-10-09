"""Explicit CAFE runtime update commands."""

from __future__ import annotations

import json

import typer
from rich.console import Console

from cafe.updates.service import UpdateApplyError, UpdateCheckResult, UpdateService


update_app = typer.Typer(help="Check or apply an explicitly approved CAFE update")
console = Console()


def _build_update_service() -> UpdateService:
    return UpdateService()


def _emit_result(result: UpdateCheckResult, *, json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(result.to_dict(), sort_keys=True))
        return
    if result.status == "unavailable":
        console.print(
            f"[yellow]Update status unavailable; continuing with "
            f"{result.installed_version or 'the current version'}.[/yellow]"
        )
        return
    console.print(
        f"installed={result.installed_version} latest={result.latest_version} "
        f"status={result.status}"
    )
    if result.status == "update_available":
        console.print(f"release={result.release_url} token={result.token}")


@update_app.command(name="check")
def update_check(
    json_output: bool = typer.Option(False, "--json", help="Emit machine JSON"),
) -> None:
    """Check installed and latest versions without changing the environment."""
    _emit_result(_build_update_service().check(), json_output=json_output)


@update_app.command(name="apply")
def update_apply(
    token: str = typer.Option(
        ..., "--token", help="Approval token returned by `cafe update check`"
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine JSON"),
) -> None:
    """Apply the exact release bound to a fresh approval token."""
    try:
        result = _build_update_service().apply(token)
    except UpdateApplyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    _emit_result(result, json_output=json_output)
