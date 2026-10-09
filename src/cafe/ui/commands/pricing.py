"""Inspect and refresh official rate cards without modifying history."""

import json
import os
from pathlib import Path

import typer

from cafe.core.pricing_sources import pricing_store

pricing_app = typer.Typer(help="Inspect or refresh official provider rates")


def _store(provider="openai"):
    cache = os.environ.get("CAFE_PRICING_CACHE_DIR")
    if cache and provider != "openai":
        cache = Path(cache) / provider
    return pricing_store(provider, cache)


def _emit(state, json_output, provider="openai"):
    snapshot = state["snapshot"]
    result = {
        "provider": provider,
        "version": snapshot["version"],
        "source_url": snapshot["source_url"],
        "fetched_at": snapshot["fetched_at"],
        "checked_at": state["checked_at"],
        "last_modified": state.get("last_modified") or snapshot.get("last_modified"),
        "etag": state.get("etag") or snapshot.get("etag"),
        "stale": state["stale"],
        "error": state.get("error"),
        "models": sorted(snapshot["data"]["rates"]),
    }
    if json_output:
        typer.echo(json.dumps(result, sort_keys=True))
    else:
        typer.echo(
            f"{provider} pricing: {'stale' if result['stale'] else 'current'}; "
            f"{len(result['models'])} models; version={result['version']}"
        )
        typer.echo(f"source={result['source_url']}")
        typer.echo(f"checked_at={result['checked_at']} last_modified={result['last_modified']}")
        if result["error"]:
            typer.echo(
                f"Refresh unavailable ({result['error']}); retaining the last valid snapshot."
            )


def _providers(provider):
    if provider == "all":
        return ("openai", "copilot", "cursor", "gemini")
    if provider not in {"openai", "copilot", "cursor", "gemini"}:
        raise typer.BadParameter("Choose openai, copilot, cursor, gemini or all")
    return (provider,)


@pricing_app.command("status")
def status(
    json_output: bool = typer.Option(False, "--json", help="Emit machine JSON"),
    provider: str = typer.Option(
        "openai", "--provider", help="openai, copilot, cursor, gemini or all"
    ),
):
    """Inspect cached/bundled pricing without making a network request."""
    for name in _providers(provider):
        store = _store() if name == "openai" else _store(name)
        _emit(store.status(), json_output, name)


@pricing_app.command("refresh")
def refresh(
    json_output: bool = typer.Option(False, "--json", help="Emit machine JSON"),
    provider: str = typer.Option(
        "openai", "--provider", help="openai, copilot, cursor, gemini or all"
    ),
):
    """Check the official page now, preserving all previously recorded costs."""
    failed = False
    for name in _providers(provider):
        store = _store() if name == "openai" else _store(name)
        state = store.get(force=True)
        _emit(state, json_output, name)
        failed = failed or bool(state.get("error"))
    if failed:
        raise typer.Exit(1)
